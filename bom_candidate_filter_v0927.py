"""RG Manager v0.9.27 Production/BOM candidate filtering.

Rules:
- Finished-product selectors show active `finished` products only.
- Component selectors show active `raw` (own-warehouse managed) products only.
- Archived products are excluded.
- `CP-<option_id>` is hidden in selector display text; DB keys are unchanged.
- add_bom() is guarded so stale UI/session values cannot save invalid combinations.
"""
from __future__ import annotations

import ast
import numbers
import re
import sqlite3
from typing import Any


_MARKER = "_rg_bom_candidate_filter_v0927"


def _conn(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _load_products(core_module, db_path=None):
    core_module.init_db(db_path or core_module.DEFAULT_DB)
    with _conn(core_module, db_path) as con:
        rows = con.execute(
            """SELECT id,item_code,option_id,name,item_type,active
               FROM products
               ORDER BY name,item_code"""
        ).fetchall()

    by_id = {}
    by_code = {}
    by_option = {}
    by_name = {}
    for row in rows:
        d = dict(row)
        pid = int(d["id"])
        by_id[pid] = d

        code = str(d.get("item_code") or "").strip()
        if code:
            by_code[code.casefold()] = pid
            m = re.fullmatch(r"CP-(\d+)", code, flags=re.IGNORECASE)
            if m:
                by_code[m.group(1).casefold()] = pid

        option_id = str(d.get("option_id") or "").strip()
        if option_id:
            by_option[option_id.casefold()] = pid
            by_code[f"CP-{option_id}".casefold()] = pid

        name = str(d.get("name") or "").strip()
        if name:
            by_name.setdefault(name.casefold(), []).append(pid)

    return {
        "rows": rows,
        "by_id": by_id,
        "by_code": by_code,
        "by_option": by_option,
        "by_name": by_name,
    }


def _extract_obj_value(value: Any, key: str):
    if isinstance(value, dict):
        return value.get(key)
    try:
        return getattr(value, key)
    except Exception:
        return None


def _candidate_pid(value: Any, maps) -> int | None:
    if value is None:
        return None

    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        pid = int(value)
        if pid in maps["by_id"]:
            return pid

    for key in ("id", "product_id"):
        v = _extract_obj_value(value, key)
        if isinstance(v, numbers.Integral) and not isinstance(v, bool):
            pid = int(v)
            if pid in maps["by_id"]:
                return pid
        if isinstance(v, str) and v.strip().isdigit():
            pid = int(v.strip())
            if pid in maps["by_id"]:
                return pid

    for key, mapping in (
        ("item_code", maps["by_code"]),
        ("option_id", maps["by_option"]),
    ):
        v = _extract_obj_value(value, key)
        if v is not None:
            s = str(v).strip().casefold()
            if s in mapping:
                return int(mapping[s])

    for key in ("name", "product_name"):
        v = _extract_obj_value(value, key)
        if v is not None:
            ids = maps["by_name"].get(str(v).strip().casefold(), [])
            if len(ids) == 1:
                return int(ids[0])

    if isinstance(value, (tuple, list)):
        for part in value:
            pid = _candidate_pid(part, maps)
            if pid is not None:
                return pid

    s = str(value or "").strip()
    if not s:
        return None

    # Most current selector labels are "상품명 [품목코드]".
    bracket_codes = re.findall(r"\[([^\[\]]+)\]", s)
    for code in reversed(bracket_codes):
        key = code.strip().casefold()
        if key in maps["by_code"]:
            return int(maps["by_code"][key])
        if key in maps["by_option"]:
            return int(maps["by_option"][key])
        if re.fullmatch(r"CP-\d+", code.strip(), flags=re.IGNORECASE):
            digits = code.strip()[3:].casefold()
            if digits in maps["by_option"]:
                return int(maps["by_option"][digits])

    key = s.casefold()
    if key in maps["by_code"]:
        return int(maps["by_code"][key])
    if key in maps["by_option"]:
        return int(maps["by_option"][key])

    if s.isdigit():
        pid = int(s)
        if pid in maps["by_id"]:
            return pid

    ids = maps["by_name"].get(key, [])
    if len(ids) == 1:
        return int(ids[0])
    return None


def _allowed(row, kind: str) -> bool:
    if row is None or int(row.get("active") or 0) != 1:
        return False
    item_type = str(row.get("item_type") or "").strip().lower()
    if kind == "finished":
        return item_type == "finished"
    if kind == "component":
        return item_type == "raw"
    raise ValueError(f"알 수 없는 BOM 후보 구분입니다: {kind}")


def filter_options(options, kind: str, core_module):
    """Filter a selector's original values without changing the selected value type."""
    try:
        values = list(options)
    except Exception:
        return options

    maps = _load_products(core_module)
    out = []
    for value in values:
        pid = _candidate_pid(value, maps)
        if pid is None:
            # Unknown values are intentionally excluded from BOM selectors.
            continue
        if _allowed(maps["by_id"].get(pid), kind):
            out.append(value)
    return out


def _clean_display_text(text: Any) -> str:
    # Display only: preserve DB item_code and option ID relationships.
    return re.sub(r"(?<![A-Za-z0-9])CP-(\d+)", r"\1", str(text))


def clean_format_func(original=None):
    """Wrap Streamlit format_func while hiding the legacy CP- display prefix."""
    def _fmt(value):
        try:
            text = original(value) if callable(original) else value
        except Exception:
            text = value
        return _clean_display_text(text)
    return _fmt


def _fetch_product(core_module, product_id: int, db_path=None):
    with _conn(core_module, db_path) as con:
        row = con.execute(
            "SELECT id,item_code,option_id,name,item_type,active FROM products WHERE id=?",
            (int(product_id),),
        ).fetchone()
    return dict(row) if row else None


def apply(core_module):
    """Protect BOM writes even if a stale UI somehow submits an invalid pair."""
    if getattr(core_module, _MARKER, False):
        return core_module

    original = core_module.add_bom

    def guarded_add_bom(parent_product_id: int, component_product_id: int, qty_per: float, db_path=None):
        parent = _fetch_product(core_module, parent_product_id, db_path)
        component = _fetch_product(core_module, component_product_id, db_path)

        if not parent:
            raise ValueError("완제품 품목을 찾지 못했습니다.")
        if int(parent.get("active") or 0) != 1:
            raise ValueError("삭제·보관된 완제품은 BOM에 사용할 수 없습니다.")
        if str(parent.get("item_type") or "").lower() != "finished":
            raise ValueError("BOM의 완제품은 품목관리의 완제품(쿠팡RG 판매상품)만 선택할 수 있습니다.")

        if not component:
            raise ValueError("구성품 품목을 찾지 못했습니다.")
        if int(component.get("active") or 0) != 1:
            raise ValueError("삭제·보관된 구성품은 BOM에 사용할 수 없습니다.")
        if str(component.get("item_type") or "").lower() != "raw":
            raise ValueError("BOM의 구성품은 품목관리의 자체창고 품목만 선택할 수 있습니다.")

        if db_path is None:
            return original(parent_product_id, component_product_id, qty_per)
        return original(parent_product_id, component_product_id, qty_per, db_path=db_path)

    core_module.add_bom = guarded_add_bom
    setattr(core_module, _MARKER, True)
    return core_module


def _selectbox_label_and_options(call: ast.Call):
    label = None
    options_node = None
    options_location = None

    if call.args:
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            label = first.value
    for kw in call.keywords:
        if kw.arg == "label" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            label = kw.value.value

    if len(call.args) >= 2:
        options_node = call.args[1]
        options_location = ("arg", 1)
    else:
        for i, kw in enumerate(call.keywords):
            if kw.arg == "options":
                options_node = kw.value
                options_location = ("kw", i)
                break
    return label, options_node, options_location


def _patch_selectbox_call(call: ast.Call, kind: str):
    _label, options_node, location = _selectbox_label_and_options(call)
    if options_node is None or location is None:
        return None

    wrapped_options = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="bom_candidate_filter_v0927", ctx=ast.Load()),
            attr="filter_options",
            ctx=ast.Load(),
        ),
        args=[
            options_node,
            ast.Constant(value=kind),
            ast.Name(id="core", ctx=ast.Load()),
        ],
        keywords=[],
    )

    if location[0] == "arg":
        call.args[location[1]] = wrapped_options
    else:
        call.keywords[location[1]].value = wrapped_options

    fmt_kw = None
    for kw in call.keywords:
        if kw.arg == "format_func":
            fmt_kw = kw
            break

    wrapper_func = ast.Attribute(
        value=ast.Name(id="bom_candidate_filter_v0927", ctx=ast.Load()),
        attr="clean_format_func",
        ctx=ast.Load(),
    )
    if fmt_kw is None:
        call.keywords.append(
            ast.keyword(
                arg="format_func",
                value=ast.Call(func=wrapper_func, args=[], keywords=[]),
            )
        )
    else:
        fmt_kw.value = ast.Call(func=wrapper_func, args=[fmt_kw.value], keywords=[])

    ast.fix_missing_locations(call)
    return call


