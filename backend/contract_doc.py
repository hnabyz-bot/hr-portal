"""연봉계약서 — 규칙(계산식·계약 기간·생년월일) · 본문 블록 · 문서 고유번호 (2026-10-22 설계 3장 · 5.1).

양식 문장의 원본은 이 파일 한 곳이다. 화면은 build_blocks 가 준 블록을 그리고, 서버 PDF 도 같은 블록으로 만든다.
(시연 화면은 서버가 없어 public/index.html 의 ctDocHtml 을 그대로 쓴다 — 양식을 바꿀 때는 둘과 TPL 을 함께 고친다.)

블록 한 개 = 아래 중 하나. 글 안의 "\\n" 은 줄바꿈이다(그리는 쪽이 <br> 로 바꾼다).
  {"t":"title","text"}                        제목
  {"t":"kv","rows":[[라벨,값,라벨,값] 또는 [라벨,값]]}   두 칸 줄(라벨·값 한 쌍)은 값이 가로 전체
  {"t":"h","text"}                            조 제목
  {"t":"p","runs":[[글, 굵게:bool], …]}        문단
  {"t":"table","head":머리 줄 수,"rows":[[칸 …]]}   칸 = 글 또는 {"c":글,"span":n}
  {"t":"center","text"}                       가운데 줄
  {"t":"signs","employer","worker"}           사업주 · 근로자 서명 자리(직인·서명 그림은 그리는 쪽이 얹는다)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
from datetime import date, timedelta
from xml.sax.saxutils import escape

from PIL import Image  # reportlab 이 이미 끌고 오는 Pillow
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TPL = "2027-1"  # 양식 판 번호 — 문장·표를 고치면 올린다(문서 고유번호에도 들어간다)
COMPANY_NAME = "㈜에이치앤아비즈"


def _round(x: float) -> int:
    return math.floor(x + 0.5)  # 사사오입 — 화면 Math.round 와 같은 값(양수만 다룬다)


def calc(base: int, ability: int, pos: int = 0, site: int = 0, qual: int = 0) -> dict:
    """양식 계산식(화면 ctCalc 와 같다): 명절상여 = 기본급, 시급 = (기본급+능력급+직책수당+현장관리수당+명절상여/12)/209,
    시간외 = 시급×10×1.5, 월 급여 계 = 기본급+시간외+능력급+직책수당+현장관리수당+자격수당,
    총 계약 연봉 = (월 급여 계 − 자격수당 − 직책수당 − 현장관리수당)×12 + 명절상여. 각각 원 단위 반올림."""
    bonus = base
    hourly = (base + ability + pos + site + bonus / 12) / 209
    ot = hourly * 10 * 1.5
    monthly = base + ot + ability + pos + site + qual
    annual = (monthly - qual - pos - site) * 12 + bonus
    return {"hourly": _round(hourly), "ot": _round(ot), "monthly": _round(monthly), "annual": _round(annual), "bonus": bonus}


def round_period(year: int) -> tuple[str, str]:
    """회차(해) 계약 기간: 그해 3월 1일 ~ 다음 해 2월 말일(윤년 자동)."""
    return f"{year}-03-01", (date(year + 1, 3, 1) - timedelta(days=1)).isoformat()


def upcoming_year(today: date) -> int:
    """다가오는 회차: 3월 이후면 내년, 1~2월이면 올해."""
    return today.year + 1 if today.month >= 3 else today.year


def running_year(today: date) -> int:
    return upcoming_year(today) - 1


def birth6_ok(s: str) -> bool:
    """생년월일 6자리(YYMMDD) — 19YY 또는 20YY 중 하나로 실제 있는 날이면 통과."""
    if not isinstance(s, str) or not re.fullmatch(r"[0-9]{6}", s):  # [0-9]: \d 는 다른 나라 숫자도 받는다
        return False
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    for century in (1900, 2000):
        try:
            date(century + yy, mm, dd)
            return True
        except ValueError:
            pass
    return False


def _won(n: int) -> str:
    return f"{n:,}원"


def _kdate(ymd: str) -> str:
    y, m, d = (int(v) for v in ymd[:10].split("-"))
    return f"{y}년 {m}월 {d}일"


def _p(*runs) -> dict:
    """문단 — 글은 그냥 글, (글, True) 는 굵게."""
    return {"t": "p", "runs": [[r, False] if isinstance(r, str) else [r[0], bool(r[1])] for r in runs]}


def build_blocks(*, company: dict, worker: dict, pay: dict, start: str, end: str, signed_on: str | None) -> list[dict]:
    """계약서 본문. company {name, ceo, addr, kind} · worker {name, birth6, dept, title} · pay {base, ability, pos, site, qual}.
    signed_on = 근로자가 서명한 날(없으면 "(서명한 날)")."""
    k = calc(pay["base"], pay["ability"], pay.get("pos", 0), pay.get("site", 0), pay.get("qual", 0))
    site_qual = pay.get("site", 0) + pay.get("qual", 0)
    name = worker["name"]
    dept_title = f"{worker['dept']} · {worker['title']}" if worker.get("title") else worker["dept"]
    ceo_line = f"{company['name']} 대표이사 {company['ceo']}"
    return [
        {"t": "title", "text": "연 봉 계 약 서"},
        {"t": "kv", "rows": [
            ["사업주", f"{company['name']} · 대표이사 {company['ceo']}", "사업의 종류", company["kind"]],
            ["소재지", company["addr"]],
            ["근로자", f"{name} (생년월일 {worker['birth6']})", "소속 · 직급", dept_title],
        ]},
        {"t": "h", "text": "제1조 (임금)"},
        _p("총 계약 연봉금액 : ", (_won(k["annual"]), True), " ((월급여 − 현장관리수당·자격수당, 직책수당) × 12 + 명절상여)"),
        _p("1) 급여 구성은 기본급과 수당으로 구성되며, 내역은 아래와 같다."),
        {"t": "table", "head": 2, "rows": [
            [{"c": "월 급여 / 月", "span": 5}, {"c": "연봉 외 수당", "span": 2}, "명절상여/年"],
            ["계", "시간급", "기본급(209H)", "시간외근로(10H)", "능력급(209H)", "현장관리수당/자격수당", "직책수당", ""],
            [_won(k["monthly"]), _won(k["hourly"]), _won(pay["base"]), _won(k["ot"]), _won(pay["ability"]),
             _won(site_qual), _won(pay.get("pos", 0)), _won(k["bonus"])],
        ]},
        _p("2) 상기 월급여는 기본급 209시간, 시간외근로수당(연장/야간/휴일근로를 포함한다) 10시간 150% 상당액을 포함한다.\n"
           "  - 직책수당, 현장관리수당·자격수당은 사업주의 급여규정에 의거 지급한다."),
        _p("3) 중도 입·퇴사자 월급여는 해당월의 일수로 일할 계산한다."),
        _p("4) 직책수당, 현장관리수당·자격수당은 연봉에 포함하지 않으며, 면직 시 해당월의 일수로 일할 계산한다."),
        _p("5) 교대수당은 매월 1일부터 말일 기간 동안 교대근무(주말근무) 4회 이상 시 일당 12,500원을 지급한다."),
        _p("6) 명절상여는 연봉에 포함되며(설, 추석 2회로 분할하여 지급), 근속 1년 이상자에 한하여 100% 지급되며, 근속 1년 미만의 경우 아래 표와 같이 지급된다."),
        {"t": "table", "head": 1, "rows": [
            ["구분", "1개월 미만", "3개월 미만", "3개월 이상\n6개월 미만", "6개월 이상\n1년 미만", "1년 이상"],
            ["설", "10만원", "30만원", "월 기본급의 30%", "월 기본급의 40%", "월 기본급의 50%"],
            ["추석", "10만원", "30만원", "월 기본급의 30%", "월 기본급의 40%", "월 기본급의 50%"],
        ]},
        {"t": "h", "text": "제2조 (임금 지급방법 및 시기)"},
        _p("1) 임금 산정기간 및 지급일 : 당월 1일 ~ 당월 말일까지 기간으로 하여 익월 20일에 지급한다."),
        _p("2) 임금 지급방법 : 본인 명의 통장 입금"),
        {"t": "h", "text": "제3조 (기밀유지)"},
        _p("1) 근로계약 내용 및 급여명세서는 절대 비밀을 유지하며, 이를 위반 시는 이로 인한 모든 민·형사상의 불이익을 감수한다."),
        _p("2) 재직하는 동안 취득한 업무상 기밀 및 회사의 중요한 정보를 누설함으로써 회사에 피해를 끼치는 일이 없도록 할 것이며, "
           "만일 이를 위반함으로써 발생하는 법적, 사회적 문제에 대해 근로자(을)은 민·형사상 책임을 진다."),
        {"t": "h", "text": "제4조 (계약기간)"},
        _p("1) 계약기간 : ", (f"{_kdate(start)}부터 {_kdate(end)}", True), "로 한다. 단, 회사 경영상의 이유가 있을 때에는 영업 종료일로 한다."),
        _p("2) 입사일로부터 3개월 간은 수습기간으로 하며, 이 기간 중 사고를 유발하거나 직원으로 부적격하다고 판단될 때는 중도에 해고예고 없이 계약을 해지할 수 있다. "
           "이 기간의 임금은 회사가 정한 지급기준에 의거하여 지급한다."),
        _p("3) 근무 중 고의로 회사에 손해를 끼치거나, 불성실한 태도로 근무 또는 잘못이 있을 시는 그에 따른 이 계약을 해지(해고)한다."),
        {"t": "h", "text": "제5조 (그 밖의 사항)"},
        _p("1) 이 계약서에 명시되지 않은 사항은 취업규칙 및 근로기준법의 관련 조항을 준용하도록 한다."),
        _p("2) 이 계약서는 2부를 작성하여 각자 1부씩 보관한다. ※ 근로계약의 내용을 이해하고 1부를 교부받았음을 확인함."),
        _p("3) 사업주와 근로자의 계약 내용에 관한 모든 법적 문제는 사업주의 관할 법원을 원칙으로 한다."),
        _p("4) 이 계약을 연장 또는 갱신한 때에 이 계약의 내용은 조정·변경한 사항을 제외하고 계속 유효하다."),
        {"t": "center", "text": "위와 같이 연봉 계약을 체결합니다."},
        {"t": "center", "text": _kdate(signed_on) if signed_on else "(서명한 날)"},
        {"t": "signs", "employer": ceo_line, "worker": name},
    ]


def doc_hash(blocks: list) -> str:
    """서명 시점 본문의 고유번호(SHA-256) — 양식 판 번호와 블록 전체를 한 번에 묶는다."""
    body = json.dumps({"tpl": TPL, "blocks": blocks}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


# ----- 서명본 PDF -----
# 화면(ct-doc)과 같은 모양을 reportlab 으로 다시 그린다. 글꼴은 export_utils 가 찾아 둔 한글 글꼴(없으면 Helvetica — 한글은 안 보여도 PDF 는 만들어진다).
_BOLD_FONTS = ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "C:/Windows/Fonts/malgunbd.ttf"]
_INK, _MUTED, _LINE, _SHADE = colors.HexColor("#1F2933"), colors.HexColor("#66707F"), colors.HexColor("#9AA6B2"), colors.HexColor("#F4F8FB")
_W = 174 * mm  # A4 가로 210 − 좌우 여백 18
# 글꼴에 없으면 그리는 쪽에서만 바꿀 글자(블록은 해시 대상이라 그대로). 맑은 고딕에는 U+2212(−)가 없어 빈칸으로 나온다.
_MAX_IMG_SIDE, _MAX_IMG_PIXELS = 4000, 8_000_000  # 직인·서명 그림 한 변 / 전체 픽셀 상한 — 이보다 크면 PDF 를 만들기 전에 ValueError
_SUBST = {"\xa0": " ", "−": "-"}


def _fonts() -> tuple[str, str]:
    """(보통, 굵게) 글꼴 이름. 굵은 글꼴 파일이 없으면 굵게도 보통 글꼴 — 한글 글꼴에서 Helvetica-Bold 로 새지 않게 한다."""
    from export_utils import _register_korean_font  # 가져오는 데 무거워서 여기서(계산·본문 규칙만 쓰는 쪽은 안 부른다)
    font = _register_korean_font()
    if font == "Helvetica":
        return font, "Helvetica-Bold"
    bold = font + "Bold"
    if bold not in pdfmetrics.getRegisteredFontNames():
        for path in _BOLD_FONTS:
            try:
                pdfmetrics.registerFont(TTFont(bold, path))
                break
            except Exception:
                continue
    if bold not in pdfmetrics.getRegisteredFontNames():
        bold = font
    registerFontFamily(font, normal=font, bold=bold, italic=font, boldItalic=bold)
    return font, bold


def _has_glyph(font: str, ch: str) -> bool:
    cmap = getattr(getattr(pdfmetrics.getFont(font), "face", None), "charToGlyph", None)
    return cmap is None or ord(ch) in cmap


def _reader(png: bytes, what: str) -> ImageReader:
    """그림 읽개 — 머리글만 읽어 크기를 먼저 보고(getSize 는 풀어 읽지 않는다), 너무 크면 그리기 전에 거부한다.
    바이트 수만 막으면 투명 PNG 한 장이 수천×수천 픽셀로 풀려 서버 메모리를 먹는다(서명 그림은 근로자가 보낸다)."""
    try:
        r = ImageReader(io.BytesIO(png))
    except Image.DecompressionBombError:  # 1억 7천만 픽셀이 넘으면 Pillow 가 먼저 거부한다 — 같은 ValueError 로 맞춘다
        raise ValueError(f"{what} 그림이 너무 커요") from None
    w, h = r.getSize()
    if w > _MAX_IMG_SIDE or h > _MAX_IMG_SIDE or w * h > _MAX_IMG_PIXELS:
        raise ValueError(f"{what} 그림이 너무 커요({w}×{h})")
    return r


class _Signs(Flowable):
    """사업주 · 근로자 서명 줄 — 직인은 글 "(인)" 자리에 겹쳐 얹고, 서명은 이름 옆에 놓는다. 그림이 없으면 빈칸."""

    def __init__(self, employer, worker, seal_png, sign_png, font):
        super().__init__()
        self.employer, self.worker, self.font = employer, worker, font
        self.seal = _reader(seal_png, "직인") if seal_png else None
        self.sign = _reader(sign_png, "서명") if sign_png else None

    def wrap(self, aw, ah):
        return _W, 22 * mm

    def draw(self):
        c, font, base, size = self.canv, self.font, 10 * mm, 10.5
        left_w = _W * 0.58
        label, mark = f"사업주 : {self.employer} ", "(인)"
        need = stringWidth(label + mark, font, size)
        if need > left_w:  # 한 줄에 안 들어가면 글자를 줄인다(잘리면 안 된다)
            size = size * left_w / need
        c.setFillColor(_INK)
        c.setFont(font, size)
        c.drawString(0, base, label)
        x_mark = stringWidth(label, font, size)
        c.setFillColor(_MUTED)
        c.drawString(x_mark, base, mark)
        if self.seal:  # 약 18mm 정사각 칸 안에 비율대로, "(인)" 가운데에
            cx = x_mark + stringWidth(mark, font, size) / 2
            c.drawImage(self.seal, cx - 9 * mm, base + size * 0.35 - 9 * mm, 18 * mm, 18 * mm, mask="auto", preserveAspectRatio=True, anchor="c")
        x0 = left_w + 6 * mm
        c.setFillColor(_INK)
        c.setFont(font, 10.5)
        text = f"근로자 : {self.worker}"
        c.drawString(x0, base, text)
        if self.sign:  # 높이 약 12mm, 이름 바로 옆
            x1 = x0 + stringWidth(text, font, 10.5) + 3 * mm
            c.drawImage(self.sign, x1, base - 3 * mm, min(_W - x1, 45 * mm), 12 * mm, mask="auto", preserveAspectRatio=True, anchor="w")


def render_pdf(blocks: list, *, seal_png: bytes | None, sign_png: bytes | None, esign: dict | None) -> bytes:
    """서명본 PDF(A4 세로) — 블록을 하나도 빼지 않고 그린다(설계 5.4). 서명하는 순간 서버가 부르고 암호화해 보관한다.
    esign {"signedAt","name","how","hash"} 가 있으면 끝에 "전자서명 정보" 상자를 붙인다. 그림 바이트가 깨졌으면 예외(서명본을 말없이 비우지 않는다)."""
    font, bold = _fonts()

    def style(name, **kw):
        return ParagraphStyle(name, fontName=kw.pop("fontName", font), wordWrap="CJK", textColor=kw.pop("textColor", _INK), **kw)

    body = style("body", fontSize=9, leading=14, spaceAfter=1.5)
    st = {
        "title": style("title", fontName=bold, fontSize=18, leading=24, alignment=TA_CENTER, spaceAfter=12),
        "h": style("h", fontName=bold, fontSize=10.5, leading=15, spaceBefore=9, spaceAfter=3, keepWithNext=1),
        "center": style("center", fontSize=10, leading=15, alignment=TA_CENTER, spaceBefore=14),
        "date": style("date", fontSize=8.5, leading=13, alignment=TA_CENTER, textColor=_MUTED),
        "k": style("k", fontName=bold, fontSize=8.5, leading=12, textColor=colors.HexColor("#556070")),
        "v": style("v", fontSize=8.5, leading=12),
        "th": style("th", fontName=bold, fontSize=7.5, leading=10.5, alignment=TA_CENTER, textColor=colors.HexColor("#556070")),
        "td": style("td", fontSize=7.5, leading=10.5, alignment=TA_CENTER),
        "box": style("box", fontSize=8.5, leading=13),
    }
    swap = {ch: alt for ch, alt in _SUBST.items() if not _has_glyph(font, ch)}

    def m(text: str) -> str:  # Paragraph 용 글: 글꼴에 없는 글자 바꾸기 → &<> 막기 → 줄바꿈
        for ch, alt in swap.items():
            text = text.replace(ch, alt)
        return escape(text).replace("\n", "<br/>")

    def grid(rows, widths, shade, cell_style):
        """칸 글을 Paragraph 로, 칸 합치기(span)는 SPAN 으로. shade(r, c) 면 머리·라벨 칸 색."""
        data, cmds = [], [("GRID", (0, 0), (-1, -1), 0.5, _LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
        for r, row in enumerate(rows):
            line, col = [], 0
            for cell in row:
                text, span = (cell["c"], cell.get("span", 1)) if isinstance(cell, dict) else (cell, 1)
                line += [Paragraph(m(text), cell_style(r, col))] + [""] * (span - 1)
                if span > 1:
                    cmds.append(("SPAN", (col, r), (col + span - 1, r)))
                if shade(r, col):
                    cmds.append(("BACKGROUND", (col, r), (col + span - 1, r), _SHADE))
                col += span
            data.append(line)
        t = Table(data, colWidths=widths)
        t.setStyle(TableStyle(cmds))
        return t

    story, centers = [], 0
    for blk in blocks:
        kind = blk["t"]
        if kind == "title":
            story.append(Paragraph(m(blk["text"]), st["title"]))
        elif kind == "h":
            story.append(Paragraph(m(blk["text"]), st["h"]))
        elif kind == "p":
            story.append(Paragraph("".join(f"<b>{m(t)}</b>" if b else m(t) for t, b in blk["runs"]), body))
        elif kind == "kv":  # 칸이 4개면 라벨·값·라벨·값, 2개면 라벨 + 값(나머지 가로 전체)
            rows = [r if len(r) == 4 else [r[0], {"c": r[1], "span": 3}] for r in blk["rows"]]
            story += [Spacer(1, 2 * mm),
                      grid(rows, [22 * mm, 65 * mm, 24 * mm, 63 * mm], lambda r, c: c in (0, 2), lambda r, c: st["k"] if c in (0, 2) else st["v"]),
                      Spacer(1, 2 * mm)]
        elif kind == "table":  # 칸 너비는 글이 안 꺾일 만큼(칸 합치기 줄은 빼고) 재서 쪽 너비에 맞춰 비례로 늘이거나 줄인다
            head, need = blk["head"], {}
            for row in blk["rows"]:
                col = 0
                for cell in row:
                    text, span = (cell["c"], cell["span"]) if isinstance(cell, dict) else (cell, 1)
                    if span == 1:
                        w = max((stringWidth(seg, bold, 7.5) for seg in re.split(r"[\n/]", text)), default=0)
                        need[col] = max(need.get(col, 0), w + 12)
                    col += span
            n = col
            widths = [max(need.get(i, 0), 24) for i in range(n)]
            widths = [_W * w / sum(widths) for w in widths]
            story += [Spacer(1, 1 * mm),
                      grid(blk["rows"], widths, lambda r, c: r < head, lambda r, c: st["th"] if r < head else st["td"]),
                      Spacer(1, 2 * mm)]
        elif kind == "center":  # 첫 줄은 맺음 문장, 그다음은 체결일(화면의 list-sub 처럼 옅고 작게)
            story.append(Paragraph(m(blk["text"]), st["center"] if centers == 0 else st["date"]))
            centers += 1
        elif kind == "signs":
            story += [Spacer(1, 4 * mm), _Signs(blk["employer"], blk["worker"], seal_png, sign_png, font)]
        else:
            raise ValueError(f"알 수 없는 본문 블록: {kind}")  # 모르는 블록을 건너뛰면 서명본이 본문과 달라진다
    if esign:
        lines = [Paragraph("전자서명 정보", style("boxh", fontName=bold, fontSize=9, leading=13)),
                 Paragraph(m(f"서명 {esign['signedAt']} · {esign['name']} · 본인 확인: {esign['how']}"), st["box"]),
                 Paragraph(m(f"문서 고유번호 {esign['hash'][:16]}…"), st["box"]),
                 Paragraph("이 계약서는 전자문서로 작성·교부합니다(근로기준법 제17조).", st["box"])]
        box = Table([[lines]], colWidths=[_W])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.7, _LINE), ("BACKGROUND", (0, 0), (-1, -1), _SHADE),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story += [Spacer(1, 6 * mm), box]
    # 맺음 문장부터 끝까지(날짜 · 서명 · 전자서명 상자)는 한 쪽에 같이 둔다 — 서명만 다음 쪽에 혼자 남지 않게
    first = next((i for i, f in enumerate(story) if isinstance(f, Paragraph) and f.style is st["center"]), None)
    if first is not None:
        story[first:] = [KeepTogether(story[first:])]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title="연봉 계약서").build(story)
    return buf.getvalue()


def selfcheck() -> None:
    assert calc(2418900, 1211000, 300000) == {"hourly": 19768, "ot": 296517, "monthly": 4226417, "annual": 49535908, "bonus": 2418900}
    assert calc(2418900, 1211000, 300000, 150000, 50000) == {"hourly": 20486, "ot": 307283, "monthly": 4437183, "annual": 49665095, "bonus": 2418900}
    assert round_period(2027) == ("2027-03-01", "2028-02-29") and round_period(2026) == ("2026-03-01", "2027-02-28")
    assert upcoming_year(date(2026, 10, 22)) == 2027 and upcoming_year(date(2027, 1, 10)) == 2027 and upcoming_year(date(2027, 3, 1)) == 2028
    assert running_year(date(2026, 10, 22)) == 2026
    assert birth6_ok("900315") and birth6_ok("040229") and not birth6_ok("900230") and not birth6_ok("90031") and not birth6_ok("901315")
    assert not birth6_ok("90031५") and not birth6_ok("9003150") and not birth6_ok("900315\n") and not birth6_ok(900315)  # 다른 나라 숫자 · 길이 · 줄바꿈 · 글 아님
    co = {"name": COMPANY_NAME, "ceo": "○○○", "addr": "가상 주소", "kind": "제조업"}
    wk = {"name": "가상하나", "birth6": "900315", "dept": "가상팀", "title": "프로"}
    pay = {"base": 2418900, "ability": 1211000, "pos": 300000, "site": 150000, "qual": 50000}
    b = build_blocks(company=co, worker=wk, pay=pay, start="2027-03-01", end="2028-02-29", signed_on="2027-02-10")
    text = json.dumps(b, ensure_ascii=False)
    assert "가상하나 (생년월일 900315)" in text and "49,665,095원" in text and "200,000원" in text and "현장관리수당/자격수당" in text
    assert "2027년 3월 1일부터 2028년 2월 29일" in text and "2027년 2월 10일" in text and b[-1] == {"t": "signs", "employer": "㈜에이치앤아비즈 대표이사 ○○○", "worker": "가상하나"}
    assert doc_hash(b) == doc_hash(json.loads(text)) and len(doc_hash(b)) == 64
    assert doc_hash(b) != doc_hash(build_blocks(company=co, worker=wk, pay={**pay, "qual": 50001}, start="2027-03-01", end="2028-02-29", signed_on="2027-02-10"))
    assert "(서명한 날)" in json.dumps(build_blocks(company=co, worker=wk, pay={"base": 1, "ability": 0, "pos": 0, "site": 0, "qual": 0},
                                                 start="2027-03-01", end="2028-02-29", signed_on=None), ensure_ascii=False)
    # 표 모양: 첫 표는 머리 2줄 · 8칸, 둘째 표는 머리 1줄 · 6칸. 굵게는 총액과 계약 기간뿐. 직급이 없으면 소속만.
    t1, t2 = [x for x in b if x["t"] == "table"]
    assert t1["head"] == 2 and t2["head"] == 1
    assert [sum(c["span"] if isinstance(c, dict) else 1 for c in r) for r in t1["rows"]] == [8, 8, 8] and [len(r) for r in t2["rows"]] == [6, 6, 6]
    assert [r[0] for x in b if x["t"] == "p" for r in x["runs"] if r[1]] == ["49,665,095원", "2027년 3월 1일부터 2028년 2월 29일"]
    assert b[1]["rows"][2][3] == "가상팀 · 프로"
    assert build_blocks(company=co, worker={**wk, "title": ""}, pay=pay, start="2027-03-01", end="2028-02-29", signed_on=None)[1]["rows"][2][3] == "가상팀"
    # 서명본 PDF — 구조만 본다(글꼴이 없는 CI 에서도 만들어져야 한다). 모양은 사람이 눈으로 확인한다.
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")  # 1x1 PNG
    pdf = render_pdf(b, seal_png=png, sign_png=png, esign={"signedAt": "2027-02-10 14:22", "name": "가상하나", "how": "하이웍스 로그인", "hash": doc_hash(b)})
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 2000
    assert render_pdf(b, seal_png=None, sign_png=None, esign=None).startswith(b"%PDF")
    # 그림 크기 상한 — 바이트는 작아도 픽셀이 큰 PNG 는 그리기 전에 거부한다(머리글만 읽는다). 머리글만 있는 가짜 PNG 로 본다.
    import struct, zlib

    def fake_png(w, h):
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b"")

    for kw in ({"seal_png": fake_png(5000, 5000)}, {"sign_png": fake_png(5000, 5000)}, {"sign_png": fake_png(4000, 2001)}, {"seal_png": fake_png(4001, 1)}, {"sign_png": fake_png(60000, 60000)}):  # 마지막은 Pillow 폭탄 방지 구간
        try:
            render_pdf(b, **{"seal_png": None, "sign_png": None, "esign": None, **kw})
            raise AssertionError("큰 그림을 받았다")
        except ValueError:
            pass
    assert _reader(fake_png(4000, 2000), "직인").getSize() == (4000, 2000)  # 한 변 4000 · 800만 픽셀은 딱 통과


if __name__ == "__main__":
    selfcheck()
    print("contract_doc 검사 통과")
