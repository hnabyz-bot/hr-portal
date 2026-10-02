"""
사내 규정 원문(PDF/DOCX)을 장·조 단위로 나누고, 조항 단위로 검색한다.

파일은 서버 로컬 디렉터리(REGULATIONS_DIR)에서만 읽는다 — 저장소에는 없다.
같은 파일을 매번 다시 읽지 않도록 수정시각 기준으로 캐시한다.

직원 화면에 필요한 것:
- 검색 결과가 "어느 규정 몇 조"인지 바로 보일 것 (조항 단위 결과)
- 띄어쓰기가 달라도("연차휴가"/"연차 휴가"), 흔한 다른 말("출장비"→여비)로도 찾을 것
- 조항을 누르면 본문이 그 위치에서 열리고, 원본 PDF 해당 쪽으로도 바로 갈 것 (page)
- 표(경조금표·자격증 목록·징계양정표 등)는 칸이 살아 있는 표로 보일 것 (tables)

PDF는 pdfplumber로 읽는다 — 표의 칸(합친 칸 포함)을 알아보고, 줄 끝 공백도 지켜
줄바꿈이 단어 사이인지 단어 중간인지 구분할 수 있다.
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
_TABLE_MARK = re.compile(r"⟦표(\d+)⟧")  # 본문 안 표 자리. 화면이 doc["tables"][n]으로 바꿔 그린다
_LIST_NO = re.compile(r"^(\d+\)|\(\d+\)|[①-⑳])$")
# 칸 안에서 새 줄로 남길 줄: 목록 기호로 시작하거나 '임원 : 6만원'처럼 '이름 :' 꼴
_CELL_BREAK = re.compile(r"^(\d+[.)]|\(\d+\)|\[[^\]]{1,3}\]|[①-⑳]|[-*※•·<]|[^\s:]{1,6}\s*:)")


def _filled(row: list) -> list[str]:
    return [c.strip() for c in row if c and c.strip()]


def _is_table(data: list[list], continued: bool) -> bool:
    """진짜 표만 고른다. 조문 문단을 테두리 상자로 감싼 규정도 있어서(인사평가규정 등)
    조 제목이 들어 있거나 머리줄이 없으면 표가 아니다. 앞 쪽 표의 이어짐이면 머리줄이 없어도 표."""
    cells = [c for r in data for c in _filled(r)]
    if len(cells) < 2 or any(_ARTICLE.match(c) or re.match(r"^제\s*\d+\s*조", c) for c in cells):
        return False
    if continued:
        return True
    head = _filled(data[0])
    return len(cells) >= 4 and len(head) >= 2 and not _LIST_NO.match(head[0])


def _white_rect(obj: dict) -> bool:
    """칸 배경으로 깔린 흰 사각형. 테두리가 안 보이는데도 칸선으로 잡혀 표를 잘게 쪼갠다(인사규정 제16조)."""
    c = obj.get("non_stroking_color")
    return (obj.get("object_type") == "rect" and isinstance(c, (tuple, list)) and len(c) > 0
            and all(isinstance(v, (int, float)) and v >= 0.95 for v in c))


def _cell_lines(chars: list[dict], bbox) -> list[dict]:
    """칸 안 글자를 줄로 묶는다 → [{text(줄 끝 공백 포함), x0, x1}]. 쪽 글자 목록에서 바로 고른다(crop보다 수십 배 빠름)."""
    x0, top, x1, bottom = bbox
    inside = [c for c in chars if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom]
    lines: list[list[dict]] = []
    for c in sorted(inside, key=lambda c: (c["top"], c["x0"])):
        if lines and abs(c["top"] - lines[-1][0]["top"]) <= 3:
            lines[-1].append(c)
        else:
            lines.append([c])
    out = []
    for ln in lines:
        ln.sort(key=lambda c: c["x0"])
        ink = [c for c in ln if c["text"].strip()]
        if ink:
            out.append({"text": "".join(c["text"] for c in ln), "x0": ink[0]["x0"], "x1": ink[-1]["x1"]})
    return out


def _cell_text(chars: list[dict], bbox) -> str:
    """칸 글자. 원본 칸이 좁아 생긴 줄바꿈은 이어 붙이고(줄 끝 공백이 있으면 띄어 씀), 일부러 바꾼 줄은 남긴다:
    목록·'이름 :' 줄, 또는 왼쪽 정렬인데 오른쪽이 많이 비고 끝난 줄."""
    out: list[str] = []
    prev = None
    for line in _cell_lines(chars, bbox):
        text = _tidy(line["text"].strip())
        if not text:
            continue
        joined = False
        if out and prev and not _CELL_BREAK.match(text):
            rgap, lgap = bbox[2] - prev["x1"], prev["x0"] - bbox[0]
            if not (rgap > 14 and lgap < rgap / 2):
                out[-1] += (" " if prev["text"].endswith(" ") else "") + text
                joined = True
        if not joined:
            out.append(text)
        prev = line
    return "\n".join(out)


def _merge_head(rows: list[list[dict]]) -> list[list[dict]]:
    """머리줄의 빈칸(원본의 보이지 않는 칸 나눔)을 아래 줄 칸 경계에 맞춰 옆 칸과 합친다.
    예: 출장 여비표 ['', '항목', '', '', '지원 금액', ''] → ['항목'(3칸), '지원 금액'(3칸)]."""
    if len(rows) < 2 or not any(not c["t"] for c in rows[0]) or any(c["rs"] > 1 for c in rows[0] + rows[1]):
        return rows
    head, groups, pos = rows[0], [], 0
    for c in rows[1]:
        groups.append((pos, pos + c["cs"]))
        pos += c["cs"]
    if sum(c["cs"] for c in head) != pos:
        return rows
    merged, i, at = [], 0, 0
    for g0, g1 in groups:
        part = []
        while i < len(head) and at < g1:
            part.append(head[i])
            at += head[i]["cs"]
            i += 1
        if at != g1 or len([c for c in part if c["t"]]) > 1:
            return rows
        merged.append({"t": next((c["t"] for c in part if c["t"]), ""), "cs": g1 - g0, "rs": 1})
    return [merged] + rows[1:]


def _edges(vals: list[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > 2:
            out.append(v)
    return out


def _loose_rows(region, t) -> list[list[dict]]:
    """표 위쪽 테두리가 없어 표 밖으로 빠진 줄(이어지는 쪽 첫 줄)을 낱말 위치로 칸에 나눠 표 줄로 되살린다."""
    boxes = [b for row in t.rows for b in row.cells if b]
    xs = _edges([b[0] for b in boxes] + [b[2] for b in boxes])
    lines: dict[int, list[list[str]]] = {}
    for w in region.extract_words(keep_blank_chars=True):
        cx = (w["x0"] + w["x1"]) / 2
        col = next((i for i in range(len(xs) - 1) if xs[i] <= cx < xs[i + 1]), None)
        if col is None:
            continue
        line = lines.setdefault(round(w["top"]), [[] for _ in range(len(xs) - 1)])
        line[col].append(w["text"].strip())
    return [[{"t": _tidy(" ".join(c)), "cs": 1, "rs": 1} for c in cols] for _, cols in sorted(lines.items())]


def _table_rows(t, chars) -> tuple[list[list[dict]], int]:
    """pdfplumber 표 → [[{t, cs, rs}]] (합친 칸은 cs/rs로). 칸 경계선 좌표로 몇 칸을 덮는지 센다."""
    boxes = [b for row in t.rows for b in row.cells if b]
    xs = _edges([b[0] for b in boxes] + [b[2] for b in boxes])
    ys = _edges([b[1] for b in boxes] + [b[3] for b in boxes])
    tops = [min(b[1] for b in row.cells if b) for row in t.rows] + [t.bbox[3]]
    rows = []
    for r, row in enumerate(t.rows):
        cells = []
        for c, b in enumerate(row.cells):
            if b is None:
                # 다른 칸에 합쳐진 자리면 건너뛴다. 아무 칸도 덮지 않은 빈틈(가로 테두리가 빠진 줄)이면 그 자리 글자로 칸을 만든다
                if len(row.cells) != len(xs) - 1:
                    continue
                hole = (xs[c], tops[r], xs[c + 1], tops[r + 1])
                cx, cy = (hole[0] + hole[2]) / 2, (hole[1] + hole[3]) / 2
                if hole[3] - hole[1] < 4 or any(o[0] <= cx <= o[2] and o[1] <= cy <= o[3] for o in boxes):
                    continue
                cells.append({"t": _cell_text(chars, hole), "cs": 1, "rs": 1})
                continue
            cells.append({
                "t": _cell_text(chars, b),
                "cs": sum(1 for x in xs if b[0] + 2 < x < b[2] - 2) + 1,
                "rs": sum(1 for y in ys if b[1] + 2 < y < b[3] - 2) + 1,
            })
        if cells:
            rows.append(cells)
    return _merge_head(rows), len(xs) - 1


def _join_left(rows: list[list[dict]]) -> None:
    """쪽이 바뀌거나 줄마다 빈칸으로 그려져 끊긴 합친 칸('성실의무 위반', '국가 기술자격', '자격기준' 등)을 잇는다.
    1) 스스로 폭을 다 채우는 줄까지 내려온 칸은 그 위에서 자른다(쪽 경계에서 한 줄 길게 읽힌 경우).
    2) 여러 줄을 덮는 칸이 끝난 바로 아래, 같은 자리의 빈칸은 그 칸에 합친다."""
    if not rows:
        return
    width = sum(c["cs"] for c in rows[0])
    tall: list[tuple[int, dict]] = []
    for i, row in enumerate(rows):
        if row and sum(c["cs"] for c in row) == width:
            for start, c in tall:
                if start + c["rs"] > i:
                    c["rs"] = i - start
            tall = []
        tall += [(i, c) for c in row if c["rs"] > 1]

    covered: set[tuple[int, int]] = set()
    above: dict[int, tuple[dict, int]] = {}  # 칸 자리 → (그 자리 맨 아래 칸, 끝나는 줄)
    for i, row in enumerate(rows):
        col, kept = 0, []
        for c in row:
            while (i, col) in covered:
                col += 1
            up = above.get(col)
            join = (not c["t"] and i > 1 and up is not None and up[1] == i
                    and up[0]["cs"] == c["cs"] and up[0]["rs"] > 1)
            owner = up[0] if join else c
            if join:
                owner["rs"] += c["rs"]
            else:
                kept.append(c)
            covered.update((i + dr, col + dc) for dr in range(c["rs"]) for dc in range(c["cs"]))
            above[col] = (owner, i + c["rs"])
            col += c["cs"]
        row[:] = kept


def _only_noise(text: str) -> bool:
    return all(not l.strip() or _NOISE.match(l.strip()) for l in text.split("\n"))


def _extract_pdf(path: str) -> tuple[list[str], list[dict]]:
    """쪽마다 '표 밖 글자'와 표 자리 표시(⟦표n⟧)를 위에서 아래 순서로 이어 붙인다.
    쪽 끝 표가 다음 쪽 맨 위 표로 이어지면 한 표로 합친다(자격증 목록·징계양정표처럼 여러 쪽짜리)."""
    import pdfplumber

    pages: list[str] = []
    tables: list[dict] = []
    open_table = None  # 앞 쪽이 표로 끝났으면 그 표 (다음 쪽에서 이어질 수 있음)
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            found = sorted(page.filter(lambda o: not _white_rect(o)).find_tables(), key=lambda t: t.bbox[1])
            x0, y0, x1, y1 = page.bbox
            picked = []
            y = y0
            for t in found:
                data = t.extract()
                above = page.crop((x0, y, x1, t.bbox[1])) if t.bbox[1] > y else None
                lead = [l for l in (above.extract_text() if above else "").split("\n") if l.strip() and not _NOISE.match(l.strip())]
                # 앞 쪽 표의 이어짐: 위에 아무것도 없거나, 표 바로 위 한두 줄이 표 폭 안에만 있을 때(테두리 빠진 첫 줄)
                loose = None
                if open_table is not None and not picked and 0 < len(lead) <= 2:
                    near = page.crop((t.bbox[0] - 2, max(y, t.bbox[1] - 16 * len(lead) - 4), t.bbox[2] + 2, t.bbox[1]))
                    if len([l for l in near.extract_text().split("\n") if l.strip()]) == len(lead):
                        loose = near
                continued = open_table is not None and not picked and (not lead or loose is not None)
                if _is_table(data, continued):
                    picked.append((t, continued, loose))
                    y = t.bbox[3]
            boxes = [(t.bbox[0], loose.bbox[1] if loose else t.bbox[1], t.bbox[2], t.bbox[3]) for t, _, loose in picked]

            def outside(obj: dict) -> bool:
                cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
                return not any(b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1 for b in boxes)

            text_only = page.filter(outside)
            parts: list[str] = []
            y = y0
            for (t, continued, loose), box in zip(picked, boxes):
                if box[1] > y:
                    parts.append(text_only.crop((x0, y, x1, box[1])).extract_text(keep_blank_chars=True))
                rows, ncols = _table_rows(t, page.chars)
                if loose is not None:
                    rows = _loose_rows(loose, t) + rows
                if continued:
                    gap = open_table["cols"] - ncols  # 이어진 쪽엔 맨 왼쪽 '구분' 칸이 없는 경우가 있다
                    if gap > 0:
                        rows = [[{"t": "", "cs": gap, "rs": len(rows)}] + rows[0]] + rows[1:]
                    open_table["rows"] += rows
                else:
                    open_table = {"page": page.page_number, "rows": rows, "cols": ncols}
                    tables.append(open_table)
                    parts.append(f"\n⟦표{len(tables) - 1}⟧\n")
                y = t.bbox[3]
            tail = text_only.crop((x0, y, x1, y1)).extract_text(keep_blank_chars=True) if y < y1 else ""
            parts.append(tail)
            if not picked or not _only_noise(tail):
                open_table = None
            pages.append("\n".join(parts))
    for t in tables:
        t.pop("cols")
        _join_left(t["rows"])
    return pages, tables


def _extract_pages(path: str) -> tuple[list[str], list[dict]]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        import docx

        return ["\n".join(p.text for p in docx.Document(path).paragraphs)], []
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
        if not line or _TABLE_MARK.fullmatch(line):
            if line:
                out.append(line)
            prev_raw = ""
            continue
        if out and prev_raw and not _PARA_START.match(line) and len(prev_raw.strip()) >= 30:
            # 앞줄 끝에 공백이 있었으면 단어 경계, 없으면 단어 중간에서 잘린 것
            out[-1] += (" " if prev_raw.endswith(" ") else "") + line
        else:
            out.append(line)
        prev_raw = raw
    return "\n".join(_tidy(l) for l in out)


def _parse(pages: list[str], tables: list[dict] | None = None) -> dict:
    """페이지 텍스트 → {effective, sections:[{key, chapter, no, title, page, body}], tables}.
    본문 속 ⟦표n⟧은 tables[n] 자리.
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
    used: set[int] = set()
    for sec in sections:
        sec["body"] = _reflow(sec.pop("lines"))
        used.update(int(n) for n in _TABLE_MARK.findall(sec["body"]))
        if sec["key"] == "addenda" and (m := _DATE.search(sec["body"])):
            effective = f"{m.group(1)}.{int(m.group(2)):02d}.{int(m.group(3)):02d}"
    tables = [t if i in used else None for i, t in enumerate(tables or [])]  # 번호는 그대로 두고 비운다
    return {"effective": effective, "sections": sections, "tables": tables}


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
        doc = _parse(*_extract_pages(path))
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


