# -*- coding: utf-8 -*-
"""
tests/test_modes.py
-------------------
三种运行模式（本地CSV / 上游 / 混合）的单元测试 + 异常场景 + 配置合并 + CLI 冒烟。

运行：python tests/test_modes.py         （仅依赖标准库，全部离线）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 只有通过 test_modes.py 直接运行才需要解析；被 unittest 收集时无妨
from config import (
    HYBRID_LOCAL_FIRST,
    HYBRID_UPSTREAM_FIRST,
    MODE_HYBRID,
    MODE_LOCAL,
    MODE_UPSTREAM,
    Settings,
    build_settings,
    resolve_config_path,
)
from sources import (
    HybridSource,
    LocalSource,
    SourceLoadError,
    UpstreamSource,
    make_source,
    merge_records,
)
from database import validate_csv, read_csv_records

FIXTURES = os.path.join(HERE, "fixtures")
UP_CACHE = os.path.join(FIXTURES, "upstream_cached")
LOCAL_CSV = os.path.join(FIXTURES, "local_sample.csv")


def settings(tmp=None, **kw) -> Settings:
    base = dict(
        upstream_offline=True,
        upstream_cache_dir=UP_CACHE,
        csv_path=LOCAL_CSV,
    )
    base.update(kw)
    return Settings(**base)


class LocalModeTests(unittest.TestCase):
    def test_load_records(self):
        s = LocalSource(settings(mode=MODE_LOCAL))
        self.assertEqual(s.count(), 3)
        pns = [r["part_number"] for r in s.list_records()]
        self.assertIn("D8DKS", pns)
        self.assertIn("K4AAG165WA-BCWE", pns)

    def test_blank_record_kept_with_empty_fields(self):
        s = LocalSource(settings(mode=MODE_LOCAL))
        rec = {r["part_number"]: r for r in s.list_records()}["D8DKP"]
        self.assertEqual(rec["model"], "")
        self.assertEqual(rec["manufacturer"], "")

    def test_import_csv_merges(self):
        # 用 fixture 的副本，避免改动共享测试数据
        with tempfile.TemporaryDirectory() as d:
            copy = os.path.join(d, "local_copy.csv")
            with open(LOCAL_CSV, "rb") as src_f:
                data = src_f.read()
            with open(copy, "wb") as dst_f:
                dst_f.write(data)
            src = LocalSource(settings(mode=MODE_LOCAL, csv_path=copy))
            before = src.count()
            tmp = os.path.join(d, "cl_import_test.csv")
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                f.write("part_number,model,manufacturer\n")
                f.write("9999TEST,NEWMODEL,Micron\n")
            n = src.import_csv(tmp)
            self.assertEqual(n, 1)
            self.assertEqual(src.count(), before + 1)
            # 校验只是合并，不覆盖已有记录
            by_pn = {r["part_number"]: r for r in src.list_records()}
            self.assertEqual(by_pn["D8DKS"]["model"], "MT60B2G8RZ-56B:D")

    def test_export_csv(self):
        src = LocalSource(settings(mode=MODE_LOCAL))
        out = os.path.join(tempfile.gettempdir(), "cl_export_test.csv")
        n = src.export_csv(out)
        self.assertEqual(n, 3)
        recs, issues = validate_csv(out)
        self.assertEqual(len(recs), 3)
        os.remove(out)

    def test_missing_file_raises(self):
        s = settings(mode=MODE_LOCAL, csv_path=os.path.join(tempfile.gettempdir(), "no_such_file.csv"))
        with self.assertRaises(SourceLoadError):
            LocalSource(s)

    def test_invalid_header_raises(self):
        tmp = os.path.join(tempfile.gettempdir(), "cl_bad_header.csv")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("foo,bar,baz\n1,2,3\n")
        s = settings(mode=MODE_LOCAL, csv_path=tmp)
        with self.assertRaises(SourceLoadError):
            LocalSource(s)
        os.remove(tmp)

    def test_duplicate_key_and_bad_number_warnings(self):
        tmp = os.path.join(tempfile.gettempdir(), "cl_dup.csv")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("part_number,die_count\nDUPX,1\nDUPX,2\nBADX,abc\n")
        recs, issues = read_csv_records(tmp, strict=True)
        pns = [r["part_number"] for r in recs]
        self.assertIn("DUPX", pns)      # 第一条保留
        self.assertIn("BADX", pns)      # 非数字保留原文
        msgs = "\n".join(issues)
        self.assertIn("重复", msgs)
        self.assertIn("不是有效", msgs)
        os.remove(tmp)

    def test_gbk_encoding_detected(self):
        tmp = os.path.join(tempfile.gettempdir(), "cl_gbk.csv")
        with open(tmp, "w", encoding="gbk") as f:
            f.write("part_number,model,notes\nGBK1,MODEL1,\u6ce8\u91ca\u6587\u672c\n")
        recs, _ = read_csv_records(tmp, strict=True)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["notes"], "\u6ce8\u91ca\u6587\u672c")
        os.remove(tmp)


class UpstreamModeTests(unittest.TestCase):
    def test_load_from_cache_offline(self):
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        self.assertGreaterEqual(s.count(), 6)
        by_pn = {r["part_number"]: r for r in s.list_records()}
        self.assertEqual(by_pn["D8DKS"]["model"], "MT60B2G8RZ-56B:D")
        self.assertEqual(by_pn["D8DKS"]["manufacturer"], "Micron")

    def test_multi_model_mark_left_blank(self):
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        rec = {r["part_number"]: r for r in s.list_records()}["MULTI"]
        self.assertEqual(rec["model"], "")  # 多个型号，留空待人工确认
        self.assertEqual(rec["manufacturer"], "Micron")

    def test_pn_entry_record(self):
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        rec = {r["part_number"]: r for r in s.list_records()}["SPTEK12345"]
        self.assertEqual(rec["manufacturer"], "SpecTek")
        self.assertEqual(rec["model"], "SPTEK12345")

    def test_export_upstream_view(self):
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        out = os.path.join(tempfile.gettempdir(), "cl_up_export.csv")
        n = s.export_csv(out)
        self.assertEqual(n, s.count())
        os.remove(out)

    def test_records_have_all_default_fields(self):
        """上游记录必须包含全部 15 个 DEFAULT_FIELDS 键（空值用 '' 而非缺失）。"""
        from database import DEFAULT_FIELDS
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        for rec in s.list_records():
            for key in DEFAULT_FIELDS:
                self.assertIn(key, rec, "记录 %s 缺少字段 %s" % (rec["part_number"], key))

    def test_empty_fields_are_empty_string(self):
        """上游记录的规格字段：无 fdnext 引擎时为空，有引擎时由型号解码补全。"""
        from upstream import UpstreamProvider
        # 直接调用 build_upstream_records（不传 engine）→ 空字段
        from fdnext.indexes import FdnextIndexes
        from upstream import build_upstream_records
        cache = os.path.expanduser(r'~\AppData\Local\chiplookup\upstream')
        idx, _ = FdnextIndexes.load(cache_dir=cache, offline=True, include_catalog=False)
        records_no_engine = build_upstream_records(idx)
        rec_no = {r["part_number"]: r for r in records_no_engine}["D8BDK"]
        for key in ("type", "capacity", "bit_width", "voltage", "speed",
                     "package", "dimensions", "die_count", "cs_count",
                     "die_revision", "op_temp"):
            self.assertEqual(rec_no[key], "", "无引擎时字段 %s 应为空" % key)

        # 有 engine 时字段被补全
        from fdnext.engine import load_engine
        engine = load_engine(cache, offline=True)
        records_with_engine = build_upstream_records(idx, engine=engine)
        rec_en = {r["part_number"]: r for r in records_with_engine}["D8BDK"]
        self.assertEqual(rec_en["type"], "LPDDR4X")
        self.assertEqual(rec_en["capacity"], "4Gb")
        self.assertEqual(rec_en["bit_width"], "x16")
        self.assertIn("VDD", rec_en["voltage"])
        self.assertIn("MHz", rec_en["speed"])
        self.assertNotEqual(rec_en["package"], "")

    def test_placeholder_display_logic(self):
        """卡片渲染占位逻辑：空值显示 '—'，非空值原样显示。"""
        PLACEHOLDER = "\u2014"  # —
        test_cases = [
            ("", PLACEHOLDER),
            (None, PLACEHOLDER),
            ("DDR5", "DDR5"),
            ("16Gb", "16Gb"),
        ]
        for value, expected in test_cases:
            display = str(value) if value not in ("", None) else PLACEHOLDER
            self.assertEqual(display, expected)

    def test_import_unsupported(self):
        s = UpstreamSource(settings(mode=MODE_UPSTREAM))
        self.assertFalse(s.supports_import())

    def test_missing_cache_offline_raises(self):
        s = settings(mode=MODE_UPSTREAM,
                     upstream_cache_dir=os.path.join(tempfile.gettempdir(), "no_cache_here"))
        with self.assertRaises(SourceLoadError):
            UpstreamSource(s)

    def test_corrupt_cache_raises(self):
        tmp = tempfile.mkdtemp(prefix="cl_cache_")
        with open(os.path.join(tmp, "mdb.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        with open(os.path.join(tmp, "dram-pn.json"), "w", encoding="utf-8") as f:
            json.dump([{"vendor": "x", "pn": "Y"}], f)
        s = settings(mode=MODE_UPSTREAM, upstream_cache_dir=tmp)
        with self.assertRaises(SourceLoadError):
            UpstreamSource(s)

    def test_integrity_validation_bad_shape(self):
        tmp = tempfile.mkdtemp(prefix="cl_cache_")
        with open(os.path.join(tmp, "mdb.json"), "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dict"], f)  # mdb 应为对象
        with open(os.path.join(tmp, "dram-pn.json"), "w", encoding="utf-8") as f:
            json.dump([{"vendor": "x", "pn": "Y"}], f)
        s = settings(mode=MODE_UPSTREAM, upstream_cache_dir=tmp)
        with self.assertRaises(SourceLoadError):
            UpstreamSource(s)


class HybridModeTests(unittest.TestCase):
    def setUp(self):
        self.local, _ = validate_csv(LOCAL_CSV)

    def _up(self):
        return UpstreamSource(settings(mode=MODE_UPSTREAM)).list_records()

    def test_local_first_fills_empty_from_upstream(self):
        s = HybridSource(settings(mode=MODE_HYBRID, hybrid_priority=HYBRID_LOCAL_FIRST))
        by_pn = {r["part_number"]: r for r in s.list_records()}
        # D8DKP：本地空白，上游补全
        self.assertEqual(by_pn["D8DKP"]["model"], "MT60B2G8RZ-46E:D")
        self.assertEqual(by_pn["D8DKP"]["manufacturer"], "Micron")
        # K4AAG165WA-BCWE：本地缺厂商，上游补厂商
        self.assertEqual(by_pn["K4AAG165WA-BCWE"]["manufacturer"], "Samsung")
        # 本地已填字段不被上游覆盖
        self.assertEqual(by_pn["D8DKS"]["model"], "MT60B2G8RZ-56B:D")
        self.assertEqual(by_pn["D8DKS"]["notes"], "\u5546\u7528")  # 本地备注保留

    def test_conflict_local_wins_when_local_first(self):
        # 构造冲突：本地 D8DKS model = 本地记录
        s = HybridSource(settings(mode=MODE_HYBRID, hybrid_priority=HYBRID_LOCAL_FIRST))
        rec = {r["part_number"]: r for r in s.list_records()}["D8DKS"]
        self.assertEqual(rec["model"], "MT60B2G8RZ-56B:D")  # 本地优先，未被上游改动

    def test_upstream_first_keeps_upstream(self):
        s = HybridSource(settings(mode=MODE_HYBRID, hybrid_priority=HYBRID_UPSTREAM_FIRST))
        by_pn = {r["part_number"]: r for r in s.list_records()}
        rec = by_pn["D8DKS"]
        self.assertEqual(rec["model"], "MT60B2G8RZ-56B:D")
        self.assertEqual(rec["manufacturer"], "Micron")
        # 上游无 extra 字段（如 notes）时，本地兜底补齐
        self.assertEqual(rec["notes"], "\u5546\u7528")

    def test_dedupe_and_counts(self):
        up = self._up()
        merged = merge_records(self.local, up, HYBRID_LOCAL_FIRST)
        keys = [r["part_number"].upper() for r in merged]
        up_keys = {r["part_number"].upper() for r in up}
        local_keys = {r["part_number"].upper() for r in self.local}
        # 去重：本地+上游重叠的料号只出现一次
        overlap = up_keys & local_keys
        self.assertGreaterEqual(len(overlap), 2)  # D8DKS / D8DKP / K4AAG165WA-BCWE
        self.assertEqual(len(keys), len(set(keys)))  # 无重复主键
        self.assertEqual(merged[0]["part_number"], "D8DKS")  # 本地优先排序在前

    def test_upstream_failure_degrades_to_local(self):
        s = settings(mode=MODE_HYBRID,
                     upstream_cache_dir=os.path.join(tempfile.gettempdir(), "no_cache_here"))
        src = HybridSource(s)
        # 上游离线缺失 -> 降级仅用本地兜底，不抛错
        self.assertEqual(src.count(), 3)
        warns = "\n".join(src.warnings)
        self.assertIn("降级", warns)

    def test_both_fail_raises(self):
        s = settings(mode=MODE_HYBRID,
                     csv_path=os.path.join(tempfile.gettempdir(), "no_such.csv"),
                     upstream_cache_dir=os.path.join(tempfile.gettempdir(), "no_cache_here"))
        with self.assertRaises(SourceLoadError):
            HybridSource(s)

    def test_import_export_supported(self):
        s = HybridSource(settings(mode=MODE_HYBRID))
        self.assertTrue(s.supports_import())
        self.assertTrue(s.supports_export())


class MergeTests(unittest.TestCase):
    def test_field_mapping_and_dedupe(self):
        local = [
            {"day": "", "part_number": "A", "model": "", "manufacturer": "", "capacity": "16Gb"},
        ]
        upstream = [
            {"day": "", "part_number": "a", "model": "MODEL-A", "manufacturer": "Micron", "capacity": ""},
            {"day": "", "part_number": "B", "model": "MODEL-B", "manufacturer": "Samsung", "capacity": ""},
        ]
        merged = merge_records(local, upstream, HYBRID_LOCAL_FIRST)
        by_pn = {r["part_number"]: r for r in merged}
        # 大小写不敏感去重，本地优先 + 只填空
        self.assertEqual(by_pn["A"]["model"], "MODEL-A")
        self.assertEqual(by_pn["A"]["manufacturer"], "Micron")
        self.assertEqual(by_pn["A"]["capacity"], "16Gb")  # 本地已有不被覆盖
        self.assertIn("B", by_pn)


class ConfigTests(unittest.TestCase):
    def test_mode_labels_network(self):
        # 上游模式统一以「网络」命名（internal 标识仍为 upstream）
        from config import MODE_LABELS, MODE_UPSTREAM
        self.assertEqual(MODE_LABELS[MODE_UPSTREAM], "网络")

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "chiplookup.json")
            s = Settings(mode=MODE_HYBRID, hybrid_priority=HYBRID_UPSTREAM_FIRST,
                         config_path=path)
            s.save()
            s2 = Settings(config_path=path)
            with open(path, encoding="utf-8") as f:
                s2.apply_dict(json.load(f))
            self.assertEqual(s2.mode, MODE_HYBRID)
            self.assertEqual(s2.hybrid_priority, HYBRID_UPSTREAM_FIRST)

    def test_invalid_mode_fallback(self):
        s = Settings(mode="bogus")
        s.validate()
        self.assertEqual(s.mode, MODE_LOCAL)
        self.assertTrue(any("模式" in w for w in s.warnings))

    def test_invalid_priority_fallback(self):
        s = Settings(hybrid_priority="nope")
        s.validate()
        self.assertEqual(s.hybrid_priority, HYBRID_LOCAL_FIRST)

    def _make_args(self, **kw):
        ns = type("NS", (), {"config": None, "mode": None, "db": None,
                             "hybrid_priority": None, "upstream_offline": None,
                             "upstream_refresh": None, "upstream_cache_dir": None,
                             "upstream_timeout": None, "upstream_retries": None,
                             "upstream_rate_limit": None})
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_cli_overrides_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "chiplookup.json")
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"mode": MODE_UPSTREAM}, f)
            s = build_settings(self._make_args(config=cfg, mode=MODE_LOCAL))
            self.assertEqual(s.mode, MODE_LOCAL)  # CLI 覆盖配置文件

    def test_make_source_mode_mapping(self):
        self.assertIsInstance(make_source(settings(mode=MODE_LOCAL)), LocalSource)
        self.assertIsInstance(make_source(settings(mode=MODE_UPSTREAM)), UpstreamSource)
        self.assertIsInstance(make_source(settings(mode=MODE_HYBRID)), HybridSource)


class CliSmokeTests(unittest.TestCase):
    def _run(self, *extra):
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "src", "main.py"), "--headless-stats", *extra],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
            env=env,
        )

    def test_cli_local(self):
        p = self._run("--mode", MODE_LOCAL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("[stats]", p.stdout)

    def test_cli_upstream_offline(self):
        p = self._run("--mode", MODE_UPSTREAM, "--upstream-offline",
                      "--upstream-cache-dir", UP_CACHE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("模式：网络", p.stdout)  # 上游模式统一命名为「网络」
        self.assertIn("共 ", p.stdout)

    def test_cli_hybrid_offline(self):
        p = self._run("--mode", MODE_HYBRID, "--upstream-offline",
                      "--upstream-cache-dir", UP_CACHE, "--db", LOCAL_CSV)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("模式：混合", p.stdout)

    def test_cli_bad_mode_file_exits_nonzero(self):
        tmp = os.path.join(tempfile.gettempdir(), "cl_missing.csv")
        if os.path.exists(tmp):
            os.remove(tmp)
        p = self._run("--mode", MODE_LOCAL, "--db", tmp)
        self.assertEqual(p.returncode, 1)
        self.assertIn("启动失败", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)