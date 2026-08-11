"""RG Manager v0.9.20 sidebar collapse recovery.

Keeps Streamlit's built-in sidebar restore control visible when the sidebar is
collapsed, even when the legacy app hides the Streamlit header.
"""
from __future__ import annotations


def apply(st_obj) -> None:
    st_obj.markdown(
        """
<style>
/* The legacy ERP theme hides Streamlit's header. In recent Streamlit builds the
   collapsed-sidebar restore control lives in/around that header, so keep only
   the restore control visible while the rest of the header stays unobtrusive. */
header[data-testid="stHeader"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    height: 0 !important;
    min-height: 0 !important;
    overflow: visible !important;
    background: transparent !important;
    box-shadow: none !important;
    pointer-events: none !important;
    z-index: 1000000 !important;
}

header[data-testid="stHeader"] [data-testid="stToolbar"],
header[data-testid="stHeader"] [data-testid="stStatusWidget"],
header[data-testid="stHeader"] [data-testid="stMainMenu"] {
    display: none !important;
}

/* Selector used by current/older Streamlit sidebar restore controls. */
div[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    pointer-events: auto !important;
    position: fixed !important;
    left: 12px !important;
    top: 12px !important;
    width: 40px !important;
    height: 40px !important;
    z-index: 1000001 !important;
}

div[data-testid="collapsedControl"] button,
[data-testid="stSidebarCollapsedControl"] button {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    visibility: visible !important;
    opacity: 1 !important;
    pointer-events: auto !important;
    width: 40px !important;
    min-width: 40px !important;
    height: 40px !important;
    min-height: 40px !important;
    padding: 0 !important;
    border: 1px solid #f3192d !important;
    border-radius: 9px !important;
    background: #f3192d !important;
    color: #ffffff !important;
    box-shadow: 0 2px 10px rgba(0,0,0,0.24) !important;
}

div[data-testid="collapsedControl"] button:hover,
[data-testid="stSidebarCollapsedControl"] button:hover {
    background: #d91529 !important;
    border-color: #d91529 !important;
}

div[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
div[data-testid="collapsedControl"] path,
[data-testid="stSidebarCollapsedControl"] path {
    color: #ffffff !important;
    fill: #ffffff !important;
    stroke: #ffffff !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )
