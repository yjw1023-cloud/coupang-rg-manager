"""v0.9.220 hard fix for unresolved return-sale matching.

A returned-item child can reuse the original/normal option_id. In that case an
option-ID alias cannot distinguish the child from the real master product. This
patch detects non-canonical product_id rows directly, asks the user which original
product they belong to, and stores that answer in return_product_aliases.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
import shared_return_match_ui_v09217 as base
import shared_product_identity_v09215 as shared


def _product_identity(core, db, product_id: int) -> tuple[str, str]:
    if not product_id:
        return "", ""
    try:
        with core._conn(db) as c:
            if not base._exists(c, "products"):
                return "", ""
            row = c.execute(
                "SELECT option_id,name FROM products WHERE id=?", (int(product_id),)
            ).fetchone()
        if not row:
            return "", ""
        return base._oid(row["option_id"]), str(row["name"] or "")
    except Exception:
        return "", ""


def _confirmed_product_ids(core, db) -> set[int]:
    try:
        with core._conn(db) as c:
            if not base._exists(c, "return_product_aliases"):
                return set()
            rows = c.execute("SELECT child_product_id FROM return_product_aliases").fetchall()
        return {int(r["child_product_id"]) for r in rows}
    except Exception:
        return set()


def _normal_representatives(core, db) -> dict[str, int]:
    try:
        by_oid, _ = shared.normal_maps(core, db)
        return {str(oid): int(target[0]) for oid, target in by_oid.items()}
    except Exception:
        return {}


def _pick_unmatched_identity(
    core,
    db,
    product_id: int,
    row_option_id: Any,
    normals: set[str],
    option_aliases: set[str],
    product_aliases: set[int],
    normal_reps: dict[str, int],
) -> dict | None:
    """Return an unresolved child descriptor, preferring exact product_id."""
    row_oid = base._oid(row_option_id)
    product_oid, product_name = _product_identity(core, db, product_id)

    if product_id and product_id not in product_aliases:
        # Critical v0.9.220 case: same normal option ID, but a different DB product
        # row than the designated master. This must be confirmed by the user rather
        # than silently treated as another normal product.
        if product_oid in normals:
            rep_pid = int(normal_reps.get(product_oid, 0) or 0)
            if rep_pid and rep_pid != int(product_id):
                return {
                    "match_kind": "product_id",
                    "child_product_id": int(product_id),
                    "option_id": product_oid,
                    "name": product_name or f"상품ID {product_id}",
                }

        # A child product with its own non-normal option ID also gets an exact
        # product mapping. For compatibility we additionally persist its option
        # alias after the user confirms it.
        if product_oid and product_oid not in normals and product_oid not in option_aliases:
            return {
                "match_kind": "product_id",
                "child_product_id": int(product_id),
                "option_id": product_oid,
                "name": product_name or f"옵션ID {product_oid}",
            }

    # Rows without a usable child product_id still fall back to the legacy
    # option-ID matching path.
    if row_oid and row_oid not in normals and row_oid not in option_aliases:
        return {
            "match_kind": "option_id",
            "child_product_id": int(product_id or 0),
            "option_id": row_oid,
            "name": product_name or f"옵션ID {row_oid}",
        }
    return None


def _save_product_alias(core, db, item: dict, parent_pid: int):
    now = core.now_iso()
    child_pid = int(item.get("child_product_id") or 0)
    child_oid = base._oid(item.get("option_id"))
    child_name = str(item.get("name") or "")
    if child_pid <= 0:
        raise ValueError("반품 자식 상품ID가 없습니다.")

    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS return_product_aliases(
          child_product_id INTEGER PRIMARY KEY,
          parent_product_id INTEGER NOT NULL,
          child_option_id TEXT,
          child_name TEXT,
          match_method TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL)""")
        c.execute(
            """INSERT INTO return_product_aliases
               (child_product_id,parent_product_id,child_option_id,child_name,match_method,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(child_product_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 child_option_id=excluded.child_option_id,
                 child_name=excluded.child_name,
                 match_method=excluded.match_method,
                 updated_at=excluded.updated_at""",
            (child_pid, int(parent_pid), child_oid, child_name, "manual_user_product_id", now, now),
        )
        c.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
          product_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,hidden_at TEXT NOT NULL)""")
        c.execute(
            """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
               VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET
               reason=excluded.reason,hidden_at=excluded.hidden_at""",
            (child_pid, "return_product_alias_manual_v09220", now),
        )
        c.execute("UPDATE products SET active=0 WHERE id=?", (child_pid,))

    # Keep the old option alias too when the child has a genuinely separate return
    # option ID. Never do this when it reuses a verified normal option ID.
    if child_oid and child_oid not in base._normal_ids(core, db):
        try:
            base._save_alias(core, db, child_oid, child_name, int(parent_pid))
        except Exception:
            pass


def _ask(core, db, items: list[dict], source: str) -> bool:
    if not items:
        return False
    products = base._normal_products(core, db)
    if not products:
        st.error("정상 원상품 목록을 불러오지 못해 반품상품을 매칭할 수 없습니다.")
        st.stop()

    item = items[0]
    oid = base._oid(item.get("option_id"))
    child_pid = int(item.get("child_product_id") or 0)
    name = str(item.get("name") or f"옵션ID {oid}")
    qty = item.get("qty")
    ordered = sorted(products, key=lambda p: base._candidate_score(item, p), reverse=True)
    ids = [int(p["id"]) for p in ordered]
    by_id = {int(p["id"]): p for p in ordered}

    st.warning(
        f"{source}에서 원상품과 별도 행으로 잡힌 반품/중복 판매상품을 발견했습니다. "
        "원상품을 한 번 선택하면 이후 ERP 전체에서 같은 상품으로 합산합니다."
    )
    with st.container(border=True):
        st.markdown(f"**매칭 필요: {name}**")
        bits = []
        if child_pid:
            bits.append(f"상품ID {child_pid}")
        if oid:
            bits.append(f"옵션ID {oid}")
        if isinstance(qty, (int, float)):
            bits.append(f"판매수량 {qty:g}")
        bits.append(f"미확정 {len(items)}건 중 1건")
        st.caption(" · ".join(bits))
        selected = st.selectbox(
            "이 상품의 원장상품",
            ids,
            format_func=lambda pid: base._product_label(by_id[int(pid)]),
            key=f"_rg_return_master_v09220_{source}_{child_pid}_{oid}",
        )
        if st.button(
            "이 원장상품으로 확정",
            type="primary",
            key=f"_rg_return_master_save_v09220_{source}_{child_pid}_{oid}",
        ):
            if item.get("match_kind") == "product_id" and child_pid:
                _save_product_alias(core, db, item, int(selected))
            else:
                base._save_alias(core, db, oid, name, int(selected))
            try:
                st.toast("원장상품 매칭을 저장했습니다.", icon="✅")
            except Exception:
                pass
            st.rerun()
    st.stop()
    return True


def period_unmatched(core, db, start, end) -> list[dict]:
    normals = base._normal_ids(core, db)
    option_aliases = base._alias_ids(core, db)
    product_aliases = _confirmed_product_ids(core, db)
    normal_reps = _normal_representatives(core, db)

    with core._conn(db) as c:
        if not (base._exists(c, "imports") and base._exists(c, "sales_stats")):
            return []
        scols = base._cols(c, "sales_stats")
        if "import_id" not in scols:
            return []
        imports = c.execute(
            """SELECT id FROM imports WHERE data_type='sales_stats'
               AND period_start>=? AND period_end<=?""",
            (str(start), str(end)),
        ).fetchall()
        if not imports:
            return []
        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        pid_expr = "product_id" if "product_id" in scols else "0 AS product_id"
        oid_expr = "option_id" if "option_id" in scols else "'' AS option_id"
        qty_expr = "SUM(COALESCE(sales_qty,0))" if "sales_qty" in scols else (
            "SUM(COALESCE(net_qty,0))" if "net_qty" in scols else "0"
        )
        rows = c.execute(
            f"SELECT {pid_expr},{oid_expr},{qty_expr} qty FROM sales_stats "
            f"WHERE import_id IN ({marks}) GROUP BY product_id,option_id",
            ids,
        ).fetchall()

    out: dict[str, dict] = {}
    for r in rows:
        try:
            pid = int(r["product_id"] or 0)
        except Exception:
            pid = 0
        item = _pick_unmatched_identity(
            core, db, pid, r["option_id"] if "option_id" in r.keys() else "",
            normals, option_aliases, product_aliases, normal_reps,
        )
        if not item:
            continue
        item["qty"] = float(r["qty"] or 0)
        key = f"p:{item['child_product_id']}" if item.get("match_kind") == "product_id" else f"o:{item['option_id']}"
        out[key] = item
    return list(out.values())


def frame_unmatched(core, db, frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    normals = base._normal_ids(core, db)
    option_aliases = base._alias_ids(core, db)
    product_aliases = _confirmed_product_ids(core, db)
    normal_reps = _normal_representatives(core, db)
    oidcol = next((c for c in ("옵션ID", "쿠팡 옵션ID", "option_id") if c in frame.columns), None)
    pidcol = "product_id" if "product_id" in frame.columns else None
    if oidcol is None and pidcol is None:
        return []

    out: dict[str, dict] = {}
    for _, row in frame.iterrows():
        pid = 0
        if pidcol:
            try:
                pid = int(float(row.get(pidcol) or 0))
            except Exception:
                pid = 0
        item = _pick_unmatched_identity(
            core, db, pid, row.get(oidcol) if oidcol else "",
            normals, option_aliases, product_aliases, normal_reps,
        )
        if not item:
            continue
        supplied_name = str(row.get("상품명") or row.get("아이템") or "").strip()
        if supplied_name:
            item["name"] = supplied_name
        qty = row.get("판매수량") if "판매수량" in frame.columns else None
        try:
            item["qty"] = float(qty) if qty is not None else None
        except Exception:
            item["qty"] = None
        key = f"p:{item['child_product_id']}" if item.get("match_kind") == "product_id" else f"o:{item['option_id']}"
        out[key] = item
    return list(out.values())


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    try:
        shared_result = shared.apply(core, db)
    except Exception as exc:
        shared_result = {"ok": False, "error": str(exc)}

    # Replace the shared matcher functions themselves. Existing wrappers call
    # these module globals dynamically, so this also fixes already-loaded pages.
    base.period_unmatched = period_unmatched
    base.frame_unmatched = frame_unmatched
    base._ask = _ask
    matcher_result = base.apply(core, db)

    core.rg_ensure_return_mappings_for_period = lambda start, end, source="ERP", db_path=None: base.ensure_period_mappings(
        core, db_path or core.DEFAULT_DB, start, end, source
    )
    core.rg_ensure_return_mappings_for_frame = lambda frame, source="ERP", db_path=None: base.ensure_frame_mappings(
        core, db_path or core.DEFAULT_DB, frame, source
    )
    return {
        "ok": True,
        "fix": "product_id_confirmation_v09220",
        "shared_identity": shared_result,
        "matcher": matcher_result,
    }
