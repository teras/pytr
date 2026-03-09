// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
package onl.ycode.pytr

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.*
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

class SetupActivity : Activity() {
    companion object {
        const val RECONNECT_INTERVAL_MS = 3000L
    }

    private lateinit var discovery: ServerDiscovery
    private lateinit var serverList: ListView
    private lateinit var discoveryStatus: TextView
    private lateinit var addressInput: EditText
    private lateinit var deviceNameInput: EditText
    private lateinit var connectButton: Button
    private lateinit var errorText: TextView

    private val servers = mutableListOf<DiscoveredServer>()
    private lateinit var adapter: ArrayAdapter<DiscoveredServer>
    private val handler = Handler(Looper.getMainLooper())
    private val pollingActive = AtomicBoolean(false)
    private var launching = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // If saved server exists, test it before launching
        val savedUrl = PreferenceHelper.getServerUrl(this)
        if (savedUrl != null) {
            // Test connection in background, show setup screen meanwhile
            showSetupScreen(savedUrl)
            return
        }

        showSetupScreen(null)
    }

    private fun showSetupScreen(savedUrl: String?) {
        setContentView(R.layout.activity_setup)

        discoveryStatus = findViewById(R.id.discoveryStatus)
        serverList = findViewById(R.id.serverList)
        addressInput = findViewById(R.id.addressInput)
        deviceNameInput = findViewById(R.id.deviceNameInput)
        connectButton = findViewById(R.id.connectButton)
        errorText = findViewById(R.id.errorText)

        // Pre-fill device name from preferences
        deviceNameInput.setText(PreferenceHelper.getDeviceName(this))

        adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, servers)
        serverList.adapter = adapter

        serverList.setOnItemClickListener { _, _, position, _ ->
            val server = servers[position]
            testAndConnect(server.address)
        }

        connectButton.setOnClickListener {
            val input = addressInput.text.toString().trim()
            if (input.isNotEmpty()) {
                val url = if (input.startsWith("http")) input else "http://$input"
                testAndConnect(url)
            }
        }

        discovery = ServerDiscovery()
        discovery.onServerFound = { server ->
            runOnUiThread {
                if (servers.none { it.id == server.id }) {
                    servers.add(server)
                    adapter.notifyDataSetChanged()
                    discoveryStatus.text = "${servers.size} server(s) found"
                }
            }
        }
        discovery.startDiscovery()

        // If we have a saved server, start polling it in the background
        if (savedUrl != null) {
            startPolling(savedUrl)
        }
    }

    private fun startPolling(url: String) {
        pollingActive.set(true)
        schedulePoll(url)
    }

    private fun stopPolling() {
        pollingActive.set(false)
        handler.removeCallbacksAndMessages(null)
    }

    private fun schedulePoll(url: String) {
        if (!pollingActive.get()) return
        // First poll immediately, subsequent ones after interval
        doPoll(url)
    }

    private fun doPoll(url: String) {
        if (!pollingActive.get()) return

        Thread {
            val alive = try {
                val conn = URL("$url/api/profiles/boot").openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                conn.requestMethod = "GET"
                val code = conn.responseCode
                conn.disconnect()
                code in 200..499
            } catch (_: Exception) {
                false
            }

            runOnUiThread {
                if (!pollingActive.get() || launching) return@runOnUiThread
                if (alive) {
                    stopPolling()
                    launchMain(url)
                } else {
                    handler.postDelayed({ doPoll(url) }, RECONNECT_INTERVAL_MS)
                }
            }
        }.start()
    }

    private fun testAndConnect(url: String) {
        errorText.visibility = View.GONE
        connectButton.isEnabled = false
        connectButton.setText(R.string.connecting)

        Thread {
            val success = try {
                val conn = URL("$url/api/profiles/boot").openConnection() as HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                conn.requestMethod = "GET"
                val code = conn.responseCode
                conn.disconnect()
                code in 200..499 // Any HTTP response means server exists
            } catch (_: Exception) {
                false
            }

            runOnUiThread {
                connectButton.isEnabled = true
                connectButton.setText(R.string.connect)
                if (success) {
                    val name = deviceNameInput.text.toString().trim().ifEmpty { "Android TV" }
                    PreferenceHelper.setDeviceName(this, name)
                    PreferenceHelper.setServerUrl(this, url)
                    launchMain(url)
                } else {
                    errorText.setText(R.string.connection_failed)
                    errorText.visibility = View.VISIBLE
                }
            }
        }.start()
    }

    private fun launchMain(url: String) {
        if (launching) return
        launching = true
        stopPolling()
        val intent = Intent(this, MainActivity::class.java)
        intent.putExtra("server_url", url)
        startActivity(intent)
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        stopPolling()
        if (::discovery.isInitialized) {
            discovery.stopDiscovery()
        }
    }
}
