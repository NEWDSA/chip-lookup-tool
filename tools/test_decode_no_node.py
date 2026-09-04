# -*- coding: utf-8 -*-
"""
tools/test_decode_no_node.py
----------------------------
无 Node.js / 无第三方依赖的「解码 CLI」端到端自检（M6-3）。

用子进程真实运行 tools/decode.py 与 tools/decode_db.py：
    1) 料号解码：MT29F512G08EBHAFJ4（raw NAND，density 524288 / TLC）
                   H5AN8G8NCJR（DDR4 8Gb x8 / CJR）
                   K9F2G08U0C（A1 回归：任何字段不得出现 null）
    2) Flash ID 解码：2C84044BA900（Micron plane_count=2）
                       ADDE14A2D0E0（A2 回归：SK hynix byte6>=0x80，
                                    移除扩展字段且 plane_count=2）
    3) known-boundary：D8DKS / W25Q64JVSSIQ -> not_found + mdb 提示，退出码 1
    4) CSV 只填空合并：真实料号补 type/capacity/manufacturer 等；
                        已填 manufacturer 不被覆盖；二次运行幂等；utf-8-sig(BOM)
    5) 环境隔离：子进程 PATH 剔除含 "node" 的目录、清空 NODE_* 环境变量，
       证明 CLI 不需要 Node（规则数据来自仓库内置快照，全程离线）。

运行（无需任何准备，快照随仓库分发）：
    python tools/test_decode_no_node.py

退出码：0 全部通过；1 断言失败；2 环境缺前置（快照缺失等）。
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

DECODE_PY = os.path.join(HERE, "decode.py")
DECODE_DB_PY = os.path.join(HERE, "decode_db.py")
SNAPSHOT_DIR = os.path.join(
    ROOT, "src", "fdnext", "data", "fdnext-core-3.2.0"
)

FIELDS = [
    "part_number", "model", "manufacturer", "type", "capacity", "bit_width",
    "voltage", "speed", "package", "dimensions", "die_count", "cs_count",
    "die_revision", "op_temp", "notes",
]
HEADER = ",".join(FIELDS) + "\n"

# (查询, 解码形态) —— 用于 --mode 测试
# 料号解码断言 (query, device/fields/meta 片段)
PART_CASES = [
    ("MT29F512G08EBHAFJ4", {
        "vendor": "micron", "chipKind": "raw_nand",
        "density": 524288, "cell_level": "TLC",
        "ruleId": "vendor.micron.raw.current.v1",
    }),
    ("H5AN8G8NCJR", {
        "vendor": "skhynix", "chipKind": "dram",
        "dram_type": "DDR4", "dram_density": 8192,
    }),
    ("K9F2G08U0C", {   # A1 回归：曾输出 null 字段
        "vendor": "samsung", "chipKind": "raw_nand",
    }),
]

# Flash ID 解码断言
ID_CASES = [
    ("2C84044BA900", {
        "vendor": "micron",
        "plane_count": 2,
    }),
    ("ADDE14A2D0E0", {  # A2 回归：SK hynix 扩展字段被移除
        "vendor": "skhynix",
        "plane_count": 2,
        "absent": ["block_size", "blocks_per_lun", "simultaneously_programmed_pages"],
    }),
]

# mdb 索引类 known-boundary（应 not_found 并给出 mdb 提示，退出码 1）
BOUNDARY_CASES = ["D8DKS", "W25Q64JVSSIQ"]


# ---------------------------------------------------------------------------
# 子进程环境：无 Node
# ---------------------------------------------------------------------------


def _env_without_node() -> dict:
    env = os.environ.copy()
    parts = [
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and "node" not in p.lower()
    ]
    env["PATH"] = os.pathsep.join(parts)
    for key in list(env):
        if key.upper().startswith("NODE"):
            env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def run_cli(script: str, args: list, env: dict,
            stdin_text: str = "") -> tuple:
    proc = subprocess.run(
        [sys.executable, script] + args,
        capture_output=True, text=True, encoding="utf-8",
        cwd=ROOT, env=env, timeout=180,
        input=stdin_text,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _json_out(stdout: str) -> dict:
    return json.loads(stdout)


def _check_no_null_fields(result: dict) -> None:
    """回归保护：解码字段里不得出现 null（JS undefined 语义）。"""
    fields = result.get("fields") or {}
    bad = [k for k, v in fields.items() if v is None]
    assert not bad, "字段含 null: %s" % bad


# ---------------------------------------------------------------------------
# 自检主流程
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    if not os.path.isdir(SNAPSHOT_DIR):
        print("错误：缺少内置规则快照 %s\n"
              "（快照应随仓库分发；缺失时需从 @itxtech/fdnext-core@3.2.0 恢复）"
              % SNAPSHOT_DIR)
        return 2

    env = _env_without_node()
    print("== 自检: decode CLI（子进程 PATH 已剔除 node 目录 / NODE_* 变量）")

    # ---- 1) 料号解码 ----
    for query, expect in PART_CASES:
        rc, out, err = run_cli(DECODE_PY, ["--json", query], env)
        assert rc == 0, "%s rc=%d stderr=%s" % (query, rc, err)
        d = _json_out(out)
        assert d["schema"] == "chiplookup.fdnext.decode.v1"
        assert d["status"] == "ok", "%s status=%s" % (query, d["status"])
        dev, fld, meta = d["device"], d["fields"], d.get("meta") or {}
        for k, v in expect.items():
            if k == "ruleId":
                assert meta.get("ruleId") == v, "%s ruleId=%s" % (query, meta.get("ruleId"))
            elif k == "vendor" or k == "chipKind":
                assert dev.get(k) == v, "%s device.%s=%s" % (query, k, dev.get(k))
            else:
                assert fld.get(k) == v, "%s fields.%s=%s" % (query, k, fld.get(k))
        _check_no_null_fields(d)
    print("料号解码断言通过: %d 例" % len(PART_CASES))

    # ---- 2) Flash ID 解码 ----
    for query, expect in ID_CASES:
        rc, out, err = run_cli(DECODE_PY, ["--json", query], env)
        assert rc == 0, "%s rc=%d stderr=%s" % (query, rc, err)
        d = _json_out(out)
        assert d["status"] == "ok"
        dev, fld = d["device"], d["fields"]
        assert dev["vendor"] == expect["vendor"]
        assert fld.get("plane_count") == expect["plane_count"]
        for absent in expect.get("absent", []):
            assert absent not in fld, "%s 不应含扩展字段 %s" % (query, absent)
        _check_no_null_fields(d)
    print("Flash ID 解码断言通过: %d 例" % len(ID_CASES))

    # ---- 3) known-boundary ----
    for query in BOUNDARY_CASES:
        rc, out, err = run_cli(DECODE_PY, [query], env)
        assert rc == 1, "%s 应返回 1（未命中）" % query
        assert "not_found" in out
        assert "mdb 索引" in out, "%s 应提示 mdb 索引边界" % query
    print("known-boundary 断言通过: %d 例（not_found + mdb 提示 + rc=1）" % len(BOUNDARY_CASES))

    # ---- 4) --stdin 多查询 ----
    rc, out, err = run_cli(
        DECODE_PY, ["--json", "--stdin"], env,
        stdin_text="MT29F512G08EBHAFJ4 2C84044BA900\n",
    )
    assert rc == 0, err
    payload = _json_out(out)
    assert len(payload["results"]) == 2
    assert all(x["result"]["status"] == "ok" for x in payload["results"])
    print("stdin 多查询断言通过")

    # ---- 5) CSV 只填空合并（decode_db）----
    tmp = tempfile.mkdtemp(prefix="chiplookup_decodetest_")
    db_path = os.path.join(tmp, "chip_database.csv")
    fixture = [
        ("MT40A1G16JC-062E", {}),
        ("H5AN8G8NCJR-VKC", {}),
        ("K4A4G165WF-BCWE", {"manufacturer": "AMD-KEEP"}),  # 已填 -> 绝不覆盖
        ("D8DKS", {}),  # mdb 边界 -> 保持原样
    ]
    with open(db_path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        for pn, pre in fixture:
            row = {k: "" for k in FIELDS}
            row["part_number"] = pn
            row.update(pre)
            f.write(",".join('"%s"' % (row[k] or "") for k in FIELDS) + "\n")

    rc, out, err = run_cli(DECODE_DB_PY, ["--db", db_path, "--write"], env)
    assert rc == 0, "decode_db rc=%d stderr=%s" % (rc, err)

    with open(db_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = {r["part_number"]: r for r in csv.DictReader(f)}
    assert rows["MT40A1G16JC-062E"]["type"] == "DDR4"
    assert rows["MT40A1G16JC-062E"]["capacity"] == "16Gb"
    assert rows["MT40A1G16JC-062E"]["manufacturer"] == "Micron"
    assert rows["H5AN8G8NCJR-VKC"]["manufacturer"] == "SK Hynix"
    assert rows["H5AN8G8NCJR-VKC"]["bit_width"] == "x8"
    # 人工字段保护
    assert rows["K4A4G165WF-BCWE"]["manufacturer"] == "AMD-KEEP"
    assert rows["K4A4G165WF-BCWE"]["type"] == "DDR4"   # 其它空列仍可补
    # mdb 边界不瞎填
    d8 = rows["D8DKS"]
    assert not any((d8.get(k) or "").strip() for k in FIELDS if k != "part_number")

    # 幂等 + BOM
    before = open(db_path, "rb").read()
    assert before.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM"
    rc, out, err = run_cli(DECODE_DB_PY, ["--db", db_path, "--write"], env)
    assert rc == 0
    after = open(db_path, "rb").read()
    assert before == after, "二次写回不应改动文件"
    print("decode_db 只填空断言通过: 覆盖保护 / 边界留空 / 幂等 / BOM")
    print("   临时数据库(可复查): %s" % db_path)

    print("== 自检全部通过: 无 Node 环境下 decode CLI / CSV 合并端到端 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
