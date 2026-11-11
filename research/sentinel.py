# -*- coding: utf-8 -*-
"""哨兵：高频地检查低频数据，触发器命中才发声。

定位（先说边界）：这不是高频行情系统——数据仍是日频/快照级，但检查
频率可以是每小时。它把卡片上的「行动条件区」从静态清单变成活的：
参考失效线、相对强度转向、催化剂到期、盯价接近一年极值，命中即推
通知。不预测、不产生买卖点，只说「条件到了，该你动手看」。

通知渠道配置（环境变量，CI 里走 Secrets；配哪个用哪个，可多个）：
  SENTINEL_SERVERCHAN_KEY   Server酱 SendKey（微信推送）
  SENTINEL_BARK_KEY         Bark key（iPhone 推送）
  SENTINEL_DINGTALK_WEBHOOK 钉钉群机器人 webhook（可选 SENTINEL_DINGTALK_SECRET 加签）
  SENTINEL_WECOM_WEBHOOK    企业微信群机器人 webhook
  SENTINEL_FEISHU_WEBHOOK   飞书群机器人 webhook
  SENTINEL_SMTP_HOST/PORT/USER/PASS/TO   邮件
"""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from datetime import date

import requests

from .card import Card
from .quotes import fetch_snapshots

# 相对强度翻转的确认幅度：|rs| 双侧都超过它才算真转向，避免 0 附近抖动
RS_FLIP_MARGIN = 2.0
POSITION_EXTREME = 0.10     # 盯价一年区间位置 ≤10% 或 ≥90% 视为接近极值


@dataclass
class Alert:
    market: str
    code: str
    name: str
    kind: str                  # 失效线 / 强度转向 / 催化剂到期 / 盯价极值
    detail: str
    action_hint: str

    def line(self) -> str:
        return (f"【{self.kind}】{self.name}（{self.market} {self.code}）：{self.detail}"
                f" → {self.action_hint}")


def check_cards(cards: list[Card]) -> tuple[list[Alert], int]:
    """对照触发器检查全部卡片。返回 (alerts, 检查张数)。"""
    from datetime import timedelta

    from .backtest import prefetch_prices
    from .commodity import changes as commodity_changes
    from .commodity import series as commodity_series
    from .execution import relative_strength

    today = date.today().isoformat()
    # CI 冷缓存时逐只串行取价会拖垮整轮检查：先并行预取全部日线
    # （load_prices 有落盘缓存，预取后逐卡计算秒回；本地热缓存无感）
    if cards:
        prefetch_prices([c.code for c in cards],
                        date.today() - timedelta(days=170), date.today(), workers=6)
    watch_codes = [(c.market, c.code) for c in cards]
    snapshots = fetch_snapshots(watch_codes) if watch_codes else {}
    alerts: list[Alert] = []

    for card in cards:
        snapshot = snapshots.get(card.code)
        price = snapshot.price if snapshot else None
        context = card.market_context or {}

        stop = context.get("stop_reference")
        if price and stop and price <= float(stop):
            alerts.append(Alert(
                card.market, card.code, card.name, "失效线",
                f"现价 {price} 已跌破 2ATR 参考失效线 {stop}",
                "打开卡片核对失效条件，决定撤或留（参考线不保证成交）"))

        prev_rs = ((card.execution or {}).get("strength") or {}).get("rs")
        if prev_rs is not None:
            try:
                strength = relative_strength(card.code, card.market)
            except Exception:
                strength = None
            if strength and strength.get("rs") is not None:
                now_rs = strength["rs"]
                if (prev_rs * now_rs < 0 and abs(prev_rs) > RS_FLIP_MARGIN
                        and abs(now_rs) > RS_FLIP_MARGIN):
                    direction = "转强" if now_rs > 0 else "转弱"
                    alerts.append(Alert(
                        card.market, card.code, card.name, "强度转向",
                        f"相对大盘 {direction}：{prev_rs:+.1f}pct → {now_rs:+.1f}pct",
                        "核对判断是否还成立（行动条件区的『何时看』）"))

        for catalyst in card.catalysts:
            if (catalyst.date and catalyst.date[:10] <= today
                    and catalyst.status == "待验证"):
                alerts.append(Alert(
                    card.market, card.code, card.name, "催化剂到期",
                    f"{catalyst.event}（{catalyst.date}）已到验证窗口",
                    "按卡片失效条件核对兑现情况，该写结论了"))

        for item in ((card.commodities or {}).get("items") or {}).values():
            try:
                stats = commodity_changes(commodity_series(item["symbol"]))
            except Exception:
                continue
            position = stats.get("position_250d")
            if position is None:
                continue
            if position <= POSITION_EXTREME or position >= 1 - POSITION_EXTREME:
                edge = "一年最低附近" if position <= POSITION_EXTREME else "一年最高附近"
                alerts.append(Alert(
                    card.market, card.code, card.name, "盯价极值",
                    f"{item['name']} {stats['latest']}，处于{edge}"
                    f"（区间位置 {position * 100:.0f}%）",
                    "对照卡片的失效条件（如价格破位线）"))
    return alerts, len(cards)


# ---------------------------------------------------------------- 通知渠道


def _send_webhook(url: str, payload: dict) -> str:
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.text[:80]


def _send_dingtalk(title: str, body: str) -> str:
    url = os.environ["SENTINEL_DINGTALK_WEBHOOK"]
    secret = os.environ.get("SENTINEL_DINGTALK_SECRET")
    if secret:
        import hashlib
        import hmac
        import time

        timestamp = str(round(time.time() * 1000))
        sign = urllib.parse.quote_plus(base64_hmac(secret, timestamp))
        url += f"&timestamp={timestamp}&sign={sign}"
    return _send_webhook(url, {"msgtype": "markdown",
                               "markdown": {"title": title, "text": f"# {title}\n{body}"}})


