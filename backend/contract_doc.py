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

import hashlib
import json
import math
import re
from datetime import date, timedelta

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


if __name__ == "__main__":
    selfcheck()
    print("contract_doc 검사 통과")
