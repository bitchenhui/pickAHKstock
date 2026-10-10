"""AKShare boundary: normalize vendor columns and fail visibly on schema drift."""

from __future__ import annotations

from datetime import date, timedelta
from math import isfinite

from .core import Bar, Quote


class DataError(RuntimeError):
    pass


def _number(value) -> float:
    if value is None or str(value).strip() in ("", "-", "nan", "None"):
        return float("nan")
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return float("nan")


def normalize_quotes(frame, market: str) -> list[Quote]:
    expected = {"代码", "名称", "最新价", "成交额"}
    if market == "A":
        expected.add("总市值")
    if not expected.issubset(frame.columns):
        raise DataError(f"{market} 快照字段变化，缺少：{', '.join(sorted(expected - set(frame.columns)))}")
    quotes = []
    for row in frame.to_dict("records"):
        price = _number(row["最新价"])
        turnover = _number(row["成交额"])
        cap = _number(row.get("总市值")) if market == "A" else None
        if not isfinite(price) or not isfinite(turnover):
            continue
        quotes.append(Quote(market, str(row["代码"]).strip().zfill(6 if market == "A" else 5),
                            str(row["名称"]).strip(), price, turnover,
                            cap if cap is not None and isfinite(cap) else None))
    return quotes


def normalize_bars(frame) -> list[Bar]:
    required = {"日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"}
    if not required.issubset(frame.columns):
        raise DataError(f"日线字段变化，缺少：{', '.join(sorted(required - set(frame.columns)))}")
    bars = []
    for row in frame.to_dict("records"):
        try:
            day = date.fromisoformat(str(row["日期"])[:10])
            bars.append(Bar(day, *(_number(row[k]) for k in ("开盘", "最高", "最低", "收盘", "成交量", "成交额"))))
        except (ValueError, TypeError):
            continue
    return bars


def fetch_quotes(market: str) -> list[Quote]:
    import akshare as ak

    try:
        frame = ak.stock_zh_a_spot_em() if market == "A" else ak.stock_hk_main_board_spot_em()
        return normalize_quotes(frame, market)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"{market} 行情快照获取失败：{exc}") from exc


def fetch_history(quote: Quote, end_day: date | None = None) -> list[Bar]:
    import akshare as ak

    end = end_day or date.today()
    start_str = (end - timedelta(days=280)).strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    try:
        if quote.market == "A":
            frame = ak.stock_zh_a_hist(symbol=quote.code, period="daily", start_date=start_str,
                                       end_date=end_str, adjust="")
        else:
            frame = ak.stock_hk_hist(symbol=quote.code, period="daily", start_date=start_str,
                                     end_date=end_str, adjust="")
        return normalize_bars(frame)
    except DataError:
        raise
    except Exception as exc:
        raise DataError(f"{quote.market}:{quote.code} 日线获取失败：{exc}") from exc


def import_csv(quotes_file, bars_file, market: str) -> tuple[list[Quote], dict[str, list[Bar]]]:
    import pandas as pd

    try:
        quote_frame = pd.read_csv(quotes_file, dtype={"代码": str}, encoding="utf-8-sig")
        bar_frame = pd.read_csv(bars_file, dtype={"代码": str}, encoding="utf-8-sig")
        if "代码" not in bar_frame.columns:
            raise DataError("日线 CSV 缺少代码列")
        quotes = normalize_quotes(quote_frame, market)
        if not quotes:
            raise DataError("快照 CSV 中没有有效的价格和成交额")
        bars_by_code = {}
        for code, group in bar_frame.groupby("代码", dropna=True):
            normalized = str(code).strip().zfill(6 if market == "A" else 5)
            bars_by_code[normalized] = normalize_bars(group)
        return quotes, bars_by_code
    except (OSError, UnicodeError, pd.errors.ParserError, ValueError) as exc:
        raise DataError(f"CSV 导入失败：{exc}") from exc
