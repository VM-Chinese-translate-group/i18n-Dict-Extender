import os
import sys
import requests
import yaml
import sqlite3
import json
import re
import tempfile
import zipfile
import shutil
from pathlib import Path
from collections import defaultdict

# --- 配置常量 ---
CONFIG_FILE = Path(__file__).parent.parent / "config/source_mods.yml"
DB_FILENAME = "Dict-Sqlite.db"
JSON_FILENAME = "Dict.json"
MINI_JSON_FILENAME = "Dict-Mini.json"

SOURCE_DB_REPO = "CFPATools/i18n-dict"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")

if not GITHUB_TOKEN or not GITHUB_REPO:
    print("错误：环境变量 GITHUB_TOKEN 和 GITHUB_REPOSITORY 未设置。")
    sys.exit(1)

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}

# --- 辅助函数 ---

def get_latest_release_db():
    """从上游仓库 CFPATools/i18n-dict 的最新 Release 下载 Dict-Sqlite.db 文件。"""
    print(f"正在从上游仓库 {SOURCE_DB_REPO} 获取最新的数据库...")
    release_url = f"https://api.github.com/repos/{SOURCE_DB_REPO}/releases/latest"
    
    response = requests.get(release_url, headers=HEADERS)
    if response.status_code != 200:
        print(f"警告：无法从 {SOURCE_DB_REPO} 获取最新 Release。将创建一个新的数据库。")
        return False

    assets = response.json().get("assets", [])
    db_asset = next((asset for asset in assets if asset['name'] == DB_FILENAME), None)

    if not db_asset:
        print(f"警告：在 {SOURCE_DB_REPO} 的最新 Release 中未找到 {DB_FILENAME}。将创建一个新的数据库。")
        return False

    print(f"正在从 {SOURCE_DB_REPO} 的最新 Release 下载 {DB_FILENAME}...")
    download_url = db_asset['url']
    headers_for_download = HEADERS.copy()
    headers_for_download['Accept'] = 'application/octet-stream'
    
    with requests.get(download_url, headers=headers_for_download, stream=True) as r:
        r.raise_for_status()
        with open(DB_FILENAME, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    print(f"{DB_FILENAME} 下载完成。")
    return True

def get_repo_default_branch(repo_slug):
    """获取指定仓库的默认分支名。"""
    print(f"正在获取仓库 {repo_slug} 的默认分支...")
    repo_info_url = f"https://api.github.com/repos/{repo_slug}"
    response = requests.get(repo_info_url, headers=HEADERS)
    response.raise_for_status()
    return response.json()['default_branch']

def parse_version_from_branch(branch_name):
    """从分支名中提取游戏版本号，例如 'mc1.20.1/dev' -> '1.20'。"""
    match = re.search(r'(\d+\.\d+)', branch_name)
    if match:
        version = match.group(1)
        print(f"从分支名 '{branch_name}' 中解析出版本号: {version}")
        return version
    print(f"警告：无法从分支名 '{branch_name}' 中解析版本号。")
    return "unknown"

def process_repo(mod_config, db_cursor):
    """处理单个模组仓库，提取翻译并更新数据库。"""
    repo_slug = mod_config['repo']
    print(f"\n--- 开始处理模组: {repo_slug} ---")
    try:
        branch = mod_config.get('branch') or get_repo_default_branch(repo_slug)
        version = mod_config.get('version') or parse_version_from_branch(branch)

        zip_url = f"https://api.github.com/repos/{repo_slug}/zipball/{branch}"
        print(f"正在从 {zip_url} 下载仓库...")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            zip_path = tmp_path / "repo.zip"

            with requests.get(zip_url, headers=HEADERS, stream=True) as r:
                r.raise_for_status()
                with open(zip_path, 'wb') as f: f.write(r.content)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_path)
            
            extracted_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
            if len(extracted_dirs) != 1:
                print(f"错误：解压后发现 {len(extracted_dirs)} 个顶层文件夹，预期为1个。无法继续处理 {repo_slug}。")
                return
            
            repo_root_dir = extracted_dirs[0]
            print(f"找到解压后的仓库根目录: {repo_root_dir.name}")
            
            lang_paths_config = mod_config.get('lang_paths', [])
            if not lang_paths_config and mod_config.get('lang_path'):
                lang_paths_config = [mod_config.get('lang_path')]
            
            if not lang_paths_config:
                print(f"错误：仓库 {repo_slug} 的配置中缺少 'lang_paths'。跳过此模组。")
                return

            found_lang_dir = None
            for relative_path in lang_paths_config:
                # 所有路径查找都基于找到的仓库根目录
                potential_dir = repo_root_dir / relative_path
                if (potential_dir / "en_us.json").exists() and (potential_dir / "zh_cn.json").exists():
                    print(f"在路径 '{relative_path}' 中找到语言文件。")
                    found_lang_dir = potential_dir
                    break
            
            if not found_lang_dir:
                print(f"错误：在所有指定路径 {lang_paths_config} 中均未找到 en_us.json 和 zh_cn.json。跳过此模组。")
                return

            # 读取和处理语言文件
            with open(found_lang_dir / "en_us.json", 'r', encoding='utf-8') as f: en_data = json.load(f)
            with open(found_lang_dir / "zh_cn.json", 'r', encoding='utf-8') as f: zh_data = json.load(f)

            common_keys = en_data.keys() & zh_data.keys()
            print(f"找到 {len(common_keys)} 个共同的翻译键。")
            
            update_count, insert_count = 0, 0
            for key in common_keys:
                origin_name, trans_name = en_data[key], zh_data[key]
                modid, curseforge = mod_config['modid'], mod_config['curseforge']

                db_cursor.execute("SELECT ID FROM dict WHERE MODID=? AND KEY=? AND VERSION=? AND CURSEFORGE=?", (modid, key, version, curseforge))
                existing_entry = db_cursor.fetchone()

                if existing_entry:
                    db_cursor.execute("UPDATE dict SET ORIGIN_NAME=?, TRANS_NAME=? WHERE ID=?", (origin_name, trans_name, existing_entry[0]))
                    update_count += 1
                else:
                    db_cursor.execute("INSERT INTO dict (ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE) VALUES (?, ?, ?, ?, ?, ?)", (origin_name, trans_name, modid, key, version, curseforge))
                    insert_count += 1
            
            print(f"处理完成：{update_count} 个条目已更新，{insert_count} 个条目已插入。")

    except Exception as e:
        print(f"处理仓库 {repo_slug} 时发生错误: {e}")
        import traceback
        traceback.print_exc()

