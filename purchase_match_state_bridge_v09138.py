"""RG Manager v0.9.201 purchase matching state bridge.

Fixes two purchase-confirmation state problems without changing purchase/inventory
business rules:
1. The compact review table and the legacy hidden purchase engine must see the
   same product ID before the legacy selectbox renders. Durable source mappings
   and manual overrides are therefore pushed into the legacy widget first.
2. Durable mappings can make the review layer repeatedly add/remove an equivalent
   transient override. A stable rerun-signature guard suppresses only repeated
   reruns whose effective override state is unchanged.

The final purchase button remains controlled by the legacy purchase engine; this
module only makes its hidden matching state agree with the visible review table.
"""
from __future__ import annotations

import hashlib
from typing import Any

_APPLIED_ATTR = "_rg_purchase_match_state_bridge_v09201_applied"
_GUARD_ATTR = "_rg_purchase_review_rerun_guard_v09201_applied"
_GUARD_STATE = "_rg_purchase_review_rerun_signature_v09201"


def _closure_value(fn, name: str):
    try:
        freevars = tuple(fn.__code__.co_freevars or ())
        closure = tuple(fn.__closure__ or ())
        for key, cell in zip(freevars, closure):
            if key == name:
                return cell.cell_contents
    except Exception:
        pass
    return None


def _durable_pid(core_module, db_path, excel_rows, idx: int) -> int | None:
    """Read the durable source-name/detail mapping if one exists."""
    try:
        src = next((r for r in excel_rows if int(r.get("index") or 0) == int(idx)), None)
        if not src:
            return None
        source_name = str(src.get("source_name") or "").strip()
        source_detail = str(src.get("source_detail") or "").strip()
        with core_module._conn(db_path) as con:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_source_product_map'"
            ).fetchone()
            if not exists:
                return None
            row = con.execute(
                """SELECT p.id
                   FROM purchase_source_product_map m
                   JOIN products p ON p.id=m.product_id
                   WHERE m.source_name=? AND m.source_detail=?
                     AND p.active=1 AND p.option_id IS NULL
                   LIMIT 1""",
                (source_name, source_detail),
            ).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        return None


def _target_pid(st_obj, base_module, core_module, db_path, context, idx: int) -> int | None:
    fp = context.get("file_fp")
    if fp:
        try:
            overrides_all = st_obj.session_state.get(base_module._OVERRIDE_KEY, {}) or {}
            overrides = overrides_all.get(fp, {}) or {}
            value = overrides.get(str(int(idx)))
            if value is not None:
                return int(value)
        except Exception:
            pass
    return _durable_pid(core_module, db_path, context.get("excel_rows") or [], int(idx))


def _preselect_legacy_widget(st_obj, base_module, core_module, db_path, context,
                             idx: int, s_args, s_kwargs) -> int | None:
    target_pid = _target_pid(st_obj, base_module, core_module, db_path, context, idx)
    if target_pid is None:
        return None

    key = s_kwargs.get("key")
    if not key:
        return target_pid

    try:
        options_obj = s_kwargs.get("options", s_args[1] if len(s_args) > 1 else [])
        options_list = list(options_obj)
    except Exception:
        options_list = []
    if not options_list:
        return target_pid

    fmt = s_kwargs.get("format_func")
    products = base_module._self_warehouse_products(core_module, db_path)
    chosen = None
    for opt in options_list:
        display = base_module._safe_format(fmt, opt)
        pid = base_module._extract_product_id(opt) or base_module._pid_from_display(display, products)
        if pid is not None and int(pid) == int(target_pid):
            chosen = opt if base_module._primitive(opt) else int(pid)
            break
    if chosen is None:
        return target_pid

    # Must happen BEFORE the original Streamlit selectbox is instantiated so the
    # legacy business logic reads the same product as the visible compact table.
    try:
        st_obj.session_state[key] = chosen
    except Exception:
        pass

    # Keep the compact cache aligned when the source came only from the durable
    # source->product table after a restart/re-upload.
    fp = context.get("file_fp")
    if fp:
        try:
            all_overrides = st_obj.session_state.setdefault(base_module._OVERRIDE_KEY, {})
            overrides = all_overrides.setdefault(fp, {})
            overrides[str(int(idx))] = int(target_pid)
            all_overrides[fp] = overrides
            st_obj.session_state[base_module._OVERRIDE_KEY] = all_overrides
        except Exception:
            pass
    return int(target_pid)


