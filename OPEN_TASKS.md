# 未完成事项跟踪（OPEN TASKS）

> 本文件用于同步「fdnext 规则引擎移植（M1–M7）」中所有**尚未完成**的工作。
> 维护约定：每完成一项，把 `- [ ]` 改为 `- [x]` 并在末尾补一句「完成说明 + 验证结果」。
> 对齐时间：2026-09-03。最近一次对拍：`python tools/fdnext_verify.py --offline` → part 28 料号 5 不一致（全部 mdb 型 known-boundary）/ flash 5 ID 0 不一致。

---

## 总览

| 里程碑 | 状态 |
|---|---|
| M1 盘点与架构 | ✅ 完成 |
| M2 数据层与类型移植（table/resources） | ✅ 完成 |
| M3 decodepack 编译链移植 | ✅ 完成 |
| M4 engine 解码链路移植 | ✅ 完成 |
| M5 与上游对拍清零 | ✅ 完成（仅剩 mdb 型 known-boundary，见下） |
| M6 产品接入 | ✅ 完成（decode.py CLI + decode_db.py 只填空 + 无 Node 自检 + 边界文案） |
| M7 文档与合规 | ⬜ 未开始 |

---

## M5：对拍清零（已完成）

> 通过标准达成：对拍仅剩 mdb 型 known-boundary（B），不再有字段级/解码级差异。

### A. 已清零的真 bug

#### A1. [x] K9F2G08U0C —— 字段输出 None 而非不输出（2 处差异）

- 修复（`src/fdnext/engine.py`）：`normalize_part_draft` 的 fields 改走 `_clean_fields`（与 identifier 路径一致）——值为 None(=JS undefined) 的字段键不进入草稿，杜绝 hook/分类后残留 `None`。`draft_field`/`set_draft_field` 本就按「缺失键=None」语义工作，不会影响后续 hook。
- 验证结果：重跑对拍 K9F2G08U0C 不再出现在 diff 列表。

#### A2. [x] ADDE14A2D0E0 —— SK hynix 位域解析差异（6 处差异）

- 根因：**不是位域 bug**。官方 `createDefaultIdentifierPostprocessor()`（identifierInfo hook，位于 decode→enrich 之间）对 SK hynix 做后处理：byte6≥0x80 时删除 `block_size/blocks_per_lun/pages_per_block/simultaneously_programmed_pages/redundant_area_size/timing_mode_async/edo/interleave/cache/ecc_level/revision/enterprise/interface_type` 等扩展字段，并把 `spp` 值写入 `plane_count`（0xE0≥0x80 → 删 5 字段 + plane_count=2）。Python 引擎此前完全没移植该 postprocessor。
- 修复：
  - 新增 `src/fdnext/postprocess_data.py`（程序化提取自官方 npm 3.2.0 dist bundle 的内嵌常量：micron/ymtc 前缀表、SK hynix byte6 集合、特殊 ID、删除清单、Samsung QLC 映射）。
  - 新增 `src/fdnext/postprocess.py`：移植官方 Lr 工厂的 partInfo(Mr)/identifierInfo(Pr/Nr/Fr/Ir) 全部分支。
  - `src/fdnext/engine.py`：拆分 `apply_identifier_info_hooks`（新）与 `apply_part_info_hooks`，前置 postprocessor 再走 derive(enrich)，`_decode_flash_id_raw` 改走 identifier 链。
- 验证结果：flash 5 ID → 0 不一致；part 无回归（28 料号只剩 B 的 5 个 mdb 型）。

### B. 验收口径：mdb 型 known-boundary（确认不修）

以下 5 条官方能命中，靠的是**内置 mdb（marking/part 索引）**；Python 引擎按 M1 边界刻意只做 rules-only、不做索引，因此输出 `not_found` 属预期，不计入 diff 清零点：

- D8DKS
- W25Q128JVSSIQ
- W25Q64JVSSIQ
- MX25L12835FMI-10G
- MX25L6433FZNI-08G

> 当对拍仅剩以上 mdb 型差异时，即视为 M5 通过。

### C. 回归工具

- `tools/fdnext_verify.py`：对拍 runner，与 `tools/golden/fdnext-core-86.json` 逐字段比对（engine 86 / npm @itxtech/fdnext-core@3.2.0）。
- 黄金文件来自 `%TEMP%\clpoc`（开发机 Node 辅助生成），仓库内校验纯 Python。

