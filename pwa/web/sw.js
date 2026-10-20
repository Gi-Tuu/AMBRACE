/* AMBRACE PWA Service Worker（二审返工版：按**部署 scope** 隔离 owner/version）
 *
 * 维修正（主审二审 §2）：上一版 `OWN_PREFIX='ambrace-pwa-'` 对同 origin 所有部署共用，
 * activate 会把**同一 origin 下另一个部署**（如 /site-b/）的缓存当成自己的旧版本删掉。
 *
 * 本版：owner 由**规范化 registration.scope** 派生，缓存名＝`ambrace-pwa-<owner>-<ver>`；
 * activate **只清理同 owner 的旧版本**：
 *   - 无关应用缓存（前缀不是本应用）              → 保留
 *   - 同应用不同部署（不同 scope owner）          → 保留
 *   - 同部署旧版本                                → 清理
 *   - 当前版本                                    → 保留
 *   - 归属不明的旧全局缓存（如 `ambrace-pwa-v1`，不带 owner）→ **保留**（不跨部署“顺手迁移”）
 *
 * 缓存纪律（沿用）：只缓存安全静态壳；`/api/`、带 Authorization、`no-store`/`private`、
 * 跨域一律不缓存；导航失败回落到明确离线页（永不卡在「正在加载」）。
 */
const SCOPE = new URL(self.registration.scope).href; // 完整规范 scope（含尾部斜杠）
const SCOPE_PATH = new URL(self.registration.scope).pathname;

/** 部署 owner：**完整规范 scope 的无损编码**（base64url）。
 *
 * 为什么不能折叠标点/大小写：上一版把 scope 路径折叠成字母数字并转小写，导致
 *   `/site-a/`≡`/site_a/`、`/Site/`≡`/site/`、`/`≡`/root/`、`/a/b/`≡`/a_b/`
 * 这些**不同部署**被当成同一个 owner ⇒ 互相删对方的缓存。base64url 无损保留
 * 大小写、标点与路径层级，`/` 与 `/root/` 也因此不同。
 */
function ownerOf(scope) {
  const bytes = encodeURIComponent(scope); // 保证 ASCII（URL 已百分号编码，故为恒等变换）
  return btoa(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const OWNER = ownerOf(SCOPE);
const APP_PREFIX = 'ambrace-pwa-';              // 本应用命名空间
const OWN_PREFIX = `${APP_PREFIX}${OWNER}-`;    // **本部署**命名空间（无损唯一）
const VERSION = `${OWN_PREFIX}v3`;              // 命名代次 v3（owner 编码换代）

const SHELL = ['index.html', 'manifest.json', 'offline.html', 'icons/ic_launcher.png'];

function shellPath(url) {
  if (url.origin !== self.location.origin) return null;
  if (!url.pathname.startsWith(SCOPE_PATH)) return null;
  return url.pathname.slice(SCOPE_PATH.length);
}

function isShell(url) {
  const rel = shellPath(url);
  return rel !== null && (rel === '' || SHELL.includes(rel));
}

function cacheable(request, url) {
  if (request.method !== 'GET') return false;
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.includes('/api/')) return false;
  if (request.headers.get('authorization')) return false;
  return true;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((c) => c.addAll(SHELL.map((p) => new URL(SCOPE + p, self.location.origin).toString())))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys
        // 只删**同 owner（同部署）**的旧版本；其它部署/无关应用/归属不明的一律保留
        .filter((k) => k.startsWith(OWN_PREFIX) && k !== VERSION)
        .map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (!cacheable(request, url)) {
    return; // 业务/认证请求不拦、不缓存
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.open(VERSION).then((c) =>
          c.match(new URL(SCOPE + 'offline.html', self.location.origin).toString())
            .then((r) => r || new Response(OFFLINE_HTML, {
              status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } })))
      )
    );
    return;
  }

  if (!isShell(url)) {
    return; // 壳外静态资源不缓存
  }

  event.respondWith(
    caches.open(VERSION).then((c) =>
      c.match(request).then((hit) => {
        if (hit) return hit;
        return fetch(request).then((resp) => {
          const cc = resp.headers.get('cache-control') || '';
          if (resp.ok && !/no-store|private/i.test(cc)) {
            const copy = resp.clone();
            c.put(request, copy);
          }
          return resp;
        });
      })
    )
  );
});

const OFFLINE_HTML = `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>离线 / Offline</title></head><body style="font-family:system-ui;padding:24px">
<h3>当前离线 · Offline</h3>
<p>只能显示已缓存的界面，联网后请刷新。</p>
<p>Showing the cached shell only. Refresh once you are back online.</p>
</body></html>`;
