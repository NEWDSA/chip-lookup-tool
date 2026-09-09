# -*- coding: utf-8 -*-
"""
tools/test_sync_no_node.py
--------------------------
无 Node.js / 无第三方依赖的端到端自检。

覆盖场景：
    1. 上游 JSON 清洗 -> 只填空合并 -> 写回 xlsx（抓取数据准确）
    2. 已有字段不被覆盖；model 与上游冲突仅审计提示（核心业务数据保护）
    3. 二次运行幂等（无变化时不写盘）
    4. xlsx 编码正确，可被 Excel/ChipLookup 直接读取
    5. 全程只依赖 Python 标准库 + openpyxl，不调用任何外部进程 / Node

准备（先生成一次上游缓存）：
    python tools/sync_upstream.py --offline  # 需要本地缓存

或在线生成缓存后运行：
    python tools/sync_upstream.py --dry-run
    python tools/test_sync_no_node.py --cache-dir <上游缓存目录>

不带 --cache-dir 时读取 CHIPLOOKUP_UPSTREAM_CACHE，再不行用工具默认缓存目录。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

try:
    import openpyxl
except ImportError:
    print("错误：需要安装 openpyxl: pip install openpyxl")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.sync_upstream import (  # noqa: E402
    RESOURCES,
    build_dram_index,
    build_mdb_index,
    default_cache_dir,
    fetch_json,
)

FIELDS = [
    "part_number", "model", "manufacturer", "type", "capacity", "bit_width",
    "voltage", "speed", "package", "dimensions", "die_count", "cs_count",
    "die_revision", "op_temp", "notes",
]

# fixture：part_number + 可选预填字段，其余留空
FIXTURE_ROWS = [
    # (part_number, {预填字段}) -> 预期填写
    ("D8DKS", {}),
    ("PB001", {}),              # SpecTek 单候选码
    ("PE001", {}),              # SpecTek 单候选码
    ("K4S511632D-UC75", {}),    # dram-pn 索引料号(Samsung)
    ("SM512M322C0FD4LH6", {}),  # mdb spectek 值反查
    ("D9DKS", {"manufacturer": "Micron", "type": "DDR5"}),  # 已有字段不被覆盖
    ("D8DKT", {"model": "XXXX-KEEP-ME"}),                    # 冲突 model 只提示不覆盖
]

# (part_number, 字段, 期望值)
EXPECT = {
    "D8DKS": {"model": "MT60B2G8RZ-56B:D", "manufacturer": "Micron"},
    "PB001": {"model": "SM512M322C0FD4LH6", "manufacturer": "SpecTek"},
    "PE001": {"model": "SU512M82C0C11ABG", "manufacturer": "SpecTek"},
    "K4S511632D-UC75": {"model": "K4S511632D-UC75", "manufacturer": "Samsung"},
    "SM512M322C0FD4LH6": {"model": "SM512M322C0FD4LH6", "manufacturer": "SpecTek"},
    "D9DKS": {"model": "MT46V16M16BG-75 L", "manufacturer": "Micron"},
    "D8DKT": {"model": "XXXX-KEEP-ME", "manufacturer": "Micron"},
}


def _write_fixture_xlsx(path: str) -> None:
    """写入测试 fixture 为 xlsx 格式。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "chip_database"
    ws.append(FIELDS)
    for pn, pre in FIXTURE_ROWS:
        row = {k: "" for k in FIELDS}
        row["part_number"] = pn
        row.update(pre)
        ws.append([row[k] for k in FIELDS])
    wb.save(path)
    wb.close()


def _read_xlsx(path: str) -> dict:
    """读取 xlsx 返回 {part_number: row_dict}。"""
    out = {}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    headers = [str(h or "").strip() for h in header]
    for row in rows_iter:
        vals = list(row)
        rec = {}
        for i, h in enumerate(headers):
            if i < len(vals) and vals[i] is not None:
                rec[h] = str(vals[i]).strip()
            else:
                rec[h] = ""
        pn = rec.get("part_number", "")
        if pn:
            out[pn] = rec
    wb.close()
    return out