---

## M6 待办：产品接入（已完成）

- [x] `tools/decode.py` CLI：接受料号 / Flash ID 输入，输出解码 JSON（对齐 `_RESULT_SCHEMA = chiplookup.fdnext.decode.v1`）
  - 完成说明：`python tools/decode.py [料号|Flash ID...]`；auto 自动判别（纯十六进制偶数串视为 Flash ID），`--mode part|flashid` 可强制；默认人类可读分组文本，`--json` 输出 schema v1 结构化 JSON（单查询直接出 result，多查询出 `{"results":[...]}`），支持 `--stdin`/`--verbose`/`--draft`。规则默认来自内置快照，全离线。
- [x] 与 ChipLookup CSV 字段映射：解码结果写入现有数据（只填空 / 不覆盖人工字段，参照 `tools/sync_upstream.py` 的合并语义）
  - 完成说明：新增 `tools/decode_db.py`。映射：manufacturer←vendor 显示名、type←dram_type、capacity←dram/die_density(Mbit→"16Gb" 串)、bit_width←xN、voltage←dram_voltage(UNKNOWN 跳过)、package/die_count/cs_count/die_revision。只填空绝不覆盖；speed/op_temp/dimensions/notes/model 不自动猜。默认 dry-run，`--write` 才写盘；mdb 边界码不瞎填并提示交给 sync_upstream。验证：4 条真实 DRAM 料号正确补全；预填 manufacturer 不被覆盖；二次运行 0 可填（幂等）。
- [x] 无 Node 回归测试：干净 venv + 剥离 PATH 下 CLI 端到端自检（复用 `tools/test_sync_no_node.py` 的既有经验）
  - 完成说明：新增 `tools/test_decode_no_node.py`：子进程 PATH 剔除含 node 目录 + 清空 NODE_* 变量 + PYTHONNOUSERSITE 下真实运行两个 CLI。断言：料号 3 例（含 A1 回归 K9F2 无 null）、Flash ID 2 例（含 A2 回归 ADDE：移除扩展字段且 plane_count=2）、known-boundary 2 例(not_found+mdb 提示+rc=1)、stdin 多查询、decode_db 只填空/覆盖保护/边界留空/幂等/utf-8-sig BOM。运行 `python tools/test_decode_no_node.py` 全部通过。
- [x] 把 5 个 mdb 型 known-boundary 写进 decode.py 的帮助信息/错误提示（给出「建议用丝印索引或人工确认」的文案）
  - 完成说明：`KNOWN_MDB_BOUNDARY` 常量进入 `--help` epilog；not_found / 规则未覆盖时输出针对性提示（含 "建议用 ChipLookup 库检索或 fm.itxtech.org/parts 人工确认"）。decode_db 未命中统计里也指向 sync_upstream 的 mdb 索引职责。

---

## M7 待办：文档与合规（未开始）

- [ ] README 补充 fdnext 引擎用法说明（CLI、数据源、缓存目录、No-Node 说明）
- [ ] AGPL-3.0 声明与署名：本引擎移植自 `iTXTech/fdnext`（AGPL-3.0），须注明上游来源、版本（npm `@itxtech/fdnext-core@3.2.0` / engine 86）与规则数据来源（`src/fdnext/data/fdnext-core-3.2.0` snapshot）
- [ ] 许可说明：明确「本项目自身的核心业务代码」与「fdnext 衍生代码」的授权边界

---

## 长期差距：与 fm.itxtech.org/parts 的能力对照（未排期）

> 「与网站功能相同」在索引层与服务层**尚无排期**。以下能力当前**明确未做**（M1 边界），如需补齐需另立任务：

| 网站能力 | 本地状态 |
|---|---|
| 料号规则解码（rules-only） | ✅ 已移植并对拍中 |
| Flash ID 规则解码（rules-only） | ✅ 已移植并对拍中 |
| mdb 索引：丝印/标记码反查（W25Q/MX25/D8DKS 类命中依赖） | ❌ 未做 |
| fdb 合并：Flash ID → die profile 补全 | ❌ 未做 |
| Micron FBGA 码解析 | ❌ 未做 |
| catalog 料号模糊搜索 | ❌ 未做 |
| Web 服务 + 前端 UI | ❌ 未做 |
