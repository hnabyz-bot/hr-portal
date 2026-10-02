"""
사내 규정 원문(PDF/DOCX)을 장·조 단위로 나누고, 조항 단위로 검색한다.

파일은 서버 로컬 디렉터리(REGULATIONS_DIR)에서만 읽는다 — 저장소에는 없다.
같은 파일을 매번 다시 읽지 않도록 수정시각 기준으로 캐시한다.

직원 화면에 필요한 것:
- 검색 결과가 "어느 규정 몇 조"인지 바로 보일 것 (조항 단위 결과)
- 띄어쓰기가 달라도("연차휴가"/"연차 휴가"), 흔한 다른 말("출장비"→여비)로도 찾을 것
- 조항을 누르면 본문이 그 위치에서 열리고, 표는 원본 PDF 해당 쪽으로 바로 갈 것 (page)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import rules_config

logger = logging.getLogger(__name__)

_DOC_CACHE: dict[str, tuple[float, dict]] = {}
SNIPPET_RADIUS = 50

# 직원이 흔히 쓰는 말 → 규정에 실제 쓰인 말. 검색어가 왼쪽이면 오른쪽 말도 함께 찾는다.
SYNONYMS = {
    "출장비": ["여비", "활동비", "숙박비"],  # '일비'는 '…일 비밀번호'에도 걸려서 뺀다
    "출장": ["여비"],
    "월급": ["급여", "기본급"],
    "임금": ["급여"],
    "월급날": ["급여일"],
    "보너스": ["상여"],
    "야근": ["시간외", "야간근로"],
    "잔업": ["시간외"],
    "초과근무": ["시간외"],
    "특근": ["휴일근로"],
    "출산휴가": ["산전후휴가", "출산휴가"],
    "경조금": ["경조사"],
    "결혼": ["결혼", "경조사"],
    "퇴사": ["퇴직"],
    "사직": ["퇴직"],
    "표창": ["포상"],
    "이의": ["재심", "고충"],
    "점심": ["식사", "식대"],
    "식비": ["식대", "식사"],
    "성희롱": ["성희롱", "괴롭힘"],
    "갑질": ["괴롭힘"],
    "유류비": ["유류비", "자차"],
    "기름값": ["유류비"],
    "자기계발": ["자격증", "교육"],
}

_ARTICLE = re.compile(r"^제\s*(\d+)\s*조\s*[【\[]\s*(.+?)\s*[】\]]\s*(.*)$")
_CHAPTER = re.compile(r"^제\s*(\d+)\s*장\s*(.*)$")
_ADDENDA = re.compile(r"^부\s*칙$")
_IN_FORCE = re.compile(r"^이\s*규정은.*시행")  # '부칙' 제목 없이 이 문장만 있는 규정도 있다
_APPENDIX = re.compile(r"^-?\s*별\s*[첨표]")
_NOISE = re.compile(r"^(㈜에이치앤아비즈|H&abyz\s*\d*)$")
_PARA_START = re.compile(r"^([①-⑳]|\d+\)|\(\d+\)|[-*※•]|단\s*,)")
_DATE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _extract_pages(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        return [page.extract_text() or "" for page in PdfReader(path).pages]
    if ext == ".docx":
        import docx

        return ["\n".join(p.text for p in docx.Document(path).paragraphs)]
    raise ValueError(f"지원하지 않는 파일 형식: {ext}")


def _tidy(s: str) -> str:
    """'총    칙'·'목  적'처럼 한 글자씩 띄운 제목은 붙이고, PDF가 숫자 뒤에 넣은 공백('15 일')은 지운다."""
    parts = s.split()
    if len(parts) > 1 and all(len(p) == 1 for p in parts):
        return "".join(parts)
    s = re.sub(r"(\d) (?=[가-힣])", r"\1", " ".join(parts))
    return re.sub(r"제 (\d+) ?(?=[조항장호])", r"제\1", s)


def _reflow(lines: list[str]) -> str:
    """PDF 줄바꿈을 문단으로 되돌린다. 항목 기호로 시작하거나 앞줄이 짧으면(표·제목) 새 줄."""
    out: list[str] = []
    prev_raw = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            prev_raw = ""
            continue
        if out and prev_raw and not _PARA_START.match(line) and len(prev_raw.strip()) >= 30:
            # 앞줄 끝에 공백이 있었으면 단어 경계, 없으면 단어 중간에서 잘린 것
            out[-1] += (" " if prev_raw.endswith(" ") else "") + line
        else:
            out.append(line)
        prev_raw = raw
    return "\n".join(_tidy(l) for l in out)


def _parse(pages: list[str]) -> dict:
    """페이지 텍스트 → {effective, sections:[{key, chapter, no, title, page, body}]}.
    첫 장·조 앞의 표지(개정 이력·작성자 이름)는 버린다."""
    sections: list[dict] = []
    chapter = ""
    cur: dict | None = None
    in_tail = False  # 부칙 뒤 — 별첨 안의 '제1조'는 조항으로 나누지 않는다

    def start(**kw: Any) -> dict:
        sec = {"chapter": chapter, "no": None, "lines": [], **kw}
        sections.append(sec)
        return sec

    for page_no, text in enumerate(pages, 1):
        for raw in text.split("\n"):
            line = raw.strip()
            if _NOISE.match(line):
                continue
            if not in_tail and (m := _CHAPTER.match(line)):
                chapter = f"제{m.group(1)}장 {_tidy(m.group(2))}".strip()
                cur = None
                continue
            if not in_tail and (m := _ARTICLE.match(line)):
                cur = start(key=f"a{len(sections)}", no=int(m.group(1)), title=_tidy(m.group(2)), page=page_no)
                if m.group(3):
                    cur["lines"].append(m.group(3) + " ")
                continue
            if _ADDENDA.match(line) or (not in_tail and _IN_FORCE.match(line)):
                in_tail = True
                chapter = ""
                cur = start(key="addenda", title="부칙", page=page_no)
                if not _ADDENDA.match(line):
                    cur["lines"].append(raw)
                continue
            if in_tail and _APPENDIX.match(line):
                cur = start(key=f"x{len(sections)}", title=_tidy(line.lstrip("-").strip()), page=page_no)
                continue
            if cur is not None:
                cur["lines"].append(raw)

    effective = None
    for sec in sections:
        sec["body"] = _reflow(sec.pop("lines"))
        if sec["key"] == "addenda" and (m := _DATE.search(sec["body"])):
            effective = f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}"
    return {"effective": effective, "sections": sections}


def get_doc(rule: dict) -> dict | None:
    """규정을 장·조 단위로 나눈 결과. 파일이 없거나 읽기 실패 시 None."""
    path = rules_config.rule_file_path(rule)
    if not os.path.isfile(path):
        return None

    mtime = os.path.getmtime(path)
    cached = _DOC_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        doc = _parse(_extract_pages(path))
    except Exception as exc:
        logger.warning("규정 텍스트 추출 실패 (%s): %s", path, exc)
        return None

    doc["pdf"] = path.lower().endswith(".pdf")
    _DOC_CACHE[path] = (mtime, doc)
    return doc


def _pattern(term: str) -> str:
    """글자 사이 공백을 허용하는 정규식 조각 — '연차휴가'가 '연차 휴가'도 찾는다."""
    return r"\s*".join(re.escape(ch) for ch in term if not ch.isspace())


def expand_terms(query: str) -> list[list[str]]:
    """검색어를 단어별로 나누고 각 단어의 다른 말을 붙인다. 모든 단어가 맞아야 결과."""
    groups = []
    for word in query.split():
        alts = [word] + [a for a in SYNONYMS.get(word, []) if a != word]
        groups.append(alts)
    return groups


def _snippet(body: str, rx: re.Pattern) -> str:
    flat = re.sub(r"\s+", " ", body)
    m = rx.search(flat)
    if not m:
        return flat[:SNIPPET_RADIUS * 2]
    start = max(0, m.start() - SNIPPET_RADIUS)
    end = min(len(flat), m.end() + SNIPPET_RADIUS)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search_doc(doc: dict, groups: list[list[str]]) -> list[dict]:
    """한 규정 안에서 모든 검색어가 들어 있는 조항. 제목에 맞으면 점수를 더 준다."""
    rxs = [re.compile("|".join(_pattern(a) for a in alts), re.I) for alts in groups]
    any_rx = re.compile("|".join(r.pattern for r in rxs), re.I)
    hits = []
    for sec in doc["sections"]:
        hay = sec["title"] + "\n" + sec["body"]
        if not all(r.search(hay) for r in rxs):
            continue
        score = sum(3 * len(r.findall(sec["title"])) + len(r.findall(sec["body"])) for r in rxs)
        hits.append({
            "key": sec["key"], "no": sec["no"], "title": sec["title"], "page": sec["page"],
            "snippet": _snippet(sec["body"], any_rx) if any_rx.search(sec["body"]) else sec["body"][:100],
            "score": score,
        })
    return hits


def list_rules() -> list[dict[str, Any]]:
    out = []
    for rule in rules_config.RULES:
        doc = get_doc(rule)
        out.append({
            "id": rule["id"], "title": rule["title"], "available": doc is not None,
            "effective": doc and doc["effective"],
            "count": doc and sum(1 for s in doc["sections"] if s["no"] is not None),
        })
    return out


def search(query: str) -> dict[str, Any]:
    """규정별로 묶은 조항 결과. 맞는 조항이 많은 규정이 위로 온다."""
    groups = expand_terms(query.strip())
    if not groups:
        return {"terms": [], "results": []}

    results = []
    for rule in rules_config.RULES:
        doc = get_doc(rule)
        if not doc:
            continue
        hits = search_doc(doc, groups)
        title_hit = all(any(re.search(_pattern(a), rule["title"]) for a in alts) for alts in groups)
        if not hits and not title_hit:
            continue
        results.append({
            "id": rule["id"], "title": rule["title"], "effective": doc["effective"],
            "titleHit": title_hit, "hits": sorted(hits, key=lambda h: -h["score"]),
            "score": sum(h["score"] for h in hits) + (20 if title_hit else 0),
        })
    results.sort(key=lambda r: -r["score"])
    return {"terms": [a for alts in groups for a in alts], "results": results}


if __name__ == "__main__":
    # 자체 점검: python -m services.rules_search  (backend 폴더에서)
    pages = [
        "㈜에이치앤아비즈\n표지 개정 이력 홍길동\n",
        "제 1 장  총    칙\n제 1 조 【목  적】\n이 규정은 직원의 근무에 관하여 정함을 목적으로 하며 아래와 같이 적용하는 \n것으로 한다.\n"
        "제 18 조 【연차휴가】\n1) 회사는 개근한 사원에게는 15 일간의 연차 유급휴가를 부여하고 2 년마다 1 일을 가산하되 휴가일\n수 한도를 25 일로 한다.\n",
        "제 2 장 여비\n제 3 조 【여비계산】 실비로 정산한다.\n이 규정은 2026년 8월 1일부로 개정 시행한다.\n-별첨  \n제1조 【인적사고】\n산재 처리한다.\n",
    ]
    d = _parse(pages)
    secs = {s["key"]: s for s in d["sections"]}
    assert [s["no"] for s in d["sections"]] == [1, 18, 3, None, None], [s["no"] for s in d["sections"]]
    assert d["sections"][0]["title"] == "목적" and d["sections"][0]["chapter"] == "제1장 총칙"
    assert "홍길동" not in str(d), "표지(작성자 이름)는 버려야 한다"
    a18 = d["sections"][1]
    assert a18["page"] == 2 and "15일간의 연차" in a18["body"] and "휴가일수 한도를 25일로" in a18["body"], a18["body"]
    assert "적용하는 것으로" in d["sections"][0]["body"]
    assert d["effective"] == "2026.08.01"
    assert "인적사고" in d["sections"][4]["body"] and d["sections"][4]["title"] == "별첨"
    assert d["sections"][2]["body"] == "실비로 정산한다."
    # 검색: 띄어쓰기 무시, 다른 말, 여러 단어
    assert [h["no"] for h in search_doc(d, expand_terms("연차휴가"))] == [18]
    assert [h["no"] for h in search_doc(d, expand_terms("연차 휴 가"))] == [18]
    assert [h["no"] for h in search_doc(d, expand_terms("출장비"))] == [3]
    assert search_doc(d, expand_terms("연차 여비")) == []
    print("rules_search 자체 점검 통과")
