"""
A 股前复权因子计算（离线，与通达信算法一致）

数据链路：
  pytdx get_xdxr_info() → 除权除息事件（送转/分红/配股）
  + 本地原始日线 close → 前复权因子链

算法：
  每个除权事件，理论除权价 T = (前收 - 每股分红 + 配股价×每股配股率) / (1 + 每股送转率 + 每股配股率)
  复权乘数 k = T / 前收
  前复权因子 factor(date) = ∏ k_e（所有发生在该日之后的事件）
  最新交易日 factor = 1（最新价 = 真实价），新除权事件只影响更早历史 → 因子天然稳定。

注意：pytdx 的 xdxr 字段 fenhong/songzhuangu/peigu 为“每 10 股”口径（如 10送5 → songzhuangu=5.0，
每10股派5元 → fenhong=5.0），peigujia（配股价）为每股价格，计算时统一 ÷10 换算为每股口径。
"""

import logging
from typing import List

import pandas as pd

from .market import code_to_tdx_market
from .tdx_client import tdx_connect

logger = logging.getLogger(__name__)


def fetch_xdxr_events(code: str, servers: list, api=None) -> List[dict]:
    """
    从 pytdx 拉取某股票全部除权除息事件（每次返回全量历史）。
    返回 [{date:'YYYYMMDD', category, fenhong, songzhuangu, peigu, peigujia, suogu}, ...]
    api: 可传入已连接 TdxHq_API 复用连接（批量刷新时避免重复建连）
    """
    market = code_to_tdx_market(code)
    try:
        if api is None:
            with tdx_connect(servers) as api:
                raw = api.get_xdxr_info(market, code) or []
        else:
            raw = api.get_xdxr_info(market, code) or []
    except Exception as e:
        logger.warning("fetch xdxr failed %s: %s", code, e)
        return []

    events = []
    for r in raw:
        # pytdx 返回 year/month/day 字段（部分服务器为 date 字符串），统一为 YYYYMMDD
        date_str = str(r.get("date", "")).replace("-", "")
        if len(date_str) != 8:
            y, m, d = r.get("year"), r.get("month"), r.get("day")
            if y and m and d:
                date_str = f"{int(y):04d}{int(m):02d}{int(d):02d}"
        if len(date_str) != 8:
            continue
        events.append({
            "date": date_str,
            "category": int(r.get("category", 1) or 1),
            "fenhong": float(r.get("fenhong", 0) or 0),
            "songzhuangu": float(r.get("songzhuangu", 0) or 0),
            "peigu": float(r.get("peigu", 0) or 0),
            "peigujia": float(r.get("peigujia", 0) or 0),
            "suogu": float(r.get("suogu", 0) or 0),
        })

    # 同一日期可能同时存在 category=1（除权除息，含真实参数）与 category=5（股本变化，全零）
    # 且两者日期相同会在 (code, xdate) 主键下互相覆盖；按日期去重，优先保留 category=1/2 的真实事件
    events.sort(key=lambda e: (e["date"], 0 if e["category"] in (1, 2) else 1))
    deduped = []
    seen = set()
    for e in events:
        if e["date"] not in seen:
            deduped.append(e)
            seen.add(e["date"])
    return deduped


def compute_qfq_factors(daily: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    """
    由原始日线 + 除权除息事件计算前复权因子序列。
    daily  : DataFrame，需含 date(datetime) 与 close 列（原始不复权价）
    events : DataFrame，需含 xdate(datetime), fenhong, songzhuangu, peigu, peigujia, suogu
    返回   : pd.Series，index=daily 的 date，value=前复权因子（最新=1.0）
    """
    if daily.empty:
        return pd.Series(dtype=float)
    daily = daily.sort_values("date").reset_index(drop=True)
    closes = daily.set_index("date")["close"]
    factor = pd.Series(1.0, index=daily["date"])

    if events is None or events.empty:
        return factor

    for _, ev in events.sort_values("xdate").iterrows():
        edate = ev["xdate"]
        if pd.isna(edate):
            continue

        if float(ev.get("suogu", 0) or 0) > 0:
            logger.warning("事件 %s 含缩股(suogu=%.4f)，暂不支持，跳过", edate, ev["suogu"])
            continue

        # 除权日之前最近一个交易日的原始收盘价
        prev = closes[closes.index < edate]
        if prev.empty:
            continue
        c_pre = float(prev.iloc[-1])
        if c_pre <= 0:
            continue

        num = c_pre - float(ev.get("fenhong", 0) or 0) / 10 + float(ev.get("peigu", 0) or 0) * float(ev.get("peigujia", 0) or 0) / 10
        den = 1 + float(ev.get("songzhuangu", 0) or 0) / 10 + float(ev.get("peigu", 0) or 0) / 10
        if num <= 0 or den <= 0:
            logger.warning("事件 %s 参数异常(num=%.4f den=%.4f)，跳过", edate, num, den)
            continue

        k = (num / den) / c_pre
        # 防御性裁剪：异常事件不得产生荒谬因子
        k = min(max(k, 0.01), 100.0)
        factor[(daily["date"] < edate).values] *= k

    return factor


def apply_qfq(df: pd.DataFrame, factors: pd.Series) -> pd.DataFrame:
    """将前复权因子乘到 OHLC 列（vol/amount 不变），原地修改并返回"""
    if df.empty or factors is None or factors.empty:
        return df
    # 按位置相乘：factors 的索引是日期值，df 的索引是 RangeIndex，直接乘会因索引不对齐产生 NaN
    f = factors.reindex(df["date"]).fillna(1.0).to_numpy()
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = df[col].to_numpy() * f
    return df