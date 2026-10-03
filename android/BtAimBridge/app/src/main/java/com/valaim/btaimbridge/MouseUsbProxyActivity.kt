package com.valaim.btaimbridge

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.*
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity
import java.nio.ByteBuffer

/**
 * USB raw mouse proxy: claim the mouse, read its interrupt-IN reports via
 * UsbRequest (per spec for INT endpoints), split the byte stream by the
 * report length derived from the HID Report Descriptor, and forward motion
 * + gated LMB through our BT HID to the PC. No system pointer involved ->
 * no screen-edge limits. Heuristic variance calibration remains as fallback
 * if the descriptor route ever fails.
 */
class MouseUsbProxyActivity : AppCompatActivity() {

    private lateinit var usbManager: UsbManager
    private var conn: UsbDeviceConnection? = null
    private var claimed: UsbInterface? = null
    private var deviceName: String = ""
    private var polling = false
    private val handler = Handler(Looper.getMainLooper())

    // ---- layout (from descriptor or heuristic) ----
    private var reportLen = 0
    private var xBit = -1; private var yBit = -1; private var wheelBit = -1
    private var btnBase = -1; private var btnCount = 0
    private var reportIdLen = 0
    private var layoutSrc = ""

    // ---- stream accumulator ----
    private val pending = ArrayList<Byte>(4096)

    // ---- forwarding state ----
    @Volatile var pendX = 0; @Volatile var pendY = 0; @Volatile var pendWheel = 0
    private var btnMask: Int
        get() = BridgeGlobals.btnMask
        set(v) { BridgeGlobals.btnMask = v }
    @Volatile private var lastDownAt = 0L
    @Volatile private var lmbHeld = false
    @Volatile private var lmbDownSent = false
    @Volatile private var lmbWantsRelease = false
    @Volatile private var lmbPhysUp = false
    @Volatile private var lastBtn = 0
    private val graceMs = 450L
    private var pressAt = 0L
    private var moves = 0
    private var nails = 0
    private var holds = 0

    private val out = android.widget.TextView(this)

    private val renderTick = object : Runnable {
        override fun run() {
            val h = BridgeGlobals.hid
            if (h != null) {
                val assist = BridgeGlobals.server?.assistOn == true
                if ((lmbHeld || lmbWantsRelease || lmbPhysUp) && !lmbDownSent) {
                    if (!assist) {
                        btnMask = btnMask or 0x01
                        lmbDownSent = true; lastDownAt = System.currentTimeMillis()
                        clickReport(btnMask)
                        if (lmbWantsRelease || lmbPhysUp) {
                            lmbHeld = false; lmbWantsRelease = false; lmbPhysUp = false
                            handler.postDelayed({
                                val up = btnMask and 1.inv()
                    btnMask = up
                                clickReport(up)
                            }, 60)
                        }
                    } else {
                        val nailed = BridgeGlobals.server?.nailOk == true
                        if (nailed || System.currentTimeMillis() - pressAt > graceMs) {
                            if (nailed) nails++ else holds++
                            btnMask = btnMask or 0x01
                        lmbDownSent = true; lastDownAt = System.currentTimeMillis()
                            clickReport(btnMask)
                            if (lmbWantsRelease || lmbPhysUp) {
                                lmbHeld = false; lmbWantsRelease = false; lmbPhysUp = false
                                handler.postDelayed({
                                    val up = btnMask and 1.inv()
                    btnMask = up
                                    clickReport(up)
                                }, 60)
                            }
                        }
                    }
                }
                while (pendX != 0 || pendY != 0) {
                    val cx = pendX.coerceIn(-127, 127)
                    val cy = pendY.coerceIn(-127, 127)
                    pendX -= cx; pendY -= cy
                    h.sendReport(btnMask, cx, cy, 0)
                }
                if (pendWheel != 0) {
                    val cw = pendWheel.coerceIn(-3, 3)
                    pendWheel -= cw
                    h.sendReport(btnMask, 0, 0, cw)
                }
                if ((btnMask and 0x01) != 0 && !lmbHeld && System.currentTimeMillis() - lastDownAt > 2000) {
                    btnMask = btnMask and 1.inv()                    lmbDownSent = false
                    clickReport(btnMask)
                    CrashLog.log("usb phantom LMB cleared")
                }
            }
            renderStatus()
            handler.postDelayed(this, 4)
        }
    }

