# -*- coding: utf-8 -*-
"""
src/fdnext/part_compiler.py
---------------------------
料号规则「逐步解释器」 —— 忠实移植上游 src/decodepack/part-compiler.ts。

规则(PartDecodeSpec)是纯 JSON 数据：
    { id, priority?, normalize?, match: {kind:"prefix",value}|{kind:"regex",value,flags?},
      set?: {...}, tokenDecoder?: { stripPrefixes?, tables?, steps:[...], assign:{...} } }

本模块负责：
    1) normalize()            —— NormalizeStep 顺序应用
    2) compile_expression()   —— DecodeExpr($var/$tpl/$path/字面量) 运行时求值
    3) run_program()          —— 依次执行 stripPrefixes + steps，最后统一做 assign
    4) compile decoder       —— 每个 spec → { id, priority, dispatch_prefixes, match, decode }
    5) decode_part_by_spec() —— tokenDecoder 存在则跑程序，否则只跑顶层 set

语义与上游关键对齐点：
    - assign 顺序：ordered_assign_entries —— "fields" 永远最后；
    - assignPath：叶子双方都是普通对象时做浅合并；
    - 缺省补 device.partNumber（取 normalized）；
    - 不做 projection 剪枝（等价于上游不带 targets 的完整解码路径）。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# 与上游 /\{\{([a-zA-Z0-9_.]+)\}\}/g 一致
_TPL_RE = re.compile(r"\{\{([a-zA-Z0-9_.]+)\}\}")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def clone_json(value: Any) -> Any:
    """结构化克隆（JSON 值）。与上游 cloneJson 等价。"""
    if isinstance(value, list):
        return [clone_json(item) for item in value]
    if isinstance(value, dict):
        return {k: clone_json(v) for k, v in value.items()}
    return value


def js_number(value: Any) -> float:
    """复刻 JS 的 Number(x) 强转语义（用于 mul/dieDensity）。"""
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------


def normalize(input_str: str, steps: Optional[List[Any]] = None) -> str:
    value = input_str
    for step in steps or []:
        if step == "trim":
            value = value.strip()
        elif step == "uppercase":
            value = value.upper()
        elif isinstance(step, dict) and isinstance(step.get("remove"), list):
            for token in step["remove"]:
                value = value.replace(token, "")
    return value


def compile_normalizer(steps: Optional[List[Any]] = None) -> Callable[[str], str]:
    if not steps:
        return lambda value: value
    return lambda value: normalize(value, steps)


# ---------------------------------------------------------------------------
# 路径 / 表达式
# ---------------------------------------------------------------------------


def compile_path(path: Any) -> List[str]:
    """'a.b' -> ['a','b']；数组原样拷贝。"""
    if isinstance(path, str):
        return path.split(".")
    return list(path)


def read_path(context: Dict[str, Any], path: Any) -> Any:
    parts = compile_path(path)
    current: Any = context
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)  # 等价 JS：current = obj[part]（undefined -> None）
    return current


def _is_var(value: Any) -> bool:
    return isinstance(value, dict) and "$var" in value


def _is_tpl(value: Any) -> bool:
    return isinstance(value, dict) and "$tpl" in value


def _is_path(value: Any) -> bool:
    return isinstance(value, dict) and "$path" in value


def compile_template(template: str) -> Callable[[Dict[str, Any]], str]:
    parts: List[Any] = []
    offset = 0
    for match in _TPL_RE.finditer(template):
        if match.start() > offset:
            parts.append(template[offset:match.start()])
        parts.append(compile_path(match.group(1)))
        offset = match.end()
    if offset < len(template):
        parts.append(template[offset:])
    if not parts:
        return lambda context: template

    def evaluate(context: Dict[str, Any]) -> str:
        out = []
        for part in parts:
            if isinstance(part, str):
                out.append(part)
            else:
                value = read_path(context, part)
                out.append("" if value is None else str(value))
        return "".join(out)

    return evaluate


def compile_expression(expr: Any) -> Callable[[Dict[str, Any]], Any]:
    if _is_var(expr):
        variable = expr["$var"]
        return lambda context: context.get(variable)
    if _is_tpl(expr):
        return compile_template(expr["$tpl"])
    if _is_path(expr):
        path = compile_path(expr["$path"])
        return lambda context: read_path(context, path)
    if isinstance(expr, list):
        items = [compile_expression(item) for item in expr]
        return lambda context: [evaluate(context) for evaluate in items]
    if isinstance(expr, dict):
        entries = [(k, compile_expression(v)) for k, v in expr.items()]
        return lambda context: {k: evaluate(context) for k, evaluate in entries}
    return lambda context: expr


# ---------------------------------------------------------------------------
# 路径赋值（assignPath 语义）
# ---------------------------------------------------------------------------


def assign_path(out: Dict[str, Any], path: List[str], value: Any) -> None:
    """与上游 assignPath 一致：value 为 None(undefined) 时跳过；
    叶子双方都是普通对象时浅合并；中间节点非对象则重建为 {}。"""
    if value is None:
        return
    current: Any = out
    for part in path[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            current[part] = {}
        current = current[part]
    leaf = path[-1]
    existing = current.get(leaf)
    if is_plain_object(existing) and is_plain_object(value):
        merged = dict(existing)
        merged.update(value)
        current[leaf] = merged
    else:
        current[leaf] = value


# ---------------------------------------------------------------------------
# Assign 顺序：orderedAssignEntries（fields 最后）
# ---------------------------------------------------------------------------


def ordered_assign_entries(assign: Dict[str, Any]) -> List[Tuple[str, Any, int]]:
    entries = [(k, v, i) for i, (k, v) in enumerate(assign.items())]
    return [e for e in entries if e[0] != "fields"] + [e for e in entries if e[0] == "fields"]


def _compile_assignments(entries: List[Tuple[str, Any, int]]) -> List[Tuple[str, List[str], Callable[[Dict[str, Any]], Any], int]]:
    return [
        (key, compile_path(key), compile_expression(expr), index)
        for key, expr, index in entries
    ]


# ---------------------------------------------------------------------------
# takeLongest 匹配器（compileStartMatcher + optional-dash + scope 分组）
# ---------------------------------------------------------------------------


def _start_matcher(entries: List[Tuple[str, str, Any]]) -> Callable[[str], Dict[str, Any]]:
    """entries: [(token, key, table_value)]，先按 token 长度降序试前缀；
    全部失败后对含 '-' 的 token 用 optional-dash 规则再试。"""
    sorted_entries = sorted(entries, key=lambda e: len(e[0]), reverse=True)
    optional_dash = [e for e in sorted_entries if "-" in e[0]]

    def match(value: str) -> Dict[str, Any]:
        for token, key, table_value in sorted_entries:
            if value.startswith(token):
                return {"matched": True, "rest": value[len(token):], "key": key, "value": table_value}
        for token, key, table_value in optional_dash:
            consumed = _match_optional_dash_prefix(value, token)
            if consumed is not None:
                return {"matched": True, "rest": value[consumed:], "key": key, "value": table_value}
        return {"matched": False, "rest": value}

    return match


def _match_optional_dash_prefix(value: str, key: str) -> Optional[int]:
    if "-" not in key:
        return None
    value_index = 0
    for char in key:
        if char == "-":
            if value_index < len(value) and value[value_index] == "-":
                value_index += 1
            continue
        if value_index >= len(value) or value[value_index] != char:
            return None
        value_index += 1
    return value_index


def _take_longest_matcher(table: Dict[str, Any], scoped: bool, separator: str = ":") -> Callable[[str, Any], Dict[str, Any]]:
    entries = [(k, k, v) for k, v in table.items()]
    base = _start_matcher(entries)
    if not scoped:
        return lambda value, scope=None: base(value)

    grouped: Dict[str, List[Tuple[str, str, Any]]] = {}
    for key, value in table.items():
        sep_index = key.find(separator)
        while sep_index >= 1:
            scope = key[:sep_index]
            token = key[sep_index + len(separator):]
            grouped.setdefault(scope, []).append((token, key, value))
            sep_index = key.find(separator, sep_index + len(separator))
    scoped_matchers = {scope: _start_matcher(entries) for scope, entries in grouped.items()}

    def match(value: str, scope: Any = None) -> Dict[str, Any]:
        scope_value = "" if scope is None else str(scope)
        matcher = scoped_matchers.get(scope_value)
        scoped_result = matcher(value) if matcher else None
        if scoped_result and scoped_result["matched"]:
            return scoped_result
        return base(value)

    return match


# ---------------------------------------------------------------------------
# 程序运行时
# ---------------------------------------------------------------------------


class ProgramRuntime:
    """一个 tokenDecoder 的编译结果：编译好的正则/模板/表达式/查表/匹配器。"""

    def __init__(self, program: Dict[str, Any], shared_tables: Optional[Dict[str, Any]] = None):
        # 上游把 sharedTables 与 decoder.tables 合并后统一规范化（本表优先）
        merged_tables: Dict[str, Any] = {}
        for name, table in (shared_tables or {}).items():
            merged_tables[name] = table
        for name, table in (program.get("tables") or {}).items():
            merged_tables[name] = table
        from .table import normalize_decode_tables
        normalized = normalize_decode_tables(merged_tables)
        self.tables: Dict[str, Dict[str, Any]] = {}
        for name, table in normalized.items():
            self.tables[name] = {k: clone_json(v) for k, v in table.items()}

        self.program = program
        self.patterns: Dict[int, re.Pattern] = {}
        self.templates: Dict[int, Callable[[Dict[str, Any]], str]] = {}
        self.set_expressions: Dict[int, Callable[[Dict[str, Any]], Any]] = {}
        self.longest_matchers: Dict[int, Callable[[str, Any], Dict[str, Any]]] = {}

        steps = program.get("steps") or []
        for index, step in enumerate(steps):
            op = step.get("op")
            if op == "takeRegex":
                self.patterns[index] = _compile_js_regex(step["pattern"])
            elif op == "tpl":
                self.templates[index] = compile_template(step["template"])
            elif op == "set":
                self.set_expressions[index] = compile_expression(step["value"])
            elif op == "takeLongest":
                self.longest_matchers[index] = _take_longest_matcher(
                    self.tables.get(step.get("table"), {}),
                    bool(step.get("scope")),
                    step.get("scopeSeparator", ":"),
                )

        # assign 顺序：fields 最后
        self.assignments = _compile_assignments(ordered_assign_entries(program.get("assign") or {}))
        self.strip_prefixes: List[str] = list(program.get("stripPrefixes") or [])


def run_program(part_number: str, runtime: ProgramRuntime) -> Dict[str, Any]:
    """执行 stripPrefixes → steps → assign。不做 projection（等价上游无 targets 全量解码）。"""
    context: Dict[str, Any] = {"partNumber": part_number, "rest": part_number}

    for prefix in runtime.strip_prefixes:
        rest = str(context["rest"] or "")
        if rest.startswith(prefix):
            context["rest"] = rest[len(prefix):]

    steps = runtime.program.get("steps") or []
    for index, step in enumerate(steps):
        op = step.get("op")
        rest = str(context["rest"] or "")

        if op == "take":
            length = step["len"]
            if length > len(rest):
                context[step["to"]] = ""
            else:
                context[step["to"]] = rest[:length]
                context["rest"] = rest[length:]
            continue

        if op == "takeRegex":
            match = runtime.patterns[index].search(rest)
            matched = bool(match) and match.start() == 0
            to = step.get("to")
            groups = step.get("groups") or {}
            if matched:
                if to:
                    context[to] = match.group(0)
                context["rest"] = rest[len(match.group(0)):]
                for group_to, group in groups.items():
                    context[group_to] = _group_value(match, group)
            else:
                if to:
                    context[to] = clone_json(step.get("default", ""))
                for group_to in groups:
                    context[group_to] = ""
            continue

        if op == "stripIfPrefix":
            cond = step.get("if")
            if cond is not None and not context.get(cond):
                if step.get("to"):
                    context[step["to"]] = False
                continue
            matched = rest.startswith(step["prefix"])
            if matched:
                context["rest"] = rest[len(step["prefix"]):]
            if step.get("to"):
                context[step["to"]] = matched
            continue

        if op == "markLookupPartNumber":
            consumed_length = max(0, len(part_number) - len(rest))
            context[step["to"]] = part_number[:consumed_length]
            continue

        if op == "tpl":
            context[step["to"]] = runtime.templates[index](context)
            continue

        if op == "fallback":
            primary = context.get(step["primary"])
            secondary = context.get(step["secondary"])
            if primary is None:
                context[step["to"]] = secondary
            elif isinstance(primary, str):
                context[step["to"]] = primary if len(primary) > 0 else secondary
            else:
                context[step["to"]] = primary
            continue

        if op == "dieDensity":
            density = js_number(context.get(step["density"]))
            die_count = js_number(context.get(step["dieCount"]))
            matched = math.isfinite(density) and math.isfinite(die_count) and die_count > 0
            context[step["to"]] = _format_die_density_mbit(density / die_count) if matched else clone_json(step.get("default", ""))
            continue

        if op == "set":
            context[step["to"]] = runtime.set_expressions[index](context)
            continue

        if op == "mul":
            a = js_number(context.get(step["a"]))
            b = js_number(context.get(step["b"]))
            if math.isfinite(a) and math.isfinite(b):
                context[step["to"]] = a * b
            else:
                context[step["to"]] = step.get("default", 0)
            continue

        if op == "merge":
            into = context.get(step["into"])
            from_value = context.get(step["from"])
            if is_plain_object(into) and is_plain_object(from_value):
                merged = dict(into)
                merged.update(from_value)
                context[step["into"]] = merged
            continue

        if op == "omit":
            source = context.get(step["from"])
            target = step.get("to", step["from"])
            if is_plain_object(source):
                next_value = dict(source)
                for key in step["keys"]:
                    next_value.pop(key, None)
                context[target] = next_value
            continue

        if op == "notEmpty":
            value = context.get(step["from"])
            text = "" if value is None else str(value)
            context[step["to"]] = len(text) > 0
            continue

        if op == "mergeIf":
            if not context.get(step["if"]):
                continue
            into = context.get(step["into"])
            from_value = context.get(step["from"])
            if is_plain_object(into) and is_plain_object(from_value):
                merged = dict(into)
                merged.update(from_value)
                context[step["into"]] = merged
            continue

        if op == "takeLongest":
            result = runtime.longest_matchers[index](rest, context.get(step.get("scope")) if step.get("scope") else None)
            if result["matched"]:
                context[step["to"]] = clone_json(result["value"])
                if step.get("keyTo"):
                    context[step["keyTo"]] = result["key"]
                context["rest"] = result["rest"]
            else:
                context[step["to"]] = clone_json(step.get("default"))
            continue

        # op == "map"
        table = runtime.tables.get(step.get("table"), {})
        source = str(context.get(step["from"]) or "")
        matched = source in table
        if matched:
            context[step["to"]] = clone_json(table[source])
            if step.get("keyTo"):
                context[step["keyTo"]] = source
        else:
            context[step["to"]] = clone_json(step.get("default"))
        continue

    # assign（fields 最后）
    out: Dict[str, Any] = {}
    for key, path, evaluate, _index in runtime.assignments:
        value = evaluate(context)
        assign_path(out, path, value)

    _ensure_device_part_number(out, part_number)
    return out


def _group_value(match: re.Match, group: Any) -> str:
    """提取 (数字/命名) 捕获组；等价 JS match[group] ?? "" / match.groups?.[name] ?? ""。"""
    if isinstance(group, int):
        try:
            value = match.group(group)
        except IndexError:
            value = None
        return "" if value is None else value
    value = match.groupdict().get(group)
    return "" if value is None else value


def _js_num_str(value: float) -> str:
    """JS Number 的默认字符串形态（整数值不带小数位）。"""
    if value == int(value):
        return str(int(value))
    return repr(value)


def _format_die_density_mbit(value: float) -> str:
    """复刻上游 formatDieDensityMbit。"""
    if not math.isfinite(value) or value <= 0:
        return ""
    if value >= 1024 * 1024 and value % (1024 * 1024) == 0:
        return "%dTb" % int(value // (1024 * 1024))
    if value >= 1024 * 1024:
        return "%sTb" % _trim_fixed2(value / (1024 * 1024))
    if value % 1024 == 0:
        return "%sGb" % _js_num_str(value / 1024)
    return "%sMb" % _js_num_str(value)


def _trim_fixed2(x: float) -> str:
    """Number((x).toFixed(2)) 的字符串形态（去尾零）。"""
    s = "%.2f" % x
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _ensure_device_part_number(out: Dict[str, Any], part_number: str) -> None:
    current = read_path(out, ["device", "partNumber"])
    if not current:
        assign_path(out, ["device", "partNumber"], part_number)


# ---------------------------------------------------------------------------
# Match / dispatch prefix / spec 编译
# ---------------------------------------------------------------------------

_JS_NAMED_GROUP_RE = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")
_JS_NAMED_BACKREF_RE = re.compile(r"\\k<([A-Za-z_][A-Za-z0-9_]*)>")


def _js_regex_to_py(pattern: str) -> str:
    """把 JS 专属正则写法转成 Python re 可用的等价形式。

    目前上游规则只用到命名组：(?<name>…) -> (?P<name>…)。
    其余常见 JS 语法(前瞻/后瞻/非捕获组) Python re 原生支持。
    """
    pattern = _JS_NAMED_GROUP_RE.sub(r"(?P<\1>", pattern)
    pattern = _JS_NAMED_BACKREF_RE.sub(r"\\g<\1>", pattern)
    return pattern


_FLAGS_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def _compile_js_regex(pattern: str, flags: Optional[str] = None) -> re.Pattern:
    py_flags = 0
    for flag in flags or "":
        if flag in _FLAGS_MAP:
            py_flags |= _FLAGS_MAP[flag]
    return re.compile(_js_regex_to_py(pattern), py_flags)


def check_match(normalized: str, match: Dict[str, Any]) -> bool:
    """复刻上游 checkMatch。"""
    if match.get("kind") == "prefix":
        return normalized.startswith(match["value"])
    return bool(_compile_js_regex(match["value"], match.get("flags")).search(normalized))


def _has_top_level_alternation(pattern: str) -> bool:
    depth = 0
    in_class = False
    index = 1
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 1
        elif char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif in_class:
            pass
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "|" and depth == 0:
            return True
        index += 1
    return False


def literal_regex_prefix(pattern: str) -> Optional[str]:
    """从正则里提取字面前缀（与上游 literalRegexPrefix 逐字符一致）。"""
    if not pattern.startswith("^") or _has_top_level_alternation(pattern):
        return None
    prefix: List[str] = []
    index = 1
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            break
        if char in "?*{":
            if prefix:
                prefix.pop()
            break
        if char == "+":
            break
        if char in ".$()[]|":
            break
        prefix.append(char)
        index += 1
    return "".join(prefix) if prefix else None


def dispatch_prefixes_for_rule(rule: Dict[str, Any], normalize_input: Callable[[str], str]) -> List[str]:
    match = rule.get("match") or {}
    raw_prefix = match.get("value") if match.get("kind") == "prefix" else literal_regex_prefix(match.get("value", ""))
    if not raw_prefix:
        return []
    normalized = normalize_input(raw_prefix)
    if re.fullmatch(r"[0-9A-Z:-]+", normalized, re.IGNORECASE):
        return [normalized.upper()]
    return []


def decode_part_by_spec(
    rule: Dict[str, Any],
    normalized: str,
    shared_tables: Optional[Dict[str, Any]] = None,
    rule_runtime: Optional[Any] = None,
) -> Dict[str, Any]:
    """tokenDecoder 存在 -> 跑程序；否则只跑顶层 set（插入序）。"""
    runtime = rule_runtime if rule_runtime is not None else create_part_rule_runtime(rule, shared_tables)
    program = runtime[0]
    if program is not None:
        return run_program(normalized, program)
    context: Dict[str, Any] = {"partNumber": normalized, "rest": normalized}
    out: Dict[str, Any] = {}
    for key, path, evaluate, _index in runtime[1]:
        assign_path(out, path, evaluate(context))
    _ensure_device_part_number(out, normalized)
    return out


def create_part_rule_runtime(
    rule: Dict[str, Any],
    shared_tables: Optional[Dict[str, Any]] = None,
):
    token_decoder = rule.get("tokenDecoder")
    program = ProgramRuntime(token_decoder, shared_tables) if isinstance(token_decoder, dict) else None
    set_assignments = _compile_assignments(
        [(k, v, i) for i, (k, v) in enumerate((rule.get("set") or {}).items())]
    )
    return program, set_assignments


class PartDecoder:
    """单个已编译料号解码器（对应上游 compilePartDecodeSpecs 的产物）。"""

    def __init__(self, rule: Dict[str, Any], shared_tables: Optional[Dict[str, Any]] = None):
        self.id: str = rule.get("id", "")
        self.priority: Optional[int] = rule.get("priority")
        self.rule = rule
        self._runtime = create_part_rule_runtime(rule, shared_tables)
        self._normalize_input = compile_normalizer(rule.get("normalize"))
        self._match_prefix = rule["match"]["value"] if rule.get("match", {}).get("kind") == "prefix" else None
        self._match_pattern = (
            _compile_js_regex(rule["match"]["value"], rule["match"].get("flags"))
            if self._match_prefix is None
            else None
        )
        self.dispatch_prefixes: List[str] = dispatch_prefixes_for_rule(rule, self._normalize_input)

    def matches_normalized(self, normalized: str) -> bool:
        if self._match_prefix is not None:
            return normalized.startswith(self._match_prefix)
        return bool(self._match_pattern.search(normalized))

    def match(self, part_number: str) -> Optional[Dict[str, str]]:
        """返回 {decoder_id, input, normalized} 或 None。"""
        normalized = self._normalize_input(part_number)
        if not self.matches_normalized(normalized):
            return None
        return {"decoder_id": self.id, "input": part_number, "normalized": normalized}

    def decode(self, matched: Dict[str, str]) -> Dict[str, Any]:
        if matched.get("decoder_id") != self.id:
            raise TypeError(
                "Part-number match for %s cannot be decoded by %s" % (matched.get("decoder_id"), self.id)
            )
        return decode_part_by_spec(self.rule, matched["normalized"], None, self._runtime)

    def project(self, matched: Dict[str, str], targets: List[str]) -> Dict[str, Any]:
        return self.decode(matched)  # 不做 projection 剪枝：语义等于上游全量解码


def compile_part_decode_specs(
    rules: List[Dict[str, Any]],
    shared_tables: Optional[Dict[str, Any]] = None,
) -> List[PartDecoder]:
    return [PartDecoder(rule, shared_tables) for rule in rules]
