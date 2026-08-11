"""RG Manager v0.9.28 BOM delete tab.

Adds a third `BOM 삭제` tab to the existing 생산·BOM page.
Deleting a BOM changes only the current recipe in bom_items.
Past inventory transactions and production_orders remain untouched.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from typing import Any

import pandas as pd


_PATCH_MARKER = "# _rg_bom_delete_v0928"


def _conn(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _display_code(item_code: Any, option_id: Any = None) -> str:
    code = str(item_code or "").strip()
    oid = str(option_id or "").strip()
    m = re.fullmatch(r"CP-(\d+)", code, flags=re.IGNORECASE)
    if m:
        return oid or m.group(1)
    return code or oid


def _ensure_log_table(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_change_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            bom_id INTEGER,
            parent_product_id INTEGER NOT NULL,
            component_product_id INTEGER NOT NULL,
            qty_per REAL NOT NULL,
            changed_at TEXT NOT NULL,
            note TEXT
        )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_bom_change_log_parent ON bom_change_log(parent_product_id, changed_at)"
    )


def _bom_rows(core_module, db_path=None):
    with _conn(core_module, db_path) as con:
        rows = con.execute(
            """SELECT
                   b.id AS bom_id,
                   b.parent_product_id,
                   p.name AS parent_name,
                   p.item_code AS parent_code,
                   p.option_id AS parent_option_id,
                   p.active AS parent_active,
                   b.component_product_id,
                   c.name AS component_name,
                   c.item_code AS component_code,
                   c.option_id AS component_option_id,
                   c.active AS component_active,
                   b.qty_per,
                   c.unit_cost AS component_cost
               FROM bom_items b
               JOIN products p ON p.id=b.parent_product_id
               JOIN products c ON c.id=b.component_product_id
               ORDER BY p.name, p.item_code, c.name, c.item_code"""
        ).fetchall()
    return [dict(r) for r in rows]


def _parent_label(row) -> str:
    label = f"{row['parent_name']} [{_display_code(row['parent_code'], row['parent_option_id'])}]"
    if int(row.get("parent_active") or 0) != 1:
        label += " · 보관품목"
    return label


def _component_label(row) -> str:
    label = f"{row['component_name']} [{_display_code(row['component_code'], row['component_option_id'])}]"
    if int(row.get("component_active") or 0) != 1:
        label += " · 보관품목"
    return label


def _delete_one(core_module, bom_id: int, db_path=None) -> dict:
    with _conn(core_module, db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT id,parent_product_id,component_product_id,qty_per FROM bom_items WHERE id=?",
            (int(bom_id),),
        ).fetchone()
        if not row:
            raise ValueError("이미 삭제되었거나 존재하지 않는 BOM 구성입니다.")
        _ensure_log_table(con)
        con.execute(
            """INSERT INTO bom_change_log
               (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "DELETE_COMPONENT",
                int(row["id"]),
                int(row["parent_product_id"]),
                int(row["component_product_id"]),
                float(row["qty_per"] or 0),
                core_module.now_iso(),
                "BOM 삭제 탭에서 구성품 1개 삭제",
            ),
        )
        con.execute("DELETE FROM bom_items WHERE id=?", (int(bom_id),))
        return {"parent_product_id": int(row["parent_product_id"]), "deleted": 1}


