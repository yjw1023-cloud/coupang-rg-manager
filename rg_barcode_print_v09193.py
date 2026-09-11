"""RG Manager v0.9.193 — 50x30 mm Rocket Growth inbound barcode labels.

This patch wraps the existing production/inbound page.  After a Coupang Rocket
Growth inbound workbook is uploaded, the same rows whose V-column inbound qty is
used for production are reused for label printing.

Key rules
- Label stock is exactly 50 mm x 30 mm.
- Print quantity defaults to the inbound quantity already entered in column V.
- Barcode master is persisted by Coupang vendorItemId (= ERP option_id), so a
  barcode only needs to be learned once.
- Barcodes can be pulled manually from Coupang's product detail API using the
  sellerProductId already present in the inbound workbook.  No network request is
  made automatically.
- A user may type/paste or correct a barcode in the grid; values used for print
  are saved back to the local barcode master.
- Printing is browser based and does not require UniLabel or an intermediate
  Excel file.  The print document uses @page size 50mm 30mm.
"""
from __future__ import annotations

import html
import importlib
import re
from typing import Any


_MARKER = "_rg_barcode_print_v09193_applied"
LABEL_WIDTH_MM = 50
LABEL_HEIGHT_MM = 30
MAX_TOTAL_LABELS = 10000

# Code 128 symbol width patterns, values 0..106.  Values 103/104/105 are
# START A/B/C and 106 is STOP.  Each digit is a bar/space module width.
_CODE128_PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _oid(value: Any) -> str:
    value = _text(value)
    if value.upper().startswith("CP-"):
        value = value[3:]
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    return value


