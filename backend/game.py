"""X-ray 특공대 게임 ↔ 인사포털 (2026-10-20 인사담당자 결정 — 로그인해야 게임, 실제 직원 랭킹·선물, PC·휴대폰이 같은 기록).

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/game/save           내 게임 기록(출동권·칭호·모자·최고점 …) — 본인만
  POST /api/game/save           저장. 판 번호가 다르면 409 + 서버 것 (워크스페이스와 같은 방식)
  POST /api/game/score          한 판 점수 → 이번 주 최고점 (한 주 = 한국 시간 월요일 0시부터)
  GET  /api/game/rank           이번 주 · 역대 최고 · 부서 대항전 · 명예의 전당 · 지난번 내 순위(주간 보상)
  GET  /api/game/gifts          동료 선물: 보낼 수 있는 동료 · 오늘 보낸 사람 · 받은 선물함
  POST /api/game/gifts          {"to": id} 출동권 1장 보내기 — 하루 3명 · 한 사람에게 하루 1장
  POST /api/game/gifts/claim    {"ids": [...]} 받기 — 하루 5장까지

점수·기록은 게임 화면이 보낸다 — 마음먹고 고치면 막을 수 없다(사내 놀이라 모양·범위만 본다).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, g, jsonify, request

import auth

bp = Blueprint("game", __name__)

SAVE_MAX_BYTES = 200_000  # 지금 기록은 2KB 안팎
SAVE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
SCORE_MAX = 100_000_000
SEND_MAX, RECV_MAX, GIFT_DAYS = 3, 5, 7  # 게임 화면(GIFT_SEND_MAX · GIFT_RECV_MAX)과 같게


def _week(d: date) -> str:
    """그 주 월요일 (한국 날짜)."""
    return (d - timedelta(days=d.weekday())).isoformat()


def _ms(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@bp.get("/api/game/save")
def save_get() -> Response:
    """{"data": {칸: 값} | null, "version": n} — version 0 = 아직 서버에 없음."""
    row = auth._db().execute("SELECT data, version FROM game_saves WHERE user_id = ?", (g.actor.user_id,)).fetchone()
    return jsonify(data=json.loads(row[0]) if row else None, version=row[1] if row else 0)


@bp.post("/api/game/save")
def save_put() -> Response:
    """{"data": {칸: JSON 글자}, "version": 화면이 아는 판} → {"version": 새 판}. 판이 다르면 409 + 지금 서버 것."""
    raw, b = request.get_data(cache=True), _body()
    data, ver = b.get("data"), b.get("version")
    if (not isinstance(data, dict) or len(data) > 100 or type(ver) is not int or ver < 0 or len(raw) > SAVE_MAX_BYTES
            or not all(SAVE_KEY.fullmatch(k) and isinstance(v, str) for k, v in data.items())):
        return jsonify(error="저장할 내용의 모양이 올바르지 않습니다."), 400
    conn, uid = auth._db(), g.actor.user_id
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT data, version FROM game_saves WHERE user_id = ?", (uid,)).fetchone()
        now = row[1] if row else 0
        if now != ver:
            conn.execute("ROLLBACK")
            return jsonify(error="다른 기기에서 먼저 저장했습니다.", data=json.loads(row[0]) if row else None, version=now), 409
        conn.execute(
            "INSERT INTO game_saves (user_id, data, version, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET data = excluded.data, version = excluded.version, updated_at = excluded.updated_at",
            (uid, json.dumps(data, ensure_ascii=False), now + 1, auth._iso(auth._now())))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(version=now + 1)


@bp.post("/api/game/score")
def score_put() -> Response:
    """{"score": n} — 이번 주 내 최고점보다 높을 때만 바뀐다."""
    s = _body().get("score")
    if type(s) is not int or not 0 < s <= SCORE_MAX:
        return jsonify(error="점수 모양이 올바르지 않습니다."), 400
    auth._db().execute(
        "INSERT INTO game_scores (user_id, week, score, at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (user_id, week) DO UPDATE SET score = excluded.score, at = excluded.at WHERE excluded.score > game_scores.score",
        (g.actor.user_id, _week(auth._today()), s, auth._iso(auth._now())))
    return jsonify(ok=True)


@bp.get("/api/game/rank")
def rank() -> Response:
    """다른 직원 줄은 나를 뺀다(화면이 '나'를 붙인다). 비활성 직원은 없다. 같은 점수면 먼저 낸 사람이 위."""
    conn, me, today = auth._db(), g.actor.user_id, auth._today()
    week = _week(today)
    who = {r[0]: {"name": r[1], "dept": r[2], "title": r[3] or ""} for r in conn.execute(
        "SELECT u.id, u.name, d.name, u.title FROM users u JOIN departments d ON d.id = u.department_id WHERE u.is_active = 1")}

    def people(sql: str, *a: object) -> list[dict]:
        return [dict(who[uid], score=score) for uid, score in conn.execute(sql, a) if uid in who and uid != me]

    hof = [{"week": wk, **who[uid], "me": uid == me} for wk, uid in conn.execute(
        "SELECT week, user_id FROM (SELECT week, user_id, ROW_NUMBER() OVER (PARTITION BY week ORDER BY score DESC, at) AS n"
        " FROM game_scores s WHERE week < ? AND user_id IN (SELECT id FROM users WHERE is_active = 1)) WHERE n = 1 ORDER BY week", (week,))]
    # 주간 보상: 내가 마지막으로 한 지난 주의 순위 — 화면이 한 번만 준다(받았는지는 게임 기록 prizePaid)
    last = conn.execute("SELECT week, score, at FROM game_scores WHERE user_id = ? AND week < ? ORDER BY week DESC LIMIT 1", (me, week)).fetchone()
    if last:
        last = {"week": last[0], "rank": 1 + conn.execute(
            "SELECT COUNT(*) FROM game_scores s JOIN users u ON u.id = s.user_id"
            " WHERE s.week = ? AND u.is_active = 1 AND s.user_id <> ? AND (s.score > ? OR (s.score = ? AND s.at < ?))",
            (last[0], me, last[1], last[1], last[2])).fetchone()[0]}
    mine = conn.execute("SELECT COALESCE(MAX(CASE WHEN week = ? THEN score END), 0), COALESCE(MAX(score), 0) FROM game_scores WHERE user_id = ?",
                        (week, me)).fetchone()
    return jsonify(
        week=people("SELECT user_id, score FROM game_scores WHERE week = ? ORDER BY score DESC, at", week),
        all=people("SELECT user_id, MAX(score) FROM game_scores GROUP BY user_id ORDER BY 2 DESC, MIN(at)"),
        dept=[{"name": n, "score": s, "n": k, "me": bool(m)} for n, s, k, m in conn.execute(
            "SELECT d.name, SUM(s.score), COUNT(*), d.id = (SELECT department_id FROM users WHERE id = ?) FROM game_scores s"
            " JOIN users u ON u.id = s.user_id JOIN departments d ON d.id = u.department_id"
            " WHERE s.week = ? AND u.is_active = 1 GROUP BY d.id ORDER BY 2 DESC", (me, week))],
        hof=hof, last=last, me={"week": mine[0], "best": mine[1]}, thisWeek=week,
    )


@bp.get("/api/game/gifts")
def gifts() -> Response:
    """보낼 수 있는 동료 = 포털을 쓰는 활성 직원(나 빼고). 선물함 = 아직 안 받은 7일 안의 선물."""
    conn, me, today = auth._db(), g.actor.user_id, auth._today()
    mates = [{"id": r[0], "name": r[1], "dept": r[2]} for r in conn.execute(
        "SELECT u.id, u.name, d.name FROM users u JOIN departments d ON d.id = u.department_id"
        " WHERE u.is_active = 1 AND u.portal_enabled = 1 AND u.id <> ? ORDER BY u.name", (me,))]
    sent = [r[0] for r in conn.execute("SELECT to_user FROM game_gifts WHERE from_user = ? AND day = ?", (me, today.isoformat()))]
    inbox = [{"id": r[0], "name": r[1], "dept": r[2], "at": _ms(r[3])} for r in conn.execute(
        "SELECT x.id, u.name, d.name, x.created_at FROM game_gifts x JOIN users u ON u.id = x.from_user JOIN departments d ON d.id = u.department_id"
        " WHERE x.to_user = ? AND x.claimed_on IS NULL AND x.day > ? ORDER BY x.id", (me, (today - timedelta(days=GIFT_DAYS)).isoformat()))]
    return jsonify(mates=mates, sent=sent, inbox=inbox)


@bp.post("/api/game/gifts")
def gift_send() -> Response:
    """{"to": id} → {"ok": true} | 409 {"ok": false, "msg"} (오늘 이미 · 3명 다)."""
    to, conn, me, day = _body().get("to"), auth._db(), g.actor.user_id, auth._today().isoformat()
    if type(to) is not int or to == me or not conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND is_active = 1 AND portal_enabled = 1", (to,)).fetchone():
        return jsonify(ok=False, msg="보낼 수 없는 사람이에요"), 400
    conn.execute("BEGIN IMMEDIATE")
    try:
        sent = [r[0] for r in conn.execute("SELECT to_user FROM game_gifts WHERE from_user = ? AND day = ?", (me, day))]
        msg = "오늘은 이미 보냈어요" if to in sent else f"오늘은 {SEND_MAX}명에게 다 보냈어요" if len(sent) >= SEND_MAX else ""
        if not msg:
            conn.execute("INSERT INTO game_gifts (from_user, to_user, day, created_at) VALUES (?, ?, ?, ?)",
                         (me, to, day, auth._iso(auth._now())))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return (jsonify(ok=False, msg=msg), 409) if msg else jsonify(ok=True)


@bp.post("/api/game/gifts/claim")
def gift_claim() -> Response:
    """{"ids": [...]} → {"ids": 받아진 것}. 오늘 받은 것까지 합쳐 5장을 넘는 만큼은 안 받아진다(선물함에 남는다)."""
    ids = _body().get("ids")
    if not isinstance(ids, list) or not 0 < len(ids) <= 50 or not all(type(i) is int for i in ids):
        return jsonify(error="받을 선물을 골라 주세요."), 400
    conn, me, today = auth._db(), g.actor.user_id, auth._today()
    conn.execute("BEGIN IMMEDIATE")
    try:
        left = RECV_MAX - conn.execute("SELECT COUNT(*) FROM game_gifts WHERE to_user = ? AND claimed_on = ?", (me, today.isoformat())).fetchone()[0]
        got = [r[0] for r in conn.execute(
            f"SELECT id FROM game_gifts WHERE to_user = ? AND claimed_on IS NULL AND day > ? AND id IN ({','.join('?' * len(ids))}) ORDER BY id LIMIT ?",
            (me, (today - timedelta(days=GIFT_DAYS)).isoformat(), *ids, max(0, left)))]
        conn.executemany("UPDATE game_gifts SET claimed_on = ? WHERE id = ?", [(today.isoformat(), i) for i in got])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(ids=got)
