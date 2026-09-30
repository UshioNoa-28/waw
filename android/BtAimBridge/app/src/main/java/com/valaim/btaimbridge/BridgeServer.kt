package com.valaim.btaimbridge

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import kotlin.concurrent.thread

/**
 * TCP server. Accepts a PC client and parses the tiny line protocol:
 *
 *   "M <dx> <dy>\n"   relative mouse move
 *   "B <mask>\n"      set button mask (bit0 left, bit1 right, bit2 middle)
 *   "W <ticks>\n"     wheel
 *   "P\n"             keepalive ping
 *
 * Robustness rules (learned the hard way):
 *   * A new client kicks any stale previous client - a PC that was killed
 *     leaves a half-open socket whose readLine() would block forever and
 *     silently starve every future connection.
 *   * On every connect AND disconnect, and when the app unregisters, a
 *     zero-button report is emitted so the host never gets stuck with a
 *     physically-held mouse button.
 */
class BridgeServer(
    private val portProvider: () -> Int,
    private val hid: HidMouseController,
    private val onLog: (String) -> Unit,
    private val onClient: (String) -> Unit,
) {
    @Volatile private var running = false
    private var serverSocket: ServerSocket? = null
    private var worker: Thread? = null

    @Volatile private var client: Socket? = null
    @Volatile private var buttonMask = 0

    fun start() {
        if (running) return
        running = true
        worker = thread(name = "bridge-server") { loop() }
    }

    fun stop() {
        running = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        releaseAll()
        try { client?.close() } catch (_: Exception) {}
        client = null
    }

    private fun loop() {
        while (running) {
            val port = portProvider()
            try {
                val ss = ServerSocket(port)
                serverSocket = ss
                onLog("Listening on 0.0.0.0:$port")
                while (running) {
                    val incoming = ss.accept()
                    val old = client
                    if (old != null && old !== incoming) {
                        onLog("Kicking stale PC connection")
                        try { old.close() } catch (_: Exception) {}
                    }
                    client = incoming
                    buttonMask = 0
                    releaseAll()
                    onClient(incoming.inetAddress?.hostAddress ?: "?")
                    thread(name = "bridge-client") { handleClient(incoming) }
                }
            } catch (e: Exception) {
                if (running) onLog("Server error: ${e.message}")
                try { Thread.sleep(1000) } catch (_: InterruptedException) {}
            }
        }
    }

    private fun handleClient(socket: Socket) {
        try {
            socket.tcpNoDelay = true
            socket.soTimeout = 15000
            val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
            while (running && client === socket) {
                val line = try {
                    reader.readLine()
                } catch (e: SocketTimeoutException) {
                    continue // keepalive window elapsed, keep waiting
                } ?: break
                applyCommand(line)
            }
        } catch (e: Exception) {
            if (running && client === socket) onLog("Client error: ${e.message}")
        } finally {
            try { socket.close() } catch (_: Exception) {}
            if (client === socket) {
                client = null
                releaseAll()
                onLog("PC disconnected")
            }
        }
    }

    /** Force the host to see "no buttons held" - cures stuck-click states. */
    private fun releaseAll() {
        try { hid.sendReport(0, 0, 0, 0) } catch (_: Exception) {}
    }

    private fun applyCommand(line: String) {
        val parts = line.trim().split(Regex("\\s+"), limit = 3)
        if (parts.isEmpty() || parts[0].isEmpty()) return
        when (parts[0]) {
            "M" -> {
                if (parts.size < 3) return
                val dx = parts[1].toIntOrNull() ?: return
                val dy = parts[2].toIntOrNull() ?: return
                var rx = dx
                var ry = dy
                // HID reports carry at most +-127 per axis; split larger moves.
                while (rx != 0 || ry != 0) {
                    val sx = rx.coerceIn(-127, 127)
                    val sy = ry.coerceIn(-127, 127)
                    hid.sendReport(buttonMask, sx, sy, 0)
                    rx -= sx
                    ry -= sy
                }
            }
            "B" -> {
                buttonMask = (parts.getOrNull(1)?.toIntOrNull() ?: 0) and 0x1F
                hid.sendReport(buttonMask, 0, 0, 0)
            }
            "W" -> {
                val ticks = parts.getOrNull(1)?.toIntOrNull() ?: return
                val step = if (ticks > 0) 1 else -1
                hid.sendReport(buttonMask, 0, 0, step)
            }
            "P" -> { /* keepalive */ }
        }
    }
}
