"""RG Manager v0.9.45 deletion UX and return-child negative-stock cleanup.

- Remove the obsolete delete/restore controls embedded at the bottom of Item Master.
  Deletion is handled only by the dedicated Item Delete page.
- A user-confirmed returned-item child may legitimately show negative RG stock because
  it was previously posted as an ordinary sale.  After manual return-code cleanup,
  remove any residual ordinary sales-deduction rows from that child and archive it.
- Non-sales inventory/BOM/production/purchase evidence is still protected by the
  v0.9.44 blocker rules.
"""
from __future__ import annotations

_APPLIED = False


def apply(item_ui_module, item_delete_module, core_module) -> None:
    global _APPLIED
    if _APPLIED or getattr(item_delete_module, "_rg_item_delete_fix_v0945_applied", False):
        return

    # The old Item Master footer used the generic archive routine, which correctly
    # blocks any non-zero balance but is the wrong tool for return-option children.
    # Keep one clear deletion path to avoid sending users to the wrong control.
    def _render_archive_manager_redirect(st, core, all_df):
        st.markdown("---")
        st.info(
            "품목 삭제·복원은 사이드바의 ‘품목 삭제’ 메뉴에서 처리합니다. "
            "쿠팡 반품 할인판매 옵션은 반드시 ‘반품코드 정리’를 사용하세요."
        )

    item_ui_module._render_archive_manager = _render_archive_manager_redirect

    original_manual_return = item_delete_module._manual_return

    def _manual_return(core, child_id, parent_id):
        # v0.9.44 already refuses BOM/production/purchase/non-sales inventory
        # evidence.  Therefore a remaining negative balance on a confirmed child
        # is ordinary SALESSTAT deduction history and must not block cleanup.
        result = original_manual_return(core, child_id, parent_id)

        with item_delete_module._conn(core) as con:
            # _post_discount normally removes SALESSTAT-<import_id> rows itself.
            # This second cleanup catches older/mismatched ordinary-sale postings
            # after the user has explicitly confirmed that the child is a return ID.
            con.execute(
                "DELETE FROM inventory_txns WHERE product_id=? AND txn_type='판매차감'",
                (int(child_id),),
            )
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (core.now_iso(), int(child_id)),
            )

        return result

    item_delete_module._manual_return = _manual_return
    item_delete_module._rg_item_delete_fix_v0945_applied = True
    _APPLIED = True
