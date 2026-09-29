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
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Foreground service: owns the HID controller and the TCP bridge server so the
 * Android process is not frozen or killed while the game is in the foreground.
 */
class BridgeService : Service() {

    companion object {
        private const val TAG = "BtAimBridgeSvc"
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

    private val main = Handler(Looper.getMainLooper())
    private var lastStatus = "Idle"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i(TAG, "onStartCommand action=${intent?.action}")
        try {
            when (intent?.action) {
                ACTION_STOP -> {
                    stopBridge()
                    stopSelf()
                    return START_NOT_STICKY
                }
                else -> startBridge()
            }
        } catch (t: Throwable) {
            // Never let an exception leave the UI stuck on "Starting...".
            Log.e(TAG, "onStartCommand failed", t)
            publish("ERROR: ${t.javaClass.simpleName}: ${t.message}")
        }
        return START_STICKY
    }

    private fun startBridge() {
        publish("Starting...")

        // Android 14+ requires the foreground type declared in the manifest.
        // If startForeground throws, keep going so the UI still reports status.
        try {
            startForeground(NOTIF_ID, buildNotification("Starting..."))
        } catch (t: Throwable) {
            Log.e(TAG, "startForeground failed", t)
            publish("startForeground failed: ${t.message}")
        }

        val port = prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT)

        val hidCtrl = HidMouseController(this) { msg -> publish(msg) }
        hid = hidCtrl
        try {
            hidCtrl.register()
        } catch (t: Throwable) {
            publish("HID register error: ${t.message}")
        }

        server = BridgeServer(
            portProvider = { prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT) },
            hid = hidCtrl,
            onLog = { msg -> publish(msg) },
            onClient = { ip -> publish("PC connected from $ip") },
        ).also {
            try {
                it.start()
            } catch (t: Throwable) {
                publish("Server start error: ${t.message}")
            }
        }

        acquireLocks()
        publish("Bridge listening on TCP $port - waiting for HID host")
    }

    private fun stopBridge() {
        try { server?.stop() } catch (_: Exception) {}
        server = null
        try { hid?.unregister() } catch (_: Exception) {}
        hid = null
        releaseLocks()
        publish("Stopped")
    }

    private fun acquireLocks() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "btaimbridge:cpu").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {}
        try {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "btaimbridge:wifi").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {}
    }

    private fun releaseLocks() {
        try { wakeLock?.release() } catch (_: Exception) {}
        try { wifiLock?.release() } catch (_: Exception) {}
        wakeLock = null
        wifiLock = null
    }

    private fun publish(status: String) {
        Log.i(TAG, "status: $status")
        lastStatus = status
        main.post {
            try {
                val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIF_ID, buildNotification(status))
            } catch (_: Exception) {}
            try {
                sendBroadcast(Intent(ACTION_STATUS).putExtra(EXTRA_STATUS, status))
            } catch (_: Exception) {}
        }
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