def initialize_db(conn):
    print("正在初始化新的数据库...")
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dict(
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        ORIGIN_NAME     TEXT    NOT NULL,
        TRANS_NAME      TEXT    NOT NULL,
        MODID           TEXT    NOT NULL,
        KEY             TEXT    NOT NULL,
        VERSION         TEXT    NOT NULL,
        CURSEFORGE      TEXT    NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_origin_name ON dict (ORIGIN_NAME);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lookup ON dict (MODID, KEY, VERSION, CURSEFORGE);")
    conn.commit()
    print("数据库初始化完成。")

def regenerate_release_files():
    """
    从更新后的数据库重新生成 Dict.json 和 Dict-Mini.json。
    此函数的逻辑严格遵循参考项目的代码，以确保生成的文件内容和格式一致。
    """
    print("\n--- 开始从数据库重新生成 Release 文件 (遵循源项目逻辑) ---")
    if not Path(DB_FILENAME).exists():
        print(f"错误：{DB_FILENAME} 不存在，无法生成 JSON 文件。")
        return

    # 1. 从数据库读取所有数据
    conn = sqlite3.connect(DB_FILENAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE FROM dict")
    # 将数据库行转换为与源项目 'i' 变量结构一致的字典列表
    all_db_entries = [{
        'origin_name': row['ORIGIN_NAME'],
        'trans_name': row['TRANS_NAME'],
        'modid': row['MODID'],
        'key': row['KEY'],
        'version': row['VERSION'],
        'curseforge': row['CURSEFORGE']
    } for row in cursor.fetchall()]
    conn.close()

    # 2. 遵循源项目的逻辑处理数据
    integral = []
    integral_mini_temp = defaultdict(list)

    print(f'处理从数据库读取的 {len(all_db_entries)} 个词条中...')
    for entry in all_db_entries:
        # 筛选条件1: 原文长度不能超过50
        if len(entry['origin_name']) > 50:
            continue
        # 筛选条件2: 原文不能为空
        if entry['origin_name'] == '':
            continue
        
        # 加入完整词典
        integral.append(entry)
        
        # 筛选条件3: 只有原文和译文不同时才加入迷你词典
        if entry['origin_name'] != entry['trans_name']:
            integral_mini_temp[entry['origin_name']].append(entry['trans_name'])
    
    # 3. 整理迷你词典：去重并按出现次数排序
    integral_mini_final = {}
    for origin_name, trans_list in integral_mini_temp.items():
        # nset: 获取所有不重复的译名
        nset = set(trans_list)
        # 排序：根据每个译名在原始列表(nlist)中的出现次数进行降序排序
        sorted_trans = sorted(nset, key=lambda x: trans_list.count(x), reverse=True)
        integral_mini_final[origin_name] = sorted_trans

    print('开始生成整合文件')

    # 4. 生成JSON文本，格式与源项目完全一致
    # Dict.json: 格式化，缩进为4
    text = json.dumps(integral, ensure_ascii=False, indent=4)
    # Dict-Mini.json: 压缩格式，无多余空格
    mini_text = json.dumps(integral_mini_final, ensure_ascii=False, separators=(',', ':'))

    # 5. 保存文件
    if text != '[]':
        Path(JSON_FILENAME).write_text(text, encoding='utf-8')
        print(f'已生成 {JSON_FILENAME}，共有词条 {len(integral)} 个')
    else:
        print(f'{JSON_FILENAME} 为空，不生成文件。')

    if mini_text != '{}':
        Path(MINI_JSON_FILENAME).write_text(mini_text, encoding='utf-8')
        print(f'已生成 {MINI_JSON_FILENAME}，共有词条 {len(integral_mini_final)} 个')
    else:
        print(f'{MINI_JSON_FILENAME} 为空，不生成文件。')

# --- 主逻辑 ---
def main():
    # 步骤 1: 获取或创建数据库
    if not get_latest_release_db():
        conn = sqlite3.connect(DB_FILENAME)
        initialize_db(conn)
    else:
        conn = sqlite3.connect(DB_FILENAME)
    
    cursor = conn.cursor()

    # 步骤 2: 读取配置并处理每个模组
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    for mod_config in config.get('mods', []):
        process_repo(mod_config, cursor)
    
    conn.commit()
    conn.close()

    # 步骤 3: 从更新后的数据库重新生成所有文件
    regenerate_release_files()

    print(f"\n所有任务完成！将在仓库 {GITHUB_REPO} 上创建 Release。")

if __name__ == "__main__":
    main()