def _override_signature(st_obj, base_module, file_fp: str) -> str:
    """Stable signature of the effective transient matching state after review."""
    try:
        all_overrides = st_obj.session_state.get(base_module._OVERRIDE_KEY, {}) or {}
        overrides = all_overrides.get(file_fp, {}) or {}
        pairs = sorted((str(k), str(v)) for k, v in overrides.items())
        payload = repr((str(file_fp), pairs)).encode("utf-8", "replace")
    except Exception:
        payload = str(file_fp).encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()


def _install_review_rerun_guard(review_module, base_module):
    """Suppress only a repeated review rerun with an identical matching signature."""
    if review_module is None or getattr(review_module, _GUARD_ATTR, False):
        return
    original_review = getattr(review_module, "_render_review_table", None)
    if not callable(original_review):
        setattr(review_module, _GUARD_ATTR, True)
        return

    def guarded_review(st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta):
        original_rerun = getattr(st_obj, "rerun", None)
        if not callable(original_rerun):
            return original_review(st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta)

        def guarded_rerun(*args, **kwargs):
            sig = _override_signature(st_obj, base_module, str(file_fp or ""))
            previous = str(st_obj.session_state.get(_GUARD_STATE, "") or "")
            if previous == sig:
                # Durable mapping restoration may produce a raw add/remove change
                # while the effective product choice is identical. Do not restart
                # the whole page again for that same stable state.
                return None
            st_obj.session_state[_GUARD_STATE] = sig
            return original_rerun(*args, **kwargs)

        st_obj.rerun = guarded_rerun
        try:
            return original_review(st_obj, pd_obj, core_obj, db_path, file_fp, excel_rows, meta)
        finally:
            st_obj.rerun = original_rerun

    review_module._render_review_table = guarded_review
    try:
        base_module._render_review_table = guarded_review
    except Exception:
        pass
    setattr(review_module, _GUARD_ATTR, True)


