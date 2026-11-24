# -*- coding: utf-8 -*-
"""网络请求优先走 IPv4。

为什么要这一层：有的网络到行情数据源的 IPv6 线路时好时坏——实测（2026-11-24
盘中）腾讯行情、东财数据中心走 IPv6 八秒无响应，同花顺要七秒，而走 IPv4 都是
零点几秒。系统解析域名时默认把 IPv6 排在前面，Python 的 HTTP 库会挨个地址试，
**每个地址都各等一个完整的超时**才换下一个，于是一个请求白白多等十几秒，
一次刷新几十个请求就拖成十几分钟。凌晨 IPv6 正常时同样的刷新只要 13 秒。

做法：把域名解析结果里的 IPv4 地址排到 IPv6 前面。IPv4 连不上时仍然会退回
IPv6，所以纯 IPv6 的环境不受影响。
"""

from __future__ import annotations

import socket

_original_getaddrinfo = None


def prefer_ipv4() -> None:
    """让本进程之后的域名解析结果里 IPv4 排在 IPv6 前面。重复调用没有副作用。"""
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        return
    _original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = _original_getaddrinfo(host, port, family, type, proto, flags)
        # 只在调用方没有指定协议族时重排；指定了 AF_INET6/AF_INET 的保持原样
        if family not in (0, socket.AF_UNSPEC):
            return results
        return sorted(results, key=lambda item: item[0] != socket.AF_INET)

    socket.getaddrinfo = getaddrinfo


def restore() -> None:
    """撤销 prefer_ipv4（测试用）。"""
    global _original_getaddrinfo
    if _original_getaddrinfo is not None:
        socket.getaddrinfo = _original_getaddrinfo
        _original_getaddrinfo = None
