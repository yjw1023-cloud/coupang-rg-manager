"""v0.9.172 consistent latest-number JDS allocation.

The purchase new-item table and the durable create path must show/use the same
code. New JDS codes always start at max(existing numeric JDS)+1; gaps are never
reused.
"""
from __future__ import annotations
import re


def _next_in_connection(con):
    max_no = 0
    used = set()
    for r in con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall():
        code = str(r["item_code"] or "").strip()
        used.add(code.upper())
        m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            max_no = max(max_no, int(m.group(1)))
    n = max_no + 1
    while f"JDS{n}".upper() in used:
        n += 1
    return f"JDS{n}"


def _many(core, db_path, count):
    core.init_db(db_path)
    out = []
    with core._conn(db_path) as con:
        max_no = 0
        used = set()
        for r in con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall():
            code = str(r["item_code"] or "").strip()
            used.add(code.upper())
            m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
            if m:
                max_no = max(max_no, int(m.group(1)))
        n = max_no + 1
        while len(out) < int(count):
            code = f"JDS{n}"
            if code.upper() not in used:
                out.append(code)
                used.add(code.upper())
            n += 1
    return out


def apply(core):
    patched = []
    try:
        mod = __import__("purchase_new_item_persist_v09136", fromlist=["*"])
        mod._next_jds_code_in_connection = _next_in_connection
        patched.append("purchase_new_item_persist_v09136")
    except Exception:
        pass

    try:
        mod = __import__("purchase_match_ui_v091", fromlist=["*"])
        def _unused_jds_codes(core_module, db_path, count):
            return _many(core_module, db_path, count)
        mod._unused_jds_codes = _unused_jds_codes
        patched.append("purchase_match_ui_v091")
    except Exception:
        pass

    try:
        mod = __import__("item_ui_v086", fromlist=["*"])
        def _next_jds_code(core_module):
            core_module.init_db(core_module.DEFAULT_DB)
            with core_module._conn(core_module.DEFAULT_DB) as con:
                return _next_in_connection(con)
        mod._next_jds_code = _next_jds_code
        patched.append("item_ui_v086")
    except Exception:
        pass

    return {"ok": True, "patched": patched}
