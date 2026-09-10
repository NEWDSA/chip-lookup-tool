ChipLookup
============

跨平台芯片料号查询器，深色卡片风格 GUI，UI 参考 https://fm.itxtech.org/zh/parts/D8DKS 。
Python 3 + Tkinter，核心查询运行时仅依赖标准库；另附手写数学公式白板（TrOCR 离线识别）。
数据库采用 CSV 文件，可手工维护、可批量导入/导出，无需改动代码。

打包体积两种形态：
- **含白板**：onedir 目录约 2.1 GB（torch + transformers + 1.3 GB 模型权重），启动仍 1-2 秒
- **不含白板**：单文件 exe 约 17 MB（`CHIPLOOKUP_NO_WHITEBOARD=1` 构建）

特性
----
1. 实时查询：边输入边返回候选列表（无需回车）。
2. 多字段匹配：料号 / 型号 / 厂商 / 容量 / 类型 / 位宽 / 电压 / 速度 等。
3. 模糊排序：精确 > 前缀 > 子串 > 模糊相似度，按相关度从高到低排序。
4. 键盘友好：上下键选候选，Enter 看详情 / 进候选列表，Esc 清空，Ctrl+C 复制料号。
5. 一键复制：「复制全部」按字段格式化、「复制料号」只取料号。
6. 友好提示：无结果时给可能原因 + 相近料号建议。
7. 数据可视化：可看到数据库行数、来源路径；支持导入/导出 CSV、刷新磁盘文件。
8. 跨平台：Windows 10+、macOS、Linux 均可运行。Python 3.8 兼容 Windows 7。
9. 候选区分页：候选区固定显示 8 行（PAGE_SIZE）；当候选数 > 8 时提供两种用户可选的分页机制：
   - **分页**：底部「« 第 N/M 页 » · 共 X 条」，支持上一页/下一页
   - **加载更多**：底部「加载更多（还剩 N 条）已显示 X / Y 条」，点一次追加 8 条
   候选数 ≤ 8 时不显示任何分页控件（保持简洁）
10. 上游数据同步：tools/sync_upstream.py 从 fdnext 索引抓取清洗后「只填空」
    补全 model / 厂商，全程纯 Python 标准库，无需安装 Node.js 即可运行。
11. 三种数据加载模式：本地 CSV / 上游索引 / 混合（合并+去重），
    启动参数或 chiplookup.json 配置即可切换，UI 顶部也可随时热切换。
12. 手写数学公式白板：左栏手写算式 → 「识别公式」→ 出 LaTeX 与计算结果。
    模型用 TrOCR（Google MathWriting 手写数学数据集微调），全离线推理。