    private fun clickReport(mask: Int) {
        val h = BridgeGlobals.hid ?: return
        h.sendReport(mask, 1, 0, 0)
        h.sendReport(mask, -1, 0, 0)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_mouse_test)
        usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
        findViewById<android.widget.Button>(R.id.btnStart).text = "开始接管鼠标(USB直读)"
        findViewById<android.widget.Button>(R.id.btnStart).setOnClickListener { takeOver() }
        findViewById<android.widget.Button>(R.id.btnStop).text = "释放鼠标(还给系统)"
        findViewById<android.widget.Button>(R.id.btnStop).setOnClickListener { release() }
        CrashLog.log("USB proxy onCreate")
        renderStatus()
        handler.postDelayed(renderTick, 4)
    }

    private fun renderStatus() {
        val extra = "\n---- 诊断 ----\n" + CrashLog.tail(3)
        out.text = buildString {
            appendLine(if (polling) "USB 直读运行中 [$layoutSrc]" else "未接管")
            appendLine("移动转发 $moves 帧 | 钉住放 $nails / 超时放 $holds")
            appendLine("左键: ${if (lmbHeld) if (lmbDownSent) "已放行" else "扣住等待钉住…" else "-"} 辅助:${BridgeGlobals.server?.assistOn}")
            appendLine("报文 $totalReports 条/秒 ~$reportsPerSec")
            append(extra)
        }
    }

    // ---------------- takeover ----------------

    private fun takeOver() {
        val mouse = usbManager.deviceList.values.firstOrNull { dev ->
            (0 until dev.interfaceCount).any { k ->
                val it = dev.getInterface(k)
                it.interfaceClass == UsbConstants.USB_CLASS_HID &&
                    (0 until it.endpointCount).any { e ->
                        val ep = it.getEndpoint(e)
                        ep.type == UsbConstants.USB_ENDPOINT_XFER_INT && ep.direction == UsbConstants.USB_DIR_IN
                    }
            }
        }
        if (mouse == null) { renderRaw("没找到 USB 鼠标(检查 OTG/接口)"); return }
        if (!usbManager.hasPermission(mouse.deviceName)) {
            val flags = if (android.os.Build.VERSION.SDK_INT >= 31) 0 else PendingIntent.FLAG_IMMUTABLE
            usbManager.requestPermission(
                mouse,
                PendingIntent.getActivity(this, 0, Intent(this, javaClass::class.java), flags),
            )
            deviceName = mouse.deviceName
            return
        }
        openAndRun(mouse)
    }

    private fun openAndRun(mouse: UsbDevice) {
        try {
            CrashLog.log("usb step: openDevice ${mouse.deviceName}")
            val c = usbManager.openDevice(mouse) ?: run { renderRaw("openDevice 失败"); return }
            conn = c
            deviceName = mouse.deviceName
            var targetIntf: UsbInterface? = null
            var ep: UsbEndpoint? = null
            for (k in 0 until mouse.interfaceCount) {
                val it = mouse.getInterface(k)
                if (it.interfaceClass == UsbConstants.USB_CLASS_HID) {
                    for (e in 0 until it.endpointCount) {
                        val a = it.getEndpoint(e)
                        if (a.type == UsbConstants.USB_ENDPOINT_XFER_INT && a.direction == UsbConstants.USB_DIR_IN) {
                            targetIntf = it; ep = a; break
                        }
                    }
                }
                if (targetIntf != null) break
            }
            if (targetIntf == null || ep == null) { renderRaw("无 HID INT-IN 端点"); return }
            CrashLog.log("usb step: claimInterface(force)")
            if (!c.claimInterface(targetIntf, true)) { renderRaw("claimInterface 被拒"); return }
            claimed = targetIntf
            CrashLog.log("usb step: claimed ok ep maxPkt=${ep.maxPacketSize}")

            // ---- descriptor-driven layout ----
            val layouts = tryReadDescriptor(c, targetIntf)
            if (layouts != null && layouts.isNotEmpty()) {
                val l = layouts.values.maxByOrNull { it.wireLen }!!
                reportLen = l.wireLen
                reportIdLen = if (l.id != 0) 8 else 0
                xBit = l.xBit; yBit = l.yBit; wheelBit = l.wheelBit
                btnBase = l.btnBase; btnCount = l.btnCount
                layoutSrc = "描述符 id${l.id} len${reportLen}B x@${xBit} y@${yBit}"
                CrashLog.log("usb layout from descriptor: $layoutSrc")
            } else {
                layoutSrc = "校准中(晃鼠标)"
                heuristicDone = false
                reportLen = 4; xBit = 8; yBit = 16; wheelBit = 24; btnBase = 0
                CrashLog.log("usb descriptor parse failed -> heuristic fallback")
            }

            polling = true
            startRequestLoop(c, ep)
            registerDetach()
            renderRaw("USB 鼠标已接管(直读,无边界)")
        } catch (e: Exception) {
            CrashLog.log("usb init crash: ${e.javaClass.name}: ${e.message}")
            renderRaw("初始化失败: ${e.message}")
        }
    }

    private fun tryReadDescriptor(c: UsbDeviceConnection, it: UsbInterface): Map<Int, HidLayout>? {
        return try {
            val raw = c.rawDescriptors ?: return null
            // walk to our interface's HID class descriptor (0x21) -> wDescriptorLength at +7
            var off = 0
            var inIntf = -1
            var wantLen = -1
            var idx = 0
            while (off + 2 <= raw.size) {
                val len = raw[off].toInt() and 0xFF
                val type = raw[off + 1].toInt() and 0xFF
                if (len <= 0) break
                when (type) {
                    0x04 -> { inIntf = raw[off + 3].toInt() and 0xFF }
                    0x21 -> {
                        if (len >= 9) {
                            wantLen = ((raw[off + 8].toInt() and 0xFF) shl 8) or (raw[off + 7].toInt() and 0xFF)
                        }
                    }
                }
                off += len
                idx++
            }
            if (wantLen <= 0 || wantLen > 4096) return null
            val buf = ByteArray(wantLen)
            val got = c.controlTransfer(
                0xA1, // DIR_IN | TYPE_CLASS | RECIP_INTERFACE
                0x06, 0x2200, it.id, buf, buf.size, 500,
            )
            CrashLog.log("usb step: GET_REPORT_DESCRIPTOR want=$wantLen got=$got")
            if (got <= 0) return null
            val map = HidReportParser.parse(buf.copyOf(got))
            map.values.firstOrNull { it.xBit >= 0 && it.yBit >= 0 }?.let { return map }
            map
        } catch (e: Exception) {
            CrashLog.log("usb descriptor err: ${e.message}")
            null
        }
    }

    // ---------------- USB request loop ----------------

    @Volatile private var totalReports = 0L
    private var reportsPerSec = "?"
    private var rpWindowAt = 0L
    private var rpBase = 0L

    private fun startRequestLoop(c: UsbDeviceConnection, ep: UsbEndpoint) {
        Thread {
            try {
                val req = UsbRequest()
                if (!req.initialize(c, ep)) { runOnUiThread { renderRaw("UsbRequest.initialize 失败") }; return@Thread }
                val buf = ByteBuffer.allocateDirect(ep.maxPacketSize.coerceAtLeast(64))
                CrashLog.log("usb step: entering UsbRequest loop")
                while (polling) {
                    buf.clear()
                    if (!req.queue(buf)) break
                    val done = c.requestWait() ?: break
                    if (done === req) {
                        buf.flip()
                        val n = buf.remaining()
                        if (n > 0) {
                            val chunk = ByteArray(n); buf.get(chunk)
                            feedBytes(chunk)
                        }
                    }
                }
                CrashLog.log("usb step: loop exited (detach or error)")
            } catch (e: Exception) {
                CrashLog.log("usb loop crash: ${e.message}")
            }
            runOnUiThread { polling = false; renderRaw("读取停止(拔线或错误),点释放再接管") }
        }.start()
    }

    private fun feedBytes(data: ByteArray) {
        var i = 0
        while (i < data.size) {
            val n = pending.size
            pending.add(data[i]); i++
            if (n + 1 >= reportLen && reportLen > 0) {
                val rep = ByteArray(reportLen)
                for (k in 0 until reportLen) rep[k] = pending[k]
                for (k in 0 until reportLen) pending.removeAt(0)
                dispatchReport(rep)
                if (pending.size > 2048) { pending.clear() } // resync on drift
            }
        }
    }

    private fun dispatchReport(rep: ByteArray) {
        totalReports++
        if (totalReports - rpBase > 50) {
            val now = System.currentTimeMillis()
            if (rpWindowAt == 0L) { rpWindowAt = now; rpBase = totalReports }
            else if (now - rpWindowAt > 1000) {
                reportsPerSec = ((totalReports - rpBase) * 1000 / (now - rpWindowAt)).toString()
                rpWindowAt = now; rpBase = totalReports
            }
        }
        if (!heuristicDone) maybeCalibrate(rep)
        val dx = if (xBit >= 0) HidReportParser.sfield(rep, xBit - reportIdLen, 8) else 0
        val dy = if (yBit >= 0) HidReportParser.sfield(rep, yBit - reportIdLen, 8) else 0
        val wh = if (wheelBit >= 0) HidReportParser.sfield(rep, wheelBit - reportIdLen, 8) else 0
        val btns = readButtons(rep)
        if (dx != 0 || dy != 0) { pendX += dx; pendY += dy; moves++ }
        if (wh != 0) pendWheel += wh
        // LMB edge (gated)
        val left = btns and 0x01 != 0
        val was = lastBtn and 0x01 != 0
        if (left && !was) onPhysPress()
        if (!left && was) onPhysRelease()
        // non-LMB buttons forward immediately
        val others = btns and 0x01.inv()
        if (others != (lastBtn and 0x01.inv())) {
            btnMask = (btnMask and 0x01.inv()) or others            clickReport(btnMask)
        }
        lastBtn = btns
    }

    private fun readButtons(rep: ByteArray): Int {
        if (btnBase < 0) return 0
        var v = 0
        for (k in 0 until btnCount.coerceAtMost(5)) {
            val bit = btnBase - reportIdLen + k
            if (bit < rep.size * 8) v = v or (((rep[bit / 8].toInt() shr (bit % 8)) and 1) shl k)
        }
        return v
    }

    // ---- gate plumbing (same semantics as relay page) ----
    private fun onPhysPress() {
        if (!lmbHeld && (lmbWantsRelease || lmbPhysUp) && !lmbDownSent) {
            btnMask = btnMask or 0x01            clickReport(btnMask)
            handler.postDelayed({
                val up = btnMask and 1.inv()
                    btnMask = up
                clickReport(up)
            }, 60)
            lmbWantsRelease = false; lmbPhysUp = false
            holds++
        }
        lmbHeld = true; lmbPhysUp = false
        pressAt = System.currentTimeMillis()
        BridgeGlobals.server?.sendLmb(true)
    }

    private fun onPhysRelease() {
        lmbPhysUp = true
        BridgeGlobals.server?.sendLmb(false)
        if (lmbDownSent) {
            lmbDownSent = false
            btnMask = btnMask and 1.inv()            clickReport(btnMask)
        } else {
            lmbWantsRelease = true
        }
    }

    // ---- heuristic fallback (unchanged idea, applied on stride-resolved reports) ----
    @Volatile private var heuristicDone = true
    private val calSamples = ArrayList<ByteArray>()
    private fun maybeCalibrate(rep: ByteArray) {
        if (layoutSrc.startsWith("描述符")) return
        calSamples.add(rep.copyOf())
        if (calSamples.size >= 40) {
            runCalibration()
        }
    }

    private fun runCalibration() {
        val len = calSamples[0].size
        val score = DoubleArray(len)
        for (b in 0 until len) {
            var mx = 0.0
            for (s0 in calSamples) mx += s0[b]
            mx /= calSamples.size
            var v = 0.0
            for (s0 in calSamples) { val d = s0[b] - mx; v += d * d }
            score[b] = v
        }
        val order = score.indices.sortedByDescending { score[it] }
        val hot = order.take(2).sorted()
        if (hot.size == 2) {
            xBit = hot[0] * 8 + reportIdLen
            yBit = hot[1] * 8 + reportIdLen
            btnBase = ((hot[0] - 1).coerceAtLeast(0)) * 8 + reportIdLen
            btnCount = 8
            layoutSrc = "校准 dx@${hot[0]} dy@${hot[1]}"
            heuristicDone = true
            CrashLog.log("usb calibrated: $layoutSrc")
        }
    }

    // ---- lifecycle ----
    private var detachRc: BroadcastReceiver? = null
    private fun registerDetach() {
        if (detachRc != null) return
        val rc = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent?.action == UsbManager.ACTION_USB_DEVICE_DETACHED) {
                    val dev = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (dev?.deviceName == deviceName) {
                        CrashLog.log("usb detached broadcast")
                        polling = false
                        release()
                    }
                }
            }
        }
        registerReceiver(rc, IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED))
        detachRc = rc
    }

    private fun release() {
        polling = false
        try { claimed?.let { conn?.releaseInterface(it) } } catch (_: Exception) {}
        try { conn?.close() } catch (_: Exception) {}
        conn = null; claimed = null
        pending.clear()
        renderRaw("已释放,鼠标回到手机系统")
    }

    private fun renderRaw(s: String) { CrashLog.log("usb: $s"); handler.post { renderStatus() } }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        polling = false
        release()
        super.onDestroy()
    }
}
