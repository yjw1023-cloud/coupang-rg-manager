"""Guards for future Claude ERP imports after v0.8.2.

- JDS codes are internal/own-warehouse items and must not fuzzy-match RG products.
- The v0.7 importer accidentally used pandas Series.name (row index) as product name.
  We make the DataFrame index equal to the actual name before calling its commit.
"""
def apply(mod):
    if getattr(mod, "_rg_v082_guard_applied", False):
        return mod

    original_prep = mod.prep
    original_commit = mod.commit

    def prep(snap, core, pur, db=None):
        match, candidates, products = original_prep(snap, core, pur, db)
        if match is None or len(match) == 0:
            return match, candidates, products
        work = match.copy()
        for idx, row in work.iterrows():
            code = str(row.get("code", "") or "")
            method = str(row.get("method", "") or "")
            if code.upper().startswith("JDS") and method not in ("remembered", "item_code"):
                work.at[idx, "action"] = "create"
                work.at[idx, "method"] = "create_internal"
                work.at[idx, "status"] = "신규 내부품목"
                work.at[idx, "pid"] = None
                work.at[idx, "dest"] = str(row.get("name", "") or "")
                work.at[idx, "opt"] = ""
                work.at[idx, "score"] = 1.0
        return work, candidates, products

    def commit(snap, match, choices, core, pur, db=None, imp_rg=False, fill_cost=True, inv_date=None):
        work = match.copy()
        # v0.7 uses r.name internally. Make that index the real item name instead of row number.
        if "name" in work.columns:
            work.index = work["name"].astype(str)
        return original_commit(
            snap, work, choices, core, pur, db=db,
            imp_rg=imp_rg, fill_cost=fill_cost, inv_date=inv_date
        )

    mod.prep = prep
    mod.commit = commit
    mod._rg_v082_guard_applied = True
    return mod
