"""v0.9.121 archive obsolete items even when legacy ERP stock remains.

User-confirmed obsolete items may carry accounting stock imported from the old ERP
although physical stock is actually zero.  Do not physically delete the product or
its history.  Instead post auditable inventory adjustments that bring every
warehouse balance to zero, then set products.active=0.
"""
from __future__ import annotations

import uuid

import pandas as pd


_APPLIED = False


def _fmt_qty(v):
    try:
        x = float(v or 0)
        if abs(x - round(x)) < 1e-9:
            return f"{int(round(x)):,}"
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _archive_zero_stock(core, target, pids):
    ids = [int(x) for x in pids]
    if not ids:
        return {"items": 0, "adjustments": 0, "ref_no": ""}

    now = core.now_iso()
    txn_date = str(now)[:10]
    ref_no = "ARCHIVE-ZERO-" + str(now).replace("-", "").replace(":", "").replace("T", "")[:14] + "-" + uuid.uuid4().hex[:8]
    adjustments = 0

    with target._conn(core) as con:
        for pid in ids:
            product = con.execute(
                "SELECT id,item_code,option_id,name,active FROM products WHERE id=?",
                (pid,),
            ).fetchone()
            if not product:
                raise ValueError(f"ERP상품ID {pid}를 찾지 못했습니다.")
            if int(product["active"] or 0) != 1:
                continue

            rows = con.execute(
                """SELECT t.warehouse_id,COALESCE(w.name,'미지정') warehouse,
                          COALESCE(SUM(t.qty_delta),0) qty
                   FROM inventory_txns t
                   LEFT JOIN warehouses w ON w.id=t.warehouse_id
                   WHERE t.product_id=?
                   GROUP BY t.warehouse_id,w.name""",
                (pid,),
            ).fetchall()

            for r in rows:
                qty = float(r["qty"] or 0)
                if abs(qty) <= 1e-9:
                    continue
                delta = -qty
                memo = (
                    "품목 보관처리 · 실물재고 0 확인 · 이전 ERP 잔존재고 정리"
                    f" · 기존 {_fmt_qty(qty)} → 0"
                )
                con.execute(
                    """INSERT INTO inventory_txns
                       (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        txn_date, pid, int(r["warehouse_id"]), delta,
                        "재고실사조정", ref_no, memo, now,
                    ),
                )
                adjustments += 1

            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (now, pid),
            )

    return {"items": len(ids), "adjustments": adjustments, "ref_no": ref_no}


def _general_delete_list(st, core, target):
    active = target._products(core)
    active = active[active["active"] == 1].copy()
    if active.empty:
        st.info("삭제할 품목이 없습니다.")
        return

    table = pd.DataFrame({
        "선택": False,
        "_id": active["id"].astype(int),
        "품목코드": [target._code(r.item_code, r.option_id) for r in active.itertuples()],
        "상품명": active["name"].fillna(""),
        "자체창고": active["own_stock"].fillna(0),
        "쿠팡RG": active["rg_stock"].fillna(0),
        "반품창고": active["return_stock"].fillna(0),
    })
    table["처리방법"] = [
        "바로 보관" if not target._balances(core, int(pid)) else "재고 0 정리 후 보관"
        for pid in table["_id"]
    ]

    edited = st.data_editor(
        table,
        key="general_delete_list_v09121",
        hide_index=True,
        use_container_width=True,
        height=min(650, max(260, 38 * (len(table) + 1))),
        disabled=[c for c in table.columns if c != "선택"],
        column_config={"_id": None},
    )
    selected = [int(x) for x in edited.loc[edited["선택"] == True, "_id"].tolist()]
    if not selected:
        st.caption("보관처리할 품목의 체크박스를 선택하세요.")
        return

    stocked = [pid for pid in selected if target._balances(core, pid)]

    if stocked:
        details = []
        for pid in stocked[:8]:
            row = active[active["id"] == pid].iloc[0]
            bals = target._balances(core, pid)
            details.append(
                f"{target._code(row['item_code'], row['option_id'])} · "
                + ", ".join(f"{w} {_fmt_qty(q)}" for w, q in bals)
            )
        st.warning(
            "선택 품목 중 ERP상 재고가 남아 있습니다. 실제 재고가 없는 폐기/미사용 품목이라면 "
            "아래 확인 후 ERP 잔존재고를 0으로 조정하고 보관처리할 수 있습니다."
        )
        if details:
            st.caption(" / ".join(details))

        confirm = st.checkbox(
            f"선택한 품목 {len(selected):,}개는 실제 재고가 0입니다. ERP에 남은 재고를 0으로 조정한 뒤 보관처리합니다.",
            key="general_legacy_zero_confirm_v09121",
        )
        if st.button(
            "재고 0 정리 후 선택 품목 보관",
            type="primary",
            disabled=not confirm,
            key="general_legacy_zero_submit_v09121",
        ):
            try:
                result = _archive_zero_stock(core, target, selected)
                st.success(
                    f"품목 {result['items']:,}개를 보관처리했습니다. "
                    f"ERP 잔존재고 조정이력 {result['adjustments']:,}건을 남기고 현재고를 0으로 정리했습니다."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        return

    confirm = st.checkbox(
        f"선택한 품목 {len(selected):,}개를 보관처리합니다.",
        key="general_bulk_confirm_v09121",
    )
    if st.button(
        "선택 품목 보관처리",
        type="primary",
        disabled=not confirm,
        key="general_bulk_submit_v09121",
    ):
        try:
            for pid in selected:
                target._archive(core, pid)
            st.success(f"품목 {len(selected):,}개를 보관처리했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def apply(item_delete_module):
    global _APPLIED
    if _APPLIED or getattr(item_delete_module, "_rg_item_archive_cleanup_v09121_applied", False):
        return item_delete_module

    def patched(st, core):
        return _general_delete_list(st, core, item_delete_module)

    item_delete_module._general_delete_list = patched
    item_delete_module._rg_item_archive_cleanup_v09121_applied = True
    _APPLIED = True
    return item_delete_module