目录结构
--------
chip-lookup-tool/
├── README.md                      本文件
├── requirements.txt               运行依赖（openpyxl / pillow / torch / transformers / sympy）
├── ChipLookup.spec                PyInstaller 配置（默认 onedir，含白板；附 Win7 打包注释）
├── data/
│   └── chip_database.csv          默认数据库（含 D8DKS 等 30+ 条真实料号）
├── models/
│   └── trocr-math/                白板模型权重（~1.3GB，git 忽略，用 tools/fetch_models.py 拉取）
├── src/
│   ├── main.py                    入口（解析参数/配置 → 构建数据源 → 启动 UI 或 stats）
│   ├── ui.py                      Tkinter 界面（含顶部数据源模式热切换）
│   ├── math_canvas.py             手写板 + TrOCR 识别 + sympy 计算
│   ├── database.py                CSV 数据层（编码探测/校验/加载/增删改/导入导出）
│   ├── config.py                  模式常量、Settings、chiplookup.json 读写、CLI 参数
│   ├── sources.py                 三种数据源本地/上游/混合 + 合并去重
│   ├── upstream.py                上游提供层（复用 fdnext 索引缓存）
│   ├── search.py                  搜索匹配与排序
│   ├── paths.py                   数据文件路径解析（exe 模式/源码模式）
│   └── synthetic_card.py          数据驱动的完整卡片 PNG 渲染（截图/分享用）
├── tests/
│   ├── test_modes.py              三模式/异常/配置/CLI 冒烟测试（全离线）
│   ├── test_whiteboard.py         白板识别测试（计算层/画布层/集成层/精度层/UI 层）
│   └── fixtures/                  测试用本地 CSV 与上游索引缓存
├── tools/
│   ├── import_export.py           命令行工具：开 UI 也能维护数据
│   ├── sync_upstream.py           上游数据同步器（纯 Python，无 Node 依赖）
│   ├── fetch_models.py            下载并固化白板模型权重到 models/
│   ├── bench_handwriting_models.py 手写识别模型选型评测（pix2tex vs TrOCR）
│   └── test_sync_no_node.py       无 Node 端到端自检（只依赖标准库）
├── build/                         PyInstaller 中间产物
├── dist/                          打包产物：dist/ChipLookup/（onedir，含白板）
└── screenshots/                   界面截图与合成卡片示例
    ├── ui_exact_D8DKS.png         实际 UI：精确查询 D8DKS（屏幕可视区域）
    ├── ui_fuzzy_D8DK.png          实际 UI：模糊搜索 D8DK
    ├── ui_notfound.png            实际 UI：未找到 + 相近候选 + 操作建议
    ├── ui_paginate_D8D.png        实际 UI：D8D 模糊查询，分页模式（20 候选 → 3 页）
    ├── ui_load_more_D8D.png       实际 UI：D8D 模糊查询，加载更多模式
    ├── card_D8DKS.png             合成卡片：D8DKS 全部字段（不受屏幕尺寸限制）
    └── card_D8DKP.png             合成卡片：D8DKP 全部字段

快速使用（推荐：Windows 用户直接用 exe）
----------------------------------------
1. 进入 dist/ChipLookup/ 目录
2. 双击 ChipLookup.exe
3. 首次运行会在 exe 旁边生成 data/chip_database.csv（从包内嵌的种子拷贝）
4. 在「查询输入」框输入 D8DKS → 立刻显示 Micron DDR5 16Gb x8 的完整卡片
5. 想试白板：在左栏「手写数学公式」区用鼠标写个 `2+3` → 点「识别公式」

源数据保留在 exe 旁边的 data/chip_database.csv 中，可手工用 Excel/Numbers 编辑。
所有「导入 / 导出 / 增 / 删 / 改」都会写到这里。

快捷键
------
- 输入即查询
- 回车：在输入框 → 选中首条候选项并显示详情；其他 → 切换焦点到候选列表
- ↑/↓：在候选列表上下选择
- Enter（候选列表里）：把焦点退回输入框
- 双击候选行：直接进入详情
- Esc：清空输入
- Ctrl+C：复制当前料号
- F5：刷新数据（从磁盘重新加载）

命令行维护（不开 UI 也能改库）
------------------------------
$ python tools/import_export.py --help

子命令：
- show   <料号>           查看某条记录
- add    <料号> <型号>    新增/更新一条，支持 --manufacturer / --type / --capacity ...
- del    <料号>           删除一条
- stats                  统计信息
- import <csv> [--replace]   合并或替换导入
- export <csv>           导出全部到 CSV

例：
  $ python tools/import_export.py add D8DKS "MT60B2G8RZ-56B:D" --manufacturer Micron --type DDR5 --capacity 16Gb --bit-width x8
  $ python tools/import_export.py export backup.csv
  $ python tools/import_export.py import new_batch.csv

上游数据同步（纯 Python，无需 Node.js）
----------------------------------------
从 iTXTech/fdnext（AGPL-3.0）发布的上游 JSON 索引自动抓取、清洗并同步到
本地 CSV。整个链路只用 Python 标准库（urllib / json / csv），
不依赖 Node.js / npm，也不调用任何外部进程，未装 Node 的电脑同样可用。

