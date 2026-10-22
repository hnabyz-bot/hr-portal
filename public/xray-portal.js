/* X-ray 특공대 ↔ 인사포털 (2026-10-20 인사담당자 결정). 게임(xray/index.html)이 맨 먼저 읽고, XRSQ_GATE 가 끝나야 게임이 시작한다.
   · 로그인돼 있으면: 실제 직원 랭킹(실명 + 팀) · 동료 선물(window.XRSQ_PORTAL) + 게임 기록(xrsq_* — 출동권·칭호·모자·최고점 …)을
     서버에 사람별로 둔다 → PC 와 휴대폰이 같은 기록. 서버: backend/game.py
   · 로그인이 안 돼 있으면: 게임에 못 들어간다 — 랭킹을 피해 로그인 없이 하는 사람이 없게 (인사담당자)
   · 서버가 없으면(PC 미리보기 · 시연 사이트): 예전처럼 예시 모드
   PC 는 포털처럼 하이웍스 메뉴로 연 창에서만 (sessionStorage hrEntry — 포털 index.html authEntered).
   게임 폴더(public/xray/)는 원본으로 통째로 덮어쓰므로 이 파일은 그 밖에 둔다. */
(() => {
  const P = 'xrsq_', DEVICE = ['lowfx', 'sound', 'music', 'instNudge', 'installed'];   // 기기마다 따로 두는 것 (화질·소리·설치 안내)
  const MAX = ['best', 'diffOpen', 'evBest'], UNION = ['titles', 'hofWins'];      // 두 기기 기록을 합칠 때 큰 값 · 둘 다
  const JH = { 'Content-Type': 'application/json' };
  const ls = window.localStorage;
  const S = { ver: 0, last: {}, dirty: new Set(), timer: 0, busy: false, offset: 0 };
  const rk = { week: [], all: [], dept: [], hof: [], last: null, me: { week: 0, best: 0 } };   // 게임 SAMPLE 과 같은 모양

  const J = v => { try { return JSON.parse(v); } catch (e) { return null; } };
  function local() {   // 이 기기의 게임 기록 {칸: JSON 글자}
    const o = {};
    for (let i = 0; i < ls.length; i++) { const k = ls.key(i); if (k.startsWith(P) && !DEVICE.includes(k.slice(P.length))) o[k.slice(P.length)] = ls.getItem(k); }
    return o;
  }
  function put(data) { for (const k in data) try { ls.setItem(P + k, data[k]); } catch (e) {} }
  function pick(k, s, l, mine) {   // mine: 이 기기에서 바꾸고 아직 못 올린 칸 → 이 기기 것
    if (s === undefined) return l;
    if (l === undefined || s === l) return s;
    const a = J(s), b = J(l);
    if (MAX.includes(k)) return JSON.stringify(Math.max(+a || 0, +b || 0));
    if (UNION.includes(k)) return JSON.stringify([...new Set([].concat(a || [], b || []))]);
    if (k === 'weekBest' && a && b) return JSON.stringify(a.key !== b.key ? (a.key > b.key ? a : b) : { key: a.key, score: Math.max(+a.score || 0, +b.score || 0) });
    return mine ? l : s;
  }
  function merge(srv, loc, mine) {
    const out = {};
    for (const k of new Set([...Object.keys(srv), ...Object.keys(loc)])) out[k] = pick(k, srv[k], loc[k], mine(k));
    return out;
  }
  const same = (k, a, b) => a === b || (k === 'tix' && !!a && !!b && (J(a) || {}).n === (J(b) || {}).n);   // 출동권 칸은 1초마다 시각만 바꿔 다시 쓴다
  function keepDirty() { try { ls.setItem('xrsqDirty', JSON.stringify([...S.dirty])); } catch (e) {} }

  const entered = () => { try { return sessionStorage.getItem('hrEntry') === '1'; } catch (e) { return true; } };
  const phone = /iPhone|Android|Mobile/.test(navigator.userAgent);
  const LOCK = {
    login: ['인사포털에서 열어 주세요', 'X-ray 특공대는 인사포털에 로그인한 상태에서만 할 수 있어요.<br>' + (phone
      ? '<b>인사포털 → 탕비실 → 게임 열기</b>로 들어와 주세요.<br>처음이면 PC 인사포털 홈의 <b>휴대폰 등록</b>부터 해 주세요.'
      : '하이웍스에서 <b>인사포털</b> 메뉴로 들어온 뒤<br><b>탕비실 → 게임 열기</b>를 눌러 주세요.')],
    err: ['잠시 뒤 다시 열어 주세요', '게임 기록을 불러오지 못했어요.'],
  };
  function lock(kind) {   // 게임 위를 덮는다. 돌려주는 약속은 끝나지 않는다 = 게임이 시작하지 않는다
    if (!document.getElementById('xrLock')) {
      const [t, b] = LOCK[kind || 'login'], el = document.createElement('div');
      el.id = 'xrLock'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true');
      el.style.cssText = 'position:fixed;inset:0;z-index:9999;display:grid;place-items:center;padding:24px 16px;background:#061131;color:#eef6ff;text-align:center;font:16px/1.75 "XPixel","Do Hyeon",sans-serif';
      el.innerHTML = '<div style="max-width:480px"><p style="margin:0 0 14px;font-size:22px;color:#ffd23f">' + t + '</p><p style="margin:0 0 24px">' + b + '</p>'
        + (kind === 'err' ? '' : '<a href="' + (phone ? '../' : 'https://login.office.hiworks.com/') + '" rel="noopener" style="display:inline-block;padding:12px 26px;border-radius:10px;background:#ffd23f;color:#3d1c00;text-decoration:none">' + (phone ? '인사포털로 가기' : '하이웍스 열기') + '</a>')
        + '</div>';
      document.body.appendChild(el);
    }
    return new Promise(() => {});
  }
  async function getJSON(u) {
    const r = await fetch(u, { headers: { Accept: 'application/json' } });
    if (r.status === 401) { lock(); throw new Error('login'); }
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }
  async function post(u, body) {
    const r = await fetch(u, { method: 'POST', headers: JH, body: JSON.stringify(body) });
    if (r.status === 401) { lock(); throw new Error('login'); }
    return r.json();
  }

  // ---- 게임 기록 올리기: 바뀐 칸이 생기면 2초 안에 통째로. 다른 기기가 먼저 올렸으면(409) 서버 것에 이 기기에서 바꾼 칸만 얹어 다시 ----
  function schedule(ms) {   // 이미 잡혀 있으면 미루지 않는다 — 게임이 1초마다 쓰는 칸 때문에 영영 안 올라가지 않게
    if (S.timer && ms === undefined) return;
    clearTimeout(S.timer); S.timer = setTimeout(push, ms === undefined ? 2000 : ms);
  }
  async function push(keep) {
    clearTimeout(S.timer); S.timer = 0;
    if (S.busy || !S.dirty.size) return;
    S.busy = true;
    const data = local(), sent = [...S.dirty];
    S.dirty.clear();
    let r = null, b = {};
    try { r = await fetch('/api/game/save', { method: 'POST', headers: JH, body: JSON.stringify({ data, version: S.ver }), keepalive: keep === true }); b = await r.json(); } catch (e) {}
    S.busy = false;
    if (r && r.status === 401) return lock();
    if (r && r.ok) { S.ver = b.version; S.last = data; }
    else if (r && r.status === 409) {
      const srv = b.data || {}, m = merge(srv, local(), k => sent.includes(k) || S.dirty.has(k));
      put(m); S.ver = b.version; S.last = srv;
      for (const k in m) if (m[k] !== srv[k]) S.dirty.add(k);
    } else sent.forEach(k => S.dirty.add(k));   // 연결이 끊김 — 조금 뒤 다시
    keepDirty();
    if (S.dirty.size) schedule(r && (r.ok || r.status === 409) ? 0 : 30000);
  }
  async function pull() {   // 다른 기기에서 하고 돌아왔을 때 — 이 기기에 못 올린 것이 없을 때만
    if (S.busy || S.dirty.size) return;
    try { const sv = await getJSON('/api/game/save'); if (sv.version !== S.ver && !S.dirty.size) { put(sv.data || {}); S.ver = sv.version; S.last = sv.data || {}; } } catch (e) {}
  }
  async function loadSave(me) {
    const owner = ls.getItem('xrsqOwner'), uid = String(me.user_id);
    if (owner && owner !== uid) for (const k of Object.keys(local())) ls.removeItem(P + k);   // 같은 PC 를 쓰던 다른 직원의 기록 — 섞지 않는다
    const pend = new Set(owner === uid ? J(ls.getItem('xrsqDirty')) || [] : []);
    const sv = await getJSON('/api/game/save'), srv = sv.data || {};
    // 처음 연결하는 기기: 서버에 기록이 없으면 이 기기 기록을 올리고, 있으면 서버 것 + 최고점·칭호 같은 것만 합친다
    const m = merge(srv, local(), k => pend.has(k));
    put(m); S.ver = sv.version; S.last = srv;
    try { ls.setItem('xrsqOwner', uid); } catch (e) {}
    for (const k in m) if (m[k] !== srv[k]) S.dirty.add(k);
    keepDirty();
    if (S.dirty.size) schedule(0);
  }
  async function refresh() {   // 랭킹 — 다른 직원 줄은 게임 SAMPLE 모양 [부서, 이름, 점수, 직급]
    const d = await getJSON('/api/game/rank'), row = p => [p.dept, p.name, p.score, p.title];
    Object.assign(rk, { week: d.week.map(row), all: d.all.map(row), dept: d.dept.map(x => [x.name, x.score, x.n, x.me]),
      hof: d.hof.map(h => [h.week, h.me ? '나' : h.name, h.dept, h.me]), last: d.last, me: d.me });
    return rk;
  }

  // ---- 오픈시간 (2026-10-22 인사담당자 — HR 이 탕비실에서 바꾼다): 게임 isOpenAt 이 hours 를 쓴다 (게임의 "포털 사본에만 있는 수정 7") ----
  //   slots = [[시작, 끝)] 분 — 끝이 시작보다 이르면 다음날 아침까지라 두 칸으로 · rows = 안내창 표 · days = 하루 종일 여는 날(kstDay 번호)
  //   closed = {msg: HR 이 적은 한 줄(HTML 로 바꿔 둠), until: 다시 여는 때(ms) | null}
  const hours = { slots: [], rows: [], weekend: true, days: new Set(), closed: null };
  const hm = m => Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0');
  const esc = v => String(v).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
  async function loadHours() {
    const d = await getJSON('/api/game/hours');
    Object.assign(hours, {
      slots: d.slots.flatMap(s => s.from < s.to ? [[s.from, s.to]] : [[s.from, 1440], [0, s.to]]),
      rows: d.slots.map(s => [s.name, hm(s.from) + '~' + (s.to === 0 ? '24:00' : (s.to < s.from ? '다음날 ' : '') + hm(s.to))])
        .concat([['주말', d.weekend === 'all' ? '하루 종일' : '평일과 같은 시간']], d.days.length ? [['공휴일 등', '하루 종일']] : []),
      weekend: d.weekend === 'all',
      days: new Set(d.days.map(x => Date.UTC(+x.date.slice(0, 4), +x.date.slice(5, 7) - 1, +x.date.slice(8, 10)) / 864e5)),
      closed: d.closed && { msg: esc(d.closed.msg), until: d.closed.until },
    });
  }

  let gq = null;   // 동료 선물 — 게임이 동료·보낸 사람·선물함을 한꺼번에 물으므로 한 번만 받는다
  const gifts = () => gq || (gq = getJSON('/api/game/gifts').finally(() => setTimeout(() => { gq = null; }, 1000)));

  window.XRSQ_GATE = (async () => {
    let r;
    try { r = await fetch('/api/me', { headers: { Accept: 'application/json' } }); } catch (e) { return; }   // 서버 없음(시연 사이트 · 파일로 연 것) = 예시 모드
    if (r.status === 401) return lock();
    if (!r.ok || !(r.headers.get('content-type') || '').includes('application/json')) return;   // PC 미리보기(정적 서버) = 예시 모드
    const me = await r.json();
    if (me.kind !== 'phone' && !entered()) return lock();   // 주소로 연 PC 창 — 포털과 달리 로그아웃은 안 한다(포털 창이 함께 열려 있을 수 있다)
    const t = Date.parse(r.headers.get('Date') || ''); if (t) S.offset = t - Date.now();   // 오픈시간은 서버 시각으로
    try { await Promise.all([loadSave(me), refresh(), loadHours()]); } catch (e) { return lock(document.getElementById('xrLock') ? 'login' : 'err'); }
    const foot = document.querySelector('.rk-foot span');
    if (foot) foot.textContent = '이번 주 랭킹은 매주 월요일 0시에 초기화돼요. 이번 주에 한 판이라도 한 직원이 올라가요.';
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') push(true); else { pull(); loadHours().catch(() => {}); } });
    setInterval(() => { if (document.visibilityState === 'visible') loadHours().catch(() => {}); }, 5 * 60e3);   // 켜 둔 채로 있어도 HR 이 바꾼 오픈시간 · 잠시 닫기를 5분 안에
    window.addEventListener('pagehide', () => push(true));
    window.XRSQ_PORTAL = {
      rk, refresh, hours,
      now: () => Date.now() + S.offset,
      saved(k) { if (DEVICE.includes(k) || same(k, ls.getItem(P + k), S.last[k])) return; S.dirty.add(k); keepDirty(); schedule(); },   // 게임 store.set 이 부른다
      score(n) {   // 한 판 끝 — 랭킹의 내 점수는 바로 올리고, 서버에 보낸 뒤 다른 직원 순위도 새로
        n = Math.floor(n); if (!(n > 0)) return;
        rk.me.week = Math.max(rk.me.week, n); rk.me.best = Math.max(rk.me.best, n);
        post('/api/game/score', { score: n }).then(refresh).catch(() => {});
      },
      colleagues: () => gifts().then(d => d.mates),
      sentToday: () => gifts().then(d => d.sent),
      inbox: () => gifts().then(d => d.inbox),
      send: id => post('/api/game/gifts', { to: id }),
      claim: ids => post('/api/game/gifts/claim', { ids }).then(d => d.ids || []),
    };
  })();
})();
