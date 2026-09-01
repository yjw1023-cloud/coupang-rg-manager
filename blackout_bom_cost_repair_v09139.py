"""RG Manager v0.9.139 — repair blackout blind BOM links.

Problem
- v0.9.133 created temporary zero-cost raw items for the three blackout sizes
  before the purchase Excel was finally posted.
- Later purchase confirmation could create/use a different JDS raw product.
- The finished product BOM therefore stayed linked to the temporary zero-cost row,
  while purchase history/cost lived on another JDS row.

Repair
- For the three known Coupang blackout option IDs, inspect purchase_lines and find
  the purchased raw product matching the exact size.
- If the current BOM component has no purchase history / zero cost and one clear
  purchased candidate exists, rewire the BOM to that purchased product.
- Inventory and purchase history are never moved or fabricated. Only the BOM link
  is corrected. The temporary zero-cost product is left intact for audit safety.
- Idempotent on every app rerun.
"""
from __future__ import annotations

import re
from typing import Any


TARGETS = [
    {
        "option_id": "95985636464",
        "label": "암막 블라인드 블랙 1m x 1.48m",
        "size_tokens": ["1mx148m", "1m148m", "1x148", "1*148", "100x148", "100cm148", "1米148"],
        "reject_tokens": ["2mx148m", "2m148m", "2x148", "2*148", "200x148", "4mx148m", "4m148m", "4x148", "4*148", "400x148"],
    },
    {
        "option_id": "95985636462",
        "label": "암막 블라인드 블랙 2m x 1.48m",
        "size_tokens": ["2mx148m", "2m148m", "2x148", "2*148", "200x148", "200cm148", "2米148"],
        "reject_tokens": ["1mx148m", "1m148m", "1x148", "1*148", "100x148", "4mx148m", "4m148m", "4x148", "4*148", "400x148"],
    },
    {
        "option_id": "95985636463",
        "label": "암막 블라인드 블랙 4m x 1.48m",
        "size_tokens": ["4mx148m", "4m148m", "4x148", "4*148", "400x148", "400cm148", "4米148"],
        "reject_tokens": ["1mx148m", "1m148m", "1x148", "1*148", "100x148", "2mx148m", "2m148m", "2x148", "2*148", "200x148"],
    },
]

_BLACKOUT_WORDS = (
    "암막", "블라인드", "시트지", "blackout", "遮光", "窗帘", "窗簾", "涂银", "塗銀", "遮阳", "遮陽"
)


def _norm(v: Any) -> str:
    text = str(v or "").lower().replace("×", "x").replace("＊", "*")
    text = text.replace("1.48", "148").replace("1,48", "148")
    return re.sub(r"\s+", "", text)