同步规则（务必了解，防止误改数据）：
- 「只填空」：仅当 model / manufacturer 为空时才写入上游确认值；
  已人工填写的字段永远不被覆盖。
- 「不猜规格」：上游 JSON 只有「标记码 -> 型号」和「料号厂商」两类索引，
  不含容量/位宽/电压等完整规格。工具不会凭空推断这些字段，
  需要完整规格仍按老办法人工补充 / 用 Excel 批量导入。
- 「可审计」：已有 model 与上游不一致时只打印提示，不动数据。
- 同一 FBGA 码命中多个型号时（SpecTek 部分码），model 留空待人工确认，
  避免写错。

常用命令：
  $ python tools/sync_upstream.py                  # 在线抓取并同步到 data/chip_database.csv
  $ python tools/sync_upstream.py --dry-run        # 预览将补的字段，不改盘
  $ python tools/sync_upstream.py --verbose        # 逐条打印补了什么
  $ python tools/sync_upstream.py --db other.csv   # 同步到指定库
  $ python tools/sync_upstream.py --offline        # 离线：只用本地缓存（断网/CI）
  $ python tools/sync_upstream.py --export-index 上游索引.csv   # 把清洗后的索引导出成 CSV
  $ python tools/sync_upstream.py --refresh        # 忽略 7 天缓存，强制重新下载

  自检（无 Node 环境回归测试，纯标准库）：
  $ python tools/test_sync_no_node.py              # 先生成一次上游缓存即可
  # 验证内容：抓取清洗准确、只填空不覆盖、幂等、CSV 为 UTF-8 BOM

支持上游资源（GitHub raw，urllib 直下）：
  mdb.json         Micron FBGA 顶标码(15427) + SpecTek 标记码(2153) -> 完整型号
  dram-pn.json     DRAM 料号索引(3169 条)，覆盖 Samsung/SK Hynix/Nanya/... 等 13 家

定时拉取（可选）：用系统计划任务/cron 周期执行上面的同步命令即可，
例如「每日 02:00 同步」后，UI 内按 F5 刷新即可看到补全结果。

三种数据加载模式（local / upstream / hybrid）
-----------------------------------------------
用 `--mode` 或 chiplookup.json 里的 `mode` 选择数据从哪来：

| mode      | 说明 |
|-----------|------|
| `local`   | 只加载本地 CSV（默认，行为与旧版一致） |
| `upstream`| 只用上游 fdnext 索引（不读本地 CSV），适合只查标记码/料号厂商 |
| `hybrid`  | 本地 CSV + 上游索引合并去重后查询 |

hybrid 的合并优先级（`hybrid_priority`）：
- `local_first`（默认）：以本地记录为主，本地为空的字段用上游补全；
  本地已在用人工维护的数据永不被上游覆盖，适合日常使用。
- `upstream_first`：以上游为主、本地兜底补齐，适合先看上游权威数据。

合并/去重规则：
- 按 part_number 主键去重（大小写不敏感），重叠料号只保留一条。
- 「只填空」：主源已有值不被副源覆盖；副源仅在主源该字段为空时补上。
- 同一标记码对应多个型号时（如 SpecTek 部分码），model 留空待人工确认。

故障降级：
- 上游加载失败（离线且无缓存/缓存损坏/网络异常）在 hybrid 下不会退出，
  自动降级仅用本地数据，并在 UI 状态栏提示；local / upstream 单源模式下则报错退出。
- 上游索引带 7 天新鲜期缓存 + 失败自动降级旧缓存，多数时间可离线使用。

UI 内热切换：窗口顶部「数据源模式」三个分段按钮一键切换，选择会写入
chiplookup.json 持久化，下次启动沿用。

配置文件 chiplookup.json
------------------------
默认放在程序根目录（exe 运行时在 exe 旁）。参数优先级：命令行 > 配置文件 > 默认值。

