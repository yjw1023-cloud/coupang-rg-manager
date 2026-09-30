"""RG Manager v0.9.238 — RG product + BOM Excel registration workflow.

Excel columns:
- B + C: product name
- G: Coupang option ID (ERP product code)
- AB: product barcode

Workflow:
upload -> parse/preview -> infer raw-material BOM candidate -> user edit ->
review -> explicit confirmation -> transactional product/BOM/barcode registration.
"""
from __future__ import annotations

import io
import hashlib
import importlib
import re
from difflib import SequenceMatcher
from typing import Any

PAGE_LABEL = "RG상품/BOM 등록"
_REVIEW_KEY = "rg_product_bom_v09238_review"
_REVIEW_FILE_KEY = "rg_product_bom_v09242_review_file"
_EXTRA_BOM_KEY = "rg_product_bom_v09247_extra_bom"


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _oid(v: Any) -> str:
    s = _text(v)
    if s.upper().startswith("CP-"):
        s = s[3:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _parse_excel(uploaded):
    openpyxl = importlib.import_module("openpyxl")
    data = uploaded.getvalue() if hasattr(uploaded, "getvalue") else uploaded.read()
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    try:
        ws = wb.active
        rows = []
        seen = set()
        for r in range(1, ws.max_row + 1):
            b = _text(ws.cell(r, 2).value)
            c = _text(ws.cell(r, 3).value)
            option_id = _oid(ws.cell(r, 7).value)
            barcode = _text(ws.cell(r, 28).value)
            if not option_id or not option_id.isdigit():
                continue
            # Coupang template/example rows can have a numeric G value but an
            # instructional sentence in AB (e.g. the Snoopy sample row).
            # Treat only compact ASCII barcode values as real product rows.
            if not barcode or not re.fullmatch(r"[A-Za-z0-9-]{3,40}", barcode):
                continue
            if option_id in seen:
                continue
            seen.add(option_id)
            name = f"{b} / {c}" if b and c else (b or c)
            rows.append(
                {
                    "등록": True,
                    "상품명": name or f"옵션ID {option_id}",
                    "옵션ID": option_id,
                    "바코드": barcode,
                    "BOM 구성품": "",
                    "소요수량": 1,
                    "원본행": r,
                }
            )
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if not rows:
        raise ValueError("G열에서 숫자형 옵션ID를 찾지 못했습니다. 로켓그로스 상품 Excel인지 확인해 주세요.")
    return rows


def _load_raw_products(core):
    core.init_db(core.DEFAULT_DB)
    with core._conn(core.DEFAULT_DB) as con:
        rows = con.execute(
            """SELECT id,item_code,name,unit_cost
               FROM products
               WHERE COALESCE(active,1)=1 AND item_type='raw'
               ORDER BY name,item_code"""
        ).fetchall()
    return [dict(r) for r in rows]


def _existing_options(core, option_ids):
    ids = [_oid(x) for x in option_ids if _oid(x)]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with core._conn(core.DEFAULT_DB) as con:
        rows = con.execute(
            f"""SELECT id,option_id,item_code,name
                FROM products
                WHERE CAST(option_id AS TEXT) IN ({placeholders})""",
            ids,
        ).fetchall()
    return {_oid(r["option_id"]): dict(r) for r in rows}


def _norm_name(v: Any) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", " ", _text(v).lower())
    return " ".join(s.split())


def _compact_name(v: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _text(v).lower())


def _korean_tokens(v: Any):
    text = _norm_name(v)
    stop = {
        "개", "세트", "1개", "2개", "3개", "4개", "5개", "10개",
        "블랙", "화이트", "실버", "그레이", "우드", "투명", "레드", "오렌지",
        "free", "jd", "xl", "s", "m", "l",
        "미니", "휴대용", "다용도", "고급", "대형", "소형", "세트",
        "거치대", "홀더", "받침대", "케이스", "보관함", "용기", "도구",
    }
    return [x for x in text.split() if len(x) >= 2 and x not in stop]


_SEMANTIC_GROUPS = {
    "수모": ("수영모자", "수모"),
    "비누": ("비누",),
    "거치": ("거치대", "홀더", "받침대"),
    "스텐": ("스텐", "스테인리스", "스테인레스"),
    "캔오프너": ("캔오프너", "캔따개"),
    "지퍼백": ("지퍼백", "비닐백"),
    "브러쉬": ("브러쉬", "브러시", "솔"),
    "철브러쉬": ("철브러쉬", "철솔", "쇠솔", "와이어브러쉬", "와이어브러시"),
    "방향제": ("방향제", "디퓨저"),
    "공병": ("공병", "용기", "리필병"),
    "스퀴지": ("스퀴지", "헤라"),
    "테이블보": ("테이블보", "식탁보"),
    "네임택": ("네임택", "라벨태그", "이름표"),
    "빗": ("빗", "헤어콤", "콤"),
    "티슈": ("티슈", "와이프"),
    "실리콘": ("실리콘",),
}


def _semantic_terms(v: Any):
    compact = _compact_name(v)
    out = set()
    for canon, variants in _SEMANTIC_GROUPS.items():
        if any(_compact_name(x) in compact for x in variants):
            out.add(canon)
    return out


def _distinctive_overlap(product_name, raw_name):
    a_tokens = set(_korean_tokens(product_name))
    b_compact = _compact_name(raw_name)
    b_tokens = set(_korean_tokens(raw_name))
    a_compact = _compact_name(product_name)

    matches = set()
    for token in a_tokens:
        if token in b_tokens or _compact_name(token) in b_compact:
            matches.add(token)
    for token in b_tokens:
        if _compact_name(token) in a_compact:
            matches.add(token)

    sem_a = _semantic_terms(product_name)
    sem_b = _semantic_terms(raw_name)
    semantic_matches = sem_a & sem_b

    # "거치" alone is too generic to prove a BOM match.
    meaningful_semantic = {x for x in semantic_matches if x not in {"거치", "브러쉬"}}
    return matches, meaningful_semantic, semantic_matches


def _similarity_score(product_name, raw_name):
    target = _norm_name(product_name)
    cand = _norm_name(raw_name)
    if not target or not cand:
        return 0.0

    target_compact = _compact_name(product_name)
    cand_compact = _compact_name(raw_name)
    lexical, meaningful_semantic, all_semantic = _distinctive_overlap(product_name, raw_name)

    # Do not let a generic word such as 거치대/홀더 alone auto-match unrelated
    # products (e.g. 비누거치대 -> 면도기거치대).
    has_distinctive = bool(lexical or meaningful_semantic)
    if not has_distinctive:
        return 0.0

    contains = 1.0 if (
        len(cand_compact) >= 4 and cand_compact in target_compact
        or len(target_compact) >= 4 and target_compact in cand_compact
    ) else 0.0

    seq = SequenceMatcher(None, target_compact, cand_compact).ratio()
    lexical_score = min(1.0, len(lexical) / 2.0)
    semantic_score = min(1.0, len(all_semantic) / 2.0)

    return min(
        1.0,
        contains * 0.20
        + lexical_score * 0.45
        + semantic_score * 0.25
        + seq * 0.10,
    )


def _candidate_label(row):
    return f"{_text(row.get('item_code'))} | {_text(row.get('name'))}"


def _rank_components(product_name, raw_rows, limit=3):
    scored = []
    for raw in raw_rows:
        score = _similarity_score(product_name, raw.get("name"))
        scored.append((score, _candidate_label(raw)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:limit]


def _infer_component(product_name, raw_rows):
    ranked = _rank_components(product_name, raw_rows, 3)
    if not ranked:
        return ""
    score, label = ranked[0]
    # Conservative auto-pick; lower-confidence matches remain recommendations
    # for manual confirmation instead of being silently saved.
    return label if score >= 0.62 else ""



def _prepare_rows(core, parsed):
    raw_rows = _load_raw_products(core)
    existing = _existing_options(core, [r["옵션ID"] for r in parsed])
    for row in parsed:
        row["상태"] = "이미 등록됨" if row["옵션ID"] in existing else "신규"
        if row["상태"] != "신규":
            row["등록"] = False
        ranked = _rank_components(row["상품명"], raw_rows, 3)
        row["BOM 구성품"] = _infer_component(row["상품명"], raw_rows)
        for idx in range(3):
            row[f"추천{idx + 1}"] = ranked[idx][1] if idx < len(ranked) else ""
    return parsed, raw_rows


def _component_map(raw_rows):
    return {_candidate_label(r): r for r in raw_rows}


def _records(value):
    if hasattr(value, "to_dict"):
        return [dict(x) for x in value.to_dict("records")]
    return [dict(x) for x in (value or [])]


def _validate_review(edited, raw_rows, extra_bom_rows=None):
    cmap = _component_map(raw_rows)
    extra_bom_rows = extra_bom_rows or {}
    selected = []
    errors = []

    for row in _records(edited):
        if not bool(row.get("등록")):
            continue

        name = _text(row.get("상품명"))
        oid = _oid(row.get("옵션ID"))
        barcode = _text(row.get("바코드"))

        if not name:
            errors.append(f"옵션ID {oid}: 상품명이 없습니다.")
        if not oid or not oid.isdigit():
            errors.append(f"{name or oid}: 옵션ID가 올바르지 않습니다.")
        if not barcode:
            errors.append(f"{name or oid}: 바코드가 없습니다.")

        bom_lines = []

        primary_label = _text(row.get("BOM 구성품"))
        try:
            primary_qty = int(row.get("소요수량") or 0)
        except Exception:
            primary_qty = 0

        if primary_label:
            if primary_label not in cmap:
                errors.append(f"{name or oid}: 기본 BOM 구성품을 다시 선택해 주세요.")
            elif primary_qty < 1:
                errors.append(f"{name or oid}: 기본 BOM 소요수량은 1 이상이어야 합니다.")
            else:
                component = cmap[primary_label]
                bom_lines.append(
                    {
                        "BOM 구성품": primary_label,
                        "component_id": int(component["id"]),
                        "소요수량": primary_qty,
                    }
                )

        for idx, extra in enumerate(extra_bom_rows.get(oid, []) or [], 1):
            label = _text(extra.get("BOM 구성품"))
            try:
                qty = int(extra.get("소요수량") or 0)
            except Exception:
                qty = 0

            if not label:
                errors.append(f"{name or oid}: 추가 BOM {idx}의 구성품을 선택해 주세요.")
                continue
            if label not in cmap:
                errors.append(f"{name or oid}: 추가 BOM {idx}의 구성품을 다시 선택해 주세요.")
                continue
            if qty < 1:
                errors.append(f"{name or oid}: 추가 BOM {idx}의 소요수량은 1 이상이어야 합니다.")
                continue

            component = cmap[label]
            bom_lines.append(
                {
                    "BOM 구성품": label,
                    "component_id": int(component["id"]),
                    "소요수량": qty,
                }
            )

        if not bom_lines:
            errors.append(f"{name or oid}: BOM 구성품을 하나 이상 선택해 주세요.")
            continue

        component_ids = [x["component_id"] for x in bom_lines]
        if len(component_ids) != len(set(component_ids)):
            errors.append(f"{name or oid}: 같은 BOM 구성품이 중복 입력되어 있습니다.")
            continue

        if name and oid and oid.isdigit() and barcode:
            selected.append(
                {
                    "상품명": name,
                    "옵션ID": oid,
                    "바코드": barcode,
                    "BOM": bom_lines,
                }
            )

    if not selected and not errors:
        errors.append("등록할 신규상품을 하나 이상 선택해 주세요.")
    return selected, errors


def _existing_barcode_updates(core, parsed_rows):
    """Return only Excel rows whose G option_id already exists in ERP.

    Existing ERP products are never created here. AB barcode is upserted into the
    shared barcode master only when both option_id and barcode are present.
    """
    option_ids = [_oid(r.get("옵션ID")) for r in parsed_rows if _oid(r.get("옵션ID"))]
    existing = _existing_options(core, option_ids)
    updates = []
    for row in parsed_rows:
        oid = _oid(row.get("옵션ID"))
        barcode = _text(row.get("바코드"))
        if not oid or oid not in existing or not barcode:
            continue
        product = existing[oid]
        updates.append(
            {
                "옵션ID": oid,
                "상품명": _text(product.get("name")) or _text(row.get("상품명")),
                "바코드": barcode,
            }
        )
    return updates


def _apply_existing_barcodes(core, updates):
    barcode_mod = importlib.import_module("rg_barcode_print_v09193")
    if not updates:
        return 0
    payload = [
        {
            "option_id": r["옵션ID"],
            "barcode": r["바코드"],
            "product_name": r["상품명"],
        }
        for r in updates
    ]
    return barcode_mod._save_rows(core, payload, source="rg_product_bom_existing")


def _register(core, rows):
    barcode_mod = importlib.import_module("rg_barcode_print_v09193")
    barcode_mod.ensure_schema(core)
    now = core.now_iso()

    option_ids = [r["옵션ID"] for r in rows]
    existing = _existing_options(core, option_ids)
    if existing:
        names = ", ".join(existing.keys())
        raise ValueError(f"확정 직전에 이미 등록된 옵션ID가 확인되었습니다: {names}")

    created = []
    with core._conn(core.DEFAULT_DB) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                oid = row["옵션ID"]
                item_code = f"CP-{oid}"
                if con.execute("SELECT 1 FROM products WHERE item_code=?", (item_code,)).fetchone():
                    raise ValueError(f"이미 사용 중인 상품코드입니다: {oid}")

                cur = con.execute(
                    """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                       VALUES(?,?,?,'finished',0,1,?)""",
                    (item_code, oid, row["상품명"], now),
                )
                parent_id = int(cur.lastrowid)

                for bom in row.get("BOM") or []:
                    con.execute(
                        """INSERT INTO bom_items(parent_product_id,component_product_id,qty_per)
                           VALUES(?,?,?)""",
                        (parent_id, int(bom["component_id"]), int(bom["소요수량"])),
                    )

                con.execute(
                    """INSERT INTO rg_barcode_master
                       (vendor_item_id,barcode,seller_product_id,product_name,option_name,source,updated_at)
                       VALUES(?,?,?,?,?,'rg_product_bom',?)
                       ON CONFLICT(vendor_item_id) DO UPDATE SET
                         barcode=excluded.barcode,
                         product_name=excluded.product_name,
                         source=excluded.source,
                         updated_at=excluded.updated_at""",
                    (oid, row["바코드"], "", row["상품명"], "", now),
                )
                created.append({"id": parent_id, **row})
            con.commit()
        except Exception:
            con.rollback()
            raise
    return created


def render_page(st, core):
    pd = importlib.import_module("pandas")

    st.title("RG상품/BOM 등록")
    st.caption("로켓그로스 신규상품과 BOM을 Excel에서 읽어 확인 후 일괄 등록합니다.")

    st.info(
        "Excel 기준: B열+C열=상품명 · G열=옵션ID(상품코드) · AB열=바코드. "
        "AB열이 실제 바코드가 아닌 설명문인 쿠팡 예시행은 자동 제외합니다. "
        "BOM 구성품과 수량은 자동 추론 후 반드시 직접 확인할 수 있습니다."
    )

    uploaded = st.file_uploader(
        "RG 상품 Excel 업로드",
        type=["xlsx"],
        key="rg_product_bom_v09238_upload",
        help="쿠팡 로켓그로스 상품 Excel을 선택하세요.",
    )
    if uploaded is None:
        st.caption("Excel을 업로드하면 신규상품과 BOM 후보를 읽어 아래에 표시합니다.")
        return

    file_bytes = uploaded.getvalue()
    file_fp = hashlib.sha256(file_bytes).hexdigest()[:16]
    previous_fp = st.session_state.get("rg_product_bom_v09242_file_fp")
    if previous_fp != file_fp:
        st.session_state["rg_product_bom_v09242_file_fp"] = file_fp
        st.session_state.pop(_REVIEW_KEY, None)
        st.session_state.pop(_REVIEW_FILE_KEY, None)
        st.session_state[_EXTRA_BOM_KEY] = {}

    try:
        parsed = _parse_excel(uploaded)
        prepared, raw_rows = _prepare_rows(core, parsed)
    except Exception as exc:
        st.error(f"Excel 확인 실패: {exc}")
        return

    new_count = sum(1 for r in prepared if r["상태"] == "신규")
    existing_count = len(prepared) - new_count
    c1, c2, c3 = st.columns(3)
    c1.metric("Excel 상품", f"{len(prepared):,}개")
    c2.metric("신규 등록대상", f"{new_count:,}개")
    c3.metric("이미 등록됨", f"{existing_count:,}개")

    barcode_updates = _existing_barcode_updates(core, prepared)
    if barcode_updates:
        st.subheader("기존 ERP 상품 바코드 업데이트")
        st.caption(
            "G열 옵션ID가 ERP와 정확히 일치하는 기존 상품만 표시합니다. "
            "ERP에 없는 옵션ID는 무시하며 새 상품을 만들지 않습니다."
        )
        barcode_df = pd.DataFrame(
            [
                {
                    "상품명": r["상품명"],
                    "옵션ID": r["옵션ID"],
                    "AB열 바코드": r["바코드"],
                }
                for r in barcode_updates
            ]
        )
        st.dataframe(barcode_df, use_container_width=True, hide_index=True)
        if st.button(
            f"기존 상품 바코드 {len(barcode_updates):,}개 ERP에 반영",
            type="primary",
            key="rg_product_bom_v09239_existing_barcode_apply",
        ):
            try:
                written = _apply_existing_barcodes(core, barcode_updates)
                st.success(f"기존 ERP 상품 바코드 {written:,}개를 반영했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"기존 상품 바코드 반영 실패: {exc}")

    st.subheader("상품 및 BOM 확인")
    st.caption("등록할 상품만 체크하고, 자동 추론된 BOM 구성품과 소요수량이 맞는지 수정하세요.")

    raw_options = [""] + [_candidate_label(r) for r in raw_rows]
    frame = pd.DataFrame(prepared)[
        ["등록", "상품명", "옵션ID", "바코드", "BOM 구성품", "소요수량", "추천1", "추천2", "추천3", "상태"]
    ]
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=min(760, max(300, 38 * (min(len(frame), 18) + 1))),
        key=f"rg_product_bom_v09242_editor_{file_fp}",
        disabled=["옵션ID", "추천1", "추천2", "추천3", "상태"],
        column_config={
            "등록": st.column_config.CheckboxColumn("등록", width="small"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "옵션ID": st.column_config.TextColumn("옵션ID", width="medium"),
            "바코드": st.column_config.TextColumn("바코드", width="medium"),
            "BOM 구성품": st.column_config.SelectboxColumn(
                "BOM 구성품", options=raw_options, width="large",
                help="ERP 자체창고 기초상품 중에서 선택합니다.",
            ),
            "소요수량": st.column_config.NumberColumn(
                "소요수량", min_value=1, step=1, format="%d", width="small"
            ),
            "추천1": st.column_config.TextColumn("추천 1", width="large"),
            "추천2": st.column_config.TextColumn("추천 2", width="large"),
            "추천3": st.column_config.TextColumn("추천 3", width="large"),
            "상태": st.column_config.TextColumn("상태", width="small"),
        },
    )

    if not raw_rows:
        st.error("ERP에 등록된 자체창고 기초상품(raw)이 없습니다. BOM 구성품을 먼저 등록해 주세요.")
        return

    # A finished RG product may require multiple raw components.
    # The main grid is BOM line 1; additional lines are maintained here.
    current_records = _records(edited)
    selectable_products = [
        r for r in current_records
        if bool(r.get("등록")) and _oid(r.get("옵션ID"))
    ]
    extra_store = st.session_state.setdefault(_EXTRA_BOM_KEY, {})
    file_extra_store = extra_store.setdefault(file_fp, {})

    if selectable_products:
        st.markdown("### 추가 BOM 구성품")
        st.caption(
            "한 상품에 구성품이 2개 이상이면 상품을 선택한 뒤 '+ BOM 줄 추가'를 누르세요. "
            "필요한 만큼 여러 줄을 추가할 수 있습니다."
        )
        product_labels = {
            _oid(r.get("옵션ID")): f"{_text(r.get('상품명'))} · {_oid(r.get('옵션ID'))}"
            for r in selectable_products
        }
        target_oid = st.selectbox(
            "추가 BOM을 넣을 상품",
            options=list(product_labels.keys()),
            format_func=lambda x: product_labels.get(x, x),
            key=f"rg_product_bom_v09247_extra_target_{file_fp}",
        )

        add_col, _ = st.columns([1, 3])
        if add_col.button(
            "+ BOM 줄 추가",
            use_container_width=True,
            key=f"rg_product_bom_v09247_add_{file_fp}_{target_oid}",
        ):
            file_extra_store.setdefault(target_oid, []).append(
                {"BOM 구성품": "", "소요수량": 1}
            )
            extra_store[file_fp] = file_extra_store
            st.session_state[_EXTRA_BOM_KEY] = extra_store
            st.rerun()

        for oid in list(file_extra_store.keys()):
            if oid not in product_labels:
                continue
            lines = file_extra_store.get(oid) or []
            if not lines:
                continue

            st.markdown(f"**{product_labels[oid]}**")
            remove_indexes = []
            for idx, line in enumerate(lines):
                c1, c2, c3 = st.columns([5, 1.3, 1])
                current_label = _text(line.get("BOM 구성품"))
                try:
                    current_index = raw_options.index(current_label) if current_label in raw_options else 0
                except Exception:
                    current_index = 0

                selected_component = c1.selectbox(
                    f"BOM 구성품 {idx + 2}",
                    options=raw_options,
                    index=current_index,
                    key=f"rg_product_bom_v09247_comp_{file_fp}_{oid}_{idx}",
                )
                qty = c2.number_input(
                    f"수량 {idx + 2}",
                    min_value=1,
                    step=1,
                    value=max(1, int(line.get("소요수량") or 1)),
                    key=f"rg_product_bom_v09247_qty_{file_fp}_{oid}_{idx}",
                )
                if c3.button(
                    "삭제",
                    key=f"rg_product_bom_v09247_del_{file_fp}_{oid}_{idx}",
                    use_container_width=True,
                ):
                    remove_indexes.append(idx)

                line["BOM 구성품"] = selected_component
                line["소요수량"] = int(qty)

            for idx in reversed(remove_indexes):
                lines.pop(idx)
            file_extra_store[oid] = lines
            if remove_indexes:
                extra_store[file_fp] = file_extra_store
                st.session_state[_EXTRA_BOM_KEY] = extra_store
                st.rerun()

    if st.button("등록 내용 확인", type="primary", use_container_width=True):
        review, errors = _validate_review(edited, raw_rows, file_extra_store)
        if errors:
            for msg in errors[:15]:
                st.error(msg)
            st.session_state.pop(_REVIEW_KEY, None)
        else:
            # Recheck duplicates immediately before the confirmation step.
            dup = _existing_options(core, [r["옵션ID"] for r in review])
            if dup:
                st.error("이미 ERP에 등록된 옵션ID가 포함되어 있습니다: " + ", ".join(dup.keys()))
                st.session_state.pop(_REVIEW_KEY, None)
            else:
                st.session_state[_REVIEW_KEY] = review
                st.session_state[_REVIEW_FILE_KEY] = file_fp
                st.rerun()

    review = st.session_state.get(_REVIEW_KEY)
    if st.session_state.get(_REVIEW_FILE_KEY) != file_fp:
        review = None
    if not review:
        return

    st.divider()
    st.subheader("최종 등록 확인")
    st.caption("아래 내용은 아직 DB에 등록되지 않았습니다. 확인 후 확정 버튼을 눌러야 등록됩니다.")
    review_rows = []
    for r in review:
        for idx, bom in enumerate(r.get("BOM") or [], 1):
            review_rows.append(
                {
                    "상품명": r["상품명"],
                    "옵션ID": r["옵션ID"],
                    "바코드": r["바코드"],
                    "BOM 순번": idx,
                    "BOM 구성품": bom["BOM 구성품"],
                    "소요수량": int(bom["소요수량"]),
                }
            )
    review_df = pd.DataFrame(review_rows)
    st.dataframe(review_df, use_container_width=True, hide_index=True)

    b1, b2 = st.columns(2)
    if b1.button("수정하러 돌아가기", use_container_width=True):
        st.session_state.pop(_REVIEW_KEY, None)
        st.rerun()

    if b2.button("상품과 BOM 최종 등록", type="primary", use_container_width=True):
        try:
            created = _register(core, review)
            st.session_state.pop(_REVIEW_KEY, None)
            total_bom = sum(len(r.get("BOM") or []) for r in created)
            st.success(f"등록 완료: 신규 RG 상품 {len(created):,}개, BOM 구성품 {total_bom:,}줄과 바코드를 저장했습니다. 바코드는 바코드 인쇄 메뉴에서도 즉시 사용됩니다.")
            st.rerun()
        except Exception as exc:
            st.error(f"등록 실패: {exc}")
