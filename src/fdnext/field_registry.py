# -*- coding: utf-8 -*-
"""
src/fdnext/field_registry.py
----------------------------
上游 fdnextFieldRegistry 的键集（fdnext.ts 派生的字段白名单）。

enrichNandDieProfileFields / setDraftField 等只允许写「已注册」字段。
这里保留与上游一致的键清单，供 is_fdnext_field_key 判定。
"""

# 提取自 @itxtech/fdnext-core 3.2.0 的 fdnextFieldRegistry 键（101 个，
# 与 golden 生成所用官方引擎一致；GitHub master 快照版本较旧会缺字段）
FDNEXT_FIELD_KEYS = (
    "part_number", "vendor", "original_vendor", "chip_kind",
    "product_type", "identifier", "id_scheme", "marking_code",
    "density", "sector_size", "die_density", "die_codename",
    "process_alias", "component_density", "storage_density", "dram_density",
    "dram_configuration", "cell_level", "process_node", "layer_count",
    "device_width", "voltage", "package", "form_factor",
    "packing_type", "assembly", "segment", "lead_free",
    "halogen_free", "wafer", "bad_block", "sku",
    "multi_chip", "cu", "storage_interface", "generation_info",
    "die_stack", "dram_type", "dram_speed", "cas_latency",
    "dram_width", "dram_voltage", "page_size", "block_size",
    "blocks_per_lun", "pages_per_block", "simultaneously_programmed_pages", "redundant_area_size",
    "half_page_and_size", "die_count", "ce_count", "cs_count",
    "rb_count", "bank_count", "channel_count", "plane_count",
    "controller", "controller_code", "controller_revision", "config_code",
    "package_code", "solder_type", "package_configuration", "die_revision",
    "product_family", "product_version", "product_mode", "product_class",
    "product_generation", "managed_family", "nand_technology", "series_info",
    "speed_grade", "timing_mode_async", "edo", "interleave",
    "cache", "component_width", "nand_component", "component_voltage",
    "dram_die_density", "dram_die_count", "dram_generation", "die_code",
    "interface_type", "interface_note", "toggle", "ecc_level",
    "ecc_enabled", "micron_part_number", "prod_date", "diffusion_loc",
    "encapsulation_loc", "prod_status", "feature_code", "special_option",
    "enterprise", "revision", "package_functionality_partial_type", "density_grade",
    "operation_temperature",
)

_FDNEXT_FIELD_SET = frozenset(FDNEXT_FIELD_KEYS)


def is_fdnext_field_key(key: str) -> bool:
    """字段名是否在 fdnext 已注册字段白名单内（对应上游 isFdnextFieldKey）。"""
    return key in _FDNEXT_FIELD_SET
