import return_sale_match_v0944 as m


def test_side_mirror_suffix_is_strong_match():
    parent = "보조거울 백미러 사이드미러 2p 보조미러"
    child = "보조거울 백미러 사이드미러 2p 보조미러, 2개, 천차종"
    assert m._strong_name_relation(child, parent)
    assert m._name_score(child, parent) >= 0.90


def test_different_pack_quantity_is_not_auto_merged():
    assert not m._strong_name_relation(
        "수납백 2개 블랙",
        "수납백 4개 블랙",
    )


def test_unrelated_product_is_not_auto_merged():
    assert not m._strong_name_relation(
        "보조거울 백미러 사이드미러 2p 보조미러",
        "LED 공부 시계 타이머 1개 화이트",
    )
