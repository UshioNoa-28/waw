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
    /** PC-side latch state, drives the mouse-proxy left-button gate. */
    @Volatile var nailOk: Boolean = false
    @Volatile var assistOn: Boolean = false
    var onNail: ((Boolean) -> Unit)? = null
    @Volatile private var buttonMask = 0

    // Coalesced, rate-paced move output: the PC can burst commands faster
    // than Bluetooth can drain; replaying every queued move would stack up
    // stale corrections and overshoot the target. Keep the latest pending
    // move and emit at a real-mouse-like report rate instead.
    @Volatile private var pendX = 0
    @Volatile private var pendY = 0
    private var drainThread: Thread? = null

    fun isAlive(): Boolean = running && client != null

    /** Fire button press from the big red button -> 'T' to the PC over the client socket. */
    private val tx = java.util.concurrent.Executors.newSingleThreadExecutor()

    private fun sendAsync(line: String) {
        val c = client ?: return
        tx.execute {
            try {
                c.getOutputStream().write(line.toByteArray())
                c.getOutputStream().flush()
            } catch (_: Exception) { }
        }
    }

    fun sendLmb(on: Boolean) {
        sendAsync(if (on) "L1\n" else "L0\n")
    }

    fun sendTrigger() {
        sendAsync("T\n")
    }

    fun start() {
        if (running) return
        running = true
        worker = thread(name = "bridge-server") { loop() }
        drainThread = thread(name = "bridge-drain") { drainLoop() }
    }

    fun stop() {
        running = false
        assistOn = false
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        drainThread?.interrupt()
        drainThread = null
        releaseAll()
        try { client?.close() } catch (_: Exception) {}
        client = null
    }

    private fun drainLoop() {
        while (running) {
            val x = pendX
            val y = pendY
            if (x != 0 || y != 0) {
                pendX = 0
                pendY = 0
                sendSplit(x, y)
            }
            try { Thread.sleep(6) } catch (_: InterruptedException) { break }
        }
    }

    private fun sendSplit(x: Int, y: Int) {
        var rx = x
        var ry = y
        while (rx != 0 || ry != 0) {
            val sx = rx.coerceIn(-127, 127)
            val sy = ry.coerceIn(-127, 127)
            hid.sendReport(buttonMask, sx, sy, 0)
            rx -= sx
            ry -= sy
        }
    }

    private fun loop() {
        while (running) {
            val port = portProvider()
            try {
                val ss = ServerSocket()
                    ss.reuseAddress = true
                    ss.bind(java.net.InetSocketAddress(port))
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
                    pendX = 0
                    pendY = 0
                    releaseAll()
                    onClient(incoming.inetAddress?.hostAddress ?: "?")
                    try {
                        val dump = android.util.Base64.encodeToString(
                            CrashLog.tailAll().toByteArray(), android.util.Base64.NO_WRAP)
                        incoming.getOutputStream().write(("C " + dump + "\n").toByteArray())
                        incoming.getOutputStream().flush()
                    } catch (_: Exception) { }
                    thread(name = "bridge-client") { handleClient(incoming) }
                }
            } catch (e: Exception) {
                if (running) onLog("Server error: ${e.message}; rebinding")
                serverSocket?.let { try { it.close() } catch (_: Exception) {} }
                serverSocket = null
                try { client?.close() } catch (_: Exception) {}
                client = null
                try { Thread.sleep(500) } catch (_: InterruptedException) {}
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
    /** Ask the PC to toggle its aim-lock (button in MainActivity). */
    fun sendLockToggle() {
        sendAsync("L\n")
    }

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
                pendX = dx
                pendY = dy
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
            "N" -> {
                val on = (parts.getOrNull(1)?.toIntOrNull() ?: 0) != 0
                if (on != nailOk) { nailOk = on; onNail?.invoke(on) }
            }
            "A" -> {
                assistOn = (parts.getOrNull(1)?.toIntOrNull() ?: 0) != 0
                if (!assistOn) nailOk = false
            }
        }
    }
}
