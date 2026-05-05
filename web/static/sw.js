// Minimal service worker — enables Chrome's PWA install banner over HTTPS or
// on localhost. No-op fetch handler keeps every request on the network; we
// avoid caching because pytr is network-bound and stale caches would mask
// backend changes. Over plain HTTP this script is never registered (Chrome
// gates SW behind secure contexts), so HTTP-only setups silently skip it.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
