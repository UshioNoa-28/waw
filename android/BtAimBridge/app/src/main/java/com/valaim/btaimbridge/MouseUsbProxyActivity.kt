package com.valaim.btaimbridge

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlin.concurrent.thread

/**
 * USB raw mouse proxy: reads HID boot-protocol reports straight from the mouse
 * (OTG), bypassing Android's pointer layer entirely - no system cursor, no
 * screen-edge clamping, no OEM pointer-capture policy.
 *
 * Buttons byte: bit0 L, bit1 R, bit2 M. Movement forwarded immediately;
 * LEFT is held until the PC nails (or 140ms grace).
 */
class MouseUsbProxyActivity : AppCompatActivity() {

    companion object { const val ACTION_PERM = "com.valaim.btaimbridge.USBPerm" }

    private lateinit var out: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var usbManager: UsbManager? = null
    private var conn: UsbDeviceConnection? = null
    private var claimed: UsbInterface? = null
    @Volatile private var polling = false
    @Volatile private var btnMask = 0
    @Volatile private var lmbHeld = false
    @Volatile private var lmbDownSent = false
    @Volatile private var pressAt = 0L
    private val graceMs = 140L
    private var reports = 0
    @Volatile private var dxOff = 1
    @Volatile private var dyOff = 2
    @Volatile private var whOff = 3
    @Volatile private var btnOff = 0
    @Volatile private var calibrated = false
    private val calSamples = ArrayList<ByteArray>()
    private var lastReportAt = 0L
    private var gateNail = 0
    private var gateTimeout = 0