def base64_hmac(secret: str, timestamp: str) -> str:
    import base64
    import hashlib
    import hmac as _hmac

    digest = _hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(),
                       hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _send_wecom(title: str, body: str) -> str:
    return _send_webhook(os.environ["SENTINEL_WECOM_WEBHOOK"],
                         {"msgtype": "markdown", "markdown": {"content": f"## {title}\n{body}"}})


def _send_feishu(title: str, body: str) -> str:
    return _send_webhook(os.environ["SENTINEL_FEISHU_WEBHOOK"],
                         {"msg_type": "text", "content": {"text": f"{title}\n{body}"}})


def _send_serverchan(title: str, body: str) -> str:
    key = os.environ["SENTINEL_SERVERCHAN_KEY"]
    response = requests.post(f"https://sctapi.ftqq.com/{key}.send",
                             data={"title": title, "desp": body}, timeout=15)
    response.raise_for_status()
    return response.text[:80]


def _send_smtp(title: str, body: str) -> str:
    import smtplib
    from email.mime.text import MIMEText

    host = os.environ["SENTINEL_SMTP_HOST"]
    port = int(os.environ.get("SENTINEL_SMTP_PORT", "465"))
    user = os.environ["SENTINEL_SMTP_USER"]
    password = os.environ["SENTINEL_SMTP_PASS"]
    to = os.environ["SENTINEL_SMTP_TO"]
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = title
    message["From"] = user
    message["To"] = to
    with smtplib.SMTP_SSL(host, port, timeout=20) as server:
        server.login(user, password)
        server.sendmail(user, [to], message.as_string())
    return "sent"


PAGE_URL = "https://bitchenhui.github.io/pickAHKstock"
BARK_GROUP = "研究台哨兵"
# 关键触发用时效性通知（iPhone 可穿专注模式），其余普通通知
TIME_SENSITIVE = ("失效线", "催化剂到期")
BARK_MAX_PUSH = 8              # 防轰炸：逐条上限，超出并入一条汇总


def _bark_push(item: dict) -> str:
    """Bark POST /push：device_key 放 JSON 里，支持分组/铃声/跳转/等级。"""
    item = {"device_key": os.environ["SENTINEL_BARK_KEY"], **item}
    response = requests.post("https://api.day.app/push", json=item, timeout=15)
    response.raise_for_status()
    return response.text[:60]


def _bark_alert_item(alert: Alert) -> dict:
    return {
        "title": f"【{alert.kind}】{alert.name}",
        "body": f"{alert.detail} → {alert.action_hint}",
        "group": BARK_GROUP,
        "sound": "minima",
        "level": "timeSensitive" if alert.kind in TIME_SENSITIVE else "active",
        # 点通知直达这张卡的线上笔记页
        "url": f"{PAGE_URL}/{alert.market}_{alert.code}.html",
    }


def notify_alerts(alerts: list[Alert]) -> list[str]:
    """按渠道发触发器。Bark 走 iPhone 原生体验：逐条推送、分组折叠、
    点击直达对应卡片；其余渠道汇总一条长文。"""
    results: list[str] = []
    if os.environ.get("SENTINEL_BARK_KEY"):
        for alert in alerts[:BARK_MAX_PUSH]:
            try:
                results.append(f"Bark[{alert.kind}]: {_bark_push(_bark_alert_item(alert))}")
            except Exception as exc:
                results.append(f"Bark[{alert.kind}]: 失败 {type(exc).__name__}: {str(exc)[:60]}")
        if len(alerts) > BARK_MAX_PUSH:
            extra = len(alerts) - BARK_MAX_PUSH
            summary = {"title": f"研究台哨兵：还有 {extra} 条触发",
                       "body": "点击打开汇总页查看全部",
                       "group": BARK_GROUP, "sound": "minima", "level": "active",
                       "url": f"{PAGE_URL}/"}
            try:
                results.append(f"Bark[汇总]: {_bark_push(summary)}")
            except Exception as exc:
                results.append(f"Bark[汇总]: 失败 {str(exc)[:60]}")
    title = f"研究台哨兵：{len(alerts)} 条触发"
    body = "\n".join(alert.line() for alert in alerts)
    results.extend(_notify_text_channels(title, body))
    return results


def _notify_text_channels(title: str, body: str) -> list[str]:
    """文本汇总类渠道（Bark 之外，长文友好）。"""
    senders = {
        "Server酱": ("SENTINEL_SERVERCHAN_KEY", _send_serverchan),
        "钉钉": ("SENTINEL_DINGTALK_WEBHOOK", _send_dingtalk),
        "企业微信": ("SENTINEL_WECOM_WEBHOOK", _send_wecom),
        "飞书": ("SENTINEL_FEISHU_WEBHOOK", _send_feishu),
        "邮件": ("SENTINEL_SMTP_HOST", _send_smtp),
    }
    results = []
    for name, (env_key, sender) in senders.items():
        if not os.environ.get(env_key):
            continue
        try:
            results.append(f"{name}: {sender(title, body)}")
        except Exception as exc:      # 单渠道失败不挡其他渠道
            results.append(f"{name}: 失败 {type(exc).__name__}: {str(exc)[:80]}")
    return results


def notify(title: str, body: str) -> list[str]:
    """兼容入口：只发文本汇总渠道（Bark 的逐条体验走 notify_alerts）。"""
    return _notify_text_channels(title, body)
