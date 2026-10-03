package com.valaim.btaimbridge

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.hardware.usb.UsbRequest
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity
import java.nio.ByteBuffer

/**
 * USB raw mouse proxy (v2, spec-reviewed):
 *  - claim the HID interface, read INTERRUPT-IN via UsbRequest (cancellable)
 *  - report length/field bits come from the HID Report Descriptor;
 *    variance calibration remains only as a fallback
 *  - stream accumulator splits multi-report transfers correctly
 *  - LMB gate keeps the never-swallow semantics of the relay page
 */
class MouseUsbProxyActivity : AppCompatActivity() {

    private lateinit var usbManager: UsbManager
    private var conn: UsbDeviceConnection? = null
    private var claimed: UsbInterface? = null
    private var deviceName: String = ""
    @Volatile private var polling = false
    private val handler = Handler(Looper.getMainLooper())

    private var reportLen = 4
    private var reportIdLen = 0
    private var xBit = 8
    private var yBit = 16
    private var wheelBit = 24
    private var btnBase = 0
    private var btnCount = 5
    private var layoutSrc = "未接管"

    private val pending = ArrayList<Byte>(4096)

    @Volatile private var pendX = 0
    @Volatile private var pendY = 0
    @Volatile private var pendWheel = 0
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

    @Volatile private var totalReports = 0L
    private var reportsPerSec = "?"
    private var rpWindowAt = 0L
    private var rpBase = 0L

