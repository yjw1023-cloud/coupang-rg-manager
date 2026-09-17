"""v0.9.195 user confirmation for ambiguous returned-item resale matching.

When an unknown Coupang option cannot be safely matched automatically, keep the
strict matcher but stage the unresolved option for operator confirmation. The
operator can permanently link it to an original ERP product or mark it as a
normal new product. Confirmed return aliases repair already-imported historical
sales and inventory postings immediately.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

_APPLIED = False
_PENDING_TABLE = "return_sale_match_pending"
_NORMAL_TABLE = "return_sale_normal_overrides"
_RUN_RENDERED = False


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _ensure_schema(core, db) -> None:
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            f"""CREATE TABLE IF NOT EXISTS {_PENDING_TABLE}(
                discount_option_id TEXT PRIMARY KEY,
                discount_name TEXT,
                qty REAL NOT NULL DEFAULT 0,
                net_sales_amount REAL,
                amount_known INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                candidates_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        c.execute(
            f"""CREATE TABLE IF NOT EXISTS {_NORMAL_TABLE}(
                option_id TEXT PRIMARY KEY,
                product_name TEXT,
                confirmed_at TEXT NOT NULL,
                note TEXT
            )"""
        )


def _normal_overrides(core, db) -> set[str]:
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(f"SELECT option_id FROM {_NORMAL_TABLE}").fetchall()
    return {_oid(r["option_id"]) for r in rows if _oid(r["option_id"])}


def _candidate_rows(rd, matcher, core, db, row: dict, parsed: list[dict]) -> list[dict]:
    child_oid = _oid(row.get("option_id"))
    child_name = str(row.get("name") or "")
    child_price = matcher._row_unit_price(row)

    products = rd._load_products(core, db)
    aliases = rd._alias_map(core, db)
    alias_oids = set(str(x) for x in aliases)
    normal_ids = matcher._normal_registry_ids(core, db)
    known_returns = matcher._known_return_option_ids()
    parsed_by_oid = {_oid(x.get("option_id")): x for x in parsed}

    scored = []
    for p in products:
        poid = _oid(p.get("option_id"))
        if not poid or poid == child_oid:
            continue
        if poid in alias_oids or poid in known_returns:
            continue
        try:
            if rd._placeholder(p):
                continue
        except Exception:
            pass

        same_file = parsed_by_oid.get(poid)
        candidate_name = (
            str(same_file.get("name") or "")
            if same_file and same_file.get("name")
            else str(p.get("name") or "")
        )
        score = float(matcher._name_score(child_name, candidate_name) or 0.0)
        if score < 0.45:
            continue

        ref_price = matcher._row_unit_price(same_file) if same_file else None
        if not ref_price:
            ref_price = matcher._historical_price(rd, core, db, int(p["id"]))

        discounted = bool(
            child_price is not None
            and ref_price is not None
            and child_price < float(ref_price) * 0.995
        )
        scored.append(
            {
                "parent_product_id": int(p["id"]),
                "parent_option_id": poid,
                "parent_name": str(p.get("name") or candidate_name),
                "score": round(score, 4),
                "reference_price": float(ref_price) if ref_price else None,
                "discounted": discounted,
                "registered_normal": poid in normal_ids,
                "active": int(p.get("active") or 0),
            }
        )

    scored.sort(
        key=lambda x: (
            1 if x.get("registered_normal") else 0,
            float(x.get("score") or 0),
            1 if x.get("discounted") else 0,
            1 if x.get("active") else 0,
        ),
        reverse=True,
    )
    return scored[:8]


def _reason_for(row: dict, candidates: list[dict]) -> str:
    if not candidates:
        return "유사한 ERP 원상품 후보를 찾지 못함"
    top = candidates[0]
    score = float(top.get("score") or 0)
    if score < 0.80:
        return f"최상위 상품명 유사도 {score:.2f}로 자동확정 기준 0.80 미만"
    if len(candidates) > 1:
        second = float(candidates[1].get("score") or 0)
        if score - second < 0.06:
            return f"원상품 후보가 비슷함 (1순위 {score:.2f}, 2순위 {second:.2f})"
    if not top.get("discounted"):
        return "가격 조건만으로 반품 재판매를 확정하기 어려움"
    return "자동매칭 조건을 모두 충족하지 않아 사용자 확인 필요"


def _stage_pending(core, db, rd, matcher, parsed: list[dict], only_oids: set[str] | None = None) -> int:
    _ensure_schema(core, db)
    aliases = rd._alias_map(core, db)
    normal_overrides = _normal_overrides(core, db)
    normal_ids = matcher._normal_registry_ids(core, db)
    products = rd._load_products(core, db)
    by_oid = {_oid(p.get("option_id")): p for p in products if _oid(p.get("option_id"))}
    known_returns = matcher._known_return_option_ids()

    staged = 0
    now = core.now_iso()
    for row in parsed:
        oid = _oid(row.get("option_id"))
        if only_oids is not None and oid not in only_oids:
            continue
        if not oid or oid in aliases or oid in normal_overrides or oid in normal_ids:
            continue

        existing = by_oid.get(oid)
        if existing:
            try:
                is_placeholder = bool(rd._placeholder(existing))
            except Exception:
                is_placeholder = False
            if not is_placeholder and oid not in known_returns:
                continue

        candidates = _candidate_rows(rd, matcher, core, db, row, parsed)
        reason = _reason_for(row, candidates)
        with core._conn(db) as c:
            c.execute(
                f"""INSERT INTO {_PENDING_TABLE}
                    (discount_option_id,discount_name,qty,net_sales_amount,amount_known,
                     reason,candidates_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(discount_option_id) DO UPDATE SET
                      discount_name=excluded.discount_name,
                      qty=excluded.qty,
                      net_sales_amount=excluded.net_sales_amount,
                      amount_known=excluded.amount_known,
                      reason=excluded.reason,
                      candidates_json=excluded.candidates_json,
                      updated_at=excluded.updated_at""",
                (
                    oid,
                    str(row.get("name") or ""),
                    float(_num(row.get("qty"))),
                    float(_num(row.get("amount"))) if row.get("amount_known") else None,
                    1 if row.get("amount_known") else 0,
                    reason,
                    json.dumps(candidates, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        staged += 1
    return staged


def _alias_ids(core, db) -> set[str]:
    with core._conn(db) as c:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='return_discount_aliases'"
        ).fetchone()
        if not exists:
            return set()
        rows = c.execute("SELECT discount_option_id FROM return_discount_aliases").fetchall()
    return {_oid(r["discount_option_id"]) for r in rows if _oid(r["discount_option_id"])}


def list_pending(core, db=None) -> list[dict]:
    db = db or core.DEFAULT_DB
    _ensure_schema(core, db)
    aliases = _alias_ids(core, db)
    normals = _normal_overrides(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            f"""SELECT discount_option_id,discount_name,qty,net_sales_amount,
                       amount_known,reason,candidates_json,created_at,updated_at
                FROM {_PENDING_TABLE}
                ORDER BY updated_at DESC, discount_option_id"""
        ).fetchall()
    out = []
    for r in rows:
        oid = _oid(r["discount_option_id"])
        if not oid or oid in aliases or oid in normals:
            continue
        try:
            candidates = json.loads(str(r["candidates_json"] or "[]"))
        except Exception:
            candidates = []
        out.append(
            {
                "option_id": oid,
                "name": str(r["discount_name"] or ""),
                "qty": _num(r["qty"]),
                "amount": _num(r["net_sales_amount"]),
                "amount_known": bool(r["amount_known"]),
                "reason": str(r["reason"] or ""),
                "candidates": candidates if isinstance(candidates, list) else [],
                "updated_at": str(r["updated_at"] or ""),
            }
        )
    return out


def _find_parent_by_option(core, db, parent_option_id: str):
    poid = _oid(parent_option_id)
    with core._conn(db) as c:
        return c.execute(
            """SELECT id,option_id,name,unit_cost,active
               FROM products
               WHERE CAST(option_id AS TEXT)=?
               ORDER BY active DESC,id DESC LIMIT 1""",
            (poid,),
        ).fetchone()


def _repair_historical_alias(rd, core, db, discount_oid: str, parent_pid: int) -> int:
    amount_col = rd._amount_column(core, db)
    with core._conn(db) as c:
        child = c.execute(
            """SELECT id,name FROM products
               WHERE CAST(option_id AS TEXT)=?
               ORDER BY active DESC,id DESC LIMIT 1""",
            (discount_oid,),
        ).fetchone()
        if not child:
            return 0
        child_pid = int(child["id"])
        if amount_col:
            rows = c.execute(
                f"""SELECT import_id,COALESCE(SUM(net_qty),0) qty,
                           COALESCE(SUM("{amount_col}"),0) amount
                    FROM sales_stats
                    WHERE product_id=?
                    GROUP BY import_id""",
                (child_pid,),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT import_id,COALESCE(SUM(net_qty),0) qty
                   FROM sales_stats
                   WHERE product_id=?
                   GROUP BY import_id""",
                (child_pid,),
            ).fetchall()

    repaired = 0
    for sr in rows:
        qty = _num(sr["qty"])
        if abs(qty) <= 1e-12:
            continue
        parsed = [
            {
                "option_id": discount_oid,
                "name": str(child["name"] or ""),
                "name_key": rd._name_key(child["name"]),
                "qty": qty,
                "amount": _num(sr["amount"]) if amount_col and "amount" in sr.keys() else None,
                "amount_known": bool(amount_col),
            }
        ]
        rd._post_discount(core, db, int(sr["import_id"]), parsed, {discount_oid: int(parent_pid)})
        repaired += 1
    return repaired


def confirm_alias(core, discount_option_id: str, parent_option_id: str, db=None) -> dict:
    db = db or core.DEFAULT_DB
    _ensure_schema(core, db)

    import return_discount_v099 as rd

    child_oid = _oid(discount_option_id)
    parent = _find_parent_by_option(core, db, parent_option_id)
    if not child_oid:
        raise ValueError("확인할 쿠팡 옵션ID가 없습니다.")
    if not parent:
        raise ValueError(f"원상품 옵션ID {parent_option_id}를 ERP에서 찾지 못했습니다.")

    with core._conn(db) as c:
        pending = c.execute(
            f"SELECT discount_name FROM {_PENDING_TABLE} WHERE discount_option_id=?",
            (child_oid,),
        ).fetchone()
        child = c.execute(
            """SELECT name FROM products WHERE CAST(option_id AS TEXT)=?
               ORDER BY active DESC,id DESC LIMIT 1""",
            (child_oid,),
        ).fetchone()
        name = (
            str(pending["discount_name"] or "")
            if pending
            else str(child["name"] or "")
            if child
            else ""
        )
        now = core.now_iso()
        c.execute(
            """INSERT INTO return_discount_aliases
               (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(discount_option_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 discount_name=excluded.discount_name,
                 match_method=excluded.match_method,
                 updated_at=excluded.updated_at""",
            (child_oid, int(parent["id"]), name, "user_confirmed", now, now),
        )
        c.execute(f"DELETE FROM {_NORMAL_TABLE} WHERE option_id=?", (child_oid,))

    repaired = _repair_historical_alias(rd, core, db, child_oid, int(parent["id"]))

    with core._conn(db) as c:
        c.execute(f"DELETE FROM {_PENDING_TABLE} WHERE discount_option_id=?", (child_oid,))

    return {
        "option_id": child_oid,
        "parent_option_id": _oid(parent["option_id"]),
        "parent_name": str(parent["name"] or ""),
        "repaired_imports": repaired,
    }


def confirm_normal(core, option_id: str, db=None) -> dict:
    db = db or core.DEFAULT_DB
    _ensure_schema(core, db)
    oid = _oid(option_id)
    if not oid:
        raise ValueError("확인할 쿠팡 옵션ID가 없습니다.")

    with core._conn(db) as c:
        pending = c.execute(
            f"SELECT discount_name FROM {_PENDING_TABLE} WHERE discount_option_id=?",
            (oid,),
        ).fetchone()
        product = c.execute(
            """SELECT id,name FROM products WHERE CAST(option_id AS TEXT)=?
               ORDER BY active DESC,id DESC LIMIT 1""",
            (oid,),
        ).fetchone()
        name = (
            str(pending["discount_name"] or "")
            if pending
            else str(product["name"] or "")
            if product
            else ""
        )
        now = core.now_iso()
        c.execute(
            f"""INSERT INTO {_NORMAL_TABLE}(option_id,product_name,confirmed_at,note)
                VALUES(?,?,?,?)
                ON CONFLICT(option_id) DO UPDATE SET
                  product_name=excluded.product_name,
                  confirmed_at=excluded.confirmed_at,
                  note=excluded.note""",
            (oid, name, now, "user_confirmed_normal"),
        )
        c.execute("DELETE FROM return_discount_aliases WHERE discount_option_id=?", (oid,))
        c.execute(f"DELETE FROM {_PENDING_TABLE} WHERE discount_option_id=?", (oid,))
        if product:
            c.execute(
                "UPDATE products SET active=1,updated_at=? WHERE id=?",
                (now, int(product["id"])),
            )

    return {"option_id": oid, "name": name}


def _format_price(v: Any) -> str:
    x = _num(v)
    return f"{int(round(x)):,}원" if x else "-"


def render_pending(st_obj=st, core=None, db=None, location: str = "page") -> int:
    global _RUN_RENDERED
    if core is None:
        import core as core_module
        core = core_module
    db = db or core.DEFAULT_DB
    rows = list_pending(core, db)
    if not rows:
        return 0
    if _RUN_RENDERED:
        return len(rows)
    _RUN_RENDERED = True

    st_obj.warning(
        f"⚠️ 반품 재판매 원상품 확인이 필요한 옵션이 {len(rows)}개 있습니다. "
        "자동으로 합치지 않았습니다. 아래에서 원상품을 확인해 주세요."
    )

    for item in rows:
        oid = item["option_id"]
        key_base = f"rg_return_confirm_{oid}"
        candidates = item.get("candidates") or []
        amount = item.get("amount") if item.get("amount_known") else 0.0
        qty = abs(_num(item.get("qty")))
        unit_price = amount / qty if amount and qty > 1e-12 else 0.0

        with st_obj.container(border=True):
            st_obj.markdown(
                f"**확인할 옵션:** `{oid}` · {item.get('name') or '(상품명 없음)'}"
            )
            st_obj.caption(
                f"판매수량 {qty:g}개 · 판매단가 {_format_price(unit_price)} · "
                f"판정: {item.get('reason') or '사용자 확인 필요'}"
            )

            labels = []
            by_label = {}
            for c in candidates:
                score = float(c.get("score") or 0)
                ref = _format_price(c.get("reference_price"))
                label = (
                    f"{c.get('parent_option_id')} | {c.get('parent_name')} "
                    f"| 유사도 {score:.2f} | 정상판매가 {ref}"
                )
                labels.append(label)
                by_label[label] = c

            selected_label = None
            if labels:
                selected_label = st_obj.selectbox(
                    "원상품 후보",
                    labels,
                    key=f"{key_base}_candidate",
                )

            manual_parent = st_obj.text_input(
                "다른 원상품을 지정하려면 옵션ID 입력",
                value="",
                key=f"{key_base}_manual_parent",
                placeholder="예: 94185578349",
            ).strip()

            col1, col2 = st_obj.columns(2)
            if col1.button(
                "선택 상품의 반품재판매입니다",
                key=f"{key_base}_alias",
                use_container_width=True,
                type="primary",
            ):
                parent_oid = manual_parent
                if not parent_oid and selected_label:
                    parent_oid = str(by_label[selected_label].get("parent_option_id") or "")
                if not parent_oid:
                    st_obj.error("합산할 원상품을 선택하거나 옵션ID를 입력해 주세요.")
                else:
                    result = confirm_alias(core, oid, parent_oid, db)
                    st_obj.success(
                        f"{oid} → {result['parent_option_id']} 연결을 저장했습니다. "
                        f"기존 판매자료 {result['repaired_imports']}건도 재처리했습니다."
                    )
                    st_obj.rerun()

            if col2.button(
                "정상 신상품입니다",
                key=f"{key_base}_normal",
                use_container_width=True,
            ):
                confirm_normal(core, oid, db)
                st_obj.success(f"{oid}를 정상 신상품으로 저장했습니다.")
                st_obj.rerun()

    return len(rows)


def _scan_existing(rd, matcher, core, db) -> dict:
    _ensure_schema(core, db)
    aliases = rd._alias_map(core, db)
    normals = _normal_overrides(core, db)
    normal_ids = matcher._normal_registry_ids(core, db)
    amount_col = rd._amount_column(core, db)
    products = rd._load_products(core, db)
    scanned = 0
    staged_before = len(list_pending(core, db))

    for child in products:
        oid = _oid(child.get("option_id"))
        if not oid or oid in aliases or oid in normals or oid in normal_ids:
            continue
        try:
            placeholder = bool(rd._placeholder(child))
        except Exception:
            placeholder = False
        if not placeholder:
            continue

        with core._conn(db) as c:
            if amount_col:
                sr = c.execute(
                    f"""SELECT COALESCE(SUM(net_qty),0) qty,
                               COALESCE(SUM("{amount_col}"),0) amount
                        FROM sales_stats WHERE product_id=?""",
                    (int(child["id"]),),
                ).fetchone()
            else:
                sr = c.execute(
                    "SELECT COALESCE(SUM(net_qty),0) qty FROM sales_stats WHERE product_id=?",
                    (int(child["id"]),),
                ).fetchone()
        qty = _num(sr["qty"]) if sr else 0.0
        if abs(qty) <= 1e-12:
            continue
        parsed = [
            {
                "option_id": oid,
                "name": str(child.get("name") or ""),
                "name_key": str(child.get("name_key") or ""),
                "qty": qty,
                "amount": _num(sr["amount"]) if amount_col and sr and "amount" in sr.keys() else None,
                "amount_known": bool(amount_col),
            }
        ]
        _stage_pending(core, db, rd, matcher, parsed)
        scanned += 1

    return {
        "scanned": scanned,
        "pending_before": staged_before,
        "pending_after": len(list_pending(core, db)),
    }


def apply(return_discount_module, core_module, db_path=None):
    global _APPLIED
    rd = return_discount_module
    db = db_path or core_module.DEFAULT_DB
    _ensure_schema(core_module, db)

    import return_sale_match_v0944 as matcher

    if not getattr(rd, "_rg_return_confirm_resolve_v09195_applied", False):
        original_resolve = rd._resolve

        def resolve(core, target_db, parsed):
            confirmed_normals = _normal_overrides(core, target_db)
            filtered = [
                row for row in parsed
                if _oid(row.get("option_id")) not in confirmed_normals
            ]
            try:
                return original_resolve(core, target_db, filtered)
            except ValueError as exc:
                import re
                unresolved_oids = set(
                    re.findall(r"(?m)^(\d{8,})\s*\|", str(exc))
                )
                staged = _stage_pending(
                    core,
                    target_db,
                    rd,
                    matcher,
                    filtered,
                    only_oids=unresolved_oids or None,
                )
                if staged:
                    try:
                        render_pending(st, core, target_db, location="import")
                    except Exception:
                        pass
                    raise ValueError(
                        "반품 재판매 원상품 확인이 필요합니다. "
                        "아래 확인 알림에서 원상품을 선택한 뒤 같은 판매통계 파일을 다시 반영해 주세요."
                    ) from exc
                raise

        rd._resolve = resolve
        rd._rg_return_confirm_resolve_v09195_applied = True

    base_page_config = getattr(st, "_rg_return_confirm_base_page_config_v09195", None)
    if base_page_config is None:
        base_page_config = st.set_page_config
        st._rg_return_confirm_base_page_config_v09195 = base_page_config

    def set_page_config_wrapper(*args, **kwargs):
        global _RUN_RENDERED
        _RUN_RENDERED = False
        return base_page_config(*args, **kwargs)

    st.set_page_config = set_page_config_wrapper

    base_uploader = getattr(st, "_rg_return_confirm_base_uploader_v09195", None)
    if base_uploader is None:
        base_uploader = st.file_uploader
        st._rg_return_confirm_base_uploader_v09195 = base_uploader

    def file_uploader_wrapper(*args, **kwargs):
        result = base_uploader(*args, **kwargs)
        if st.session_state.get("_rg_sales_stats_period_active", False):
            try:
                render_pending(st, core_module, db, location="upload")
            except Exception as exc:
                st.caption(f"반품 재판매 확인 목록을 불러오지 못했습니다: {exc}")
        return result

    st.file_uploader = file_uploader_wrapper

    try:
        core_module._rg_return_match_scan_v09195 = _scan_existing(
            rd, matcher, core_module, db
        )
    except Exception as exc:
        core_module._rg_return_match_scan_v09195 = {"error": str(exc)}

    core_module._rg_return_sale_confirm_v09195_applied = True
    _APPLIED = True
    return rd