    private val flush = object : Runnable {
        override fun run() {
            val srv = BridgeGlobals.server
            if (lmbHeld && !lmbDownSent) {
                if (srv?.nailOk == true || System.currentTimeMillis() - pressAt > graceMs) {
                    if (srv?.nailOk == true) gateNail++ else gateTimeout++
                    btnMask = btnMask or 0x01
                    BridgeGlobals.hid?.sendReport(btnMask, 0, 0, 0)
                    lmbDownSent = true
                }
            }
            handler.postDelayed(this, 4)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        out = TextView(this).apply { textSize = 14f; setPadding(32, 48, 32, 32) }
        CrashLog.log("USB proxy onCreate enter")
        val row = android.widget.LinearLayout(this).apply { orientation = android.widget.LinearLayout.VERTICAL }
        row.addView(out, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        val btn = android.widget.Button(this).apply { text = "开始接管鼠标" }
        btn.setOnClickListener { safeInit() }
        row.addView(btn, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT))
        val fb = android.widget.Button(this).apply { text = "退回中转模式(边界受限但更兼容)" }
        fb.setOnClickListener { startActivity(android.content.Intent(this@MouseUsbProxyActivity, com.valaim.btaimbridge.MouseProxyActivity::class.java)) }
        row.addView(fb, android.widget.LinearLayout.LayoutParams(
            android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
            android.widget.LinearLayout.LayoutParams.WRAP_CONTENT))
        setContentView(row)
    }

    override fun onDestroy() {
        polling = false
        handler.removeCallbacks(flush)
        try { unregisterReceiver(permReceiver) } catch (_: Exception) {}
        claimed?.let { conn?.releaseInterface(it) }
        try { conn?.close() } catch (_: Exception) {}
        super.onDestroy()
    }

    private var inited = false
    private var fallbackBtn: android.widget.Button? = null

    private fun safeInit() {
        if (inited) return
        try {
            usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
            val rflags = if (android.os.Build.VERSION.SDK_INT >= 33) Context.RECEIVER_NOT_EXPORTED else 0
            registerReceiver(permReceiver, IntentFilter(ACTION_PERM), rflags)
            handler.post(flush)
            inited = true
            render("已就绪,点「开始接管鼠标」")
            autoPick()
        } catch (e: Exception) {
            CrashLog.log("init fail: ${e}")
            render("启动失败: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private val permReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == ACTION_PERM &&
                intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)) {
                (intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE))?.let { open(it) }
            }
        }
    }

    private fun hidMice(): List<UsbDevice> {
        val list = ArrayList<UsbDevice>()
        usbManager?.deviceList?.values?.forEach { d ->
            for (i in 0 until d.interfaceCount) {
                val itf = d.getInterface(i)
                if (itf.interfaceClass == UsbConstants.USB_CLASS_HID && itf.interfaceSubclass == 1 && hasInEp(itf)) {
                    list.add(d); return@forEach
                }
            }
        }
        return list
    }

    private fun autoPick() {
        try { autoPick0() } catch (e: Exception) {
            render("扫描失败: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun autoPick0() {
        val mice = hidMice()
        CrashLog.log("hidMice found: ${mice.size}")
        if (mice.isEmpty()) { render("没找到 USB 鼠标。检查: OTG开关已开? 鼠标插在hub的A口? 重新点一次。"); return }
        val d = mice[0]
        val mgr = usbManager ?: return
        if (!mgr.hasPermission(d)) {
            val flags = if (android.os.Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_IMMUTABLE else 0
            mgr.requestPermission(d, PendingIntent.getBroadcast(this, 0, Intent(ACTION_PERM), flags))
            render("弹出授权框请点允许(勾选默认允许)。")
            return
        }
        open(d)
    }

    private fun hasInEp(itf: UsbInterface): Boolean {
        for (i in 0 until itf.endpointCount) {
            val e = itf.getEndpoint(i)
            if (e.type == UsbConstants.USB_ENDPOINT_XFER_INT && e.direction == UsbConstants.USB_DIR_IN) return true
        }
        return false
    }

    private fun findBootInterface(dev: UsbDevice): UsbInterface? {
        var anyMouse: UsbInterface? = null
        for (i in 0 until dev.interfaceCount) {
            val itf = dev.getInterface(i)
            if (itf.interfaceClass == UsbConstants.USB_CLASS_HID && itf.interfaceSubclass == 1 && hasInEp(itf)) {
                if (itf.interfaceProtocol == 2) return itf
                if (anyMouse == null) anyMouse = itf
            }
        }
        return anyMouse
    }

    private fun open(dev: UsbDevice) {
        try { open0(dev) } catch (e: Exception) {
            render("接管失败: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun open0(dev: UsbDevice) {
        val itf = findBootInterface(dev) ?: run { render("设备无 boot-protocol HID 接口: ${dev.deviceName}"); return }
        val mgr = usbManager ?: return
        CrashLog.log("usb step: openDevice?")
        val c = mgr.openDevice(dev) ?: run { render("openDevice 失败"); return }
        CrashLog.log("usb step: claimInterface(force)")
        if (!c.claimInterface(itf, true)) { render("claimInterface 被拒(可能被系统指针进程占用)"); c.close(); return }
        CrashLog.log("usb step: claimed ok")
        conn = c; claimed = itf
        // idle=0 => always deliver; report descriptor says size, use 8 and clamp
        try { c.controlTransfer(0x21, 0x0A, 0, 0, ByteArray(0), 0, 100) } catch (_: Exception) { }
        CrashLog.log("usb step: set_idle sent")
        polling = true
        CrashLog.log("claimed ok, polling thread start")
        thread(name = "usb-mouse") { pollLoop(c, itf) }
        render("USB 鼠标已接管: ${dev.vendorId.toString(16)}:${dev.productId.toString(16)}")
    }

    private fun pollLoop(c: UsbDeviceConnection, itf: UsbInterface) {
        try {
        var ep: android.hardware.usb.UsbEndpoint? = null
        for (i in 0 until itf.endpointCount) {
            val e = itf.getEndpoint(i)
            if (e.type == UsbConstants.USB_ENDPOINT_XFER_INT && e.direction == UsbConstants.USB_DIR_IN) { ep = e; break }
        }
        if (ep == null) { CrashLog.log("no IN endpoint"); handler.post { render("该鼠标无输入端点,换页或换鼠标") }; return }
        val buf = ByteArray(if (ep.maxPacketSize > 0) ep.maxPacketSize else 8)
        CrashLog.log("usb step: entering bulk loop (pkt=${buf.size})")
        var firstLogged = false
        while (polling) {
            val n = c.bulkTransfer(ep, buf, buf.size, 200)
            if (!firstLogged && n > 0) {
                firstLogged = true
                CrashLog.log("usb step: FIRST REPORT n=$n bytes=${buf.take(n.coerceAtMost(6)).joinToString(",")}")
            }
            if (n >= 3) {
                reports++; lastReportAt = System.currentTimeMillis()
                if (!calibrated) {
                    calSamples.add(buf.copyOf(n))
                    if (calSamples.size >= 40) runCalibration()
                    else { handler.post { render("校准中…请匀速晃晃鼠标 (${calSamples.size}/40)") }; continue }
                }
                val buttons = buf[btnOff].toInt() and 0x07
                var dx = buf[dxOff].toInt(); var dy = buf[dyOff].toInt()
                val wheel = if (n > whOff) buf[whOff].toInt() else 0
                // LMB gate
                val nowLmb = buttons and 0x01 != 0
                if (nowLmb && !lmbHeld) { lmbHeld = true; pressAt = System.currentTimeMillis() }
                if (!nowLmb && lmbHeld) {
                    lmbHeld = false
                    if (lmbDownSent) {
                        lmbDownSent = false
                        btnMask = btnMask and 1.inv()
                        BridgeGlobals.hid?.sendReport(btnMask, 0, 0, 0)
                    } else {
                        btnMask = btnMask or 0x01
                        BridgeGlobals.hid?.sendReport(btnMask, 0, 0, 0)
                        handler.postDelayed({
                            btnMask = btnMask and 1.inv()
                            BridgeGlobals.hid?.sendReport(btnMask, 0, 0, 0)
                        }, 14)
                    }
                }
                // R/M passthrough
                val wantR = if (buttons and 0x02 != 0) 0x02 else 0
                val wantM = if (buttons and 0x04 != 0) 0x04 else 0
                val others = wantR or wantM
                if ((btnMask and 0x06) != others) {
                    btnMask = (btnMask and 0x06.inv()) or others
                    BridgeGlobals.hid?.sendReport(btnMask, 0, 0, 0)
                }
                var mx = dx; var my = dy
                while (mx != 0 || my != 0) {
                    val cx = mx.coerceIn(-127, 127); val cy = my.coerceIn(-127, 127)
                    mx -= cx; my -= cy
                    BridgeGlobals.hid?.sendReport(btnMask, cx, cy, 0)
                }
                if (wheel != 0) BridgeGlobals.hid?.sendReport(btnMask, 0, 0, wheel.coerceIn(-3, 3))
            } else if (n == -1) {
                handler.post { render("读取超时/拔线, 重开本页恢复。"); }
                Thread.sleep(200)
            }
        }
        } catch (e: Exception) {
            CrashLog.log("pollLoop died: ${e.javaClass.simpleName}: ${e.message}")
            handler.post { render("读取线程异常: ${e.javaClass.simpleName}") }
        }
    }

    private fun runCalibration() {
        // per-byte signed variance across samples; the two hottest bytes are dx/dy
        val len = calSamples[0].size
        val score = DoubleArray(len)
        for (b in 0 until len) {
            var mx = 0.0
            for (s0 in calSamples) mx += s0[b]
            mx /= calSamples.size
            var v = 0.0
            for (s0 in calSamples) { val d = s0[b] - mx; v += d * d }
            score[b] = if (b == 0) v * 0.2 else v   // byte0 rarely swings = report id bias
        }
        val order = score.indices.sortedByDescending { score[it] }
        val hot = order.take(2).sorted()
        dxOff = hot[0]; dyOff = hot[1]
        btnOff = (hot[0] - 1).coerceAtLeast(0)
        whOff = (hot[1] + 1).coerceAtMost(len - 1)
        calibrated = true
        CrashLog.log("calibrated btn=$btnOff dx=$dxOff dy=$dyOff wheel=$whOff bytes=$len")
        handler.post { render("校准完成 dx@$dxOff dy@$dyOff 键@$btnOff") }
    }

    private fun render(extra: String = "") {
        val age: Double = if (lastReportAt == 0L) -1.0 else (System.currentTimeMillis() - lastReportAt) / 1000.0
        out.text = buildString {
            appendLine("USB 鼠标代理 (boot-protocol 直读)")
            appendLine("报文: $reports (最近 ${if (age < 0) "-" else String.format("%.1f", age) + "s 前"})")
            appendLine(if (calibrated) "校准: 键@$btnOff dx@$dxOff dy@$dyOff" else "未校准:晃鼠标")
            appendLine("左键: ${if (lmbHeld) "按住" else "-"} 已放行(钉住$gateNail/超时$gateTimeout)")
            appendLine("PC 连接: ${if (BridgeGlobals.clientAlive()) "在线" else "离线"}")
            if (extra.isNotEmpty()) appendLine(extra)
            appendLine("---- 诊断 ----")
            appendLine(CrashLog.tail(3))
        }
        handler.postDelayed({ if (!isFinishing) render() }, 500)
    }
}
