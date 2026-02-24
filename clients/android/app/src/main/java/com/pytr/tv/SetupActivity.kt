// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.pytr.tv

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.*
import java.net.HttpURLConnection
import java.net.URL

class SetupActivity : Activity() {
    private lateinit var discovery: ServerDiscovery
    private lateinit var serverList: ListView
    private lateinit var discoveryStatus: TextView
    private lateinit var addressInput: EditText
    private lateinit var connectButton: Button
    private lateinit var errorText: TextView

    private val servers = mutableListOf<DiscoveredServer>()
    private lateinit var adapter: ArrayAdapter<DiscoveredServer>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Skip setup if server already configured
        PreferenceHelper.getServerUrl(this)?.let { url ->
            launchMain(url)
            return
        }

        setContentView(R.layout.activity_setup)

        discoveryStatus = findViewById(R.id.discoveryStatus)
        serverList = findViewById(R.id.serverList)
        addressInput = findViewById(R.id.addressInput)
        connectButton = findViewById(R.id.connectButton)
        errorText = findViewById(R.id.errorText)

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
        val intent = Intent(this, MainActivity::class.java)
        intent.putExtra("server_url", url)
        startActivity(intent)
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::discovery.isInitialized) {
            discovery.stopDiscovery()
        }
    }
}
