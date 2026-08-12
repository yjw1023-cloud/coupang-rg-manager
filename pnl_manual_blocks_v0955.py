"""RG Manager provisional manual-adjustment block helpers.

v0.9.57:
- the old manual advertising allocation feature is retired
- delete any legacy rows in `provisional_manual_ad_spend` when provisional P&L opens
- keep only the product-level realized-price / RG-cost manual adjustment block
"""
from __future__ import annotations


def _cleanup_legacy_manual_ad(core, db):
    """Remove obsolete manual-ad records. Safe to run repeatedly."""
    try:
        core.init_db(db)
        with core._conn(db) as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisional_manual_ad_spend'"
            ).fetchone()
            if not exists:
                return 0
            row = c.execute("SELECT COUNT(*) n FROM provisional_manual_ad_spend").fetchone()
            count = int(row["n"] if row else 0)
            if count:
                c.execute("DELETE FROM provisional_manual_ad_spend")
            return count
    except Exception:
        # Cleanup must never prevent the ERP from opening. The retired table is
        # no longer read by v0.9.56+, so a cleanup failure cannot affect P&L.
        return 0


def _inject(st):
    st.markdown(
        """
        <style>
        /* Product-estimate manual-input block: intentionally high contrast. */
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
    """Legacy compatibility only; no current page should call this in v0.9.56+."""
    _cleanup_legacy_manual_ad(core, db)
    return None


def render_adjust(st, manual_adjust_module, core, month: str, auto_view, db):
    _cleanup_legacy_manual_ad(core, db)
    _inject(st)
    box = st.container(border=True)
    with box:
        return manual_adjust_module.render_editor(st, core, month, auto_view, db)