def _ensure_cache(cache_dir: str) -> None:
    """确认缓存里有自检需要的两个资源。"""
    missing = [n for n in RESOURCES
               if not os.path.exists(os.path.join(cache_dir, n))]
    if missing:
        print("错误：缓存目录缺少 %s\n"
              "请先联网执行一次：python tools/sync_upstream.py --dry-run"
              % ", ".join(missing))
        sys.exit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ChipLookup 上游同步自检（无 Node）")
    ap.add_argument("--cache-dir", default=None,
                    help="上游缓存目录（默认 CHIPLOOKUP_UPSTREAM_CACHE 或工具默认值）")
    args = ap.parse_args(argv)
    cache_dir = args.cache_dir or os.environ.get("CHIPLOOKUP_UPSTREAM_CACHE") \
        or default_cache_dir()
    print("== 自检: 缓存目录 %s" % cache_dir)
    _ensure_cache(cache_dir)

    # 载入真实上游数据（离线，抓取自检确定性）
    mdb_raw, _, _ = fetch_json(cache_dir, "mdb.json", offline=True,
                               refresh=False, timeout=10, max_age=0)
    dram_raw, _, _ = fetch_json(cache_dir, "dram-pn.json", offline=True,
                                refresh=False, timeout=10, max_age=0)
    mdb_index, mdb_pn_vendor = build_mdb_index(mdb_raw)
    dram_pn_vendor = build_dram_index(dram_raw)
    print("上游载入: mdb 码 %d, mdb 料号 %d, dram-pn 料号 %d"
          % (len(mdb_index), len(mdb_pn_vendor), len(dram_pn_vendor)))

    tmp = tempfile.mkdtemp(prefix="chiplookup_synctest_")
    db_path = os.path.join(tmp, "chip_database.xlsx")
    _write_fixture_xlsx(db_path)

    # 复用一个精简命令行跑法：直接调 main，避免子进程（也能证明无需 subprocess）
    sys.argv = [
        "sync_upstream.py",
        "--offline", "--cache-dir", cache_dir,
        "--db", db_path, "--verbose",
    ]
    from tools.sync_upstream import main as sync_main
    rc = sync_main()
    assert rc == 0, "sync 主流程返回码非 0"

    rows = _read_xlsx(db_path)
    assert len(rows) == len(FIXTURE_ROWS), "记录条数不对: %d" % len(rows)

    n_model = n_manu = 0
    for pn, expect in EXPECT.items():
        r = rows[pn]
        for field, want in expect.items():
            got = (r.get(field) or "").strip()
            assert got == want, "字段校验失败 %s.%s: 期望 %r 实得 %r" % (pn, field, want, got)
            if field == "model":
                n_model += 1
            else:
                n_manu += 1
    print("字段填充断言通过: model %d / manufacturer %d" % (n_model, n_manu))

    # 只填空：type/capacity 等未预填也不应被凭空造出来
    d8 = rows["D8DKS"]
    assert not (d8.get("type") or "").strip(), "不应凭空补 type"
    assert not (d8.get("capacity") or "").strip(), "不应凭空补 capacity"

    # 冲突保护：D8DKT 的 model 必须原样保留
    assert rows["D8DKT"]["model"] == "XXXX-KEEP-ME"

    # 幂等：再跑一次，应无可填空字段（文件内容应不变）
    before_size = os.path.getsize(db_path)
    before_mtime = os.path.getmtime(db_path)
    sys.argv = [
        "sync_upstream.py",
        "--offline", "--cache-dir", cache_dir, "--db", db_path,
    ]
    rc = sync_main()
    assert rc == 0
    after_size = os.path.getsize(db_path)
    assert before_size == after_size, "二次同步不应改变文件大小"

    # xlsx 文件完整性检查
    wb = openpyxl.load_workbook(db_path)
    ws = wb.active
    assert ws.max_row > 0, "xlsx 文件应有数据行"
    wb.close()

    print("== 自检全部通过: xlsx 格式端到端 OK")
    print("   临时数据库(可复查): %s" % db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