def _tables(con) -> set[str]:
    return {str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _purchase_cost_expr() -> tuple[str, str]:
    qty = "COALESCE(pl.qty_receipt,pl.qty_source,0)"
    unit = (
        "CASE "
        "WHEN COALESCE(pl.landed_unit_cost_krw,0)<>0 THEN pl.landed_unit_cost_krw "
        f"WHEN {qty}<>0 AND COALESCE(pl.landed_total_krw,0)<>0 THEN pl.landed_total_krw/{qty} "
        "ELSE COALESCE(pl.unit_price,0) END"
    )
    return qty, unit


def _purchase_stats(con, product_id: int) -> dict[str, float]:
    if "purchase_lines" not in _tables(con):
        return {"count": 0, "qty": 0.0, "amount": 0.0, "avg": 0.0, "latest": 0.0}
    qty_expr, unit_expr = _purchase_cost_expr()
    row = con.execute(
        f"""
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM({qty_expr}),0) AS qty,
               COALESCE(SUM(CASE
                    WHEN COALESCE(pl.landed_total_krw,0)<>0 THEN pl.landed_total_krw
                    WHEN COALESCE(pl.total_amount,0)<>0 THEN pl.total_amount
                    ELSE ({qty_expr})*({unit_expr}) END),0) AS amount
        FROM purchase_lines pl
        WHERE pl.product_id=?
        """,
        (int(product_id),),
    ).fetchone()
    latest = con.execute(
        f"""SELECT {unit_expr} AS unit_cost
            FROM purchase_lines pl
            WHERE pl.product_id=?
            ORDER BY COALESCE(pl.purchase_date,'' ) DESC, pl.id DESC
            LIMIT 1""",
        (int(product_id),),
    ).fetchone()
    qty = float(row["qty"] or 0) if row else 0.0
    amount = float(row["amount"] or 0) if row else 0.0
    return {
        "count": int(row["cnt"] or 0) if row else 0,
        "qty": qty,
        "amount": amount,
        "avg": (amount / qty) if qty else 0.0,
        "latest": float(latest["unit_cost"] or 0) if latest else 0.0,
    }


def _candidate_rows(con):
    if "purchase_lines" not in _tables(con):
        return []
    return con.execute(
        """
        SELECT p.id,p.item_code,p.name,p.unit_cost,p.active,p.item_type,
               COALESCE(GROUP_CONCAT(DISTINCT pl.source_name),'') AS src_names,
               COALESCE(GROUP_CONCAT(DISTINCT pl.source_detail),'') AS src_details,
               COUNT(pl.id) AS purchase_count,
               MAX(COALESCE(pl.purchase_date,'')) AS last_purchase_date
        FROM products p
        JOIN purchase_lines pl ON pl.product_id=p.id
        WHERE p.option_id IS NULL
        GROUP BY p.id,p.item_code,p.name,p.unit_cost,p.active,p.item_type
        ORDER BY MAX(COALESCE(pl.purchase_date,'')) DESC,p.id DESC
        """
    ).fetchall()


def _score(row, target) -> int:
    combined = _norm(" ".join([
        str(row["name"] or ""),
        str(row["src_names"] or ""),
        str(row["src_details"] or ""),
    ]))
    if not combined:
        return -10_000

    # Never cross-link a clearly different blackout length.
    if any(_norm(t) in combined for t in target["reject_tokens"]):
        return -10_000

    size_hits = sum(1 for t in target["size_tokens"] if _norm(t) in combined)
    if size_hits <= 0:
        return -10_000

    score = 500 + min(size_hits, 3) * 80
    if any(_norm(w) in combined for w in _BLACKOUT_WORDS):
        score += 180
    name_n = _norm(row["name"])
    if "암막" in name_n:
        score += 100
    if "블라인드" in name_n or "시트지" in name_n:
        score += 70
    if str(row["item_code"] or "").upper().startswith("JDS"):
        score += 30
    if str(row["item_type"] or "") == "raw":
        score += 20
    if int(row["active"] or 0) == 1:
        score += 10
    return score


def _find_parent(con, option_id: str):
    return con.execute(
        """SELECT id,item_code,name FROM products
           WHERE CAST(option_id AS TEXT)=?
           ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,id
           LIMIT 1""",
        (str(option_id),),
    ).fetchone()


def _current_bom(con, parent_id: int):
    return con.execute(
        """SELECT b.component_product_id,b.qty_per,p.item_code,p.name,p.unit_cost
           FROM bom_items b
           JOIN products p ON p.id=b.component_product_id
           WHERE b.parent_product_id=?
           ORDER BY b.rowid""",
        (int(parent_id),),
    ).fetchall()


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    result = {"changed": [], "unchanged": [], "unresolved": []}

    with core_module._conn(db) as con:
        if "bom_items" not in _tables(con) or "purchase_lines" not in _tables(con):
            result["unresolved"].append({"reason": "필수 테이블 없음"})
            return result

        candidates = list(_candidate_rows(con))

        for target in TARGETS:
            parent = _find_parent(con, target["option_id"])
            if parent is None:
                result["unresolved"].append({"option_id": target["option_id"], "reason": "완제품 없음"})
                continue

            bom_rows = _current_bom(con, int(parent["id"]))
            if len(bom_rows) != 1:
                result["unresolved"].append({
                    "option_id": target["option_id"],
                    "reason": f"현재 BOM 구성품 수 {len(bom_rows)}개",
                })
                continue

            current = bom_rows[0]
            current_stats = _purchase_stats(con, int(current["component_product_id"]))
            current_cost = max(
                float(current["unit_cost"] or 0),
                float(current_stats["latest"] or 0),
                float(current_stats["avg"] or 0),
            )
            if current_stats["count"] > 0 and current_cost > 0:
                result["unchanged"].append({
                    "option_id": target["option_id"],
                    "component_code": str(current["item_code"] or ""),
                    "component_name": str(current["name"] or ""),
                    "cost": current_cost,
                    "reason": "현재 BOM 구성품에 실제 매입이력 있음",
                })
                continue

            ranked = []
            for row in candidates:
                pid = int(row["id"])
                if pid == int(current["component_product_id"]):
                    continue
                score = _score(row, target)
                if score < 0:
                    continue
                stats = _purchase_stats(con, pid)
                cost = max(float(row["unit_cost"] or 0), stats["latest"], stats["avg"])
                if stats["count"] <= 0 or cost <= 0:
                    continue
                ranked.append((score, str(row["last_purchase_date"] or ""), pid, row, stats, cost))

            ranked.sort(key=lambda x: (-x[0], x[1], x[2]), reverse=False)
            if not ranked:
                result["unresolved"].append({
                    "option_id": target["option_id"],
                    "current_component": str(current["item_code"] or ""),
                    "reason": "동일 사이즈의 실제 매입 JDS를 찾지 못함",
                })
                continue

            best = ranked[0]
            # If another candidate has exactly the same score, do not guess.
            if len(ranked) > 1 and ranked[1][0] == best[0]:
                result["unresolved"].append({
                    "option_id": target["option_id"],
                    "reason": "동일 점수의 매입 JDS 후보가 여러 개라 자동변경 보류",
                    "candidates": [str(best[3]["item_code"] or ""), str(ranked[1][3]["item_code"] or "")],
                })
                continue

            _, _, new_pid, new_row, new_stats, new_cost = best
            qty_per = float(current["qty_per"] or 1)
            con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (int(parent["id"]),))
            con.execute(
                "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
                (int(parent["id"]), int(new_pid), qty_per),
            )
            result["changed"].append({
                "option_id": target["option_id"],
                "finished": str(parent["name"] or target["label"]),
                "old_component_code": str(current["item_code"] or ""),
                "old_component_name": str(current["name"] or ""),
                "new_component_code": str(new_row["item_code"] or ""),
                "new_component_name": str(new_row["name"] or ""),
                "qty_per": qty_per,
                "purchase_count": int(new_stats["count"]),
                "cost": float(new_cost),
            })

        try:
            con.commit()
        except Exception:
            pass

    result["ok"] = not result["unresolved"]
    return result
