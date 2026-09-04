import pandas as pd

import provisional_pnl_expense_guard_v09154 as guard


class DummyUI:
    @staticmethod
    def _like(old, value, pct=False):
        return value


def test_positive_sale_forces_all_expenses_negative():
    df = pd.DataFrame([
        {
            "옵션ID": "1",
            "상품명": "sample",
            "판매수량": 2,
            "예상매출": 18800,
            "원가/개": 368.5,
            "매출원가": 737,
            # Regression input: commission arrived with the wrong positive sign.
            "판매수수료": 2030.4,
            "입출고비": 100,
            "배송비": 200,
            "반품충당": 50,
            "광고비": 300,
            "광고제외이익": 0,
            "예상이익": 0,
            "이익률(%)": 0,
        }
    ])

    out = guard.recalculate(DummyUI, df)
    row = out.iloc[0]

    assert row["매출원가"] == -737
    assert row["판매수수료"] == -2030.4
    assert row["입출고비"] == -100
    assert row["배송비"] == -200
    assert row["반품충당"] == -50
    assert row["광고비"] == -300
    assert row["광고제외이익"] == 15682.6
    assert row["예상이익"] == 15382.6
    assert row["이익률(%)"] < 100


def test_negative_net_sale_reverses_cogs_and_commission_only():
    df = pd.DataFrame([
        {
            "옵션ID": "2",
            "상품명": "return-only",
            "판매수량": -1,
            "예상매출": -10000,
            "원가/개": 3000,
            "매출원가": -3000,
            "판매수수료": -1080,
            "입출고비": 0,
            "배송비": 0,
            "반품충당": 500,
            "광고비": 0,
            "광고제외이익": 0,
            "예상이익": 0,
            "이익률(%)": 0,
        }
    ])

    out = guard.recalculate(DummyUI, df)
    row = out.iloc[0]

    assert row["매출원가"] == 3000
    assert row["판매수수료"] == 1080
    assert row["반품충당"] == -500
    assert row["예상이익"] == -6420
