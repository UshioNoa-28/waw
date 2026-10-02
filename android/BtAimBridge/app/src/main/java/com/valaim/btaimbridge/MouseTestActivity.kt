package com.valaim.btaimbridge

import android.os.Bundle
import android.view.InputDevice
import android.view.MotionEvent
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

/**
 * Diagnostics: can our own window SEE a Bluetooth/USB mouse attached to the
 * phone? If counters move while this screen is open, the input-proxy
 * architecture is viable (the app can observe user motion + hold LMB).
 */
class MouseTestActivity : AppCompatActivity() {

    private lateinit var out: TextView
    private var moves = 0
    private var btns = 0
    private var totalDx = 0f
    private var totalDy = 0f
    private var lastLmb = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        out = TextView(this).apply {
            textSize = 15f
            setPadding(40, 60, 40, 40)
            text = "等待鼠标事件...\n(把鼠标配对到手机 → 打开本页面 → 晃动并点击)\n\n按返回键退出"
        }
        setContentView(out)
    }

    private fun render() {
        out.text = buildString {
            appendLine("移动事件次数: $moves")
            appendLine("累计位移: ${totalDx.toInt()} , ${totalDy.toInt()}")
            appendLine("按键事件: $btns   左键按住: $lastLmb")
            appendLine()
            appendLine("✅ 有数字在动 = 代理方案可行")
            appendLine("❌ 一直是0 = 系统没把鼠标事件给我们(方案否决)")
        }
    }

    override fun dispatchGenericMotionEvent(ev: MotionEvent): Boolean {
        if (ev.isFromSource(InputDevice.SOURCE_MOUSE)) {
            when (ev.actionMasked) {
                MotionEvent.ACTION_HOVER_MOVE, MotionEvent.ACTION_MOVE -> {
                    moves++
                    totalDx += ev.getAxisValue(MotionEvent.AXIS_X)
                    totalDy += ev.getAxisValue(MotionEvent.AXIS_Y)
                }
                MotionEvent.ACTION_BUTTON_PRESS, MotionEvent.ACTION_BUTTON_RELEASE -> {
                    btns++
                    lastLmb = ev.actionMasked == MotionEvent.ACTION_BUTTON_PRESS &&
                        ev.buttonState and MotionEvent.BUTTON_PRIMARY != 0
                }
            }
            render()
            return true
        }
        return super.dispatchGenericMotionEvent(ev)
    }
}
