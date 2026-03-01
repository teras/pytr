// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.pytr.tv

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import android.webkit.*
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var webView: WebView
    private var backPressedOnce = false
    private val handler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val serverUrl = intent.getStringExtra("server_url")
            ?: PreferenceHelper.getServerUrl(this)
            ?: run {
                startActivity(Intent(this, SetupActivity::class.java))
                finish()
                return
            }

        webView = findViewById(R.id.webView)
        setupWebView()

        // Persist cookies across restarts
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            setAcceptThirdPartyCookies(webView, true)
        }

        webView.loadUrl(serverUrl)
    }

    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            databaseEnabled = true
            allowFileAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.toString().startsWith("pytr://setup")) {
                    PreferenceHelper.clearServerUrl(this@MainActivity)
                    startActivity(Intent(this@MainActivity, SetupActivity::class.java))
                    finish()
                    return true
                }
                return false
            }

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                // Inject TV mode activation and device name
                val deviceName = PreferenceHelper.getDeviceName(this@MainActivity)
                    .replace("'", "\\'").replace("\\", "\\\\")
                view.evaluateJavascript(
                    """
                    (function() {
                        localStorage.setItem('tv-mode', 'android');
                        localStorage.setItem('pytr-device-name', '$deviceName');
                        document.body.classList.add('tv-nav-active');
                        // Trigger tv-nav.js initialization if it checks on load
                        window.dispatchEvent(new Event('storage'));
                    })();
                    """.trimIndent(),
                    null
                )
            }

            override fun onReceivedError(
                view: WebView, request: WebResourceRequest, error: WebResourceError
            ) {
                // If main frame fails to load, go back to setup
                if (request.isForMainFrame) {
                    PreferenceHelper.clearServerUrl(this@MainActivity)
                    startActivity(Intent(this@MainActivity, SetupActivity::class.java))
                    finish()
                }
            }
        }

        webView.webChromeClient = WebChromeClient()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        when (keyCode) {
            // Back button: dispatch BrowserBack to web page, let tv-nav.js handle it
            KeyEvent.KEYCODE_BACK -> {
                webView.evaluateJavascript(
                    """
                    (function() {
                        var handled = false;
                        var e = new KeyboardEvent('keydown', {
                            key: 'BrowserBack', code: 'BrowserBack',
                            bubbles: true, cancelable: true
                        });
                        document.addEventListener('keydown', function handler(evt) {
                            if (evt === e && evt.defaultPrevented) handled = true;
                            document.removeEventListener('keydown', handler);
                        });
                        document.dispatchEvent(e);
                        return handled ? 'handled' : 'not_handled';
                    })();
                    """.trimIndent()
                ) { result ->
                    if (result.contains("handled")) {
                        // tv-nav.js consumed the back press
                        return@evaluateJavascript
                    }
                    // Fallback: WebView back navigation or exit
                    runOnUiThread {
                        if (webView.canGoBack()) {
                            webView.goBack()
                        } else {
                            handleExit()
                        }
                    }
                }
                return true
            }

            // Media keys: dispatch to WebView as keyboard events
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                dispatchMediaKey("MediaPlayPause")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PLAY -> {
                dispatchMediaKey("MediaPlayPause")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PAUSE -> {
                dispatchMediaKey("MediaPlayPause")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                dispatchMediaKey("MediaFastForward")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_REWIND -> {
                dispatchMediaKey("MediaRewind")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_NEXT -> {
                dispatchMediaKey("MediaTrackNext")
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PREVIOUS -> {
                dispatchMediaKey("MediaTrackPrevious")
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun dispatchMediaKey(key: String) {
        webView.evaluateJavascript(
            """
            document.dispatchEvent(new KeyboardEvent('keydown', {
                key: '$key', code: '$key', bubbles: true, cancelable: true
            }));
            """.trimIndent(),
            null
        )
    }

    private fun handleExit() {
        if (backPressedOnce) {
            finish()
            return
        }
        backPressedOnce = true
        Toast.makeText(this, "Press back again to exit", Toast.LENGTH_SHORT).show()
        handler.postDelayed({ backPressedOnce = false }, 2000)
    }

    override fun onResume() {
        super.onResume()
        CookieManager.getInstance().flush()
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()
    }
}
