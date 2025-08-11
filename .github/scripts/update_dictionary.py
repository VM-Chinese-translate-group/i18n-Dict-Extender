import os
import sys
import requests
import yaml
import sqlite3
import re
import tempfile
import zipfile
from pathlib import Path
from collections import defaultdict, Counter
import json

# --- 配置常量 ---
CONFIG_FILE = Path(__file__).parent.parent / "config/source_mods.yml"
DB_FILENAME = "Dict-Sqlite.db"
JSON_FILENAME = "Dict.json"
MINI_JSON_FILENAME = "Dict-Mini.json"
DIFF_JSON_FILENAME = "diff.json"
RELEASE_BODY_FILENAME = "release_body.md"

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


def find_file_case_insensitive(directory, filename):
    """在目录中不区分大小写地查找文件。"""
    if not directory.is_dir():
        return None
    filename_lower = filename.lower()
    for item in directory.iterdir():
        if item.is_file() and item.name.lower() == filename_lower:
            return item
    return None


def parse_lang_file(f):
    """解析 .lang 文件流，返回一个字典。忽略注释和空行。"""
    data = {}
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            data[key.strip()] = value.strip()
    return data


def process_repo(mod_config, db_cursor, diff_entries):
    """处理单个模组仓库，提取翻译并更新数据库。"""
    repo_slug = mod_config['repo']
    print(f"\n--- 开始处理模组: {repo_slug} ---")

    branch_name_for_summary = "N/A"
    update_count, insert_count = 0, 0  # 在开头初始化

    try:
        branch = mod_config.get('branch') or get_repo_default_branch(repo_slug)
        branch_name_for_summary = branch # 保存分支名用于摘要
        version = mod_config.get('version') or parse_version_from_branch(branch)

        # 根据版本决定文件格式和加载函数
        major_str, minor_str, *_ = (version + '.0.0').split('.')
        use_json = int(major_str) > 1 or (int(major_str) == 1 and int(minor_str) >= 13)

        if use_json:
            print(f"版本 {version} >= 1.13，将读取 .json 文件。")
            en_filename = "en_us.json"
            zh_filename = "zh_cn.json"
            load_func = json.load
        else:
            print(f"版本 {version} <= 1.12，将读取 .lang 文件。")
            en_filename = "en_us.lang"
            zh_filename = "zh_cn.lang"
            load_func = parse_lang_file

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
            
            repo_root_dir = next(d for d in tmp_path.iterdir() if d.is_dir())
            print(f"找到解压后的仓库根目录: {repo_root_dir.name}")
            
            lang_paths_config = mod_config.get('lang_paths', [])
            if not lang_paths_config and mod_config.get('lang_path'):
                lang_paths_config = [mod_config.get('lang_path')]
            if not lang_paths_config:
                raise ValueError(f"仓库 {repo_slug} 的配置中缺少 'lang_paths'。")

            en_data, zh_data = {}, {}
            merge_mode = mod_config.get('merge_paths', False)

            if merge_mode:
                print("模式：合并多个语言文件。")
                for relative_path in lang_paths_config:
                    lang_dir = repo_root_dir / relative_path
                    if en_file_path := find_file_case_insensitive(lang_dir, en_filename):
                        with open(en_file_path, 'r', encoding='utf-8') as f: en_data.update(load_func(f))
                    if zh_file_path := find_file_case_insensitive(lang_dir, zh_filename):
                        with open(zh_file_path, 'r', encoding='utf-8') as f: zh_data.update(load_func(f))
                if not en_data or not zh_data:
                    raise FileNotFoundError(f"合并模式下，未能找到 {en_filename} 或 {zh_filename} 文件。")

            else:
                print("模式：按优先级查找单个语言文件。")
                en_path, zh_path = None, None
                for p in lang_paths_config:
                    lang_dir = repo_root_dir / p
                    if not en_path:
                        en_path = find_file_case_insensitive(lang_dir, en_filename)
                    if not zh_path:
                        zh_path = find_file_case_insensitive(lang_dir, zh_filename)
                    if en_path and zh_path: break

                if not en_path or not zh_path:
                    raise FileNotFoundError(f"未能找到 {en_filename} 或 {zh_filename}。")

                with open(en_path, 'r', encoding='utf-8') as f:
                    en_data = load_func(f)
                with open(zh_path, 'r', encoding='utf-8') as f:
                    zh_data = load_func(f)

            common_keys = en_data.keys() & zh_data.keys()
            print(f"找到 {len(common_keys)} 个共同的翻译键。")

            # 1. 一次性查询出所有可能相关的现有条目
            db_cursor.execute("SELECT key, ID FROM dict WHERE modid=? AND version=? AND curseforge=?",
                              (mod_config['modid'], version, mod_config['curseforge']))
            existing_entries_map = {row[0]: row[1] for row in db_cursor.fetchall()}

            # 2. 在内存中分类需要更新和需要插入的数据
            to_update = []
            to_insert = []
            skipped_count = 0 # 初始化计数器

            for key in common_keys:
                origin_value = en_data[key]
                trans_value = zh_data[key]

                # 检查原文和译文的值是否都是字符串，如果不是，则跳过
                if not isinstance(origin_value, str) or not isinstance(trans_value, str):
                    skipped_count += 1
                    continue

                entry_data = {
                    'origin_name': origin_value,
                    'trans_name': trans_value,
                    'modid': mod_config['modid'], 'key': key,
                    'version': version, 'curseforge': mod_config['curseforge']
                }
                diff_entries.append(entry_data)

                existing_id = existing_entries_map.get(key)

                if existing_id:
                    # 准备更新数据: (origin, trans, id)
                    to_update.append((entry_data['origin_name'], entry_data['trans_name'], existing_id))
                else:
                    # 准备插入数据
                    to_insert.append(tuple(entry_data.values()))

            # 报告跳过的条目数量
            if skipped_count > 0:
                print(f"已跳过 {skipped_count} 个非字符串值的词条 (例如 JSON 文本组件)。")
            # 3. 使用 executemany() 进行批量更新和插入
            if to_update:
                db_cursor.executemany("UPDATE dict SET ORIGIN_NAME=?, TRANS_NAME=? WHERE ID=?", to_update)
            if to_insert:
                db_cursor.executemany(
                    "INSERT INTO dict (ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE) VALUES (?, ?, ?, ?, ?, ?)",
                    to_insert)

            update_count, insert_count = len(to_update), len(to_insert)

            print(f"处理完成：{update_count} 个条目已更新，{insert_count} 个条目已插入。")

    except Exception as e:
        print(f"处理仓库 {repo_slug} 时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return {'repo': repo_slug, 'branch': branch_name_for_summary, 'updated': 0, 'inserted': 0, 'error': str(e)}

    return {'repo': repo_slug, 'branch': branch_name_for_summary, 'updated': update_count, 'inserted': insert_count,
            'error': None}


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
    """
    从更新后的数据库重新生成 Dict.json 和 Dict-Mini.json。
    此函数的逻辑严格遵循参考项目的代码，以确保生成的文件内容和格式一致。
    """
    print("\n--- 开始从数据库重新生成 Release 文件 (遵循源项目逻辑) ---")
    if not Path(DB_FILENAME).exists():
        print(f"错误：{DB_FILENAME} 不存在，无法生成 JSON 文件。")
        return

    conn = sqlite3.connect(DB_FILENAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print(f"正在生成 {JSON_FILENAME}...")
    cursor.execute("SELECT ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE FROM dict")
    all_db_entries = [
        {'origin_name': r['ORIGIN_NAME'], 'trans_name': r['TRANS_NAME'], 'modid': r['MODID'], 'key': r['KEY'],
         'version': r['VERSION'], 'curseforge': r['CURSEFORGE']} for r in cursor.fetchall()]
    conn.close()

    integral = []
    integral_mini_temp = defaultdict(list)

    print(f'处理从数据库读取的 {len(all_db_entries)} 个词条中...')
    for entry in all_db_entries:
        if len(entry['origin_name']) > 50 or entry['origin_name'] == '': continue
        integral.append(entry)
        if entry['origin_name'] != entry['trans_name']:
            integral_mini_temp[entry['origin_name']].append(entry['trans_name'])

    # 使用 Counter 进行高效排序
    integral_mini_final = {
        origin_name: [item for item, count in Counter(trans_list).most_common()]
        for origin_name, trans_list in integral_mini_temp.items()
    }

    print('开始生成整合文件')

    text = json.dumps(integral, ensure_ascii=False, indent=4)
    mini_text = json.dumps(integral_mini_final, ensure_ascii=False, separators=(',', ':'))

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

# --- 生成 Release Body 的 Markdown 文本 ---
def generate_release_body(summaries, diff_count):
    body = []
    body.append("## 自动词典数据更新")
    body.append(f"本次运行共计处理了 **{diff_count}** 个新增或更新的词条。")
    body.append("\n### 数据来源与变更摘要\n")
    
    if not summaries:
        body.append("本次运行未从任何仓库拉取新数据。")
        return "\n".join(body)

    # 表头
    body.append("| 模组仓库 | 分支 | 新增条目 | 更新条目 | 状态 |")
    body.append("|---|---|---:|---:|:---|")
    
    # 表格内容
    for s in summaries:
        status = "✅ 成功" if not s.get('error') else f"❌ 失败: `{s['error']}`"
        row = f"| `{s['repo']}` | `{s['branch']}` | {s['inserted']} | {s['updated']} | {status} |"
        body.append(row)
        
    body.append("\n`diff.json` 文件包含了本次运行所有新增和更新的条目详情。")
    return "\n".join(body)

def main():
    if not get_latest_release_db():
        conn = sqlite3.connect(DB_FILENAME)
        initialize_db(conn)
    else:
        conn = sqlite3.connect(DB_FILENAME)
    
    cursor = conn.cursor()
    
    run_summaries = []
    diff_entries = [] # 存储所有变动的条目

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    for mod_config in config.get('mods', []):
        # 传入 diff_entries 列表以收集变动
        summary = process_repo(mod_config, cursor, diff_entries)
        if summary:
            run_summaries.append(summary)
            
    conn.commit()
    conn.close()

    # 从更新后的数据库重新生成主要文件
    regenerate_release_files()
    
    # 生成 diff.json
    print(f"\n正在生成 {DIFF_JSON_FILENAME}，包含 {len(diff_entries)} 个变动条目...")
    with open(DIFF_JSON_FILENAME, 'w', encoding='utf-8') as f:
        json.dump(diff_entries, f, ensure_ascii=False, indent=4)
    print(f"{DIFF_JSON_FILENAME} 生成完毕。")

    # 生成 Release Body 文件
    print(f"正在生成 {RELEASE_BODY_FILENAME}...")
    release_body_content = generate_release_body(run_summaries, len(diff_entries))
    Path(RELEASE_BODY_FILENAME).write_text(release_body_content, encoding='utf-8')
    print(f"{RELEASE_BODY_FILENAME} 生成完毕。")
    
    print(f"\n所有任务完成！将在仓库 {GITHUB_REPO} 上创建 Release。")

if __name__ == "__main__":
    main()