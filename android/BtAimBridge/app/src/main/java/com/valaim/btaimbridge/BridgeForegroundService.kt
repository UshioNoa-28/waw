package com.valaim.btaimbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager

class BridgeForegroundService : Service() {
    private var wl: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val ch = NotificationChannel("bridge_live", "Aim bridge", NotificationManager.IMPORTANCE_LOW)
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(ch)
        val n: Notification = Notification.Builder(this, "bridge_live")
            .setContentTitle("瞄准桥接运行中")
            .setContentText("保持手机不被系统冻结")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(7, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(7, n)
        }
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "valaim:bridge").apply {
            setReferenceCounted(false)
            acquire(10 * 60 * 1000L)
        }
    }

    override fun onDestroy() {
        try { wl?.release() } catch (_: Exception) {}
        super.onDestroy()
    }

    companion object {
        fun ensure(context: android.content.Context) {
            val i = Intent(context, BridgeForegroundService::class.java)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
        }
    }
}
