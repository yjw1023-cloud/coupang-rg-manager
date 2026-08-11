"""RG Manager v0.9.55 strong visual blocks for provisional manual inputs.

The manual advertising and product-estimate controls are operational controls, not
ordinary page content.  Give both a strong, always-visible border so they are easy
to find on a dense provisional P&L page.
"""
from __future__ import annotations


def _inject(st):
    st.markdown(
        """
        <style>
        /* Provisional manual-input blocks: intentionally strong and high contrast. */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border: 4px solid #0067E8 !important;
            border-radius: 14px !important;
            background: linear-gradient(180deg, #F3F8FF 0%, #FFFFFF 86px) !important;
            box-shadow: 0 0 0 1px rgba(0,103,232,.18), 0 7px 20px rgba(0,70,160,.10) !important;
            padding: 8px !important;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] h3 {
            color: #004DAF !important;
            font-weight: 850 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_ad(st, manual_ad_module, core, month: str, db):
    _inject(st)
    return manual_ad_module.render_input(st, core, month, db)


def render_adjust(st, manual_adjust_module, core, month: str, auto_view, db):
    _inject(st)
    box = st.container(border=True)
    with box:
        return manual_adjust_module.render_editor(st, core, month, auto_view, db)
