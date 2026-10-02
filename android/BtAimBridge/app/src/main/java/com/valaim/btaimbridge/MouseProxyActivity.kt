package com.valaim.btaimbridge

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.InputDevice
import android.view.MotionEvent
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * USB-mouse input proxy (Vanguard-proof by construction: everything happens
 * on the phone, the game just sees one Bluetooth mouse).
 *
 *  - movement: forwarded to the PC immediately (4ms batches, auto-split to
 *    HID +-127 chunks) so your aim feel is untouched
 *  - LEFT button: held until the PC reports the crosshair is nailed ("N 1")
 *    or GRACE_MS expires (your own timing always wins eventually)
 *  - right / middle / wheel: passthrough
 */
class MouseProxyActivity : AppCompatActivity() {

    private val graceMs = 80L
    private lateinit var out: TextView
    private val handler = Handler(Looper.getMainLooper())

    @Volatile private var pendX = 0
    @Volatile private var pendY = 0
    @Volatile private var pendWheel = 0
    @Volatile private var btnMask = 0
    @Volatile private var trackedButtons = 0
    @Volatile private var lastX = -1f
    @Volatile private var lastY = -1f

    @Volatile private var lmbHeld = false
    @Volatile private var lmbDownSent = false
    @Volatile private var lmbWantsRelease = false
    @Volatile private var pressAt = 0L
    private var moves = 0
    private var holds = 0
    private var nails = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        out = TextView(this).apply { textSize = 14f; setPadding(32, 48, 32, 32) }
        setContentView(out)
        handler.post(tick)
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        if (lmbDownSent) { btnMask = btnMask and 1.inv(); hid()?.sendReport(btnMask, 0, 0, 0) }
        super.onDestroy()
    }

    private fun hid() = BridgeGlobals.hid

    private val tick = object : Runnable {
        override fun run() {
            val h = hid()
            if (h != null) {
                var released = false
                if (lmbHeld && !lmbDownSent) {
                    val nailed = BridgeGlobals.server?.nailOk == true
                    if (nailed || System.currentTimeMillis() - pressAt > graceMs) {
                        if (nailed) nails++ else holds++
                        btnMask = btnMask or 0x01
                        lmbDownSent = true
                        released = true
                        if (lmbWantsRelease) {           // quick tap while gated
                            lmbHeld = false; lmbWantsRelease = false
                            handler.postDelayed({
                                btnMask = btnMask and 1.inv()
                                hid()?.sendReport(btnMask, 0, 0, 0)
                            }, 15)
                        }
                    }
                }
                if (released) h.sendReport(btnMask, 0, 0, 0)
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
            }
            handler.postDelayed(this, 4)
        }
    }

    private fun render() {
        out.text = buildString {
            appendLine("鼠标代理运行中")
            appendLine("移动转发 $moves 帧 | 扣左键: 钉住放 $nails / 超时放 $holds")
            appendLine("左键状态: ${if (lmbHeld) if (lmbDownSent) "已放行" else "扣住等待钉住…" else "无"}")
            appendLine("PC 钉住信号: ${BridgeGlobals.server?.nailOk}")
            append("移动即时转发,左键最多等 ${graceMs}ms")
        }
    }

    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {
        if (!ev.isFromSource(InputDevice.SOURCE_MOUSE)) return super.dispatchGenericMotionEvent(ev)
        when (ev.actionMasked) {
            MotionEvent.ACTION_MOVE, MotionEvent.ACTION_HOVER_MOVE -> {
                val x = ev.getAxisValue(MotionEvent.AXIS_X)
                val y = ev.getAxisValue(MotionEvent.AXIS_Y)
                if (lastX >= 0f) {
                    val dx = (x - lastX).toInt()
                    val dy = (y - lastY).toInt()
                    if (kotlin.math.abs(dx) < 400 && kotlin.math.abs(dy) < 400 && (dx != 0 || dy != 0)) {
                        pendX += dx; pendY += dy; moves++
                    }
                }
                lastX = x; lastY = y
            }
            MotionEvent.ACTION_BUTTON_PRESS, MotionEvent.ACTION_BUTTON_RELEASE -> {
                val press = ev.actionMasked == MotionEvent.ACTION_BUTTON_PRESS
                val state = ev.buttonState
                val diff = if (press) state and trackedButtons.inv() else trackedButtons and state.inv()
                trackedButtons = state
                if (diff and MotionEvent.BUTTON_PRIMARY != 0) {
                    if (press) { lmbHeld = true; pressAt = System.currentTimeMillis() }
                    else {
                        lmbHeld = false
                        if (lmbDownSent) {
                            lmbDownSent = false
                            btnMask = btnMask and 1.inv()
                            hid()?.sendReport(btnMask, 0, 0, 0)
                        } else lmbWantsRelease = true
                    }
                }
                if (diff and MotionEvent.BUTTON_SECONDARY != 0) {
                    btnMask = if (press) btnMask or 0x02 else btnMask and 0x02.inv()
                    hid()?.sendReport(btnMask, 0, 0, 0)
                }
                if (diff and MotionEvent.BUTTON_TERTIARY != 0) {
                    btnMask = if (press) btnMask or 0x04 else btnMask and 0x04.inv()
                    hid()?.sendReport(btnMask, 0, 0, 0)
                }
            }
        }
        val vs = ev.getAxisValue(MotionEvent.AXIS_VSCROLL).toInt()
        if (vs != 0) pendWheel += vs
        if (moves % 30 == 0 || ev.actionMasked == MotionEvent.ACTION_BUTTON_PRESS ||
            ev.actionMasked == MotionEvent.ACTION_BUTTON_RELEASE) render()
        return true
    }
}