    private val out = android.widget.TextView(this)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
        val start = android.widget.Button(this).apply { text = "开始接管鼠标(USB直读,无边界)" }
        start.setOnClickListener { takeOver() }
        val rel = android.widget.Button(this).apply { text = "释放鼠标(还给系统)" }
        rel.setOnClickListener { release() }
        val back = android.widget.Button(this).apply { text = "退回中转模式" }
        back.setOnClickListener {
            startActivity(android.content.Intent(this@MouseUsbProxyActivity, MouseProxyActivity::class.java))
        }
        out.textSize = 13f
        val pad = (resources.displayMetrics.density * 12).toInt()
        out.setPadding(pad, pad, pad, pad)
        val col = android.widget.LinearLayout(this)
        col.orientation = android.widget.LinearLayout.VERTICAL
        val lp = android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT)
        col.addView(start, lp); col.addView(rel, lp); col.addView(back, lp); col.addView(out, lp)
        val sc = android.widget.ScrollView(this)
        sc.addView(col)
        setContentView(sc)
        CrashLog.log("USB proxy v2 onCreate")
        renderStatus()
        handler.postDelayed(tick, 4)
    }

    private fun clickReport(mask: Int) {
        val h = BridgeGlobals.hid ?: return
        h.sendReport(mask, 1, 0, 0)
        h.sendReport(mask, -1, 0, 0)
    }

    private val tick = object : Runnable {
        override fun run() {
            val h = BridgeGlobals.hid
            if (h != null) {
                val assist = BridgeGlobals.server?.assistOn == true
                if ((lmbHeld || lmbWantsRelease || lmbPhysUp) && !lmbDownSent) {
                    val nailed = BridgeGlobals.server?.nailOk == true
                    val expired = System.currentTimeMillis() - pressAt > graceMs
                    if (!assist) {
                        fireDown()
                    } else if (nailed) {
                        nails++; fireDown()
                    } else if (expired) {
                        holds++; fireDown()
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
                if ((btnMask and 0x01) != 0 && !lmbHeld && !lmbWantsRelease && !lmbPhysUp &&
                    lmbDownSent && System.currentTimeMillis() - lastDownAt > 2000) {
                    btnMask = btnMask and 1.inv()
                    lmbDownSent = false
                    clickReport(btnMask)
                    CrashLog.log("usb phantom LMB cleared")
                }
            }
            renderStatus()
            handler.postDelayed(this, 4)
        }
    }

    private fun Boolean.millisStillHeld(): Boolean = this
    private fun fireDown() {
        btnMask = btnMask or 0x01
        lmbDownSent = true
        lastDownAt = System.currentTimeMillis()
        clickReport(btnMask)
        if (lmbWantsRelease || lmbPhysUp || !lmbHeld) {
            lmbHeld = false; lmbWantsRelease = false; lmbPhysUp = false
            handler.postDelayed({
                val up = btnMask and 1.inv()
                btnMask = up
                clickReport(up)
            }, 60)
        }
    }

    private fun renderStatus() {
        out.text = buildString {
            appendLine(if (polling) "USB 直读运行中 [$layoutSrc]" else "未接管")
            appendLine("移动转发 $moves 帧 | 钉住放 $nails / 超时放 $holds")
            appendLine("左键: ${if (lmbHeld) if (lmbDownSent) "已放行" else "扣住…" else "-"} 辅助:${BridgeGlobals.server?.assistOn}")
            appendLine("报文 $totalReports (~$reportsPerSec/s)")
            appendLine("---- 诊断 ----")
            append(CrashLog.tail(3))
        }
    }

    // ---------- takeover ----------

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
            CrashLog.log("usb step: openDevice")
            val c = usbManager.openDevice(mouse) ?: run { renderRaw("openDevice 失败"); return }
            conn = c
            deviceName = mouse.deviceName
            var targetIntf: UsbInterface? = null
            var ep: UsbEndpoint? = null
            outer@ for (k in 0 until mouse.interfaceCount) {
                val it = mouse.getInterface(k)
                if (it.interfaceClass == UsbConstants.USB_CLASS_HID) {
                    for (e in 0 until it.endpointCount) {
                        val a = it.getEndpoint(e)
                        if (a.type == UsbConstants.USB_ENDPOINT_XFER_INT && a.direction == UsbConstants.USB_DIR_IN) {
                            targetIntf = it; ep = a; break@outer
                        }
                    }
                }
            }
            if (targetIntf == null || ep == null) { renderRaw("无 HID INT-IN 端点"); return }
            CrashLog.log("usb step: claimInterface(force)")
            if (!c.claimInterface(targetIntf, true)) { renderRaw("claimInterface 被拒"); return }
            claimed = targetIntf
            CrashLog.log("usb step: claimed ok, maxPkt=${ep.maxPacketSize}")

            val layouts = tryReadDescriptor(c, targetIntf)
            val l = layouts?.values?.filter { it.xBit >= 0 && it.yBit >= 0 }?.maxByOrNull { it.wireLen }
            if (l != null) {
                reportLen = l.wireLen
                reportIdLen = if (l.id != 0) 8 else 0
                xBit = l.xBit; yBit = l.yBit; wheelBit = l.wheelBit
                btnBase = if (l.btnBase >= 0) l.btnBase else 0
                btnCount = if (l.btnCount > 0) l.btnCount else 5
                layoutSrc = "描述符 id${l.id} ${l.wireLen}B"
                CrashLog.log("usb layout: $layoutSrc x@$xBit y@$yBit btn@$btnBase")
            } else {
                reportLen = 4; reportIdLen = 0; xBit = 8; yBit = 16; wheelBit = 24; btnBase = 0
                layoutSrc = "校准模式(请晃鼠标)"
                CrashLog.log("usb: descriptor unavailable -> calibration mode")
            }
            calSamples.clear()

            polling = true
            startRequestLoop(c, ep)
            registerDetach()
            renderRaw("已接管:$layoutSrc")
        } catch (e: Exception) {
            CrashLog.log("usb init crash: ${e.javaClass.name}: ${e.message}")
            renderRaw("初始化失败: ${e.message}")
        }
    }

    private fun tryReadDescriptor(c: UsbDeviceConnection, it: UsbInterface): Map<Int, HidLayout>? {
        return try {
            val raw = c.rawDescriptors ?: return null
            var off = 0
            var wantLen = -1
            while (off + 2 <= raw.size) {
                val len = raw[off].toInt() and 0xFF
                val type = raw[off + 1].toInt() and 0xFF
                if (len <= 0) break
                if (type == 0x21 && len >= 9) {
                    wantLen = ((raw[off + 8].toInt() and 0xFF) shl 8) or (raw[off + 7].toInt() and 0xFF)
                }
                off += len
            }
            if (wantLen <= 0 || wantLen > 4096) return null
            val buf = ByteArray(wantLen)
            val got = c.controlTransfer(0xA1 /* IN|CLASS|INTERFACE */, 0x06, 0x2200, it.id, buf, buf.size, 500)
            CrashLog.log("usb step: GET_REPORT_DESCRIPTOR want=$wantLen got=$got")
            if (got <= 0) return null
            HidReportParser.parse(buf.copyOf(got))
        } catch (e: Exception) {
            CrashLog.log("usb descriptor err: ${e.message}")
            null
        }
    }

    // ---------- read loop ----------

    private fun startRequestLoop(c: UsbDeviceConnection, ep: UsbEndpoint) {
        Thread {
            try {
                val req = UsbRequest()
                if (!req.initialize(c, ep)) { runOnUiThread { renderRaw("UsbRequest.initialize 失败") }; return@Thread }
                val buf = ByteBuffer.allocateDirect(ep.maxPacketSize.coerceAtLeast(64))
                CrashLog.log("usb step: UsbRequest loop start")
                while (polling) {
                    buf.clear()
                    if (!req.queue(buf)) break
                    val done = c.requestWait() ?: break
                    if (done === req) {
                        buf.flip()
                        val n = buf.remaining()
                        if (n > 0) {
                            val chunk = ByteArray(n)
                            buf.get(chunk)
                            feedBytes(chunk)
                        }
                    }
                }
                CrashLog.log("usb step: loop exit")
            } catch (e: Exception) {
                CrashLog.log("usb loop crash: ${e.message}")
            }
            runOnUiThread { polling = false; renderRaw("读取停止(拔线/错误) - 可点释放再接管") }
        }.start()
    }

    private fun feedBytes(data: ByteArray) {
        for (b in data) {
            pending.add(b)
            if (pending.size >= reportLen) {
                val rep = ByteArray(reportLen)
                for (k in 0 until reportLen) rep[k] = pending[k]
                repeat(reportLen) { pending.removeAt(0) }
                dispatchReport(rep)
            }
        }
        if (pending.size > 4096) pending.clear()
    }

    private val calSamples = ArrayList<ByteArray>()

    private fun dispatchReport(rep: ByteArray) {
        totalReports++
        if (rpWindowAt == 0L) { rpWindowAt = System.currentTimeMillis(); rpBase = totalReports }
        else if (totalReports - rpBase > 200) {
            val now = System.currentTimeMillis()
            reportsPerSec = ((totalReports - rpBase) * 1000 / (now - rpWindowAt)).toString()
            rpWindowAt = now; rpBase = totalReports
        }
        if (layoutSrc.startsWith("校准")) {
            calSamples.add(rep.copyOf())
            if (calSamples.size >= 40) runCalibration()
        }
        val dx = HidReportParser.sfield(rep, xBit - reportIdLen, 8)
        val dy = HidReportParser.sfield(rep, yBit - reportIdLen, 8)
        val wh = if (wheelBit >= 0) HidReportParser.sfield(rep, wheelBit - reportIdLen, 8) else 0
        var btns = 0
        if (btnBase >= 0) {
            for (k in 0 until btnCount.coerceAtMost(5)) {
                val bit = btnBase - reportIdLen + k
                if (bit in 0 until rep.size * 8) {
                    btns = btns or (((rep[bit / 8].toInt() shr (bit % 8)) and 1) shl k)
                }
            }
        }
        if (dx != 0 || dy != 0) { pendX += dx; pendY += dy; moves++ }
        if (wh != 0) pendWheel += wh
        val left = btns and 0x01 != 0
        val wasLeft = lastBtn and 0x01 != 0
        if (left && !wasLeft) onPhysPress()
        if (!left && wasLeft) onPhysRelease()
        val others = btns and 0x01.inv()
        val prevOthers = lastBtn and 0x01.inv()
        if (others != prevOthers) {
            btnMask = (btnMask and 0x01.inv()) or others
            clickReport(btnMask)
        }
        lastBtn = btns
    }

    private fun onPhysPress() {
        if (!lmbHeld && (lmbWantsRelease || lmbPhysUp) && !lmbDownSent) {
            // second tap while first still gated: flush first as plain click
            btnMask = btnMask or 0x01
            clickReport(btnMask)
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
            btnMask = btnMask and 1.inv()
            clickReport(btnMask)
        } else {
            lmbWantsRelease = true
        }
    }

    private fun runCalibration() {
        val len = calSamples.firstOrNull()?.size ?: return
        val score = DoubleArray(len)
        for (b in 0 until len) {
            var mean = 0.0
            for (s0 in calSamples) mean += s0[b]
            mean /= calSamples.size
            var v = 0.0
            for (s0 in calSamples) { val d = s0[b] - mean; v += d * d }
            score[b] = v
        }
        val hot = score.indices.sortedByDescending { score[it] }.take(2).sorted()
        if (hot.size == 2) {
            xBit = hot[0] * 8 + reportIdLen
            yBit = hot[1] * 8 + reportIdLen
            btnBase = ((hot[0] - 1).coerceAtLeast(0)) * 8 + reportIdLen
            btnCount = 8
            layoutSrc = "校准 dx@${hot[0]} dy@${hot[1]}"
            CrashLog.log("usb calibrated: $layoutSrc")
        }
    }

    // ---------- lifecycle ----------

    private var detachRc: BroadcastReceiver? = null
    private fun registerDetach() {
        if (detachRc != null) return
        val rc = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent?.action == UsbManager.ACTION_USB_DEVICE_DETACHED) {
                    @Suppress("DEPRECATION")
                    val dev = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (dev?.deviceName == deviceName) {
                        CrashLog.log("usb detached")
                        polling = false
                        release()
                    }
                }
            }
        }
        try {
            if (android.os.Build.VERSION.SDK_INT >= 33) {
                registerReceiver(rc, IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED), Context.RECEIVER_NOT_EXPORTED)
            } else {
                registerReceiver(rc, IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED))
            }
        } catch (_: Exception) {}
        detachRc = rc
    }

    private fun release() {
        polling = false
        try { claimed?.let { conn?.releaseInterface(it) } } catch (_: Exception) {}
        try { conn?.close() } catch (_: Exception) {}
        conn = null; claimed = null
        pending.clear()
        renderRaw("已释放,鼠标回手机系统")
    }

    private fun renderRaw(s: String) {
        CrashLog.log("usb: $s")
        handler.post { renderStatus() }
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        polling = false
        release()
        super.onDestroy()
    }
}
