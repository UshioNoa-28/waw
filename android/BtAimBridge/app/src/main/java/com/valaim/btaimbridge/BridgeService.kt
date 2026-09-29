package com.valaim.btaimbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat

/**
 * Foreground service: owns the HID controller and the TCP bridge server so the
 * Android process is not frozen or killed while the game is in the foreground.
 */
class BridgeService : Service() {

    companion object {
        const val ACTION_START = "com.valaim.btaimbridge.START"
        const val ACTION_STOP = "com.valaim.btaimbridge.STOP"
        const val ACTION_STATUS = "com.valaim.btaimbridge.STATUS"
        const val EXTRA_STATUS = "status"
        const val EXTRA_PORT = "port"
        const val CHANNEL_ID = "btaimbridge"
        const val NOTIF_ID = 1001
    }

    private lateinit var prefs: SharedPreferences
    private var hid: HidMouseController? = null
    private var server: BridgeServer? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopBridge()
                stopSelf()
                return START_NOT_STICKY
            }
            else -> startBridge()
        }
        return START_STICKY
    }

    private fun startBridge() {
        startForeground(NOTIF_ID, buildNotification("Starting..."))

        val port = prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT)
        val hidCtrl = HidMouseController(this) { msg -> publish(msg) }
        hid = hidCtrl
        hidCtrl.register()

        server = BridgeServer(
            portProvider = { prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT) },
            hid = hidCtrl,
            onLog = { msg -> publish(msg) },
            onClient = { ip -> publish("PC connected from $ip (port $port)") },
        ).also { it.start() }

        acquireLocks()
        publish("Bridge running on port $port")
    }

    private fun stopBridge() {
        server?.stop()
        server = null
        hid?.unregister()
        hid = null
        releaseLocks()
        publish("Stopped")
    }

    private fun acquireLocks() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "btaimbridge:cpu").apply {
            setReferenceCounted(false)
            acquire()
        }
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "btaimbridge:wifi").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseLocks() {
        try { wakeLock?.release() } catch (_: Exception) {}
        try { wifiLock?.release() } catch (_: Exception) {}
        wakeLock = null
        wifiLock = null
    }

    private fun publish(status: String) {
        // Update notification text and broadcast to the Activity.
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_ID, buildNotification(status))
        sendBroadcast(Intent(ACTION_STATUS).putExtra(EXTRA_STATUS, status))
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID, "BtAimBridge", NotificationManager.IMPORTANCE_LOW
            )
            ch.description = "Bluetooth HID bridge status"
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(ch)
        }
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("BtAimBridge")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    override fun onDestroy() {
        stopBridge()
        super.onDestroy()
    }
}
