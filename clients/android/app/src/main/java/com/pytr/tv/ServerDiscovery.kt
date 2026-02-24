// Copyright (c) 2026 Panayotis Katsaloulis
// SPDX-License-Identifier: AGPL-3.0-or-later
package com.pytr.tv

import android.util.Log
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.atomic.AtomicBoolean

data class DiscoveredServer(val id: String, val name: String, val address: String) {
    override fun toString(): String = "$name ($address)"
}

/**
 * UDP broadcast discovery.
 * Sends "who is PytrServer?" to 255.255.255.255:5444,
 * server responds with JSON: {"Id", "Name", "Address"}.
 */
class ServerDiscovery {
    companion object {
        private const val TAG = "ServerDiscovery"
        private const val DISCOVERY_PORT = 5444
        private const val QUERY = "who is PytrServer?"
        private const val SCAN_INTERVAL_MS = 3000L
    }

    private val running = AtomicBoolean(false)
    private var thread: Thread? = null

    var onServerFound: ((DiscoveredServer) -> Unit)? = null

    fun startDiscovery() {
        if (!running.compareAndSet(false, true)) return

        thread = Thread {
            try {
                val socket = DatagramSocket().apply {
                    broadcast = true
                    soTimeout = 2000
                }
                val queryBytes = QUERY.toByteArray()
                val broadcastAddr = InetAddress.getByName("255.255.255.255")
                val sendPacket = DatagramPacket(queryBytes, queryBytes.size, broadcastAddr, DISCOVERY_PORT)
                val buf = ByteArray(1024)
                val recvPacket = DatagramPacket(buf, buf.size)

                while (running.get()) {
                    try {
                        socket.send(sendPacket)
                        Log.d(TAG, "Sent discovery query")

                        val deadline = System.currentTimeMillis() + SCAN_INTERVAL_MS
                        while (running.get() && System.currentTimeMillis() < deadline) {
                            try {
                                socket.receive(recvPacket)
                                val json = String(recvPacket.data, 0, recvPacket.length)
                                val obj = JSONObject(json)
                                val id = obj.optString("Id", "")
                                val name = obj.optString("Name", "")
                                val address = obj.optString("Address", "")
                                if (id.isNotEmpty() && address.isNotEmpty()) {
                                    val server = DiscoveredServer(id, name, address)
                                    Log.d(TAG, "Found: $server")
                                    onServerFound?.invoke(server)
                                }
                            } catch (_: java.net.SocketTimeoutException) {
                                // No more responses, wait for next cycle
                            } catch (e: Exception) {
                                Log.w(TAG, "Failed to parse response", e)
                            }
                        }
                    } catch (_: java.net.SocketTimeoutException) {
                        // Normal
                    } catch (e: Exception) {
                        Log.w(TAG, "Broadcast error", e)
                    }
                }

                socket.close()
            } catch (e: Exception) {
                Log.e(TAG, "Discovery failed", e)
            }
        }.apply { isDaemon = true; start() }
    }

    fun stopDiscovery() {
        running.set(false)
        thread?.interrupt()
        thread = null
    }
}
