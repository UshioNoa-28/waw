package com.valaim.btaimbridge

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.graphics.Path
import android.accessibilityservice.GestureDescription

/** Warps the system pointer back to screen center when the BT mouse hits an edge. */
class RecenterService : AccessibilityService() {
    companion object {
        @Volatile var instance: RecenterService? = null
        @Volatile private var lastWarp = 0L

        fun isOn(): Boolean = instance != null

        fun warp(fromX: Float, fromY: Float, cx: Float, cy: Float): Boolean {
            val s = instance ?: return false
            val now = System.currentTimeMillis()
            if (now - lastWarp < 350) return false
            lastWarp = now
            val p = Path()
            p.moveTo(fromX, fromY)
            p.lineTo(cx, cy)
            val g = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(p, 0, 90))
                .build()
            return s.dispatchGesture(g, null, null)
        }
    }

    override fun onServiceConnected() {
        instance = this
        super.onServiceConnected()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }
}
