"""披露原文取文：下载巨潮 PDF 并抽出文本，供人工核对与卡片证据引用。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

CACHE_DIR = Path.home() / ".cache" / "pickahkstock" / "pdf"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


class PdfError(RuntimeError):
    pass


def cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.pdf"


def download(url: str) -> Path:
    """下载并缓存；同名文件直接复用，重复研究同一份公告不再联网。"""
    if not url.lower().endswith(".pdf"):
        raise PdfError(f"不是 PDF 链接：{url}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(url)
    if path.exists() and path.stat().st_size > 1000:
        return path
    try:
        response = requests.get(url, headers=HEADERS, timeout=40)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise PdfError(f"公告下载失败 {url}：{exc}") from exc
    if not response.content.startswith(b"%PDF"):
        raise PdfError(f"返回内容不是 PDF：{url}")
    path.write_bytes(response.content)
    return path


def extract_text(url: str) -> str:
    """逐页抽取文本。扫描件抽不出文字是正常的，此时必须人工看原文，不能当成无内容。"""
    from pypdf import PdfReader

    path = download(url)
    try:
        reader = PdfReader(str(path))
        pages = []
        for number, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append(f"[[page {number}]]\n{text}")
        return "\n".join(pages)
    except Exception as exc:
        raise PdfError(f"PDF 解析失败 {url}：{exc}") from exc


def summarize(text: str, limit: int = 1600) -> str:
    """压缩成可读片段：去掉页眉页脚与空白，保留前若干字。"""
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned[:limit]
