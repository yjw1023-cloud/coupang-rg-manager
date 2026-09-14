"""RG Manager purchase matching bridge / rerun guard v0.9.200.

Why this exists
---------------
The durable source->product mapping layer restores confirmed mappings into the
compact purchase review table.  On a later Streamlit rerun the legacy hidden
selectbox already reports that same product as its current value.  The v0.9.1
review code then removes the now-redundant transient override and requests an
immediate rerun.  The durable layer restores it again on the next run, so the
same remove -> rerun cycle can repeat forever.  In the UI this appears as the
whole page dimming/brightening continuously after the final confirmation
checkbox is clicked.

v0.9.200 keeps the existing purchase/import engine and durable mapping rules, but
adds two safety measures:
1. Before the legacy matching controls render, previously cached manual choices
   are copied back to their original widget keys.
2. A repeated review-table rerun with exactly the same post-edit override state
   is suppressed once, allowing the page to continue to the final confirmation
   controls instead of entering an infinite Streamlit rerun loop.

No purchase, inventory, price or matching facts are changed by this module.
"""
from __future__ import annotations

import hashlib
from typing import Any

_RULE = "0.9.200-purchase-review-rerun-guard"
_APPLIED_ATTR = "_rg_purchase_match_state_bridge_v09200_applied"
_GUARD_ATTR = "_rg_purchase_review_rerun_guard_v09200_applied"
_GUARD_STATE = "_rg_purchase_review_rerun_signature_v09200"


def _file_bytes(uploaded: Any, base_module) -> bytes:
    try:
        return base_module._file_bytes(uploaded)
    except Exception:
        pass
    if uploaded is None:
        return b""
    try:
        return bytes(uploaded.getvalue())
    except Exception:
        return b""


def _file_fp(uploaded: Any, base_module) -> str:
    data = _file_bytes(uploaded, base_module)
    return hashlib.sha1(data).hexdigest() if data else ""


def _prefill_cached_choices(st_obj, base_module, file_fp: str) -> int:
    """Push compact-table overrides into legacy widget session keys before render."""
    if not file_fp:
        return 0
    try:
        overrides_all = st_obj.session_state.get(base_module._OVERRIDE_KEY, {}) or {}
        overrides = overrides_all.get(file_fp, {}) or {}
        cache = st_obj.session_state.get(base_module._CACHE_KEY, {}) or {}
        meta = cache.get(file_fp, {}) or {}
        rows = meta.get("rows", {}) or {}
    except Exception:
        return 0

    changed = 0
    for idx, raw_pid in list(overrides.items()):
        try:
            pid = int(raw_pid)
        except Exception:
            # New-item sentinels intentionally have no legacy product value yet.
            continue
        mr = rows.get(str(idx), {}) or {}
        try:
            if base_module._set_original_widget_choice(st_obj, mr, pid):
                changed += 1
        except Exception:
            pass
    return changed


def _override_signature(st_obj, base_module, file_fp: str) -> str:
    """Stable signature of the transient matching state after one review pass."""
    try:
        all_overrides = st_obj.session_state.get(base_module._OVERRIDE_KEY, {}) or {}
        overrides = all_overrides.get(file_fp, {}) or {}
        pairs = []
        for key, value in overrides.items():
            pairs.append((str(key), str(value)))
        pairs.sort()
        payload = repr((str(file_fp), pairs)).encode("utf-8", "replace")
        return hashlib.sha1(payload).hexdigest()
    except Exception:
        return hashlib.sha1(str(file_fp).encode("utf-8", "replace")).hexdigest()


def _install_review_rerun_guard(review_module, base_module):
    """Prevent only an identical consecutive review-triggered rerun."""
    if review_module is None or getattr(review_module, _GUARD_ATTR, False):
        return
    original_review = getattr(review_module, "_render_review_table", None)
    if not callable(original_review):
        setattr(review_module, _GUARD_ATTR, True)
        return

    def guarded_review(st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta):
        original_rerun = getattr(st_obj, "rerun", None)
        if not callable(original_rerun):
            return original_review(
                st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta
            )

        flags = {"allowed": False, "suppressed": False, "called": False}

        def guarded_rerun(*args, **kwargs):
            flags["called"] = True
            sig = _override_signature(st_obj, base_module, str(file_fp or ""))
            previous = str(st_obj.session_state.get(_GUARD_STATE, "") or "")
            if previous and previous == sig:
                # This is the second half of the exact same remove/restore cycle.
                # Consume the marker and let this run continue to the final
                # checkbox/button instead of immediately restarting again.
                st_obj.session_state.pop(_GUARD_STATE, None)
                flags["suppressed"] = True
                return None

            st_obj.session_state[_GUARD_STATE] = sig
            flags["allowed"] = True
            return original_rerun(*args, **kwargs)

        st_obj.rerun = guarded_rerun
        try:
            return original_review(
                st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta
            )
        finally:
            st_obj.rerun = original_rerun
            # If this render completed without asking for a rerun, an old marker
            # is no longer part of an active two-run cycle and must not affect a
            # future genuine user edit.
            if not flags["called"]:
                st_obj.session_state.pop(_GUARD_STATE, None)

    review_module._render_review_table = guarded_review
    # purchase_match_ui_v090 calls the same review hook through the base module.
    try:
        base_module._render_review_table = guarded_review
    except Exception:
        pass
    setattr(review_module, _GUARD_ATTR, True)


def apply(purchase_module, core_module, base_module, review_module):
    """Install purchase matching prefill + rerun-loop guard idempotently."""
    _install_review_rerun_guard(review_module, base_module)

    if purchase_module is None or getattr(purchase_module, _APPLIED_ATTR, False):
        return purchase_module

    current_render = getattr(purchase_module, "render_purchase_page", None)
    if not callable(current_render):
        setattr(purchase_module, _APPLIED_ATTR, True)
        return purchase_module

    def render_purchase_page(*args, **kwargs):
        st_obj = kwargs.get("st")
        if st_obj is None:
            for obj in args:
                if hasattr(obj, "file_uploader") and hasattr(obj, "session_state"):
                    st_obj = obj
                    break
        if st_obj is None:
            return current_render(*args, **kwargs)

        original_file_uploader = getattr(st_obj, "file_uploader", None)
        if not callable(original_file_uploader):
            return current_render(*args, **kwargs)

        def file_uploader_wrapper(*u_args, **u_kwargs):
            uploaded = original_file_uploader(*u_args, **u_kwargs)
            fp = _file_fp(uploaded, base_module)
            if fp:
                _prefill_cached_choices(st_obj, base_module, fp)
            return uploaded

        st_obj.file_uploader = file_uploader_wrapper
        try:
            return current_render(*args, **kwargs)
        finally:
            st_obj.file_uploader = original_file_uploader

    purchase_module.render_purchase_page = render_purchase_page
    setattr(purchase_module, _APPLIED_ATTR, True)
    try:
        core_module._rg_purchase_match_state_bridge_v09200 = _RULE
    except Exception:
        pass
    return purchase_module