def ensure_schema(core_module, db_path=None) -> None:
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    with core_module._conn(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS rg_barcode_master(
                vendor_item_id TEXT PRIMARY KEY,
                barcode TEXT NOT NULL,
                seller_product_id TEXT,
                product_name TEXT,
                option_name TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_rg_barcode_master_barcode
              ON rg_barcode_master(barcode);
            """
        )


def _load_master(core_module, option_ids, db_path=None) -> dict[str, dict[str, str]]:
    db = db_path or core_module.DEFAULT_DB
    ensure_schema(core_module, db)
    ids = [_oid(x) for x in option_ids if _oid(x)]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with core_module._conn(db) as con:
        rows = con.execute(
            f"""SELECT vendor_item_id,barcode,seller_product_id,product_name,
                       option_name,source,updated_at
                FROM rg_barcode_master
                WHERE vendor_item_id IN ({placeholders})""",
            ids,
        ).fetchall()
    return {
        _oid(row["vendor_item_id"]): {key: _text(row[key]) for key in row.keys()}
        for row in rows
    }


def _save_rows(core_module, rows, source="manual", db_path=None) -> int:
    db = db_path or core_module.DEFAULT_DB
    ensure_schema(core_module, db)
    now = core_module.now_iso()
    written = 0
    with core_module._conn(db) as con:
        for row in rows:
            option_id = _oid(row.get("option_id") or row.get("옵션ID"))
            barcode = _text(row.get("barcode") or row.get("바코드"))
            if not option_id or not barcode:
                continue
            if not re.fullmatch(r"[A-Za-z0-9-]{3,40}", barcode):
                raise ValueError(f"옵션ID {option_id}: 바코드에 사용할 수 없는 문자가 있습니다: {barcode}")
            con.execute(
                """INSERT INTO rg_barcode_master
                   (vendor_item_id,barcode,seller_product_id,product_name,option_name,source,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(vendor_item_id) DO UPDATE SET
                     barcode=excluded.barcode,
                     seller_product_id=COALESCE(NULLIF(excluded.seller_product_id,''),rg_barcode_master.seller_product_id),
                     product_name=COALESCE(NULLIF(excluded.product_name,''),rg_barcode_master.product_name),
                     option_name=COALESCE(NULLIF(excluded.option_name,''),rg_barcode_master.option_name),
                     source=excluded.source,
                     updated_at=excluded.updated_at""",
                (
                    option_id,
                    barcode,
                    _text(row.get("seller_product_id") or row.get("등록상품ID")),
                    _text(row.get("product_name") or row.get("상품명")),
                    _text(row.get("option_name") or row.get("옵션명")),
                    _text(source) or "manual",
                    now,
                ),
            )
            written += 1
    return written


def _extract_product_barcodes(payload: Any, fallback_seller_product_id="") -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    seller_product_id = _oid(data.get("sellerProductId") or fallback_seller_product_id)
    product_name = _text(
        data.get("sellerProductName")
        or data.get("displayProductName")
        or data.get("generalProductName")
    )
    items = data.get("items")
    if not isinstance(items, list):
        return []

    found: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rg = (
            item.get("rocketGrowthItemData")
            or item.get("rocketGrowthItem")
            or item.get("rocketGrowth")
            or {}
        )
        if not isinstance(rg, dict):
            rg = {}
        option_id = _oid(
            rg.get("vendorItemId")
            or item.get("vendorItemId")
            or rg.get("vendorInventoryItemId")
        )
        barcode = _text(rg.get("barcode") or item.get("barcode"))
        if not option_id or not barcode:
            continue
        found.append(
            {
                "option_id": option_id,
                "barcode": barcode,
                "seller_product_id": seller_product_id,
                "product_name": product_name,
                "option_name": _text(item.get("itemName")),
            }
        )
    return found


def sync_from_coupang(core_module, uploaded, target_option_ids, db_path=None) -> dict[str, Any]:
    """Fetch barcode facts only after the user explicitly clicks the sync button."""
    api = importlib.import_module("coupang_api_sync_v09140")
    credentials = api.load_credentials(core_module)
    if credentials is None:
        raise ValueError("저장된 쿠팡 API 연결정보가 없습니다. 먼저 '쿠팡 API 연동' 메뉴에서 연결정보를 저장해 주세요.")

    inbound = api.parse_rg_inbound_options(uploaded)
    target_ids = {_oid(x) for x in target_option_ids if _oid(x)}
    by_seller: dict[str, set[str]] = {}
    for row in inbound:
        option_id = _oid(row.get("vendor_item_id"))
        seller_id = _oid(row.get("seller_product_id"))
        if option_id in target_ids and seller_id:
            by_seller.setdefault(seller_id, set()).add(option_id)

    if not by_seller:
        raise ValueError("입고 Excel에서 대상 상품의 등록상품ID를 찾지 못했습니다.")

    client = api.CoupangClient(credentials)
    fetched: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    for seller_id, expected_ids in by_seller.items():
        path = f"/v2/providers/seller_api/apis/api/v1/marketplace/seller-products/{seller_id}"
        try:
            payload = client.request(path)
            for row in _extract_product_barcodes(payload, seller_id):
                option_id = _oid(row.get("option_id"))
                if option_id in target_ids:
                    fetched[option_id] = row
        except Exception as exc:
            errors.append(f"등록상품ID {seller_id}: {exc}")
            continue
        _ = expected_ids

    if fetched:
        _save_rows(core_module, list(fetched.values()), source="coupang_api", db_path=db_path)

    missing = sorted(target_ids - set(fetched))
    return {
        "requested_products": len(by_seller),
        "saved": len(fetched),
        "missing_option_ids": missing,
        "errors": errors,
    }


def _digit_run(value: str, start: int) -> int:
    i = start
    while i < len(value) and value[i].isdigit():
        i += 1
    return i - start


def _code128_values(value: str) -> list[int]:
    """Encode printable ASCII using Code128 B/C with automatic numeric compaction."""
    if not value:
        raise ValueError("빈 바코드는 인쇄할 수 없습니다.")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        raise ValueError(f"Code128로 인쇄할 수 없는 문자가 있습니다: {value}")

    codes: list[int] = [104]
    mode = "B"
    i = 0
    while i < len(value):
        run = _digit_run(value, i)
        if mode == "B":
            if run >= 4:
                if run % 2:
                    codes.append(ord(value[i]) - 32)
                    i += 1
                    run -= 1
                if run >= 4:
                    codes.append(99)
                    mode = "C"
                    continue
            codes.append(ord(value[i]) - 32)
            i += 1
            continue

        run = _digit_run(value, i)
        if run >= 2:
            codes.append(int(value[i : i + 2]))
            i += 2
            continue
        codes.append(100)
        mode = "B"

    checksum = codes[0]
    for pos, code in enumerate(codes[1:], 1):
        checksum += pos * code
    codes.append(checksum % 103)
    codes.append(106)
    return codes


def barcode_svg(value: str) -> str:
    codes = _code128_values(value)
    quiet = 10
    x = quiet
    rects = []
    for code in codes:
        pattern = _CODE128_PATTERNS[code]
        bar = True
        for width_char in pattern:
            width = int(width_char)
            if bar:
                rects.append(f'<rect x="{x}" y="0" width="{width}" height="46"/>')
            x += width
            bar = not bar
    total = x + quiet
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} 46" '
        'preserveAspectRatio="none" role="img" aria-label="barcode" '
        'shape-rendering="crispEdges">'
        + "".join(rects)
        + "</svg>"
    )


def _name_font_pt(name: str) -> float:
    n = len(name)
    if n <= 23:
        return 8.6
    if n <= 30:
        return 7.6
    if n <= 38:
        return 6.8
    return 6.0


def _label_html(row: dict[str, Any]) -> str:
    barcode = _text(row.get("바코드"))
    product = _text(row.get("상품명")) or _text(row.get("ERP 상품명"))
    option = _text(row.get("옵션명"))
    svg = barcode_svg(barcode)
    font_pt = _name_font_pt(product)
    return f"""
      <section class="label">
        <div class="bars">{svg}</div>
        <div class="barcode-text">{html.escape(barcode)}</div>
        <div class="product" style="font-size:{font_pt:.1f}pt">{html.escape(product)}</div>
        <div class="option">{html.escape(option)}</div>
        <div class="origin">MADE IN CHINA</div>
      </section>
    """


def build_print_html(rows: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for row in rows:
        qty = int(row.get("출력수량") or 0)
        if qty < 1:
            continue
        rendered = _label_html(row)
        labels.extend([rendered] * qty)
    total = len(labels)
    if total < 1:
        raise ValueError("인쇄할 라벨 수량이 없습니다.")
    if total > MAX_TOTAL_LABELS:
        raise ValueError(f"한 번에 최대 {MAX_TOTAL_LABELS:,}장까지 인쇄할 수 있습니다.")

    body = "\n".join(labels)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin:0; padding:0; background:#f3f5f7; font-family: Arial, 'Malgun Gothic', sans-serif; }}
  .toolbar {{ position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:12px;
              padding:10px 12px; background:white; border-bottom:1px solid #d9dee5; }}
  .toolbar button {{ border:0; border-radius:7px; padding:8px 16px; font-weight:700; cursor:pointer;
                     background:#1367d1; color:white; }}
  .toolbar span {{ font-size:13px; color:#4d5968; }}
  .preview {{ padding:12px; display:flex; flex-wrap:wrap; gap:10px; align-items:flex-start; }}
  .label {{ width:50mm; height:30mm; background:#fff; padding:1.0mm 2.4mm 0.9mm 2.4mm;
            display:flex; flex-direction:column; align-items:center; overflow:hidden;
            page-break-after:always; break-after:page; }}
  .bars {{ width:44.5mm; height:10.7mm; flex:0 0 10.7mm; }}
  .bars svg {{ width:100%; height:100%; display:block; fill:#000; }}
  .barcode-text {{ width:100%; height:2.7mm; line-height:2.7mm; text-align:center; font-size:6.7pt; letter-spacing:.08mm; }}
  .product {{ width:100%; height:3.7mm; line-height:3.7mm; white-space:nowrap; overflow:hidden;
              text-align:center; font-weight:400; }}
  .option {{ width:100%; height:3.6mm; line-height:3.6mm; white-space:nowrap; overflow:hidden;
             text-align:center; font-size:8.2pt; }}
  .origin {{ width:100%; margin-top:auto; height:3.7mm; line-height:3.7mm; text-align:center; font-size:8.4pt; }}
  @page {{ size:50mm 30mm; margin:0; }}
  @media print {{
    html, body {{ width:50mm; margin:0 !important; padding:0 !important; background:white; }}
    .toolbar {{ display:none !important; }}
    .preview {{ display:block; padding:0; margin:0; }}
    .label {{ margin:0; border:0; box-shadow:none; }}
  }}
</style>
</head>
<body>
  <div class="toolbar">
    <button onclick="window.print()">5×3cm 바코드 인쇄</button>
    <span>총 {total:,}장 · 프린터 설정은 용지 50×30mm / 배율 100% / 여백 없음</span>
  </div>
  <main class="preview">{body}</main>
</body>
</html>"""


def _editor_rows(core_module, batch_rows, uploaded, db_path=None):
    option_ids = [_oid(row.get("option_id")) for row in batch_rows]
    master = _load_master(core_module, option_ids, db_path)

    seller_by_option: dict[str, str] = {}
    try:
        api = importlib.import_module("coupang_api_sync_v09140")
        for row in api.parse_rg_inbound_options(uploaded):
            seller_by_option[_oid(row.get("vendor_item_id"))] = _oid(row.get("seller_product_id"))
    except Exception:
        pass

    result = []
    for row in batch_rows:
        option_id = _oid(row.get("option_id"))
        saved = master.get(option_id, {})
        qty = int(row.get("qty") or 0)
        result.append(
            {
                "선택": True,
                "상품명": _text(row.get("source_name")) or _text(row.get("erp_name")),
                "옵션명": _text(row.get("option_name")),
                "옵션ID": option_id,
                "등록상품ID": seller_by_option.get(option_id, _text(saved.get("seller_product_id"))),
                "입고수량": qty,
                "바코드": _text(saved.get("barcode")),
                "출력수량": qty,
            }
        )
    return result


def _selected_records(edited) -> list[dict[str, Any]]:
    if hasattr(edited, "to_dict"):
        records = edited.to_dict("records")
    else:
        records = list(edited or [])
    return [dict(row) for row in records if bool(row.get("선택"))]


def render_inbound_barcode_section(st, pd, core_module, batch_module, uploaded) -> None:
    try:
        parsed = batch_module.parse_production_excel(uploaded)
    except Exception:
        return
    rows = list(parsed.get("rows") or [])
    if not rows:
        return

    ensure_schema(core_module)
    st.divider()
    st.subheader("🏷️ RG 입고 바코드 라벨")
    st.caption(
        "위 입고 Excel의 옵션ID와 V열 입고수량을 그대로 사용합니다. "
        "라벨은 50×30mm이며 바코드·상품명·옵션명·MADE IN CHINA 형식으로 인쇄됩니다."
    )

    option_ids = [_oid(row.get("option_id")) for row in rows]
    master = _load_master(core_module, option_ids)
    known = sum(1 for oid in option_ids if _text(master.get(oid, {}).get("barcode")))
    c1, c2, c3 = st.columns(3)
    c1.metric("입고 상품", f"{len(rows):,}개")
    c2.metric("바코드 저장됨", f"{known:,}개")
    c3.metric("바코드 없음", f"{len(rows) - known:,}개")

    if st.button(
        "쿠팡에서 바코드 불러오기",
        key=f"rg_barcode_v09193_sync_{parsed['file_hash'][:12]}",
        help="쿠팡 API 연결정보를 사용해 현재 입고상품의 바코드만 수동 조회합니다.",
    ):
        try:
            with st.spinner("쿠팡에서 입고상품 바코드를 확인하고 있습니다..."):
                result = sync_from_coupang(core_module, uploaded, option_ids)
            if result["saved"]:
                st.success(f"바코드 {result['saved']:,}개를 저장했습니다.")
            if result["missing_option_ids"]:
                st.warning(
                    f"쿠팡 응답에서 바코드를 찾지 못한 옵션이 {len(result['missing_option_ids']):,}개 있습니다. "
                    "아래 표의 바코드 칸에 직접 입력하면 저장할 수 있습니다."
                )
            if result["errors"]:
                st.warning("일부 상품 조회 실패: " + " / ".join(result["errors"][:3]))
            st.session_state.pop(f"rg_barcode_v09193_editor_{parsed['file_hash'][:12]}", None)
            st.rerun()
        except Exception as exc:
            st.error(f"바코드 조회 실패: {exc}")

    frame = pd.DataFrame(_editor_rows(core_module, rows, uploaded))
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"rg_barcode_v09193_editor_{parsed['file_hash'][:12]}",
        disabled=["상품명", "옵션명", "옵션ID", "등록상품ID", "입고수량"],
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", width="small"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "옵션명": st.column_config.TextColumn("옵션명", width="medium"),
            "옵션ID": st.column_config.TextColumn("옵션ID", width="medium"),
            "등록상품ID": st.column_config.TextColumn("등록상품ID", width="medium"),
            "입고수량": st.column_config.NumberColumn("입고수량", format="%d"),
            "바코드": st.column_config.TextColumn("바코드", width="medium", help="쿠팡 RG 바코드 숫자/문자열"),
            "출력수량": st.column_config.NumberColumn("출력수량", min_value=0, max_value=5000, step=1, format="%d"),
        },
    )

    selected = _selected_records(edited)
    missing = [row for row in selected if int(row.get("출력수량") or 0) > 0 and not _text(row.get("바코드"))]
    total_labels = sum(max(0, int(row.get("출력수량") or 0)) for row in selected)

    b1, b2 = st.columns([1, 2])
    if b1.button(
        "입력한 바코드 저장",
        key=f"rg_barcode_v09193_save_{parsed['file_hash'][:12]}",
    ):
        try:
            save_rows = [
                {
                    "option_id": row.get("옵션ID"),
                    "barcode": row.get("바코드"),
                    "seller_product_id": row.get("등록상품ID"),
                    "product_name": row.get("상품명"),
                    "option_name": row.get("옵션명"),
                }
                for row in selected
                if _text(row.get("바코드"))
            ]
            written = _save_rows(core_module, save_rows, source="manual")
            st.success(f"바코드 {written:,}개를 저장했습니다.")
        except Exception as exc:
            st.error(f"바코드 저장 실패: {exc}")

    b2.caption(f"현재 선택 기준 인쇄 예정: {len(selected):,}개 상품 / 총 {total_labels:,}장")

    if missing:
        names = ", ".join(_text(row.get("상품명")) for row in missing[:5])
        st.warning(f"바코드가 없는 선택 상품이 {len(missing):,}개 있습니다: {names}")
        return
    if total_labels < 1:
        st.info("인쇄할 상품을 선택하고 출력수량을 1개 이상 입력해 주세요.")
        return
    if total_labels > MAX_TOTAL_LABELS:
        st.error(f"한 번에 최대 {MAX_TOTAL_LABELS:,}장까지 인쇄할 수 있습니다.")
        return

    try:
        print_html = build_print_html(selected)
        components = importlib.import_module("streamlit.components.v1")
        components.html(print_html, height=390, scrolling=True)
    except Exception as exc:
        st.error(f"라벨 미리보기 생성 실패: {exc}")


def apply(core_module, production_batch_module) -> None:
    """Append barcode printing to the existing RG production/inbound page."""
    if getattr(production_batch_module, _MARKER, False):
        return
    previous_render = production_batch_module.render_production_batch_page

    def render_production_batch_page(*args, **kwargs):
        result = previous_render(*args, **kwargs)
        st = kwargs.get("st")
        pd = kwargs.get("pd")
        if st is None or pd is None:
            return result
        try:
            uploaded = st.session_state.get("production_batch_v095_upload")
        except Exception:
            uploaded = None
        if uploaded is not None:
            render_inbound_barcode_section(
                st=st,
                pd=pd,
                core_module=core_module,
                batch_module=production_batch_module,
                uploaded=uploaded,
            )
        return result

    production_batch_module.render_production_batch_page = render_production_batch_page
    setattr(production_batch_module, _MARKER, True)