def apply(purchase_module, core_module, base_module, review_module):
    """Replace v0.9.0 presentation wrapper with pre-save bridge + rerun guard."""
    _install_review_rerun_guard(review_module, base_module)

    if purchase_module is None or getattr(purchase_module, _APPLIED_ATTR, False):
        return purchase_module

    current_render = getattr(purchase_module, "render_purchase_page", None)
    if not callable(current_render):
        setattr(purchase_module, _APPLIED_ATTR, True)
        return purchase_module

    # v0.9.0's wrapper closes over `original_render`, which is the purchase-batch
    # wrapper around the actual legacy engine. Reuse exactly that object so no
    # posting/business rule is duplicated.
    original_render = _closure_value(current_render, "original_render")
    if not callable(original_render):
        setattr(purchase_module, _APPLIED_ATTR, True)
        return purchase_module

    def render_purchase_page(*args, **kwargs):
        st_obj = kwargs.get("st")
        pd_obj = kwargs.get("pd")
        if st_obj is None:
            for obj in args:
                if hasattr(obj, "file_uploader") and hasattr(obj, "selectbox"):
                    st_obj = obj
                    break
        if pd_obj is None:
            try:
                import pandas as pd_obj
            except Exception:
                pd_obj = None
        if st_obj is None or pd_obj is None:
            return original_render(*args, **kwargs)

        db_path = core_module.DEFAULT_DB
        core_module.init_db(db_path)
        context: dict[str, Any] = {
            "current_index": None,
            "captured": {},
            "file_fp": None,
            "excel_rows": [],
            "file_name": "",
            "summary_rendered": False,
        }

        original_file_uploader = st_obj.file_uploader
        original_expander = st_obj.expander
        original_selectbox = st_obj.selectbox
        original_section = kwargs.get("section")

        class _TrackedExpander:
            def __init__(self, inner, idx, title, status):
                self._inner = inner
                self._idx = idx
                self._title = title
                self._status = status

            def __enter__(self):
                entered = self._inner.__enter__()
                context["current_index"] = self._idx
                if self._idx is not None:
                    row = context["captured"].setdefault(str(self._idx), {})
                    row["expander_title"] = self._title
                    row["status"] = self._status
                return entered

            def __exit__(self, exc_type, exc, tb):
                try:
                    return self._inner.__exit__(exc_type, exc, tb)
                finally:
                    context["current_index"] = None

            def __getattr__(self, name):
                return getattr(self._inner, name)

        def file_uploader_wrapper(*f_args, **f_kwargs):
            uploaded = original_file_uploader(*f_args, **f_kwargs)
            if uploaded is not None:
                data = base_module._file_bytes(uploaded)
                if data:
                    context["file_fp"] = hashlib.sha1(data).hexdigest()
                    context["file_name"] = str(getattr(uploaded, "name", "") or "")
                    try:
                        context["excel_rows"] = base_module._parse_purchase_excel(data)
                    except Exception:
                        context["excel_rows"] = []
            return uploaded

        def expander_wrapper(*e_args, **e_kwargs):
            label = str(e_args[0] if e_args else e_kwargs.get("label", ""))
            idx, title, status = base_module._parse_expander_label(label)
            inner = original_expander(*e_args, **e_kwargs)
            return _TrackedExpander(inner, idx, title, status)

        def selectbox_wrapper(*s_args, **s_kwargs):
            idx = context.get("current_index")
            if idx is not None:
                _preselect_legacy_widget(
                    st_obj, base_module, core_module, db_path, context,
                    int(idx), s_args, s_kwargs,
                )

            result = original_selectbox(*s_args, **s_kwargs)
            if idx is None:
                return result

            try:
                options_obj = s_kwargs.get("options", s_args[1] if len(s_args) > 1 else [])
                options_list = list(options_obj)
            except Exception:
                options_list = []
            if not options_list:
                return result

            row = context["captured"].setdefault(str(idx), {})
            if row.get("widget_captured"):
                return result
            fmt = s_kwargs.get("format_func")
            current_display = base_module._safe_format(fmt, result)
            products = base_module._self_warehouse_products(core_module, db_path)
            current_pid = (
                base_module._extract_product_id(result)
                or base_module._pid_from_display(current_display, products)
            )
            pid_to_value: dict[str, Any] = {}
            for opt in options_list:
                display = base_module._safe_format(fmt, opt)
                pid = base_module._extract_product_id(opt) or base_module._pid_from_display(display, products)
                if pid is not None:
                    pid_to_value[str(int(pid))] = opt if base_module._primitive(opt) else int(pid)
            row.update({
                "widget_captured": True,
                "widget_key": s_kwargs.get("key"),
                "current_pid": current_pid,
                "current_display": current_display,
                "match_rate": base_module._match_rate(current_display),
                "pid_to_value": pid_to_value,
            })
            return result

        def section_wrapper(*sec_args, **sec_kwargs):
            result = original_section(*sec_args, **sec_kwargs)
            title = str(sec_args[0] if sec_args else sec_kwargs.get("title", ""))
            if "상품 매칭 확인" not in title or context.get("summary_rendered"):
                return result
            context["summary_rendered"] = True
            fp = context.get("file_fp")
            excel_rows = context.get("excel_rows") or []
            cache = st_obj.session_state.get(base_module._CACHE_KEY, {})
            meta = cache.get(fp) if fp else None
            if fp and excel_rows and meta:
                st_obj.markdown(
                    """<style>
                    div[data-testid=\"stExpander\"] {display:none !important;}
                    </style>""",
                    unsafe_allow_html=True,
                )
                review_module._render_review_table(
                    st_obj, pd_obj, core_module, db_path, fp, excel_rows, meta
                )
            elif fp and excel_rows:
                st_obj.info("매칭 결과 표를 준비하고 있습니다…")
            return result

        st_obj.file_uploader = file_uploader_wrapper
        st_obj.expander = expander_wrapper
        st_obj.selectbox = selectbox_wrapper
        if callable(original_section):
            kwargs = dict(kwargs)
            kwargs["section"] = section_wrapper

        try:
            result = original_render(*args, **kwargs)
        finally:
            st_obj.file_uploader = original_file_uploader
            st_obj.expander = original_expander
            st_obj.selectbox = original_selectbox

        fp = context.get("file_fp")
        excel_rows = context.get("excel_rows") or []
        captured = context.get("captured") or {}
        if fp and excel_rows and captured:
            cache = st_obj.session_state.setdefault(base_module._CACHE_KEY, {})
            new_meta = {
                "file_name": context.get("file_name", ""),
                "rows": captured,
            }
            had_meta = fp in cache
            cache[fp] = new_meta
            if len(cache) > 5:
                for old_key in list(cache.keys())[:-5]:
                    cache.pop(old_key, None)
            st_obj.session_state[base_module._CACHE_KEY] = cache

            if not had_meta and st_obj.session_state.get(base_module._READY_KEY) != fp:
                st_obj.session_state[base_module._READY_KEY] = fp
                try:
                    st_obj.rerun()
                except Exception:
                    pass
        return result

    purchase_module.render_purchase_page = render_purchase_page
    setattr(purchase_module, _APPLIED_ATTR, True)
    return purchase_module
