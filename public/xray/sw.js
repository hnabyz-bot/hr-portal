// X-ray 특공대 · 서비스 워커
// 하는 일: 폰 홈 화면에 "앱으로 설치"할 수 있게 하고, 게임 그림·글꼴을 폰에 보관해 두 번째부터 빨리 열리게 해요.
// 하지 않는 일: 게임 페이지(index.html)와 포털(로그인·랭킹·선물)은 항상 서버에서 새로 받아요.
//               그래서 새 버전이 바로 반영되고, 하이웍스 로그인 확인도 매번 제대로 돼요.
// 음악(assets/music)은 휴대폰이 조각조각 받아 가는 파일이라 건드리지 않아요.
const CACHE = 'xrsq-assets-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith('xrsq-') && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || req.headers.has('range')) return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const base = new URL('./assets/', self.registration.scope).pathname;
  if (!url.pathname.startsWith(base) || url.pathname.startsWith(base + 'music/')) return;
  // 보관해 둔 그림을 먼저 보여 주고, 뒤에서 새 그림을 받아 다음번에 바꿔 둬요 (그림을 고쳐도 다음 실행 때 반영)
  e.respondWith(caches.open(CACHE).then(async cache => {
    const hit = await cache.match(req);
    const net = fetch(req).then(res => {
      if (res.ok && res.type === 'basic') cache.put(req, res.clone());
      return res;
    }).catch(() => hit || Response.error());
    if (hit) { e.waitUntil(net.then(() => {}, () => {})); return hit; }
    return net;
  }));
});
