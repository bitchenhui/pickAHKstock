"""市场状态：现在是上行、震荡还是下行。

为什么要单独一层：v4 的诊断发现，业绩预告大幅变动的股票是**高 beta 双向放大**
的——2026-03-31 那期指数跌 13.24%，这类票在下跌日的平均日收益是 -1.185%，
随机 A 股是 -0.889%（差 1.33 倍）；上涨市里反过来，它们涨得更多（+1.162% vs
+0.778%）。

所以「信号失效」和「市场在跌」要分开看：同样的信号，在上涨市是加分项，
在下跌市是放大器。发现层与账户层需要知道现在处在哪种状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .backtest import index_bars

# 判定门槛
MA_WINDOW = 60          # 均线窗口（交易日）
CHANGE_WINDOW = 20      # 涨跌看的窗口（交易日）
DOWN_MOVE = -0.08       # 20 日跌幅超过这个数算「下行」
UP_MOVE = 0.08          # 20 日涨幅超过这个数算「上行」


@dataclass
class MarketRegime:
    day: str = ""
    close: float | None = None
    ma60: float | None = None
    change_20d: float | None = None
    state: str = "未知"          # 上行 / 震荡 / 下行
    note: str = ""

    def to_dict(self) -> dict:
        return {"day": self.day, "close": self.close, "ma60": self.ma60,
                "change_20d": self.change_20d, "state": self.state, "note": self.note}

    @property
    def down(self) -> bool:
        return self.state == "下行"

    def position_factor(self) -> float:
        """下行市里高 beta 标的的仓位折算系数。

        1.0 = 不折算；0.5 = 减半。依据是诊断里那两期下跌市：
        强预告组在下跌日的平均日收益是随机的 1.08–1.33 倍。
        """
        return 0.5 if self.down else 1.0


def market_regime(as_of: date | None = None) -> MarketRegime:
    """沪深300 的市场状态。取不到数据就说「未知」，不猜。"""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=int(MA_WINDOW * 1.6) + 20)
    bars = index_bars(start, as_of)
    result = MarketRegime()
    if len(bars) < CHANGE_WINDOW + 2:
        result.note = "指数日线不足，状态未知"
        return result
    closes = [bar["close"] for bar in bars if bar.get("close")]
    if len(closes) < CHANGE_WINDOW + 2:
        result.note = "指数日线不足，状态未知"
        return result
    result.day = bars[-1]["day"]
    result.close = closes[-1]
    window = closes[-MA_WINDOW:] if len(closes) >= MA_WINDOW else closes
    result.ma60 = sum(window) / len(window)
    result.change_20d = closes[-1] / closes[-CHANGE_WINDOW - 1] - 1
    if result.change_20d <= DOWN_MOVE or (result.ma60 and result.close < result.ma60):
        result.state = "下行"
    elif result.change_20d >= UP_MOVE:
        result.state = "上行"
    else:
        result.state = "震荡"
    below = "" if not result.ma60 else ("低于" if result.close < result.ma60 else "高于")
    result.note = (f"沪深300 {result.close:.0f}，{below}60 日均线 {result.ma60:.0f}，"
                   f"20 日 {result.change_20d * 100:+.1f}%")
    return result
