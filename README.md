# 社区驱动的 Minecraft 模组词典整合项目

[![词典更新工作流](https://github.com/VM-Chinese-translate-group/i18n-Dict-Extender/actions/workflows/update_and_release.yml/badge.svg)](https://github.com/VM-Chinese-translate-group/i18n-Dict-Extender/actions/workflows/update_and_release.yml)
[![许可证: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

弥合社区与模组自身汉化差异，提供更全面、更及时的 Minecraft 模组翻译词典。

## 🎯 项目目标

本项目旨在解决一个长期存在的问题：部分热门的 Minecraft 模组（如机械动力）的简体中文翻译工作是由模组开发者在其官方仓库直接维护的，而非通过 [CFPA 社区](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package) 进行协作。

这导致了 CFPA 官方维护的 [MC 百科模组词典](https://dict.mcmod.cn/)（其数据源为 [i18n-dict](https://github.com/CFPATools/i18n-dict)）在收录这些模组的翻译时，可能存在**更新不及时**或**内容不完整**的问题。

为解决这一问题，本项目建立了一套自动化流程，旨在：
1.  **定期拉取**：直接从这些模组的官方 GitHub 仓库拉取最新的语言文件。
2.  **智能整合**：将获取到的最新翻译与 CFPA 的基础词典进行合并，更新旧有条目并补充新条目。
3.  **自动发布**：生成一个更全面、更贴近游戏实际体验的**增强版词典**，并每周自动发布。

## ⚙️ 工作流程

本项目完全由 GitHub Actions 驱动，每周五自动执行以下步骤：

1.  **获取基础词典**：从上游仓库 `CFPATools/i18n-dict` 的最新 Release 中获取 `Dict-Sqlite.db` 作为本次更新的基础。
2.  **拉取社区翻译**：根据配置文件 `.github/config/source_mods.yml`，依次访问指定的模组 GitHub 仓库。
3.  **整合数据**：从模组仓库中抓取最新的 `en_us.json` 和 `zh_cn.json` 文件，并将其中的翻译条目与基础词典进行比对和整合。
4.  **生成产物**：基于更新后的数据库，重新生成 `Dict.json`, `Dict-Mini.json`, `Dict-Sqlite.db` 以及记录本次变更的 `diff.json` 文件。
5.  **自动发布**：创建一个新的 Release，其中包含所有产物文件以及一份详细的变更日志，说明本次从哪些模组更新了多少条目。

## 📦 Release 文件介绍

本仓库的 [Release 页面](https://github.com/VM-Chinese-translate-group/i18n-Dict-Extender/releases) 页面会每周放出以下文件：

- `Dict.json`
- `Dict-Mini.json`
- `Dict-Sqlite.db`
- `diff.json`

### `Dict.json`
完整的词典文件。文件结构是一个 JSON 数组，每个条目形如：
```json5
{
    "origin_name": "Cart", // 英文原文
    "trans_name": "车", // 中文译文
    "modid": "cazfps_the_dead_sea", // 模组ID
    "key": "block.cazfps_the_dead_sea.cart", // 所属模组Translation Key
    "version": "1.18", // 所属游戏版本
    "curseforge": "cazfps-the-dead-sea" // CurseForge ID
}
```

### `Dict-Mini.json`
轻量化词典。它以原文为键，所有可能译名的列表为值。列表按该译名在完整词典中的出现次数降序排列。
```json
"Cart":["马车","车","货车","推车","敞篷大车"]
```

### `Dict-Sqlite.db`
包含完整词典内容的 SQLite 数据库文件，方便开发者进行快速查询和数据分析。表结构如下：
```sql
CREATE TABLE dict(
    ID INTEGER PRIMARY KEY AUTOINCREMENT,
    ORIGIN_NAME TEXT NOT NULL,
    TRANS_NAME  TEXT NOT NULL,
    MODID       TEXT NOT NULL,
    KEY         TEXT NOT NULL,
    VERSION     TEXT NOT NULL,
    CURSEFORGE  TEXT NOT NULL
);
```

### `diff.json`
**本次更新的差异文件**。它只包含在当次自动化运行中被**新增**或**更新**的条目。其结构与 `Dict.json` 相同，是一个条目数组。这个文件对于审查每次更新的具体内容非常有用。

## 🤝 如何贡献

本项目欢迎社区贡献！如果您发现有其他热门模组的汉化也是由其自身维护，并且希望将其纳入本项目的自动同步范围，您只需要：

1.  **Fork** 本仓库。
2.  编辑 `.github/config/source_mods.yml` 文件，在 `mods` 列表下添加新的模组配置。
3.  提交一个 **Pull Request**。

### 配置文件示例

以下是一个完整的配置示例，请参考其格式添加新模组：

```yaml
# .github/config/source_mods.yml
mods:
  - # --- Create 模组的配置示例 ---
    # 目标模组的 GitHub 仓库地址 (格式：用户名/仓库名)
    repo: "Creators-of-Create/Create"
    # （可选）需要拉取的分支，不填则使用仓库的默认分支
    branch: "mc1.20.1/dev"
    # 语言文件可能存在的路径列表（按优先级从上到下查找）
    # 这对于 en_us 和 zh_cn 不在同一目录的复杂项目非常有用
    lang_paths:
      - "src/generated/resources/assets/create/lang" # 优先查找生成路径
      - "src/main/resources/assets/create/lang"      # 备用查找源码路径
    # 模组的 Mod ID
    modid: "create"
    # （可选）游戏版本。不填则尝试从分支名中提取 (例如 mc1.20.1/dev -> 1.20)
    # version: "1.20" 
    # 模组的 CurseForge ID
    curseforge: "create"

  - # --- GitLab 仓库的配置示例 ---
    # 目标模组的 GitLab 项目路径 (格式：group/subgroup/project)
    repo: "example-group/subgroup/example-mod"
    # 指定仓库提供方（默认 github）
    repo_provider: "gitlab"
    # （可选）自托管 GitLab 的地址，不填则默认 https://gitlab.com
    # repo_host: "https://gitlab.example.com"
    # （可选）需要拉取的分支，不填则使用仓库的默认分支
    # branch: "main"
    lang_paths:
      - "src/main/resources/assets/example/lang/"
    modid: "example"
    curseforge: "example-mod"
```
#### 合并模式

对于像 EnderIO 这样语言文件分散在多个子模块中的复杂项目，您可以启用 `merge_paths` 模式。这会告诉脚本去**合并**所有在 `lang_paths` 中找到的语言文件，而不是只取第一个。

```yaml
  - # --- EnderIO 模组的配置示例 (使用新的合并模式) ---
    repo: "Team-EnderIO/EnderIO"
    # 新增配置项，告诉脚本合并所有路径下的文件
    merge_paths: true
    # 列出所有包含语言文件的子模块路径
    lang_paths:
      - "enderio-base/src/main/resources/assets/enderio/lang/"
      - "enderio-base/src/generated/resources/assets/enderio/lang/"
      ······
    modid: "enderio"
    curseforge: "ender-io"
```

  ### GitLab 访问说明

  如需拉取 GitLab 仓库，请在对应模组配置中设置 `repo_provider: "gitlab"`。

  - 公共仓库无需额外配置。
  - 私有仓库需要设置环境变量 `GITLAB_TOKEN`（Personal Access Token）。
  - 自托管 GitLab 可通过 `repo_host` 指定域名。

## 📜 版权归属

本项目的数据基础源自 CFPA [Minecraft 模组简体中文翻译项目](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package) 及其衍生项目 [i18n-dict](https://github.com/CFPATools/i18n-dict)。

各部分内容的授权与归属如下：

* **社区翻译内容**：最终生成的词典数据（`Dict.json` 等）遵循 [**CC BY-NC-SA 4.0**](https://creativecommons.org/licenses/by-nc-sa/4.0/) 授权。
* **官方游戏资产**：词典中包含的 Minecraft 原版语言文本及 Translation Keys 版权归属 **Mojang Studios**。本项目的 CC 授权不涵盖此类官方资产，相关提取与使用须遵守 [Minecraft EULA](https://www.minecraft.net/eula)。
* **代码及工作流**：本仓库内的自动化脚本采用 [**MIT LICENSE**](https://mit-license.org/)。