def _delete_all(core_module, parent_product_id: int, db_path=None) -> dict:
    with _conn(core_module, db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT id,parent_product_id,component_product_id,qty_per FROM bom_items WHERE parent_product_id=? ORDER BY id",
            (int(parent_product_id),),
        ).fetchall()
        if not rows:
            raise ValueError("이미 삭제되었거나 등록된 BOM이 없습니다.")
        _ensure_log_table(con)
        now = core_module.now_iso()
        for row in rows:
            con.execute(
                """INSERT INTO bom_change_log
                   (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    "DELETE_ALL",
                    int(row["id"]),
                    int(row["parent_product_id"]),
                    int(row["component_product_id"]),
                    float(row["qty_per"] or 0),
                    now,
                    "BOM 삭제 탭에서 완제품 BOM 전체 삭제",
                ),
            )
        con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (int(parent_product_id),))
        return {"parent_product_id": int(parent_product_id), "deleted": len(rows)}


def render_delete_ui(st_obj, core_module, db_path=None) -> None:
    st_obj.markdown("### BOM 삭제")
    st_obj.caption(
        "앞으로 생산할 때 사용할 현재 BOM만 삭제합니다. "
        "과거 생산수량·생산원가·재고 차감 이력은 그대로 유지됩니다."
    )

    rows = _bom_rows(core_module, db_path)
    if not rows:
        st_obj.info("현재 등록된 BOM이 없습니다.")
        return

    parent_ids = []
    parent_labels = {}
    for row in rows:
        pid = int(row["parent_product_id"])
        if pid not in parent_labels:
            parent_ids.append(pid)
            parent_labels[pid] = _parent_label(row)

    parent_id = st_obj.selectbox(
        "삭제할 완제품",
        parent_ids,
        format_func=lambda pid: parent_labels.get(int(pid), str(pid)),
        key="bom_delete_parent_v0928",
    )
    selected_rows = [r for r in rows if int(r["parent_product_id"]) == int(parent_id)]

    show = pd.DataFrame(
        [
            {
                "구성품": _component_label(r),
                "소요량": float(r["qty_per"] or 0),
                "구성품원가": float(r["component_cost"] or 0),
                "완제품 1개당 원가": float(r["qty_per"] or 0) * float(r["component_cost"] or 0),
            }
            for r in selected_rows
        ]
    )
    st_obj.dataframe(show, use_container_width=True, hide_index=True)

    mode = st_obj.radio(
        "삭제 범위",
        ["구성품 1개만 삭제", "이 완제품의 BOM 전체 삭제"],
        horizontal=True,
        key=f"bom_delete_mode_v0928_{int(parent_id)}",
    )

    target_bom_id = None
    if mode == "구성품 1개만 삭제":
        by_id = {int(r["bom_id"]): r for r in selected_rows}
        target_bom_id = st_obj.selectbox(
            "삭제할 구성품",
            list(by_id.keys()),
            format_func=lambda bid: _component_label(by_id[int(bid)]),
            key=f"bom_delete_component_v0928_{int(parent_id)}",
        )
        st_obj.warning("선택한 구성품 연결 1개만 BOM에서 삭제됩니다.")
    else:
        st_obj.warning(
            f"{parent_labels[int(parent_id)]}의 BOM 구성 {len(selected_rows):,}개를 모두 삭제합니다. "
            "삭제 후 다시 생산하려면 BOM을 새로 등록해야 합니다."
        )

    confirmed = st_obj.checkbox(
        "삭제 내용을 확인했습니다.",
        key=f"bom_delete_confirm_v0928_{int(parent_id)}_{mode}",
    )
    if st_obj.button(
        "BOM 삭제",
        type="primary",
        disabled=not confirmed,
        key=f"bom_delete_button_v0928_{int(parent_id)}_{mode}",
    ):
        try:
            if mode == "구성품 1개만 삭제":
                _delete_one(core_module, int(target_bom_id), db_path)
                st_obj.success("선택한 구성품 연결을 BOM에서 삭제했습니다.")
            else:
                result = _delete_all(core_module, int(parent_id), db_path)
                st_obj.success(f"BOM 구성 {result['deleted']:,}개를 모두 삭제했습니다.")
            try:
                st_obj.rerun()
            except Exception:
                pass
        except Exception as exc:
            st_obj.error(f"BOM 삭제 실패: {exc}")


def _is_st_tabs_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tabs"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
    )


def _tab_labels(call: ast.Call):
    if not call.args:
        return None, None
    labels = call.args[0]
    if not isinstance(labels, (ast.List, ast.Tuple)):
        return None, None
    values = []
    for elt in labels.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return labels, None
        values.append(elt.value)
    return labels, values


def _target_names(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        return [x.id for x in target.elts if isinstance(x, ast.Name)]
    if isinstance(target, ast.Name):
        return [target.id]
    return []


def _patch_tabs_assignment(source: str):
    tree = ast.parse(source)
    found = None
    last_tab_with = None

    def scan_body(body):
        nonlocal found, last_tab_with
        if found is not None:
            return
        for stmt in body:
            if isinstance(stmt, ast.Assign) and _is_st_tabs_call(stmt.value):
                labels_node, labels = _tab_labels(stmt.value)
                if labels and "BOM 구성" in labels and "생산 처리" in labels:
                    names = _target_names(stmt.targets[0]) if stmt.targets else []
                    if len(names) != len(labels):
                        raise RuntimeError("v0.9.28 생산·BOM 탭 변수 구조가 예상과 다릅니다.")
                    tab_names = set(names)
                    candidates = []
                    for x in body:
                        if isinstance(x, ast.With):
                            for item in x.items:
                                if isinstance(item.context_expr, ast.Name) and item.context_expr.id in tab_names:
                                    candidates.append(x)
                    if not candidates:
                        raise RuntimeError("v0.9.28 생산·BOM 탭 본문을 찾지 못했습니다.")
                    found = (stmt, labels_node, labels, names)
                    last_tab_with = max(candidates, key=lambda x: (x.end_lineno or x.lineno, x.end_col_offset or 0))
                    return
            for attr in ("body", "orelse"):
                child = getattr(stmt, attr, None)
                if isinstance(child, list):
                    scan_body(child)
                    if found is not None:
                        return

    scan_body(tree.body)
    if found is None:
        raise RuntimeError("v0.9.28 생산·BOM의 'BOM 구성 / 생산 처리' 탭을 찾지 못했습니다.")
    return found, last_tab_with


def _offset(lines, line_no: int, byte_col: int) -> int:
    line = lines[line_no - 1]
    prefix = line.encode("utf-8")[:byte_col].decode("utf-8")
    return sum(len(x) for x in lines[: line_no - 1]) + len(prefix)


def patch_source(source: str) -> str:
    if _PATCH_MARKER in source:
        return source

    (assign, _labels_node, _labels, _names), last_with = _patch_tabs_assignment(source)

    segment = ast.get_source_segment(source, assign)
    if not segment:
        raise RuntimeError("v0.9.28 생산·BOM 탭 선언 소스를 읽지 못했습니다.")
    cloned = ast.parse(segment).body[0]
    if not isinstance(cloned, ast.Assign):
        raise RuntimeError("v0.9.28 생산·BOM 탭 선언 형식을 해석하지 못했습니다.")
    cloned_target = cloned.targets[0]
    if not isinstance(cloned_target, (ast.Tuple, ast.List)):
        raise RuntimeError("v0.9.28 생산·BOM 탭 변수 형식을 해석하지 못했습니다.")
    cloned_target.elts.append(ast.Name(id="_rg_bom_delete_tab_v0928", ctx=ast.Store()))
    cloned_labels, cloned_values = _tab_labels(cloned.value)
    if cloned_labels is None or cloned_values is None:
        raise RuntimeError("v0.9.28 생산·BOM 탭 이름을 해석하지 못했습니다.")
    cloned_labels.elts.append(ast.Constant(value="BOM 삭제"))
    ast.fix_missing_locations(cloned)
    new_assign = ast.unparse(cloned)

    lines = source.splitlines(keepends=True)
    a_start = _offset(lines, assign.lineno, assign.col_offset)
    a_end = _offset(lines, assign.end_lineno, assign.end_col_offset)
    w_end = _offset(lines, last_with.end_lineno, last_with.end_col_offset)
    indent = " " * int(last_with.col_offset)
    inner = indent + "    "
    insertion = (
        "\n\n"
        + indent
        + "with _rg_bom_delete_tab_v0928:\n"
        + inner
        + '__import__("bom_delete_v0928").render_delete_ui(st, core)\n'
    )

    if w_end > a_end:
        source = source[:w_end] + insertion + source[w_end:]
        source = source[:a_start] + new_assign + source[a_end:]
    else:
        source = source[:a_start] + new_assign + source[a_end:]
        delta = len(new_assign) - (a_end - a_start)
        w_end += delta
        source = source[:w_end] + insertion + source[w_end:]

    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.28 BOM 삭제 탭 적용 후 소스 문법 오류: {exc}") from exc

    return _PATCH_MARKER + "\n" + source
