"""
复权因子计算单元测试（纯本地合成数据，无需网络）。

运行：
    python tests/test_adj.py
"""

import logging

import pandas as pd

from stockdb.adj import compute_qfq_factors, apply_qfq

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("test_adj")

FAILED = []


def _check(name, cond, detail=""):
    if cond:
        logger.info("✓ %s %s", name, detail)
    else:
        logger.error("✗ %s 失败: %s", name, detail)
        FAILED.append(name)


def _mkdaily(dates, closes):
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})


def _mkev(xdate, fenhong=0.0, songzhuangu=0.0, peigu=0.0, peigujia=0.0):
    return pd.DataFrame([{
        "xdate": pd.Timestamp(xdate), "fenhong": fenhong,
        "songzhuangu": songzhuangu, "peigu": peigu,
        "peigujia": peigujia, "suogu": 0.0,
    }])


def test_songzhuangu():
    """10送10：songzhuangu=10.0（每10股口径），除权前因子=0.5，除权日及之后=1.0"""
    daily = _mkdaily(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
         "2024-01-05", "2024-01-06", "2024-01-07"],
        [10.0] * 4 + [5.0] * 3,
    )
    f = compute_qfq_factors(daily, _mkev("2024-01-05", songzhuangu=10.0))
    _check("送转因子(除权前=0.5)", all(abs(x - 0.5) < 1e-9 for x in f.values[:4]))
    _check("送转因子(除权日及之后=1.0)", all(abs(x - 1.0) < 1e-9 for x in f.values[4:]))


def test_cash_dividend():
    """10派10元（每股1元）：fenhong=10.0，除权前因子=0.9"""
    daily = _mkdaily(
        ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        [10.0] * 4,
    )
    f = compute_qfq_factors(daily, _mkev("2024-01-03", fenhong=10.0))
    _check("现金分红因子=0.9", all(abs(x - 0.9) < 1e-9 for x in f.values[:2]))
    _check("现金分红(除权日起=1.0)", all(abs(x - 1.0) < 1e-9 for x in f.values[2:]))


def test_real_case_songzhuangu_dividend():
    """圣邦股份实证：10送5派5元，前收256.58 → T=(256.58-0.5)/1.5，k=T/前收"""
    daily = _mkdaily(["2022-06-21", "2022-06-22"], [256.58, 170.0])
    f = compute_qfq_factors(daily, _mkev("2022-06-22", fenhong=5.0, songzhuangu=5.0))
    k = (256.58 - 0.5) / 1.5 / 256.58
    _check("实证k精确匹配", abs(f.iloc[0] - k) < 1e-9)


def test_events_before_data_range():
    """数据区间之外的事件（无前收）应被跳过，不影响因子"""
    daily = _mkdaily(["2024-01-01", "2024-01-02"], [10.0, 10.5])
    ev = _mkev("2020-06-01", songzhuangu=10.0)
    f = compute_qfq_factors(daily, ev)
    _check("区间外事件被跳过", all(abs(x - 1.0) < 1e-9 for x in f.values))


def test_no_events():
    """无事件 → 因子全为 1.0"""
    daily = _mkdaily(["2024-01-01", "2024-01-02"], [10.0, 10.5])
    f = compute_qfq_factors(daily, pd.DataFrame())
    _check("无事件因子=1.0", all(abs(x - 1.0) < 1e-9 for x in f.values))


def test_apply_qfq_positional():
    """apply_qfq 必须按位置相乘（索引不对齐时不得产生 NaN）"""
    df = _mkdaily(["2024-01-01", "2024-01-02"], [10.0, 10.0])
    df["open"] = df["high"] = df["low"] = df["close"]
    f = pd.Series([0.5, 1.0], index=df["date"])  # 日期索引，与 df 的 RangeIndex 不同
    apply_qfq(df, f)
    _check("OHLC 无 NaN", df[["open", "high", "low", "close"]].isna().sum().sum() == 0)
    _check("close 按位置缩放", abs(df["close"].iloc[0] - 5.0) < 1e-9 and abs(df["close"].iloc[1] - 10.0) < 1e-9)


def main():
    test_songzhuangu()
    test_cash_dividend()
    test_real_case_songzhuangu_dividend()
    test_events_before_data_range()
    test_no_events()
    test_apply_qfq_positional()
    print("=" * 50)
    if FAILED:
        print(f"失败: {len(FAILED)} 项 -> {FAILED}")
    else:
        print("全部通过: 6/6")


if __name__ == "__main__":
    main()