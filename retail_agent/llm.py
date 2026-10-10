"""Optional, evidence-bound critique. The language model never chooses trades."""

import json
from dataclasses import asdict

import requests

from .core import Candidate


SYSTEM = """你是谨慎的A/H股散户研究助理，不是投顾。只使用用户消息中的数据，不使用外部记忆补充新闻、财报、行业或公司事实。中文输出一个JSON对象，且仅含以下键：opportunity、countercase、trigger、invalidation、questions。每个值是简短的中文字符串。说明量价信号只是观察线索；对缺乏财务、公告、港股每手股数、交易成本、汇率和流动性深度证据要明确提出核查。不得说稳赚、立即买入或给出确定收益。trigger与invalidation只能引用已给数值并注明次日可能跳空、停牌、T+1或滑点导致无法按参考价执行。"""
FIELDS = ("opportunity", "countercase", "trigger", "invalidation", "questions")


def critique(candidate: Candidate, api_key: str, base_url: str, model: str) -> dict[str, str]:
    if not api_key or not model:
        raise ValueError("需要模型名称及 API key")
    url = base_url.rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://localhost") and not url.startswith("http://127.0.0.1"):
        raise ValueError("模型服务须使用 HTTPS 或本机地址")
    facts = asdict(candidate)
    facts["day"] = candidate.day.isoformat()
    prompt = "仅分析以下研究记录（非实时下单报价）。请勿把行情价格当成财务基本面证据：\n" + json.dumps(facts, ensure_ascii=False)
    try:
        response = requests.post(
            url + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]},
            timeout=35,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        result = json.loads(text)
        if not isinstance(result, dict) or any(not isinstance(result.get(k), str) or not result[k].strip() for k in FIELDS):
            raise ValueError("模型返回的分析字段不完整")
        return {k: result[k].strip()[:600] for k in FIELDS}
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError(f"模型分析失败：{exc}") from exc
