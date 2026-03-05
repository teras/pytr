// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
// Bearer token auth for iframe mode (webOS TV).
// Only activates when running inside an iframe.

(function () {
    if (window.self === window.top) return; // not in iframe, do nothing

    let _bearerToken = null;
    let _parentOrigin = null;
    let _tokenReady = false;
    let _tokenReadyResolve = null;
    var _tokenPromise = new Promise(function (resolve) { _tokenReadyResolve = resolve; });

    // Receive token from parent via postMessage
    window.addEventListener('message', function (e) {
        if (!e.data || typeof e.data !== 'object') return;
        if (e.source !== window.parent) return;
        if (e.data.type === 'pytr-auth') {
            _bearerToken = e.data.token || null;
            if (!_parentOrigin) _parentOrigin = e.origin;
            if (e.data.deviceName) localStorage.setItem('pytr-device-name', e.data.deviceName);
            if (!_tokenReady) { _tokenReady = true; _tokenReadyResolve(); }
        }
    });

    // Request token from parent immediately
    window.parent.postMessage({ type: 'pytr-request-auth' }, '*');
    // Timeout: don't wait forever if parent has no token
    setTimeout(function () {
        if (!_tokenReady) { _tokenReady = true; _tokenReadyResolve(); }
    }, 500);

    // Override fetch: add Authorization header for relative URLs
    const _origFetch = window.fetch;
    window.fetch = function (input, init) {
        if (_bearerToken) {
            const url = typeof input === 'string' ? input : (input instanceof Request ? input.url : '');
            // Only add to relative URLs or same-origin
            if (url.startsWith('/') || url.startsWith(location.origin)) {
                init = init || {};
                const headers = new Headers(init.headers || {});
                if (!headers.has('Authorization')) {
                    headers.set('Authorization', 'Bearer ' + _bearerToken);
                }
                init.headers = headers;
            }
        }
        return _origFetch.call(this, input, init).then(function (resp) {
            if (resp.status === 401 && _bearerToken) {
                _bearerToken = null;
                window.parent.postMessage({ type: 'pytr-auth-expired' }, _parentOrigin || '*');
                if (typeof checkProfile === 'function') checkProfile();
            }
            return resp;
        });
    };

    // Override WebSocket: send auth as first message
    const _OrigWS = window.WebSocket;
    window.WebSocket = function (url, protocols) {
        const ws = new _OrigWS(url, protocols);
        if (_bearerToken) {
            const token = _bearerToken;
            ws.addEventListener('open', function () {
                ws.send(JSON.stringify({ type: 'auth', token: token }));
            }, { once: true });
        }
        return ws;
    };
    window.WebSocket.prototype = _OrigWS.prototype;
    window.WebSocket.CONNECTING = _OrigWS.CONNECTING;
    window.WebSocket.OPEN = _OrigWS.OPEN;
    window.WebSocket.CLOSING = _OrigWS.CLOSING;
    window.WebSocket.CLOSED = _OrigWS.CLOSED;

    // Expose for iframe pairing flow
    window._pytrSetToken = function (token) { _bearerToken = token; };
    window._pytrGetToken = function () { return _bearerToken; };
    window._pytrParentOrigin = function () { return _parentOrigin || '*'; };
    window._pytrIsIframe = true;
    // Wait for token before first checkProfile
    window._pytrWaitForToken = function () { return _tokenPromise; };
})();
