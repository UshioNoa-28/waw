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
        setContentView(out)
        try {
            usbManager = getSystemService(Context.USB_SERVICE) as UsbManager
            val flags = if (android.os.Build.VERSION.SDK_INT >= 33) Context.RECEIVER_NOT_EXPORTED else 0
            registerReceiver(permReceiver, IntentFilter(ACTION_PERM), flags)
            handler.post(flush)
            render()
            autoPick()
        } catch (e: Exception) {
            render("启动失败: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    override fun onDestroy() {
        polling = false
        handler.removeCallbacks(flush)
        try { unregisterReceiver(permReceiver) } catch (_: Exception) {}
        claimed?.let { conn?.releaseInterface(it) }
        try { conn?.close() } catch (_: Exception) {}
        super.onDestroy()
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
                if (itf.interfaceClass == UsbConstants.USB_CLASS_HID && itf.interfaceProtocol == 2) {
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
        if (mice.isEmpty()) { render("没找到 USB 鼠标(boot-protocol)。插好 OTG 后再开本页。"); return }
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

    private fun findBootInterface(dev: UsbDevice): UsbInterface? {
        for (i in 0 until dev.interfaceCount) {
            val itf = dev.getInterface(i)
            if (itf.interfaceClass == UsbConstants.USB_CLASS_HID && itf.interfaceProtocol == 2) return itf
        }
        return null
    }

    private fun open(dev: UsbDevice) {
        try { open0(dev) } catch (e: Exception) {
            render("接管失败: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun open0(dev: UsbDevice) {
        val itf = findBootInterface(dev) ?: run { render("设备无 boot-protocol HID 接口: ${dev.deviceName}"); return }
        val mgr = usbManager ?: return
        val c = mgr.openDevice(dev) ?: run { render("openDevice 失败"); return }
        if (!c.claimInterface(itf, true)) { render("claimInterface 被拒(可能被系统指针进程占用)"); c.close(); return }
        conn = c; claimed = itf
        // idle=0 => always deliver; report descriptor says size, use 8 and clamp
        c.controlTransfer(0x21, 0x0A, 0, 0, null, 0, 100)
        polling = true
        thread(name = "usb-mouse") { pollLoop(c, itf) }
        render("USB 鼠标已接管: ${dev.vendorId.toString(16)}:${dev.productId.toString(16)}")
    }

    private fun pollLoop(c: UsbDeviceConnection, itf: UsbInterface) {
        var ep = itf.getEndpoint(0)
        for (i in 0 until itf.endpointCount) {
            val e = itf.getEndpoint(i)
            if (e.type == UsbConstants.USB_ENDPOINT_XFER_INT && e.direction == UsbConstants.USB_DIR_IN) { ep = e; break }
        }
        val buf = ByteArray(if (ep.maxPacketSize > 0) ep.maxPacketSize else 8)
        while (polling) {
            val n = c.bulkTransfer(ep, buf, buf.size, 200)
            if (n >= 3) {
                reports++; lastReportAt = System.currentTimeMillis()
                val buttons = buf[0].toInt()
                var dx = buf[1].toInt(); var dy = buf[2].toInt()
                val wheel = if (n >= 4) buf[3].toInt() else 0
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
    }

    private fun render(extra: String = "") {
        val age: Double = if (lastReportAt == 0L) -1.0 else (System.currentTimeMillis() - lastReportAt) / 1000.0
        out.text = buildString {
            appendLine("USB 鼠标代理 (boot-protocol 直读)")
            appendLine("报文: $reports (最近 ${if (age < 0) "-" else String.format("%.1f", age) + "s 前"})")
            appendLine("左键: ${if (lmbHeld) "按住" else "-"} 已放行(钉住$gateNail/超时$gateTimeout)")
            appendLine("PC 连接: ${if (BridgeGlobals.clientAlive()) "在线" else "离线"}")
            if (extra.isNotEmpty()) appendLine(extra)
        }
        handler.postDelayed({ if (!isFinishing) render() }, 500)
    }
}
