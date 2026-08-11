"""RG Manager v0.9.17 grouped sidebar navigation.

Replaces the long flat sidebar radio with:
- standalone dashboard button
- four collapsible workflow groups
- automatic fallback of unknown/new menu items into Data/Admin
- JD SYSTEMS branding in the sidebar header

The page labels themselves are preserved, so existing page handlers do not change.
"""
from __future__ import annotations

import ast
from typing import Iterable


_LOGO_DATA_URI = "data:image/png;base64,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"


_GROUPS = [
    (
        "💰 손익·정산",
        [
            "📈  잠정손익",
            "✅  상품 확정손익",
            "📒  월 결산",
            "🔍  손익차이분석",
            "📄  자료별 잠정손익",
        ],
    ),
    (
        "📦 재고·생산",
        [
            "📦  재고관리",
            "🏭  생산자료",
            "↩️  반품관리",
        ],
    ),
    (
        "🛒 매입·상품",
        [
            "🧾  매입관리",
            "🗂️  매입이력",
            "📋  품목관리",
            "🏷️  상품·원가",
        ],
    ),
    (
        "📥 데이터·관리",
        [
            "📥  기존ERP 이관",
        ],
    ),
]

_DASHBOARD_HINTS = ("대시보드", "dashboard")
_DATA_HINTS = (
    "업로드",
    "자료",
    "데이터",
    "이관",
    "업데이트",
    "설정",
    "관리",
    "진단",
    "백업",
    "복원",
)


def _clean_label(value) -> str:
    return str(value or "").strip()


def _find_dashboard(options: Iterable[str]) -> str | None:
    options = list(options)
    for option in options:
        low = option.lower()
        if any(h in low for h in _DASHBOARD_HINTS):
            return option
    return options[0] if options else None


def _group_options(options: list[str]):
    present = set(options)
    dashboard = _find_dashboard(options)
    used = {dashboard} if dashboard else set()
    grouped = []

    for title, preferred in _GROUPS:
        items = [x for x in preferred if x in present and x not in used]
        used.update(items)
        grouped.append([title, items])

    leftovers = [x for x in options if x not in used]
    data_group = next(x for x in grouped if x[0] == "📥 데이터·관리")

    for item in leftovers[:]:
        text = item.lower()
        if "반품" in text:
            next(x for x in grouped if x[0] == "📦 재고·생산")[1].append(item)
            leftovers.remove(item)
        elif "매입" in text or "품목" in text or "상품·원가" in text or "상품/원가" in text:
            next(x for x in grouped if x[0] == "🛒 매입·상품")[1].append(item)
            leftovers.remove(item)
        elif any(k in text for k in ("손익", "결산")):
            next(x for x in grouped if x[0] == "💰 손익·정산")[1].append(item)
            leftovers.remove(item)
        elif any(k in text for k in _DATA_HINTS):
            data_group[1].append(item)
            leftovers.remove(item)

    data_group[1].extend(leftovers)
    return dashboard, [(title, items) for title, items in grouped if items]