```jsonc
{
  "mode": "hybrid",                 // "local" | "upstream" | "hybrid"
  "db": "data/chip_database.csv",   // 本地 CSV 路径
  "hybrid_priority": "local_first", // "local_first" | "upstream_first"
  // ── 网络类型参数（原「上游」类型；键名保持 upstream_* 兼容既有配置） ──
  "upstream_offline": false,        // true=禁用联网，只用本地缓存
  "upstream_refresh": false,        // true=忽略 7 天缓存强制重新下载
  "upstream_cache_dir": "",         // 缓存目录，空=默认
  "upstream_timeout": 30,           // 单次下载超时秒数
  "upstream_retries": 2,            // 下载失败重试次数
  "upstream_rate_limit": 0.0        // 相邻请求间隔秒数（避免触发限流）
}
```

也可在 UI 内切换模式后自动生成该文件（只含被改动的字段）。

CSV 数据字段
-----------
必填：part_number（料号，唯一主键）
其它按需填写，留空即可：
    part_number, model, manufacturer, type, capacity, bit_width, voltage, speed,
    package, dimensions, die_count, cs_count, die_revision, op_temp, notes

CSV 会自动探测编码（UTF-8 BOM / UTF-8 / GBK / GB18030 / Latin-1，常见的中文
Excel「CSV UTF-8」「另存为 GBK」都能读），推荐用 UTF-8 (BOM 可选) 保存。
改完保存后，在 UI 内点「刷新数据」或按 F5 即生效。

开发者模式（直接跑源码）
------------------------
源码本身即可运行 —— 仅依赖 Tkinter（Python 官方发行版自带）：
$ python src/main.py

参数：
  --mode <local|upstream|hybrid>    数据加载模式（默认 local）
  --db <path>                       本地数据库 CSV 路径
  --config <path>                   指定配置文件（默认 chiplookup.json）
  --hybrid-priority <en>            本地/上游优先，local_first 或 upstream_first
  --upstream-offline                禁用联网，只用本地缓存
  --upstream-refresh                忽略 7 天缓存，强制重新下载
  --upstream-cache-dir <path>       上游缓存目录
  --upstream-timeout <sec>          单次下载超时（默认 30）
  --upstream-retries <n>            下载失败重试次数（默认 2）
  --upstream-rate-limit <sec>       请求间隔限流（默认 0）
  --headless-stats                  仅打印统计信息，不弹 UI
  --screenshot <path>               启动并截图后退出（调试用，需要 Pillow）
  --screenshot-query <s>            截图前自动查询一个料号
  --screenshot-stitch               临时展开 detail 区域，截一张包含全部字段的 PNG

示例：
  $ python src/main.py --mode upstream --upstream-offline --headless-stats
  $ python src/main.py --mode hybrid --hybrid-priority upstream_first
  $ python src/main.py --mode local --db data/chip_database.csv

测试（全离线，仅依赖标准库）：
  $ python tests/test_modes.py       # 三模式 + 异常 + 配置 + CLI 冒烟（33 用例）

合成卡片（数据驱动，100% 完整）：
  $ python src/synthetic_card.py --part D8DKS --part D8DKP
  # → screenshots/card_D8DKS.png、card_D8DKP.png
  # 不依赖屏幕分辨率，能把全部 14 个字段都画出来

源码运行依赖：
- Python 3.8+（推荐 3.10 以上）
- Tkinter（标准库）：Windows/macOS 默认安装；Linux 发行版可能需 `apt install python3-tk`

打包成 exe（含白板，默认 onedir）
---------------------------------
当前 dist/ChipLookup/ 是用 Python 3.11 + PyInstaller 6.x 打出的，
适用于 Windows 10+ / 11 / Server 2016+。

```bash
# 1) 固化白板模型权重（约 1.3GB，只做一次）
python tools/fetch_models.py

# 2) 打包（产出 dist/ChipLookup/ 目录）
python -m PyInstaller ChipLookup.spec --noconfirm --workpath build_wb

# 3) 用 Inno Setup 编译 installer/setup.iss → dist_installer/ChipLookup_Setup_x.y.z.exe
```

