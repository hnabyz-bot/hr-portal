"""
사내 규정 원문(PDF/DOCX) 텍스트 추출 및 키워드 검색.

파일은 서버 로컬 디렉터리(REGULATIONS_DIR)에서만 읽는다 — 저장소에는 없다.
같은 파일을 매번 다시 읽지 않도록 수정시각 기준으로 캐시한다.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import rules_config

logger = logging.getLogger(__name__)

_TEXT_CACHE: dict[str, tuple[float, str]] = {}
SNIPPET_RADIUS = 60


def _extract_pdf_text(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx_text(path: str) -> str:
    import docx

    document = docx.Document(path)
    return "\n".join(p.text for p in document.paragraphs)


def _extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf_text(path)
    if ext == ".docx":
        return _extract_docx_text(path)
    raise ValueError(f"지원하지 않는 파일 형식: {ext}")


def get_text(rule: dict) -> str | None:
    """규정 원문 텍스트를 반환한다. 파일이 없거나 읽기 실패 시 None."""
    path = rules_config.rule_file_path(rule)
    if not os.path.isfile(path):
        return None

    mtime = os.path.getmtime(path)
    cached = _TEXT_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        text = _extract_text(path)
    except Exception as exc:
        logger.warning("규정 텍스트 추출 실패 (%s): %s", path, exc)
        return None

    _TEXT_CACHE[path] = (mtime, text)
    return text


def _build_snippet(text: str, query: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    idx = normalized.lower().find(query.lower())
    if idx == -1:
        return normalized[:120]

    start = max(0, idx - SNIPPET_RADIUS)
    end = min(len(normalized), idx + len(query) + SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(normalized) else ""
    return prefix + normalized[start:end] + suffix


def search(query: str) -> list[dict[str, Any]]:
    """제목 또는 본문에 검색어가 포함된 규정 목록을 반환한다."""
    q = query.strip()
    if not q:
        return []

    results: list[dict[str, Any]] = []
    for rule in rules_config.RULES:
        title_hit = q.lower() in rule["title"].lower()
        text = get_text(rule)
        content_hit = bool(text) and q.lower() in text.lower()

        if not (title_hit or content_hit):
            continue

        snippet = _build_snippet(text, q) if content_hit else "제목이 검색어와 일치합니다."
        results.append({
            "id": rule["id"],
            "title": rule["title"],
            "snippet": snippet,
            "available": text is not None,
        })

    return results
