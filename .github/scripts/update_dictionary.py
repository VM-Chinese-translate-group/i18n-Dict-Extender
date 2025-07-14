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

def process_repo(mod_config, db_cursor, diff_entries):
    """处理单个模组仓库，提取翻译并更新数据库。"""
    repo_slug = mod_config['repo']
    print(f"\n--- 开始处理模组: {repo_slug} ---")
    
    update_count, insert_count = 0, 0
    branch_name_for_summary = "N/A"

    try:
        branch = mod_config.get('branch') or get_repo_default_branch(repo_slug)
        branch_name_for_summary = branch # 保存分支名用于摘要
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
                found_any_en, found_any_zh = False, False
                for relative_path in lang_paths_config:
                    en_json_path = repo_root_dir / relative_path / "en_us.json"
                    zh_json_path = repo_root_dir / relative_path / "zh_cn.json"

                    if en_json_path.exists():
                        print(f"  -> 正在从 '{relative_path}' 合并 en_us.json...")
                        with open(en_json_path, 'r', encoding='utf-8') as f:
                            en_data.update(json.load(f))
                        found_any_en = True
                    
                    if zh_json_path.exists():
                        print(f"  -> 正在从 '{relative_path}' 合并 zh_cn.json...")
                        with open(zh_json_path, 'r', encoding='utf-8') as f:
                            zh_data.update(json.load(f))
                        found_any_zh = True
                
                if not found_any_en or not found_any_zh:
                    raise FileNotFoundError("合并模式下，未能找到 en_us.json 或 zh_cn.json 文件。")

            else:
                print("模式：按优先级查找单个语言文件。")
                en_path, zh_path = None, None
                for p in lang_paths_config:
                    if not en_path and (repo_root_dir / p / "en_us.json").exists(): en_path = repo_root_dir / p / "en_us.json"
                    if not zh_path and (repo_root_dir / p / "zh_cn.json").exists(): zh_path = repo_root_dir / p / "zh_cn.json"
                    if en_path and zh_path: break
                
                if not en_path or not zh_path:
                    raise FileNotFoundError(f"未能找到 en_us.json 或 zh_cn.json。")
                
                with open(en_path, 'r', encoding='utf-8') as f: en_data = json.load(f)
                with open(zh_path, 'r', encoding='utf-8') as f: zh_data = json.load(f)

            common_keys = en_data.keys() & zh_data.keys()
            print(f"找到 {len(common_keys)} 个共同的翻译键。")
            
            for key in common_keys:
                entry_data = {
                    'origin_name': en_data[key], 'trans_name': zh_data[key],
                    'modid': mod_config['modid'], 'key': key,
                    'version': version, 'curseforge': mod_config['curseforge']
                }
                
                db_cursor.execute("SELECT ID FROM dict WHERE MODID=? AND KEY=? AND VERSION=? AND CURSEFORGE=?",
                                  (entry_data['modid'], entry_data['key'], entry_data['version'], entry_data['curseforge']))
                existing_entry = db_cursor.fetchone()

                if existing_entry:
                    db_cursor.execute("UPDATE dict SET ORIGIN_NAME=?, TRANS_NAME=? WHERE ID=?",
                                      (entry_data['origin_name'], entry_data['trans_name'], existing_entry[0]))
                    update_count += 1
                else:
                    db_cursor.execute("INSERT INTO dict (ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE) VALUES (?, ?, ?, ?, ?, ?)",
                                      tuple(entry_data.values()))
                    insert_count += 1
                diff_entries.append(entry_data)
            
            print(f"处理完成：{update_count} 个条目已更新，{insert_count} 个条目已插入。")

    except Exception as e:
        print(f"处理仓库 {repo_slug} 时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return {'repo': repo_slug, 'branch': branch_name_for_summary, 'updated': 0, 'inserted': 0, 'error': str(e)}

    return {'repo': repo_slug, 'branch': branch_name_for_summary, 'updated': update_count, 'inserted': insert_count, 'error': None}


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
    print("\n--- 开始从数据库重新生成 Release 文件 (遵循源项目逻辑) ---")
    if not Path(DB_FILENAME).exists():
        print(f"错误：{DB_FILENAME} 不存在，无法生成 JSON 文件。")
        return
    conn = sqlite3.connect(DB_FILENAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print(f"正在生成 {JSON_FILENAME}...")
    cursor.execute("SELECT ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE FROM dict")
    all_db_entries = [{'origin_name': r['ORIGIN_NAME'], 'trans_name': r['TRANS_NAME'], 'modid': r['MODID'], 'key': r['KEY'], 'version': r['VERSION'], 'curseforge': r['CURSEFORGE']} for r in cursor.fetchall()]
    conn.close()
    integral = []
    integral_mini_temp = defaultdict(list)
    for entry in all_db_entries:
        if len(entry['origin_name']) > 50 or entry['origin_name'] == '': continue
        integral.append(entry)
        if entry['origin_name'] != entry['trans_name']:
            integral_mini_temp[entry['origin_name']].append(entry['trans_name'])
    integral_mini_final = { o: sorted(set(t), key=lambda x: t.count(x), reverse=True) for o, t in integral_mini_temp.items() }
    text = json.dumps(integral, ensure_ascii=False, indent=4)
    mini_text = json.dumps(integral_mini_final, ensure_ascii=False, separators=(',', ':'))
    if text != '[]': Path(JSON_FILENAME).write_text(text, encoding='utf-8'); print(f'已生成 {JSON_FILENAME}，共有词条 {len(integral)} 个')
    if mini_text != '{}': Path(MINI_JSON_FILENAME).write_text(mini_text, encoding='utf-8'); print(f'已生成 {MINI_JSON_FILENAME}，共有词条 {len(integral_mini_final)} 个')

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