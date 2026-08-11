"""RG Manager v0.9.22 permanent sidebar visibility lock.

Force the native Streamlit sidebar DOM itself to remain visible even when the
browser remembers a collapsed state. Also keep initial_sidebar_state expanded
and hide collapse controls.
"""
from __future__ import annotations

import ast
import re


def apply(st_obj) -> None:
    st_obj.markdown(
        """
<style>
/* v0.9.22: force the actual sidebar element back on-screen even if the
   Streamlit client remembers it as collapsed. */
section[data-testid="stSidebar"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    transform: none !important;
    translate: none !important;
    left: 0 !important;
    margin-left: 0 !important;
    width: 300px !important;
    min-width: 300px !important;
    max-width: 300px !important;
    flex: 0 0 300px !important;
    overflow: visible !important;
}

section[data-testid="stSidebar"] > div,
section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    transform: none !important;
    width: 100% !important;
    min-width: 100% !important;
    max-width: 100% !important;
}

/* Hide every known native collapse control. */
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button,
section[data-testid="stSidebar"] button[data-testid="stSidebarCollapseButton"],
section[data-testid="stSidebar"] button[aria-label*="sidebar" i],
section[data-testid="stSidebar"] button[aria-label*="사이드바" i],
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button[data-testid="baseButton-header"],
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button[data-testid="baseButton-headerNoPadding"] {
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}

section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
    min-height: 0 !important;
    height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: hidden !important;
}

/* Hide the collapsed restore control because the sidebar is forced visible. */
div[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
    display: none !important;
    visibility: hidden !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _find_page_config(source: str):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.22 page config parse failed: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "set_page_config"):
            continue
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "st":
            return node, tree
    return None, tree


def _force_page_config(source: str) -> str:
    node, tree = _find_page_config(source)

    if node is not None:
        old = ast.get_source_segment(source, node)
        if not old:
            raise RuntimeError("v0.9.22 could not read st.set_page_config source")

        if re.search(r"initial_sidebar_state\s*=", old):
            new = re.sub(
                r"initial_sidebar_state\s*=\s*(['\"])(?:auto|collapsed|expanded)\1",
                'initial_sidebar_state="expanded"',
                old,
                count=1,
            )
            if new == old:
                new = re.sub(
                    r"initial_sidebar_state\s*=\s*[^,\)]+",
                    'initial_sidebar_state="expanded"',
                    old,
                    count=1,
                )
        else:
            pos = old.rfind(")")
            if pos < 0:
                raise RuntimeError("v0.9.22 malformed st.set_page_config call")
            before = old[:pos].rstrip()
            if before.endswith("("):
                new = before + 'initial_sidebar_state="expanded"' + old[pos:]
            else:
                new = before + ', initial_sidebar_state="expanded"' + old[pos:]

        return source.replace(old, new, 1)

    import_node = None
    for n in getattr(tree, "body", []):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.name == "streamlit" and (alias.asname or alias.name) == "st":
                    import_node = n
                    break
        if import_node is not None:
            break

    if import_node is None:
        raise RuntimeError("v0.9.22 could not locate 'import streamlit as st'")

    lines = source.splitlines(keepends=True)
    insert_at = int(getattr(import_node, "end_lineno", import_node.lineno))
    newline = "\r\n" if lines[int(import_node.lineno) - 1].endswith("\r\n") else "\n"
    lines.insert(insert_at, f'st.set_page_config(initial_sidebar_state="expanded"){newline}')
    return "".join(lines)


def patch_source(source: str) -> str:
    if "_rg_sidebar_locked_v0921" in source:
        return source
    source = _force_page_config(source)
    return "# _rg_sidebar_locked_v0921\n" + source
