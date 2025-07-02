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
        print(f"警告：无法从 {SOURCE_DB_REPO} 获取最新 Release。可能这是第一次运行或上游仓库无 Release。将创建一个新的数据库。")
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
        # 确定分支和版本
        branch = mod_config.get('branch')
        if not branch:
            branch = get_repo_default_branch(repo_slug)
        
        version = mod_config.get('version')
        if not version:
            version = parse_version_from_branch(branch)

        # 下载仓库 zip 包
        zip_url = f"https://api.github.com/repos/{repo_slug}/zipball/{branch}"
        print(f"正在从 {zip_url} 下载仓库...")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            zip_path = tmp_path / "repo.zip"

            with requests.get(zip_url, headers=HEADERS, stream=True) as r:
                r.raise_for_status()
                with open(zip_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # 解压
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_path)
            
            # 找到解压后的根目录
            extracted_dir = next(tmp_path.iterdir())
            if not extracted_dir.is_dir():
                 extracted_dir = next(tmp_path.iterdir())
            
            lang_dir = extracted_dir / mod_config['lang_path']
            en_path = lang_dir / "en_us.json"
            zh_path = lang_dir / "zh_cn.json"

            if not en_path.exists() or not zh_path.exists():
                print(f"错误：在 {lang_dir} 中未找到 en_us.json 或 zh_cn.json。跳过此模组。")
                return

            # 读取和处理语言文件
            with open(en_path, 'r', encoding='utf-8') as f:
                en_data = json.load(f)
            with open(zh_path, 'r', encoding='utf-8') as f:
                zh_data = json.load(f)

            common_keys = en_data.keys() & zh_data.keys()
            print(f"找到 {len(common_keys)} 个共同的翻译键。")
            
            update_count = 0
            insert_count = 0

            for key in common_keys:
                origin_name = en_data[key]
                trans_name = zh_data[key]
                modid = mod_config['modid']
                curseforge = mod_config['curseforge']

                db_cursor.execute("""
                    SELECT ID FROM dict WHERE MODID=? AND KEY=? AND VERSION=? AND CURSEFORGE=?
                """, (modid, key, version, curseforge))
                
                existing_entry = db_cursor.fetchone()

                if existing_entry:
                    db_cursor.execute("""
                        UPDATE dict SET ORIGIN_NAME=?, TRANS_NAME=? WHERE ID=?
                    """, (origin_name, trans_name, existing_entry[0]))
                    update_count += 1
                else:
                    db_cursor.execute("""
                        INSERT INTO dict (ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (origin_name, trans_name, modid, key, version, curseforge))
                    insert_count += 1
            
            print(f"处理完成：{update_count} 个条目已更新，{insert_count} 个条目已插入。")

    except Exception as e:
        print(f"处理仓库 {repo_slug} 时发生错误: {e}")
        import traceback
        traceback.print_exc()


def initialize_db(conn):
    """初始化数据库表结构。"""
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
    """从更新后的数据库重新生成 Dict.json 和 Dict-Mini.json。"""
    print("\n--- 开始从数据库重新生成 Release 文件 ---")
    if not Path(DB_FILENAME).exists():
        print(f"错误：{DB_FILENAME} 不存在，无法生成 JSON 文件。")
        return

    conn = sqlite3.connect(DB_FILENAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print(f"正在生成 {JSON_FILENAME}...")
    cursor.execute("SELECT ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE FROM dict")
    all_entries = [dict(row) for row in cursor.fetchall()]
    
    with open(JSON_FILENAME, 'w', encoding='utf-8') as f:
        json.dump(all_entries, f, ensure_ascii=False, indent=4)
    print(f"{JSON_FILENAME} 生成完毕。")

    print(f"正在生成 {MINI_JSON_FILENAME}...")
    trans_counts = defaultdict(lambda: defaultdict(int))
    for entry in all_entries:
        trans_counts[entry['ORIGIN_NAME']][entry['TRANS_NAME']] += 1

    mini_dict = {}
    for origin, trans_map in trans_counts.items():
        sorted_trans = sorted(trans_map.keys(), key=lambda t: trans_map[t], reverse=True)
        mini_dict[origin] = sorted_trans
        
    with open(MINI_JSON_FILENAME, 'w', encoding='utf-8') as f:
        json.dump(mini_dict, f, ensure_ascii=False, indent=2)
    print(f"{MINI_JSON_FILENAME} 生成完毕。")

    conn.close()

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