def _plain(body: str, tables: list[dict]) -> str:
    """표 자리 표시를 표 안 글자로 바꾼 본문 — 검색·미리보기용."""
    def cells(m: re.Match) -> str:
        t = tables[int(m.group(1))] or {"rows": []}
        return "\n".join(" ".join(c["t"] for c in row if c["t"]) for row in t["rows"])
    return _TABLE_MARK.sub(cells, body)


def search_doc(doc: dict, groups: list[list[str]]) -> list[dict]:
    """한 규정 안에서 모든 검색어가 들어 있는 조항(표 안 글자 포함). 제목에 맞으면 점수를 더 준다."""
    rxs = [re.compile("|".join(_pattern(a) for a in alts), re.I) for alts in groups]
    any_rx = re.compile("|".join(r.pattern for r in rxs), re.I)
    hits = []
    for sec in doc["sections"]:
        body = _plain(sec["body"], doc.get("tables", []))
        hay = sec["title"] + "\n" + body
        if not all(r.search(hay) for r in rxs):
            continue
        score = sum(3 * len(r.findall(sec["title"])) + len(r.findall(body)) for r in rxs)
        hits.append({
            "key": sec["key"], "no": sec["no"], "title": sec["title"], "page": sec["page"],
            "snippet": _snippet(body, any_rx) if any_rx.search(body) else body[:100],
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
    t = _parse(["제 4 조 【경조사 지원】\n1) 지원범위에 따라 지원하도록 하며 아래 표와 같이 지급하고 그 밖의 경우는 따로 정한다.\n⟦표0⟧\n2) 화환을 보낸다.\n"],
               [{"page": 1, "rows": [[{"t": "본인결혼", "cs": 1, "rs": 1}, {"t": "1,000,000원", "cs": 1, "rs": 1}]]}])
    assert t["sections"][0]["body"].split("\n") == [
        "1) 지원범위에 따라 지원하도록 하며 아래 표와 같이 지급하고 그 밖의 경우는 따로 정한다.", "⟦표0⟧", "2) 화환을 보낸다."]
    assert "본인결혼 1,000,000원" in search_doc(t, expand_terms("본인 결혼"))[0]["snippet"]
    t2 = _parse(["표지 ⟦표0⟧\n제 1 조 【목적】\n본문\n"], [{"page": 1, "rows": [[{"t": "작성자 홍길동", "cs": 1, "rs": 1}]]}])
    assert t2["tables"] == [None], "본문에 자리가 없는 표(표지 개정 이력·작성자)는 내보내지 않는다"
    assert _is_table([["구분", "경조금"], ["본인결혼", "1,000,000원"]], False)
    assert not _is_table([["제1조 [목적]", None], ["이 규정은", "..."]], False), "조문을 감싼 상자는 표가 아니다"
    assert not _is_table([["2)", "계속하여 근로한"], ["", "로자에게"], ["3)", "연차"]], False), "번호 문단 상자는 표가 아니다"
    assert _is_table([[None, "산업위생기사"], [None, "소방설비기사"]], True), "앞 쪽 표의 이어짐"
    def ch(text, x, top):  # 글자 하나 = 폭 6
        return [{"text": c, "x0": x + 6 * i, "x1": x + 6 * i + 6, "top": top, "bottom": top + 8} for i, c in enumerate(text)]
    box = (0, 0, 100, 100)
    assert _cell_text(ch("경영지도사(생산", 4, 10) + ch("관리)", 16, 22), (0, 0, 56, 40)) == "경영지도사(생산관리)", "좁은 칸 줄바꿈은 붙인다"
    assert _cell_text(ch("중과실 및 ", 6, 10) + ch("고의", 18, 22), (0, 0, 42, 40)) == "중과실 및 고의", "가운데 정렬 줄 + 줄 끝 공백이면 띄어 붙인다"
    assert _cell_text(ch("[C] 근로자 ", 2, 10) + ch("기타 상황", 2, 22), box) == "[C] 근로자\n기타 상황", "왼쪽 정렬·오른쪽 빈 줄은 일부러 바꾼 줄"
    assert _cell_text(ch("임원 : 6만원", 20, 10) + ch("직원 : 4만원", 20, 22), box) == "임원 : 6만원\n직원 : 4만원"
    c1 = lambda t, cs=1: {"t": t, "cs": cs, "rs": 1}
    assert _merge_head([[c1(""), c1("항목"), c1(""), c1(""), c1("금액"), c1("")], [c1("일비", 3), c1("4만원", 3)]])[0] == [c1("항목", 3), c1("금액", 3)]
    assert _merge_head([[c1("구분"), c1("")], [c1("A"), c1("B")]])[0] == [c1("구분"), c1("")], "아래 줄과 칸이 같으면 그대로"
    rows = [[c1("구분"), c1("항목")],
            [{"t": "성실의무", "cs": 1, "rs": 2}, c1("1.")], [c1("2.")],
            [c1(""), c1("3.")], [c1(""), c1("4.")], [c1("질서"), c1("5.")]]
    _join_left(rows)
    assert rows[1][0]["rs"] == 4 and rows[3] == [c1("3.")] and rows[4] == [c1("4.")] and rows[5][0]["t"] == "질서", \
        "끊긴 왼쪽 합친 칸이 이어진다"
    rows = [[c1("구분"), c1("항목")], [{"t": "루비상", "cs": 1, "rs": 3}, c1("a")], [c1("b")], [c1(""), c1("c")]]
    _join_left(rows)
    assert rows[1][0]["rs"] == 3 and rows[3] == [c1("c")], "한 줄 길게 읽힌 칸은 잘라 맞춘 뒤 잇는다"
    rows = [[c1("구분"), c1("기준"), c1("내용")],
            [{"t": "에메랄드", "cs": 1, "rs": 3}, {"t": "자격기준", "cs": 1, "rs": 2}, c1("1)")], [c1("2)")],
            [c1(""), c1("팀웍")]]
    _join_left(rows)
    assert rows[1][1]["rs"] == 3 and rows[3] == [c1("팀웍")], "가운데 칸도 쪽이 바뀌며 끊기면 잇는다"
    print("rules_search 자체 점검 통과")
