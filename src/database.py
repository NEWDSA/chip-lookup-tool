# -*- coding: utf-8 -*-
"""
chip_lookup.database
--------------------
轻量数据层：把 data/chip_database.csv 加载到内存（dict 列表）。
维护接口：list_records / upsert / delete / export_csv / import_csv。

不引入 SQLite 是为了让"备份/分发/手改"只依赖一个文本文件，
方便业务侧直接在 Excel 里改 CSV、再用应用导入。

字段约定（与 data/chip_database.csv 表头保持一致）：
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
from typing import Iterable, List, Optional


# 默认 CSV 表头（用户也可以加新列，但导入导出要保持顺序）
DEFAULT_FIELDS = [
    "part_number", "model", "manufacturer", "type", "capacity",
    "bit_width", "voltage", "speed", "package", "dimensions",
    "die_count", "cs_count", "die_revision", "op_temp", "notes",
]


class ChipDatabase:
    """线程安全的内存数据库封装。"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self._records: List[dict] = []
        self._lock = threading.RLock()
        self._load()

    # ---------- 加载 / 持久化 ----------

    def _load(self) -> None:
        if not os.path.exists(self.csv_path):
            self._records = []
            return
        with open(self.csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            self._records = []
            for row in reader:
                # 跳过空行
                if not any((v or "").strip() for v in row.values()):
                    continue
                # 只保留已知字段，缺失字段补空字符串
                clean = {k: (row.get(k, "") or "").strip() for k in DEFAULT_FIELDS}
                self._records.append(clean)

    def save(self) -> None:
        """把当前内存数据写回 CSV（原子替换）。"""
        with self._lock:
            tmp = self.csv_path + ".tmp"
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
                writer.writeheader()
                for r in self._records:
                    writer.writerow({k: r.get(k, "") for k in DEFAULT_FIELDS})
            os.replace(tmp, self.csv_path)

    def reload(self) -> None:
        with self._lock:
            self._load()

    # ---------- 查询 ----------

    def list_records(self) -> List[dict]:
        with self._lock:
            # 返回一份拷贝，避免外部误改
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

    def import_csv(self, src_path: str, replace: bool = False) -> int:
        """
        从 src_path 导入 CSV。
        replace=False 时按主键合并；replace=True 时整库替换（危险！会先备份源 CSV）。
        返回实际写入（新增 + 更新）的记录数。
        """
        if not os.path.exists(src_path):
            raise FileNotFoundError(src_path)
        added = 0
        with open(src_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                if not any((v or "").strip() for v in row.values()):
                    continue
                rows.append({k: (row.get(k, "") or "").strip() for k in DEFAULT_FIELDS})
        with self._lock:
            if replace:
                self._records = rows
                return len(rows)
            # 合并
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

    def export_csv(self, dst_path: str) -> int:
        """导出全部记录到 CSV，返回记录数。"""
        with self._lock:
            data = [dict(r) for r in self._records]
        with open(dst_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
            writer.writeheader()
            for r in data:
                writer.writerow({k: r.get(k, "") for k in DEFAULT_FIELDS})
        return len(data)


# 提供一个默认路径的便捷构造
def default_database_path() -> str:
    """返回 data/chip_database.csv 的绝对路径（在源码同级 data/ 目录下）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "data", "chip_database.csv"))