**为什么是 onedir 而不是单文件**：白板识别依赖 torch + transformers + 1.3GB
模型权重，整包约 2GB。onefile 每次启动都要把 2GB 解压到临时目录，冷启动要
几十秒、还要占等量磁盘，不可接受。onedir 不落地解压，启动即读。

启动速度不受体积影响的另一个前提：`src/math_canvas.py` 只在 `_get_model()`
里 import torch/transformers（延迟加载），进程启动阶段不碰它们。因此 exe 启动
仍是 1-2 秒，白板首次点击才付出模型加载成本。

打包开关（环境变量）：
- `CHIPLOOKUP_ONEFILE=1`  —— 改用单文件模式（体积大时启动很慢，不建议）
- `CHIPLOOKUP_NO_WHITEBOARD=1` —— 打一个不含白板的轻量包（~17MB）

**打包后自检**（重要）
----------------------
exe 是 windowed 程序没有控制台，白板又只在点击时才加载模型，出问题很难发现。
用自检入口一次性验证 torch / transformers / 模型权重 / sympy 四条链路：

```bash
dist\ChipLookup\ChipLookup.exe --selftest-whiteboard
# 结果同时写到 exe 旁边的 whiteboard_selftest.log
```

期望输出：
```
[selftest] 模型目录：...\_internal\models\trocr-math
[selftest] 识别结果：'4\\times4'
[selftest] 计算结果：16
[selftest] OK：白板识别链路在打包环境下可用
```

如需 Windows 7 兼容版本（重点！）
---------------------------------
Python 官方从 3.9 开始放弃对 Windows 7 的支持（Python 3.8 是最后一代）。
要让 ChipLookup 在 Win7 系统上运行，需要：

1. 在一台 Windows 7 / 10 机器上安装 Python 3.8.x 32 位或 64 位
   - https://www.python.org/downloads/windows/
2. 安装 PyInstaller 5.x（最后一个兼容 Win7 的版本线）：
   pip install "pyinstaller==5.13.2"
3. 把整个 chip-lookup-tool/ 拷过去，cd 到项目根目录执行：
   pyinstaller --clean --noconfirm ChipLookup.spec
4. 产物在 dist/ChipLookup.exe，拷到 Win7 机器双击即可运行。

注：不要使用 PyInstaller 6.x 在 Win7 上启动。
6.x 的 bootloader 在 Win7 上会因为 _beginthreadex 等 API 差异直接闪退。

兼容性矩阵
----------

| 系统          | 直接运行源   | 跑 Python 3.11 打出的 exe | 跑 Python 3.8 + PyInstaller 5.x 打出的 exe |
|---------------|--------------|---------------------------|---------------------------------------------|
| Windows 11    | OK           | OK                        | OK                                          |
| Windows 10    | OK           | OK                        | OK                                          |
| Windows 8.1   | OK           | OK                        | OK                                          |
| Windows 7 SP1 | OK           | ✗ 闪退                    | OK                                          |
| macOS 12+     | OK           | OS-dependent              | OS-dependent                                |
| Linux + GTK   | OK           | -                         | -                                           |

跨平台说明
----------
- Windows：下载 dist/ChipLookup.exe（注意 Win7 用户用 Python 3.8 重新打包版本）
- macOS / Linux：执行 `python src/main.py`（直接用源码运行最快）。
  如有跨平台打包需求，可在本机用 PyInstaller 同样能生成对应平台的可执行体。

维护与扩充数据
--------------
- 单条加：UI 「刷新数据」前可手动编辑 data/chip_database.csv 并保存；
  或者用 UI 「导入 CSV」一次导入大批。
- 大量导入：在 Excel 里编辑 csv → 另存为 UTF-8 → 「导入 CSV」。
- 备份：UI 「导出 CSV」一键全量备份。

