# -*- coding: utf-8 -*-
"""
chip_lookup.database
--------------------
轻量数据层：把 data/chip_database.xlsx 加载到内存（dict 列表）。
维护接口：list_records / upsert / delete / export_xlsx / import_xlsx。

不引入 SQLite 是为了让"备份/分发/手改"只依赖一个文件，
方便业务侧直接在 Excel 里修改、再用应用导入。

字段约定（与 data/chip_database.xlsx 表头保持一致）：
    part_number  ：料号（主键，唯一）
    model        ：型号
    manufacturer ：厂商
    type         ：DRAM 类型（如 DDR5 / DDR4）
    capacity     ：容量（如 16Gb）
    bit_width    ：位宽（如 x4/x8/x16）
    voltage      ：电压（如 1.1V VDD）
    speed        ：速度等级（如 DDR5-5600B CL46）
    package      ：封装（如 VFBGA-78）
    dimensions   ：尺寸（如 7.5x11x0.9）
    die_count    ：Die 数
    cs_count     ：CS 数
    die_revision ：Die 版本
    op_temp      ：工作温度（如 商用 / 工业）
    notes        ：备注
"""

from __future__ import annotations

import csv
import os
import threading
from typing import List, Optional

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False


# 默认表头（用户也可以加新列，但导入导出要保持顺序）
DEFAULT_FIELDS = [
    "part_number", "model", "manufacturer", "type", "capacity",
    "bit_width", "voltage", "speed", "package", "dimensions",
    "die_count", "cs_count", "die_revision", "op_temp", "notes",
]

# 尝试的编码顺序：UTF-8(BOM) -> UTF-8 -> GBK -> GB18030 -> Latin-1(兜底)
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")

# 整数类型字段：读取时做类型转换/合法性校验
NUMERIC_FIELDS = ("die_count", "cs_count")


# ---------------------------------------------------------------------------
# xlsx 读写
# ---------------------------------------------------------------------------

def _read_xlsx_records(path: str, *, strict: bool = False):
    """读取 xlsx 文件并返回 (records, issues)。"""
    issues: List[str] = []
    if not os.path.exists(path):
        if strict:
            raise ValueError("xlsx 文件不存在: %s" % path)
        return [], ["xlsx 文件不存在: %s" % path]
    if not HAS_OPENPYXL:
        msg = "未安装 openpyxl，无法读取 xlsx: %s" % path
        if strict:
            raise ValueError(msg)
        return [], [msg]
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            wb.close()
            msg = "xlsx 表头为空: %s" % path
            if strict:
                raise ValueError(msg)
            return [], [msg]
        headers = [str(h or "").strip() for h in header_row]
        if "part_number" not in headers:
            wb.close()
            msg = "xlsx 缺少必填表头 part_number: %s" % path
            if strict:
                raise ValueError(msg)
            return [], [msg]
        header_idx = {h: i for i, h in enumerate(headers)}
        records: List[dict] = []
        seen: set = set()
        for ln, row in enumerate(rows_iter, start=2):
            vals = list(row)
            # 跳过全空行
            if not any((v is not None and str(v).strip()) for v in vals):
                continue
            clean = {}
            for k in DEFAULT_FIELDS:
                idx = header_idx.get(k)
                v = vals[idx] if idx is not None and idx < len(vals) else None
                clean[k] = str(v).strip() if v is not None else ""
            # 类型转换 + 校验
            for nf in NUMERIC_FIELDS:
                v = clean.get(nf)
                if v:
                    try:
                        clean[nf] = str(int(v))
                    except ValueError:
                        try:
                            clean[nf] = str(float(v))
                        except ValueError:
                            issues.append(
                                "第 %d 行 %s='%s' 不是有效的数字，保留原文"
                                % (ln, nf, v)
                            )
            pn = clean["part_number"].upper()
            if pn in seen:
                issues.append(
                    "part_number 重复已跳过: %s（第 %d 行）"
                    % (clean["part_number"], ln)
                )
                continue
            seen.add(pn)
            records.append(clean)
        wb.close()
    except Exception as exc:
        if strict:
            raise ValueError("无法读取 xlsx: %s" % exc) from exc
        issues.append("无法读取 xlsx: %s" % exc)
        return [], issues
    return records, issues