def patch_source(source: str) -> str:
    """Patch only the legacy BOM/production selectbox calls, preserving the rest of source text."""
    if "# _rg_bom_candidate_filter_v0927" in source:
        return source

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.27 BOM 후보 필터 적용 전 소스 문법 오류: {exc}") from exc

    targets = []
    counts = {"finished": 0, "component": 0}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "selectbox"):
            continue
        label, _opts, _loc = _selectbox_label_and_options(node)
        if label == "완제품":
            kind = "finished"
        elif label == "구성품":
            kind = "component"
        else:
            continue
        targets.append((node, kind))
        counts[kind] += 1

    if counts["finished"] < 1 or counts["component"] < 1:
        raise RuntimeError(
            "v0.9.27 생산·BOM의 완제품/구성품 선택창을 찾지 못했습니다. "
            "기본 실행 화면 구조를 확인해 주세요."
        )

    # Replace from bottom to top so source offsets stay valid.
    replacements = []
    for node, kind in targets:
        segment = ast.get_source_segment(source, node)
        if not segment:
            raise RuntimeError("v0.9.27 BOM 선택창 소스를 읽지 못했습니다.")
        cloned = ast.parse(segment, mode="eval").body
        patched = _patch_selectbox_call(cloned, kind)
        new_segment = ast.unparse(patched)
        replacements.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_segment))

    lines = source.splitlines(keepends=True)

    def offset(line_no, byte_col):
        # Python AST column offsets are UTF-8 byte offsets, not character offsets.
        # Convert them before slicing Korean source text.
        line = lines[line_no - 1]
        prefix = line.encode("utf-8")[:byte_col].decode("utf-8")
        return sum(len(x) for x in lines[: line_no - 1]) + len(prefix)

    for sl, sc, el, ec, new_segment in sorted(
        replacements,
        key=lambda x: (x[0], x[1]),
        reverse=True,
    ):
        start = offset(sl, sc)
        end = offset(el, ec)
        source = source[:start] + new_segment + source[end:]

    return "# _rg_bom_candidate_filter_v0927\n" + source