手写数学公式白板
----------------
左栏「手写数学公式」区：用鼠标写下算式 → 点「识别公式」→ 右侧卡片给出
LaTeX 与计算结果。全离线，不联网。

**模型选型**（实测见 `tools/bench_handwriting_models.py`，10 组鼠标手写样本，
按「数值是否算对」计分）

| 模型 | 语义正确 | 单次耗时 | 体积 | 结论 |
|------|---------|---------|------|------|
| pix2tex | 0/10 | 0.5s | 0.12GB | ✗ 只认印刷体 LaTeX 排版公式，手写等于不可用 |
| **tjoab/latex_finetuned** | **7/10** | **2.7s** | **1.3GB** | ✓ 采用（TrOCR-base，原生输出 LaTeX） |
| fhswf/TrOCR_Math_handwritten | 8/10 | 6.3s | 2.3GB | 更准但体积翻倍、慢 2.3 倍 |

TrOCR 系模型在 Google MathWriting 手写数学数据集上微调，才是对手写对症的选型。
换模型只改 `src/math_canvas.py` 里的 `MODEL_SUBDIR` / `MODEL_ID` 两个常量。

**已知局限**：手写「x」有时被认成字母 X（已用规则在数字之间自动还原为乘号）；
多位数字偶有截断（如 `5+5=10` 认成 `5+5=1`）；识别不准时会明确报错而不是
给出一个看起来像结果的噪声符号。

**首次点击较慢**：模型加载约 10-25 秒（1.3GB 权重读盘 + torch 初始化），
加载期间按钮显示「识别中…」并禁用。之后每次识别约 2-3 秒。

**源码模式跑测试**：
```bash
python tests/test_whiteboard.py            # 38 项：计算/画布/集成/整理/定位/UI
WB_SLOW=1 python tests/test_whiteboard.py  # 追加真实模型端到端识别
```

性能/资源
---------
- 启动时间：1-2 秒（onedir，不落地解压；torch 延迟到点「识别公式」才加载）
- 内存占用：~50-90 MB（不含白板）；白板首次加载模型后 +~1.5 GB
- CPU 占用：低（搜索为线性扫描 O(N)，按字符串长度 N；1 万条料号下毫秒级响应）
- 磁盘占用：整包约 2.1 GB（其中模型权重 1.3 GB、torch 约 360 MB）

常见问题
--------
Q：双击 exe 后窗口闪一下就没了？
A：检查是否被安全软件拦截了 _MEIPASS 临时目录，或尝试命令行启动看错误：
   dist/ChipLookup/ChipLookup.exe  （直接运行如有控制台会打印错误；若 console=False 时则在事件查看器）

Q：白板点了「识别公式」没反应？
A：先跑 `ChipLookup.exe --selftest-whiteboard` 看 whiteboard_selftest.log。
   若是 ModuleNotFoundError，说明打包时漏了 hiddenimports（见 ChipLookup.spec）；
   若是模型目录不对，确认 `_internal\models\trocr-math\model.safetensors` 存在。

Q：识别结果不对怎么办？
A：当前模型对手写输入的准确率约 7/10，属于已知水平。写得大一些、笔画清晰些
   会明显更准；数字之间用「x」表示乘法比用「×」更容易被认对。

Q：导入 CSV 时报编码错误？
A：用 UTF-8 (with BOM) 或 UTF-8 (no BOM) 保存。Windows Excel 另存为 CSV UTF-8 即可。

Q：搜索结果顺序不对？
A：搜索优先级 = 精确 > 前缀 > 子串 > 模糊相似度。如希望屏蔽模糊候选，
   编辑 src/search.py 把 SCORE_FUZZY_MAX 调小或把模糊比较那段删了。

Q：要重置数据库？
A：删除 exe 旁边 data/chip_database.csv 后再启动 exe，会自动从内嵌种子恢复。

许可
----
内部工具，按需使用。