def _inject_css(st_obj):
    st_obj.sidebar.markdown(
        """
        <style>
        section[data-testid="stSidebar"] div[data-testid="stExpander"] {
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            margin: 2px 0 5px 0 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stExpander"] details {
            border: 0 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
            font-weight: 750 !important;
            color: #c91528 !important;
            padding-top: 8px !important;
            padding-bottom: 8px !important;
        }
        section[data-testid="stSidebar"] .stButton > button {
            justify-content: flex-start !important;
            text-align: left !important;
            min-height: 38px !important;
            border-radius: 9px !important;
        }
        section[data-testid="stSidebar"] .stButton > button[kind="primary"],
        section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
            background: #f3192d !important;
            border-color: #f3192d !important;
            color: #ffffff !important;
            font-weight: 750 !important;
        }
        section[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover,
        section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-secondary"]:hover {
            border-color: #f3192d !important;
            color: #c91528 !important;
            background: #fff5f6 !important;
        }
        .rg-brand-wrap {
            margin: 2px 0 10px 0;
            text-align: center;
        }
        .rg-brand-logo {
            display: block;
            width: 100%;
            max-width: 240px;
            height: auto;
            margin: 0 auto 7px auto;
            border-radius: 6px;
        }
        .rg-brand-name {
            font-size: 14px;
            font-weight: 750;
            color: #334155;
            letter-spacing: -0.1px;
            margin: 0 0 8px 0;
        }
        .rg-nav-separator {
            height: 1px;
            background: #f3c3c9;
            margin: 8px 0 10px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_branding(st_obj):
    st_obj.sidebar.markdown(
        f"""
        <div class="rg-brand-wrap">
            <img src="{_LOGO_DATA_URI}" class="rg-brand-logo" alt="JD SYSTEMS" />
            <div class="rg-brand-name">주식회사 제이디씨스템즈</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(st_obj, options: list[str], default_page: str | None = None) -> str:
    """Render grouped sidebar and return the canonical existing page label."""
    options = [_clean_label(x) for x in options if _clean_label(x)]
    if not options:
        return ""

    dashboard, groups = _group_options(options)
    state_key = "_rg_sidebar_page_v0917"
    current = st_obj.session_state.get(state_key)
    if current not in options:
        current = default_page if default_page in options else dashboard or options[0]
        st_obj.session_state[state_key] = current

    _inject_css(st_obj)
    _render_branding(st_obj)

    if dashboard:
        if st_obj.sidebar.button(
            dashboard,
            key="rg_nav_dashboard_v0917",
            use_container_width=True,
            type="primary" if current == dashboard else "secondary",
        ):
            st_obj.session_state[state_key] = dashboard
            current = dashboard

    st_obj.sidebar.markdown('<div class="rg-nav-separator"></div>', unsafe_allow_html=True)

    for group_idx, (title, items) in enumerate(groups):
        expanded = current in items
        with st_obj.sidebar.expander(title, expanded=expanded):
            for item_idx, item in enumerate(items):
                active = current == item
                if st_obj.button(
                    item,
                    key=f"rg_nav_{group_idx}_{item_idx}_v0917",
                    use_container_width=True,
                    type="primary" if active else "secondary",
                ):
                    st_obj.session_state[state_key] = item
                    current = item

    return current


def _find_menu_assignment(source: str):
    """Find the final literal radio menu assignment using Python AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.17 메뉴 적용 전 소스 문법 오류: {exc}") from exc

    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "page":
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "radio"):
            continue

        option_list = None
        for arg in call.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                vals = []
                ok = True
                for elt in arg.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        vals.append(elt.value)
                    else:
                        ok = False
                        break
                if ok and vals:
                    option_list = vals
                    break
        if not option_list:
            for kw in call.keywords:
                if kw.arg in {"options", "items"} and isinstance(kw.value, (ast.List, ast.Tuple)):
                    vals = []
                    ok = True
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            vals.append(elt.value)
                        else:
                            ok = False
                            break
                    if ok and vals:
                        option_list = vals
                        break
        if not option_list:
            continue

        score = len(option_list)
        joined = " ".join(option_list)
        if "대시보드" in joined:
            score += 20
        if "재고관리" in joined:
            score += 10
        if "잠정손익" in joined or "판매·손익" in joined:
            score += 10
        candidates.append((score, node, option_list))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][2]


def patch_source(source: str) -> str:
    if "_rg_menu_options_v0917" in source:
        return source

    found = _find_menu_assignment(source)
    if not found:
        raise RuntimeError("v0.9.17 사이드바 radio 메뉴 목록을 찾지 못했습니다.")
    node, labels = found

    lines = source.splitlines(keepends=True)
    start_idx = int(node.lineno) - 1
    end_idx = int(getattr(node, "end_lineno", node.lineno))
    original_first = lines[start_idx]
    indent = original_first[: len(original_first) - len(original_first.lstrip())]
    newline = "\r\n" if original_first.endswith("\r\n") else "\n"
    replacement = (
        f"{indent}_rg_menu_options_v0917 = {labels!r}{newline}"
        f"{indent}page = pnl_month_default_v0915.render_grouped_sidebar(st, _rg_menu_options_v0917){newline}"
    )
    lines[start_idx:end_idx] = [replacement]
    return "".join(lines)
