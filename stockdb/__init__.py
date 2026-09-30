"""
stockdb — A股本地行情数据库
所有项目的统一数据入口。
"""

import os

# 清理空代理环境变量（空字符串会被 urllib 误当成有效代理，导致 ProxyError）
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    if _k in os.environ and os.environ[_k] == "":
        del os.environ[_k]

# 2026-09 通达信新协议兼容（就地 patch pytdx，必须在任何 pytdx 使用前生效）
from . import tdx_compat as _tdx_compat

_tdx_compat.apply_patches()

from .reader import StockDB

__version__ = "0.1.0"
__all__ = ["StockDB"]
