"""RG Manager v0.9.21 permanent sidebar lock.

Goals:
- force Streamlit to start with the sidebar expanded
- hide the sidebar collapse control so the menu cannot become unreachable again
- keep the change isolated to source patching + CSS
"""
from __future__ import annotations

import ast
import re


def apply(st_obj) -> None:
    """Hide sidebar collapse controls once the sidebar is visible."""
    st_obj.markdown(
        """
<style>
/* v0.9.21: keep the ERP navigation permanently available. */
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button,
section[data-testid="stSidebar"] button[data-testid="stSidebarCollapseButton"],
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button[data-testid="baseButton-header"],
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] button[data-testid="baseButton-headerNoPadding"] {
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}

/* Remove the now-unused header space so the JD SYSTEMS logo stays at the top. */
section[data-testid="stSidebar"] div[data-testid="stSidebarHeader"] {
    min-height: 0 !important;
    height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: hidden !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _find_page_config(source: str):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"v0.9.21 page config parse failed: {exc}") from exc

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
            raise RuntimeError("v0.9.21 could not read st.set_page_config source")

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
                raise RuntimeError("v0.9.21 malformed st.set_page_config call")
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
        raise RuntimeError("v0.9.21 could not locate 'import streamlit as st'")

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
