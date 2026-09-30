"""2026-09 通达信新协议兼容层（就地 patch pytdx）。

背景（实测 2026-09-30）：
    2026-09-10 起通达信服务器拒绝旧 pytdx / mootdx / xmtdx 的连接握手，
    表现为 TCP 可连、`get_security_list` 之类小命令仍返回，但
    `get_security_bars` / `get_security_quotes` 等全部返回空 —— 导致
    stockdb 日线增量、指数、分钟、tick、xdxr、finance 全线静默停更。

逆向结论（参考 a-share-tdxdata，MIT，https://github.com/ShanNing-YU/a-share-tdxdata）：
    1. 连接后必须先发 `0c02`(init) + `0c03`(tdxlevel 认证) 才能请求行情；
    2. K 线请求的版本标识由 `0x01016408` 改为 `0x01007b18`；
    3. 其余命令（quotes / xdxr / finance / tick / security_list）在新握手
       下**原样可用**，无需改动。

因此本模块不重写客户端，只做两处最小 patch（幂等）：
    - `TdxHq_API.setup`  → 发送新握手；
    - `GetSecurityBarsCmd.setParams` / `GetIndexBarsCmd.setParams`
      → 把请求包 [2:6] 的版本标识替换为 0x01007b18。

由 `stockdb/__init__.py` 在包导入时自动调用 `apply_patches()`，故所有
`from pytdx.hq import TdxHq_API` 的既有代码（reader/adj/tdx_client/scripts）
无需改动即恢复。
"""

import logging
import time

logger = logging.getLogger(__name__)

# 0c02 init（抓包原样）
C02_INIT = bytes.fromhex("0c0218940001030003000d0001")
# 0c03 tdxlevel 认证（抓包原样 42B；少 1B 服务器不响应）
C03_AUTH = bytes.fromhex(
    "0c031899000120002000db0f7464786c6576656c"
    "000000295cf740110000000000000000000000000005"
)
# 新 K 线版本标识（旧值 0x01016408）
BARS_VERSION = 0x01007B18

_APPLIED = False


def _drain(client, secs: float = 0.4):
    """把握手响应（可能分多包）读干净，避免残包污染下一条命令的帧头。"""
    end = time.time() + secs
    while time.time() < end:
        try:
            if not client.recv(65536):
                break
        except Exception:
            break


def _new_setup(self):
    """替换 pytdx 的 SetupCmd1/2/3：发 0c02 + 0c03，然后清空响应缓冲。"""
    client = self.client
    old_timeout = client.gettimeout()
    try:
        client.settimeout(1.0)
        client.send(C02_INIT)
        time.sleep(0.05)
        _drain(client, 0.4)
        client.send(C03_AUTH)
        time.sleep(0.05)
        _drain(client, 0.4)
    finally:
        client.settimeout(old_timeout)


def _patch_bars_version(cls):
    """包装 setParams：构造完请求后把版本标识替换为新值。"""
    orig = cls.setParams

    def setParams(self, category, market, code, start, count):
        orig(self, category, market, code, start, count)
        pkg = bytearray(self.send_pkg)
        pkg[2:6] = BARS_VERSION.to_bytes(4, "little")
        self.send_pkg = bytes(pkg)

    setParams.__wrapped__ = orig
    cls.setParams = setParams


def apply_patches() -> bool:
    """就地 patch pytdx，幂等；pytdx 不可用时返回 False（不抛异常）。"""
    global _APPLIED
    if _APPLIED:
        return True
    try:
        from pytdx.hq import TdxHq_API
        from pytdx.parser.get_security_bars import GetSecurityBarsCmd
        from pytdx.parser.get_index_bars import GetIndexBarsCmd
    except Exception as e:  # pragma: no cover - 仅在缺依赖时
        logger.warning("tdx_compat: pytdx 不可用，跳过新协议补丁: %s", e)
        return False

    TdxHq_API.setup = _new_setup
    _patch_bars_version(GetSecurityBarsCmd)
    _patch_bars_version(GetIndexBarsCmd)
    _APPLIED = True
    logger.info("tdx_compat: 已启用 2026-09 通达信新协议补丁（0c02/0c03 + bars version 0x01007b18）")
    return True
