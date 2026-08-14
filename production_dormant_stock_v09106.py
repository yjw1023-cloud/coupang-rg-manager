"""RG Manager v0.9.106 dormant-stock production support.

Purpose
-------
When production uses physically existing legacy/dormant raw material that is not
recorded as ERP stock, the user may explicitly fill only the BOM shortage into
자체창고 without creating a purchase record. The receipt uses the raw material's
registered ERP unit cost, then the batch production consumes it in the same
SQLite transaction.

Safety
------
- Existing negative-stock production remains the default when the option is off.
- Dormant-stock fill is explicit and previewed before execution.
- Only the shortage for the selected production batch is added.
- Previously negative balances are not silently repaired; they remain negative.
- Dormant receipt + production is atomic.
- No purchase/import row is created.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


_APPLIED = False


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _component_plan(core_module, batch_module, validation_rows, db_path=None):
    """Return aggregate BOM demand/current stock/dormant-stock shortage preview."""
    db = db_path or core_module.DEFAULT_DB
    batch_module.ensure_schema(core_module, db)

    required: dict[int, float] = {}
    meta: dict[int, dict[str, Any]] = {}

    with core_module._conn(db) as c:
        own = c.execute("SELECT id FROM warehouses WHERE name='자체창고'").fetchone()
        if not own:
            raise ValueError("자체창고를 찾지 못했습니다.")
        own_id = int(own["id"])

        for r in list(validation_rows or []):
            if r.get("errors") or not r.get("product_id") or not r.get("qty"):
                continue
            pid = int(r["product_id"])
            qty = float(r["qty"])
            bom = c.execute(
                """SELECT b.component_product_id,b.qty_per,
                          p.item_code,p.option_id,p.name,p.unit_cost
                   FROM bom_items b
                   JOIN products p ON p.id=b.component_product_id
                   WHERE b.parent_product_id=?""",
                (pid,),
            ).fetchall()
            for b in bom:
                cid = int(b["component_product_id"])
                need = _num(b["qty_per"]) * qty
                if need <= 0:
                    continue
                required[cid] = required.get(cid, 0.0) + need
                meta[cid] = {
                    "product_id": cid,
                    "item_code": batch_module._display_code(b["item_code"], b["option_id"]),
                    "name": str(b["name"] or ""),
                    "unit_cost": _num(b["unit_cost"]),
                }

        balances = {}
        if required:
            marks = ",".join("?" for _ in required)
            rows = c.execute(
                f"""SELECT product_id,COALESCE(SUM(qty_delta),0) qty
                    FROM inventory_txns
                    WHERE warehouse_id=? AND product_id IN ({marks})
                    GROUP BY product_id""",
                (own_id, *required.keys()),
            ).fetchall()
            balances = {int(x["product_id"]): _num(x["qty"]) for x in rows}

    rows = []
    for cid in sorted(required, key=lambda x: (meta[x]["name"], meta[x]["item_code"], x)):
        current = balances.get(cid, 0.0)
        # Do not erase a previous negative shortage. Only today's batch shortage is
        # supplied from dormant stock; prior negative stock remains visible.
        usable = max(current, 0.0)
        shortage = max(required[cid] - usable, 0.0)
        unit_cost = meta[cid]["unit_cost"]
        rows.append({
            **meta[cid],
            "required_qty": required[cid],
            "current_qty": current,
            "usable_qty": usable,
            "dormant_qty": shortage,
            "dormant_value": shortage * unit_cost,
        })
    return rows


def _execute_batch(core_module, batch_module, parsed, file_name, production_date,
                   db_path=None, fill_dormant_shortage=False):
    """Execute batch atomically, optionally filling BOM shortages as dormant stock."""
    db = db_path or core_module.DEFAULT_DB
    batch_module.ensure_schema(core_module, db)
    validation = batch_module.validate_rows(core_module, parsed["rows"], db)
    if not validation["ok"]:
        if validation["missing_bom"]:
            names = ", ".join(
                batch_module._text(r.get("source_name")) or batch_module._text(r.get("option_id"))
                for r in validation["missing_bom"][:8]
            )
            raise ValueError(f"BOM이 없는 상품이 있습니다. 전체 생산을 중단합니다: {names}")
        raise ValueError("생산자료에 오류가 있어 전체 생산을 중단합니다. 오류 행을 확인해 주세요.")

    hash_value = batch_module._text(parsed.get("file_hash")) or batch_module.file_hash(parsed.get("data") or b"")
    prod_date = core_module.norm_date(production_date)
    now = core_module.now_iso()
    ref_no = f"PRODBATCH-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    with core_module._conn(db) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            dup = c.execute(
                "SELECT id,file_name,production_date FROM production_batch_imports WHERE file_hash=?",
                (hash_value,),
            ).fetchone()
            if dup:
                raise ValueError(
                    f"이미 생산 처리한 동일 파일입니다. 기존 생산일: {dup['production_date']} / "
                    f"파일: {dup['file_name'] or '-'}"
                )

            own = c.execute("SELECT id FROM warehouses WHERE name='자체창고'").fetchone()
            rg = c.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
            if not own or not rg:
                raise ValueError("자체창고 또는 쿠팡RG 창고를 찾지 못했습니다.")
            own_id, rg_id = int(own["id"]), int(rg["id"])

            prepared = []
            total_need: dict[int, float] = {}
            component_meta: dict[int, dict[str, Any]] = {}

            for r in validation["rows"]:
                pid = int(r["product_id"])
                bom = c.execute(
                    """SELECT b.component_product_id,b.qty_per,
                              p.item_code,p.option_id,p.name,p.unit_cost
                       FROM bom_items b
                       JOIN products p ON p.id=b.component_product_id
                       WHERE b.parent_product_id=?""",
                    (pid,),
                ).fetchall()
                if not bom:
                    raise ValueError(
                        f"BOM이 없는 상품이 있습니다. 전체 생산을 중단합니다: "
                        f"{r['erp_name'] or r['source_name']}"
                    )
                if any(int(x["component_product_id"]) == pid for x in bom):
                    raise ValueError(
                        f"BOM 오류가 있어 전체 생산을 중단합니다: "
                        f"{r['erp_name'] or r['source_name']}"
                    )

                unit_cost = sum(_num(x["qty_per"]) * _num(x["unit_cost"]) for x in bom)
                prepared.append((r, bom, unit_cost))

                qty = float(r["qty"])
                for b in bom:
                    cid = int(b["component_product_id"])
                    need = _num(b["qty_per"]) * qty
                    total_need[cid] = total_need.get(cid, 0.0) + need
                    component_meta[cid] = {
                        "name": str(b["name"] or ""),
                        "item_code": batch_module._display_code(b["item_code"], b["option_id"]),
                        "unit_cost": _num(b["unit_cost"]),
                    }

            dormant_rows = 0
            dormant_qty = 0.0
            dormant_value = 0.0

            if fill_dormant_shortage and total_need:
                marks = ",".join("?" for _ in total_need)
                live_rows = c.execute(
                    f"""SELECT product_id,COALESCE(SUM(qty_delta),0) qty
                        FROM inventory_txns
                        WHERE warehouse_id=? AND product_id IN ({marks})
                        GROUP BY product_id""",
                    (own_id, *total_need.keys()),
                ).fetchall()
                live = {int(x["product_id"]): _num(x["qty"]) for x in live_rows}
                dormant_ref = f"{ref_no}-DORMANT"

                for cid, required_qty in total_need.items():
                    current = live.get(cid, 0.0)
                    usable = max(current, 0.0)
                    shortage = max(required_qty - usable, 0.0)
                    if shortage <= 1e-12:
                        continue
                    unit_cost = component_meta[cid]["unit_cost"]
                    memo = (
                        f"불용재고 전환입고 · 생산자료 Excel {batch_module._text(file_name)} · "
                        f"생산 필요수량 부족분 자동입고"
                    )
                    c.execute(
                        """INSERT INTO inventory_txns
                           (txn_date,product_id,warehouse_id,qty_delta,txn_type,
                            unit_cost,ref_no,memo,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            prod_date, cid, own_id, shortage, "불용재고전환입고",
                            unit_cost, dormant_ref, memo, now,
                        ),
                    )
                    dormant_rows += 1
                    dormant_qty += shortage
                    dormant_value += shortage * unit_cost

            cur = c.execute(
                """INSERT INTO production_batch_imports
                   (file_hash,file_name,production_date,target_rows,total_qty,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    hash_value, batch_module._text(file_name), prod_date,
                    len(prepared), validation["total_qty"], now,
                ),
            )
            batch_id = int(cur.lastrowid)

            for r, bom, unit_cost in prepared:
                pid = int(r["product_id"])
                qty = int(r["qty"])
                memo = (
                    f"생산자료 Excel {batch_module._text(file_name)} / "
                    f"옵션ID {batch_module._text(r['option_id'])}"
                )

                for b in bom:
                    need = _num(b["qty_per"]) * qty
                    c.execute(
                        """INSERT INTO inventory_txns
                           (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            prod_date, int(b["component_product_id"]), own_id, -need,
                            "생산소모", ref_no, memo, now,
                        ),
                    )

                c.execute(
                    """INSERT INTO inventory_txns
                       (txn_date,product_id,warehouse_id,qty_delta,txn_type,
                        unit_cost,ref_no,memo,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        prod_date, pid, rg_id, qty, "생산RG입고",
                        unit_cost, ref_no, memo, now,
                    ),
                )
                c.execute(
                    "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                    (unit_cost, now, pid),
                )
                c.execute(
                    """INSERT INTO production_orders
                       (production_date,parent_product_id,qty,warehouse_id,
                        produced_unit_cost,memo,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (prod_date, pid, qty, rg_id, unit_cost, memo, now),
                )
                c.execute(
                    """INSERT INTO production_batch_lines
                       (batch_id,source_row,option_id,product_id,qty,
                        produced_unit_cost,ref_no,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        batch_id, int(r["source_row"]), batch_module._text(r["option_id"]),
                        pid, qty, unit_cost, ref_no, now,
                    ),
                )

            c.commit()
            return {
                "status": "produced",
                "batch_id": batch_id,
                "rows": len(prepared),
                "total_qty": validation["total_qty"],
                "ref_no": ref_no,
                "dormant_rows": dormant_rows,
                "dormant_qty": dormant_qty,
                "dormant_value": dormant_value,
            }
        except Exception:
            c.rollback()
            raise


def _dormant_frame(pd_obj, plan):
    return pd_obj.DataFrame([
        {
            "원재료": x["name"],
            "품목코드": x["item_code"],
            "생산 필요수량": x["required_qty"],
            "자체창고 현재고": x["current_qty"],
            "불용재고 전환입고": x["dormant_qty"],
            "ERP 등록원가/개": round(x["unit_cost"]),
            "전환입고 원가": round(x["dormant_value"]),
        }
        for x in plan
        if x["dormant_qty"] > 1e-12
    ])


def _render_page(st, pd, date, core, batch_module, page_header, section, **_kwargs):
    db_path = core.DEFAULT_DB
    batch_module.ensure_schema(core, db_path)
    page_header(
        "생산자료",
        "쿠팡 로켓그로스 입고 Excel의 '입고 수량'을 생산수량으로 읽어 일괄 생산합니다.",
        eyebrow="PRODUCTION BATCH",
    )
    st.info(
        "파일을 업로드하는 것만으로는 생산되지 않습니다. 먼저 전체 상품의 옵션ID와 BOM을 검사하고, "
        "모든 상품이 정상일 때만 마지막 생산 실행 버튼이 활성화됩니다."
    )
    st.caption(
        "양식 기준: 시트 '로켓그로스 입고' · G열 옵션 ID · V열 입고 수량 입력. "
        "V열이 빈 상품은 생산대상에서 제외됩니다."
    )

    uploaded = st.file_uploader(
        "생산자료 Excel",
        type=["xlsx"],
        key="production_batch_v095_upload",
        help="쿠팡 로켓그로스 입고요청 Excel 양식을 그대로 사용합니다.",
    )
    production_date = st.date_input(
        "생산일", value=date.today(), key="production_batch_v095_date"
    )

    if uploaded is None:
        return

    try:
        parsed = batch_module.parse_production_excel(uploaded)
        validation = batch_module.validate_rows(core, parsed["rows"], db_path)
    except Exception as exc:
        st.error(f"생산자료 확인 실패: {exc}")
        return

    duplicate = batch_module._already_executed(core, parsed["file_hash"], db_path)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("생산대상", f"{len(validation['rows']):,}개 상품")
    c2.metric("총 생산수량", f"{validation['total_qty']:,}개")
    c3.metric("BOM 정상", f"{len(validation['rows']) - len(validation['missing_bom']):,}개")
    c4.metric("BOM 없음", f"{len(validation['missing_bom']):,}개")

    section("생산 대상 확인", "아래 표의 모든 행이 정상이어야 전체 생산을 실행할 수 있습니다.")
    preview = batch_module._preview_frame(pd, validation["rows"])
    st.dataframe(
        preview,
        use_container_width=True,
        hide_index=True,
        height=min(650, max(220, 38 * (len(preview) + 1))),
    )

    if duplicate:
        st.error(
            f"이 파일은 이미 생산 처리되었습니다. 생산일 {duplicate['production_date']} · "
            f"처리시각 {duplicate['created_at']} · 동일 파일 재생산은 차단됩니다."
        )
        return

    if validation["missing_bom"]:
        st.error(
            f"BOM이 없는 상품이 {len(validation['missing_bom']):,}개 있습니다. "
            "정상 상품을 포함한 전체 생산을 중단합니다."
        )
        missing = pd.DataFrame([
            {
                "상품명": batch_module._text(r.get("source_name")),
                "옵션명": batch_module._text(r.get("option_name")),
                "옵션ID": batch_module._text(r.get("option_id")),
                "ERP 상품명": batch_module._text(r.get("erp_name")),
            }
            for r in validation["missing_bom"]
        ])
        st.dataframe(missing, use_container_width=True, hide_index=True)
        st.warning("BOM을 등록한 뒤 생산자료를 다시 업로드해 주세요. 현재 파일에서는 어떤 상품도 생산되지 않았습니다.")
        return

    if not validation["ok"]:
        st.error(
            f"생산자료에 오류가 있는 상품이 {len(validation['blocked']):,}개 있습니다. "
            "전체 생산을 중단합니다. 상태 칸의 오류를 수정한 뒤 파일을 다시 업로드해 주세요."
        )
        return

    try:
        plan = _component_plan(core, batch_module, validation["rows"], db_path)
    except Exception as exc:
        st.error(f"원재료 재고 확인 실패: {exc}")
        return

    shortage_plan = [x for x in plan if x["dormant_qty"] > 1e-12]
    use_dormant = False
    if shortage_plan:
        total_dormant_qty = sum(x["dormant_qty"] for x in shortage_plan)
        total_dormant_value = sum(x["dormant_value"] for x in shortage_plan)
        section(
            "부족 원재료",
            "ERP 재고가 부족하지만 실제 불용재고를 사용하는 경우 아래 수량만 자체창고에 전환입고할 수 있습니다.",
        )
        st.dataframe(
            _dormant_frame(pd, shortage_plan),
            use_container_width=True,
            hide_index=True,
            height=min(420, max(160, 38 * (len(shortage_plan) + 1))),
        )
        st.caption(
            f"불용재고 전환입고 합계 {total_dormant_qty:,.0f}개 · "
            f"ERP 등록원가 기준 {total_dormant_value:,.0f}원"
        )
        use_dormant = st.checkbox(
            "부족 원재료를 매입이 아닌 '불용재고 전환입고'로 먼저 넣고 생산합니다.",
            value=False,
            key=f"production_dormant_v09106_{parsed['file_hash'][:12]}",
            help=(
                "체크하면 생산에 필요한 부족분만 자체창고에 입고하며, "
                "원가는 원재료 품목에 현재 등록된 ERP 원가를 사용합니다. 매입이력은 생성하지 않습니다."
            ),
        )
        if use_dormant:
            st.success(
                "불용재고 전환입고를 사용합니다. 전환입고와 생산은 한 번에 처리되며, "
                "중간에 오류가 나면 둘 다 반영되지 않습니다."
            )
        else:
            st.warning(
                "체크하지 않고 생산하면 기존 규칙대로 부족 원재료가 마이너스 재고로 남습니다."
            )

    st.success("모든 생산대상 상품의 옵션ID와 BOM 확인이 완료되었습니다. 아직 생산은 실행되지 않았습니다.")
    confirm_text = (
        f"위 {len(validation['rows']):,}개 상품 / 총 {validation['total_qty']:,}개를 "
        f"{production_date:%Y-%m-%d} 생산 처리합니다."
    )
    if use_dormant:
        confirm_text += " 부족 원재료는 불용재고 전환입고 후 사용합니다."
    confirm = st.checkbox(
        confirm_text,
        key=f"production_batch_v095_confirm_{parsed['file_hash'][:12]}",
    )
    if st.button(
        "전체 생산 실행",
        type="primary",
        disabled=not confirm,
        key=f"production_batch_v095_execute_{parsed['file_hash'][:12]}",
    ):
        try:
            result = _execute_batch(
                core, batch_module, parsed,
                getattr(uploaded, "name", "생산자료.xlsx"),
                production_date, db_path,
                fill_dormant_shortage=use_dormant,
            )
            msg = (
                f"생산 완료: {result['rows']:,}개 상품 / 총 {result['total_qty']:,}개. "
                "BOM 구성품은 자체창고에서 차감되고 완제품은 쿠팡RG에 입고되었습니다."
            )
            if result.get("dormant_rows"):
                msg += (
                    f" 부족 원재료 {result['dormant_qty']:,.0f}개는 매입이 아닌 불용재고 전환입고로 처리했고, "
                    f"ERP 등록원가 기준 {result['dormant_value']:,.0f}원을 원가에 반영했습니다."
                )
            st.success(msg)
            st.rerun()
        except Exception as exc:
            st.error(f"생산 실패: {exc}")


def apply(core_module, production_batch_module):
    global _APPLIED
    marker = "_rg_production_dormant_v09106_applied"
    if _APPLIED or getattr(production_batch_module, marker, False):
        return production_batch_module

    def execute_batch(core, parsed, file_name, production_date, db_path=None,
                      fill_dormant_shortage=False):
        return _execute_batch(
            core, production_batch_module, parsed, file_name, production_date,
            db_path, fill_dormant_shortage=fill_dormant_shortage,
        )

    def render_production_batch_page(st, pd, date, core, page_header, section, **kwargs):
        return _render_page(
            st, pd, date, core, production_batch_module, page_header, section, **kwargs
        )

    production_batch_module.dormant_stock_plan = (
        lambda core, validation_rows, db_path=None:
        _component_plan(core, production_batch_module, validation_rows, db_path)
    )
    production_batch_module.execute_batch = execute_batch
    production_batch_module.render_production_batch_page = render_production_batch_page
    setattr(production_batch_module, marker, True)
    _APPLIED = True
    return production_batch_module
