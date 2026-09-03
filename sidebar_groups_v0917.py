"""RG Manager v0.9.23 grouped sidebar navigation + branding.

Replaces the long flat sidebar radio with:
- standalone dashboard button
- four collapsible workflow groups
- automatic fallback of unknown/new menu items into Data/Admin

The page labels themselves are preserved, so existing page handlers do not change.
"""
from __future__ import annotations

import ast
import base64
from pathlib import Path
from typing import Iterable


def _logo_bytes() -> bytes | None:
    try:
        payload = (Path(__file__).resolve().parent / "jd_systems_logo.b64").read_text(encoding="utf-8").strip()
        if payload:
            return base64.b64decode(payload)
    except Exception:
        pass
    return None


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
            "🔗  쿠팡 API 연동",
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
    margin: 2px 0 6px 0 !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] details {
    border: 0 !important;
    background: transparent !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
    font-weight: 750 !important;
    color: #ffffff !important;
    background: rgba(255,255,255,0.055) !important;
    border: 1px solid rgba(255,255,255,0.07) !important;
    border-radius: 9px !important;
    padding: 9px 10px !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary:hover {
    background: rgba(243,25,45,0.10) !important;
    border-color: rgba(243,25,45,0.40) !important;
}
section[data-testid="stSidebar"] .stButton {
    margin: 0 0 2px 0 !important;
}
section[data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start !important;
    text-align: left !important;
    min-height: 34px !important;
    width: 100% !important;
    border-radius: 7px !important;
    background: transparent !important;
    border: 1px solid transparent !important;
    box-shadow: none !important;
    color: #dce7f8 !important;
    padding: 0.32rem 0.72rem !important;
}
section[data-testid="stSidebar"] .stButton > button p {
    color: inherit !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"],
section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-primary"] {
    background: #f3192d !important;
    border-color: #f3192d !important;
    color: #ffffff !important;
    font-weight: 750 !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="secondary"],
section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-secondary"] {
    background: transparent !important;
    border-color: transparent !important;
    color: #dce7f8 !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover,
section[data-testid="stSidebar"] .stButton > button[data-testid="stBaseButton-secondary"]:hover {
    background: rgba(243,25,45,0.12) !important;
    border-color: rgba(243,25,45,0.35) !important;
    color: #ffffff !important;
}
.rg-brand-name {
    text-align: center;
    font-size: 14px;
    line-height: 1.25;
    font-weight: 750;
    color: #ffffff;
    letter-spacing: -0.1px;
    margin: 3px 0 11px 0;
}
.rg-nav-separator {
    height: 1px;
    background: rgba(243,25,45,0.38);
    margin: 10px 0 10px 0;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _render_branding(st_obj):
    logo = _logo_bytes()
    if logo:
        try:
            st_obj.sidebar.image(logo, use_container_width=True)
        except TypeError:
            st_obj.sidebar.image(logo, use_column_width=True)
    st_obj.sidebar.markdown(
        '<div class="rg-brand-name">주식회사 제이디씨스템즈</div>',
        unsafe_allow_html=True,
    )


def _current_version() -> str:
    try:
        value = (Path(__file__).resolve().parent / "VERSION.txt").read_text(encoding="utf-8").strip()
        return value or "0.9.23"
    except Exception:
        return "0.9.23"


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
        # Dashboard is a fixed top-level entry. Keep it visually integrated with
        # the navy sidebar instead of using the red active-menu highlight.
        if st_obj.sidebar.button(
            dashboard,
            key="rg_nav_dashboard_v0917",
            use_container_width=True,
            type="secondary",
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

    st_obj.sidebar.caption(f"RG Manager v{_current_version()}")
    return current


def _find_menu_assignment(source: str):
    """Find the final literal radio menu assignment using Python AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.23 메뉴 적용 전 소스 문법 오류: {exc}") from exc

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


def _remove_legacy_sidebar_branding(source: str) -> str:
    """Remove old sidebar brand/version elements while preserving normal help captions."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    nodes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr in {"caption", "markdown", "title", "header", "subheader"}):
            continue
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "sidebar"):
            continue
        root = owner.value
        if not (isinstance(root, ast.Name) and root.id == "st"):
            continue
        if not call.args:
            continue
        arg = call.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        label = arg.value.lower()
        if (
            "rg manager" in label
            or "쿠팡 로켓그로스" in label
            or "v0." in label
            or "grouped navigation" in label
            or "monthly closing" in label
            or "legacy erp import" in label
        ):
            nodes.append(node)

    if not nodes:
        return source
    lines = source.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda x: int(x.lineno), reverse=True):
        line_start = int(node.lineno) - 1
        line_end = int(getattr(node, "end_lineno", node.lineno))
        lines[line_start:line_end] = []
    return "".join(lines)


def patch_source(source: str) -> str:
    if "_rg_menu_options_v0917" in source:
        return source

    found = _find_menu_assignment(source)
    if not found:
        raise RuntimeError("v0.9.23 사이드바 radio 메뉴 목록을 찾지 못했습니다.")
    node, labels = found
    source = _remove_legacy_sidebar_branding(source)
    found = _find_menu_assignment(source)
    if not found:
        raise RuntimeError("v0.9.23 기존 버전표시 제거 후 메뉴를 다시 찾지 못했습니다.")
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
