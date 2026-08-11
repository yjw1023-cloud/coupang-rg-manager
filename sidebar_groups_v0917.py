"""RG Manager v0.9.17 grouped sidebar navigation.

Replaces the long flat sidebar radio with:
- standalone dashboard button
- four collapsible workflow groups
- automatic fallback of unknown/new menu items into Data/Admin

The page labels themselves are preserved, so existing page handlers do not change.
"""
from __future__ import annotations

import re
from typing import Iterable


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

    # Any existing menu item not explicitly known must remain reachable.
    # Data/admin is the safest fallback because these tend to be occasional tools.
    leftovers = [x for x in options if x not in used]
    data_group = next(x for x in grouped if x[0] == "📥 데이터·관리")

    # Put obviously operational pages into the closest workflow group even if their
    # exact emoji/spacing changes in a future patch.
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
            color: #334155 !important;
            padding-top: 8px !important;
            padding-bottom: 8px !important;
        }
        section[data-testid="stSidebar"] .stButton > button {
            justify-content: flex-start !important;
            text-align: left !important;
            min-height: 38px !important;
            border-radius: 9px !important;
        }
        .rg-nav-separator {
            height: 1px;
            background: #e2e8f0;
            margin: 8px 0 10px 0;
        }
        </style>
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

    if dashboard:
        dash_label = dashboard
        if st_obj.sidebar.button(
            dash_label,
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


def _extract_menu_block(source: str):
    # The base application uses one sidebar radio whose second argument is a
    # literal list of page labels. All later patch modules add/remove strings in
    # that same list before this patch runs.
    pattern = re.compile(
        r"(?P<indent>^[ \t]*)page\s*=\s*st\.sidebar\.radio\(\s*"
        r"(?P<title>['\"][^'\"]*['\"])\s*,\s*"
        r"(?P<list>\[(?:\s*['\"][^'\"]*['\"]\s*,?\s*)+\])"
        r"(?P<tail>\s*(?:,[^\)]*)?\))",
        re.MULTILINE,
    )
    return pattern.search(source)


def patch_source(source: str) -> str:
    if "_rg_menu_options_v0917" in source:
        return source

    match = _extract_menu_block(source)
    if not match:
        raise RuntimeError("v0.9.17 사이드바 메뉴 목록을 찾지 못했습니다.")

    list_text = match.group("list")
    labels = re.findall(r"['\"]([^'\"]+)['\"]", list_text)
    if not labels:
        raise RuntimeError("v0.9.17 사이드바 메뉴 항목을 읽지 못했습니다.")

    indent = match.group("indent")
    replacement = (
        f"{indent}_rg_menu_options_v0917 = {labels!r}\n"
        f"{indent}page = pnl_month_default_v0915.render_grouped_sidebar(st, _rg_menu_options_v0917)"
    )
    return source[: match.start()] + replacement + source[match.end() :]