def _write_xlsx(path: str, records: List[dict]) -> None:
    """将记录写入 xlsx（原子替换）。"""
    if not HAS_OPENPYXL:
        raise RuntimeError("未安装 openpyxl，无法写入 xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "chip_database"
    ws.append(DEFAULT_FIELDS)
    for r in records:
        ws.append([r.get(k, "") for k in DEFAULT_FIELDS])
    tmp = path + ".tmp"
    wb.save(tmp)
    wb.close()
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CSV 读写（保留向后兼容）
# ---------------------------------------------------------------------------

def detect_encoding(path: str) -> str:
    """按解码成功率探测 CSV 实际编码（GBK 库也能正常读取）。"""
    with open(path, "rb") as f:
        head = f.read(8192)
    for enc in _ENCODINGS:
        try:
            head.decode(enc)
            return enc
        except UnicodeError:
            continue
    return "latin-1"


def read_csv_records(path: str, *, strict: bool = False):
    """
    读取 CSV 并返回 (records, issues)。
    records：按 DEFAULT_FIELDS 规整好的 dict 列表；
    issues：校验告警列表（重复主键、非法数值、无法读取等）。
    """
    issues = []
    if not os.path.exists(path):
        if strict:
            raise ValueError("CSV 文件不存在: %s" % path)
        return [], ["CSV 文件不存在: %s" % path]
    try:
        enc = detect_encoding(path)
    except OSError as exc:
        if strict:
            raise ValueError("无法读取 CSV: %s" % exc) from exc
        return [], ["无法读取 CSV: %s" % exc]

    records = []
    seen = set()
    try:
        with open(path, "r", encoding=enc, newline="") as f:
            reader = csv.DictReader(f)
            headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
            if not reader.fieldnames:
                msg = "CSV 表头为空，文件内容无效: %s" % path
                if strict:
                    raise ValueError(msg)
                return [], [msg]
            if "part_number" not in headers:
                msg = "CSV 缺少必填表头 part_number: %s" % path
                if strict:
                    raise ValueError(msg)
                return [], [msg]
            for ln, row in enumerate(reader, start=2):
                if not any((v or "").strip() for v in row.values()):
                    continue
                clean = {k: (row.get(k, "") or "").strip() for k in DEFAULT_FIELDS}
                for nf in NUMERIC_FIELDS:
                    v = clean.get(nf)
                    if v:
                        try:
                            clean[nf] = str(int(v))
                        except ValueError:
                            try:
                                clean[nf] = str(float(v))
                            except ValueError:
                                issues.append(
                                    "第 %d 行 %s='%s' 不是有效的数字，保留原文"
                                    % (ln, nf, v)
                                )
                pn = clean["part_number"].upper()
                if pn in seen:
                    issues.append(
                        "part_number 重复已跳过: %s（第 %d 行）"
                        % (clean["part_number"], ln)
                    )
                    continue
                seen.add(pn)
                records.append(clean)
    except OSError as exc:
        if strict:
            raise ValueError("无法读取 CSV: %s" % exc) from exc
        issues.append("无法读取 CSV: %s" % exc)
        return [], issues
    return records, issues


def _write_csv(path: str, records: List[dict]) -> None:
    """将记录写入 CSV（原子替换，utf-8-sig BOM）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in DEFAULT_FIELDS})
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 统一读写入口（按扩展名分派）
# ---------------------------------------------------------------------------

def _is_xlsx(path: str) -> bool:
    return path.lower().endswith(".xlsx")


def read_records(path: str, *, strict: bool = False):
    """按文件扩展名自动选择 xlsx 或 CSV 读取。返回 (records, issues)。"""
    if _is_xlsx(path):
        return _read_xlsx_records(path, strict=strict)
    return read_csv_records(path, strict=strict)


def write_records(path: str, records: List[dict]) -> None:
    """按文件扩展名自动选择 xlsx 或 CSV 写入。"""
    if _is_xlsx(path):
        _write_xlsx(path, records)
    else:
        _write_csv(path, records)


# ---------------------------------------------------------------------------
# 公共校验入口
# ---------------------------------------------------------------------------

def validate_csv(path: str):
    """严格校验数据文件（本地模式入口）。返回 (records, issues)，致命问题抛 ValueError。"""
    return read_records(path, strict=True)


# ---------------------------------------------------------------------------
# ChipDatabase
# ---------------------------------------------------------------------------

class ChipDatabase:
    """线程安全的内存数据库封装。"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self._records: List[dict] = []
        self._lock = threading.RLock()
        self.warnings: List[str] = []
        self._load()

    # ---------- 加载 / 持久化 ----------

    def _load(self) -> None:
        if not os.path.exists(self.csv_path):
            self._records = []
            self.warnings = []
            return
        records, issues = read_records(self.csv_path, strict=False)
        self._records = records
        self.warnings = list(issues)

    def save(self) -> None:
        """把当前内存数据写回文件（原子替换）。"""
        with self._lock:
            write_records(self.csv_path, self._records)

    def reload(self) -> None:
        with self._lock:
            self._load()

    # ---------- 查询 ----------

    def list_records(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._records]

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def get(self, part_number: str) -> Optional[dict]:
        if not part_number:
            return None
        key = part_number.strip().upper()
        with self._lock:
            for r in self._records:
                if (r.get("part_number", "") or "").strip().upper() == key:
                    return dict(r)
        return None

    # ---------- 增删改 ----------

    def upsert(self, record: dict) -> None:
        """以 part_number 为主键，存在则更新，不存在则追加。"""
        if not record or not record.get("part_number"):
            raise ValueError("缺少必填字段 part_number")
        clean = {k: (record.get(k, "") or "").strip() for k in DEFAULT_FIELDS}
        with self._lock:
            key = clean["part_number"].upper()
            for i, r in enumerate(self._records):
                if r["part_number"].upper() == key:
                    self._records[i] = clean
                    return
            self._records.append(clean)

    def delete(self, part_number: str) -> bool:
        if not part_number:
            return False
        key = part_number.strip().upper()
        with self._lock:
            for i, r in enumerate(self._records):
                if r["part_number"].upper() == key:
                    del self._records[i]
                    return True
        return False

    # ---------- 批量导入导出 ----------

    def import_data(self, src_path: str, replace: bool = False) -> int:
        """
        从 src_path 导入数据（支持 xlsx 和 CSV）。
        replace=False 时按主键合并；replace=True 时整库替换。
        返回实际写入（新增 + 更新）的记录数。
        """
        if not os.path.exists(src_path):
            raise FileNotFoundError(src_path)
        rows, issues = read_records(src_path, strict=True)
        added = 0
        with self._lock:
            self.warnings = self.warnings + issues
            if replace:
                self._records = rows
                return len(rows)
            index = {r["part_number"].upper(): i for i, r in enumerate(self._records)}
            for row in rows:
                i = index.get(row["part_number"].upper())
                if i is None:
                    self._records.append(row)
                    index[row["part_number"].upper()] = len(self._records) - 1
                    added += 1
                else:
                    self._records[i] = row
                    added += 1
            return added

    # 保留旧名兼容
    import_csv = import_data

    def export_csv(self, dst_path: str) -> int:
        """导出全部记录到文件（支持 xlsx 和 CSV），返回记录数。"""
        with self._lock:
            data = [dict(r) for r in self._records]
        write_records(dst_path, data)
        return len(data)


# 提供一个默认路径的便捷构造
def default_database_path() -> str:
    """返回 data/chip_database.xlsx 的绝对路径（在源码同级 data/ 目录下）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "data", "chip_database.xlsx"))
