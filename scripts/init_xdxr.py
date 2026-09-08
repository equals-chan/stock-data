"""
init_xdxr.py — 全量回填除权除息事件（xdxr 表）

前复权因子的数据源。日常由 daily_update.py 增量刷新（只刷更新过的股票），
首次接入复权功能时跑一次本脚本即可全量补齐：
    python3 scripts/init_xdxr.py

完成后首次调用 db.daily(code, adjust='qfq') 会按需生成因子缓存（data/adj/）。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tqdm import tqdm

from stockdb.adj import fetch_xdxr_events
from stockdb.config import Config
from stockdb.db import MetaDB
from stockdb.tdx_client import tdx_connect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("init_xdxr")


def main():
    cfg = Config()
    cfg.ensure_dirs()
    meta = MetaDB(cfg.db_path)

    stocks = meta.get_stocks()
    if stocks.empty:
        logger.error("meta.db 无股票列表，请先运行 init_full.py")
        sys.exit(1)

    codes = stocks["code"].astype(str).str.zfill(6).tolist()
    logger.info("共 %d 只股票，开始回填除权除息事件...", len(codes))

    done = failed = 0
    with tdx_connect(cfg.servers) as api:
        for code in tqdm(codes, desc="xdxr backfill"):
            try:
                events = fetch_xdxr_events(code, cfg.servers, api=api)
                if events:
                    meta.upsert_xdxr(code, events)
                done += 1
            except Exception as e:
                failed += 1
                logger.debug("失败 %s: %s", code, e)

    logger.info("=" * 50)
    logger.info("✅ xdxr 回填完成：%d 只成功，%d 只失败", done, failed)
    logger.info("首次调用 db.daily(code, adjust='qfq') 时将自动生成因子缓存。")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()