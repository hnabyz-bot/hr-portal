/* 인사포털 서비스 워커 — 휴대폰 알림만 받는다 (푸시 알림 설계 §4). 캐시 · 화면 가로채기(fetch)는 하지 않는다.
   서버가 보내는 값: { title, body, url, badge?, tag? } — badge 가 없으면(서버가 개수를 못 셌을 때) 아이콘 숫자는 그대로 둔다. */
self.addEventListener('install', ()=>self.skipWaiting());
self.addEventListener('activate', e=>e.waitUntil(self.clients.claim()));   // 이미 열린 창도 이 워커가 맡는다 — 알림을 누르면 그 창을 옮길 수 있다(WindowClient.navigate)

self.addEventListener('push', e=>{
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch(_){}   // 읽지 못해도 알림은 띄운다(아이폰은 알림 없이 받으면 구독이 끊긴다)
  const opts = { body: d.body || '', icon: '/android-chrome-192x192.png', badge: '/favicon-32x32.png', tag: d.tag, data: { url: d.url || '/' } };
  if(d.tag) opts.renotify = true;   // 같은 태그(9시 묶음)가 어제 지우지 않은 알림을 조용히 바꿔 치지 않고 다시 울린다. 태그 없이 renotify 를 주면 TypeError 라 태그가 있을 때만
  const work = [self.registration.showNotification(d.title || '인사포털', opts)];
  if(typeof d.badge === 'number' && self.navigator.setAppBadge) work.push(d.badge > 0 ? self.navigator.setAppBadge(d.badge) : self.navigator.clearAppBadge());
  e.waitUntil(Promise.all(work.map(p => Promise.resolve(p).catch(()=>{}))));
});

self.addEventListener('notificationclick', e=>{
  e.notification.close();
  let url = new URL('/', self.location.origin);
  try { const u = new URL(e.notification.data && e.notification.data.url || '/', self.location.origin); if(u.origin === url.origin) url = u; } catch(_){}   // 같은 출처 주소만
  e.waitUntil((async ()=>{
    const open = (await self.clients.matchAll({ type: 'window', includeUncontrolled: true })).find(c => new URL(c.url).origin === url.origin);
    if(open){
      try { await open.focus(); await open.navigate(url.href); return; } catch(_){}   // 창을 못 옮기면 아래에서 새로 연다
    }
    await self.clients.openWindow(url.href);
  })());
});
