package com.valaim.btaimbridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Foreground service that owns the HID registration and the TCP bridge.
 *
 * Android unregisters a BluetoothHidDevice app as soon as it leaves the
 * foreground, so the registration MUST live in a foreground service for the
 * link to survive the game taking focus. This is the same structure Dotori
 * and PhonePad use.
 */
class BridgeService : Service() {

    companion object {
        private const val TAG = "BtAimBridgeSvc"
        const val ACTION_START = "com.valaim.btaimbridge.START"
        const val ACTION_STOP = "com.valaim.btaimbridge.STOP"
        const val CHANNEL_ID = "btaimbridge"
        const val NOTIF_ID = 1001
    }

    inner class LocalBinder : Binder() {
        fun service(): BridgeService = this@BridgeService
    }

    private val binder = LocalBinder()
    private lateinit var prefs: SharedPreferences

    var hid: HidMouseController? = null
        private set
    private var server: BridgeServer? = null
    private var statusCallback: ((String) -> Unit)? = null

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        prefs = getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i(TAG, "onStartCommand ${intent?.action}")
        if (intent?.action == ACTION_STOP) {
            stopEverything()
            stopSelf()
            return START_NOT_STICKY
        }
        // Must call startForeground quickly or Android kills us.
        try {
            startForeground(NOTIF_ID, buildNotification("Starting..."))
        } catch (t: Throwable) {
            Log.e(TAG, "startForeground failed", t)
        }
        if (hid == null) startHid()
        return START_STICKY
    }

    fun setStatusCallback(cb: ((String) -> Unit)?) {
        statusCallback = cb
    }

    private fun emit(msg: String) {
        Log.i(TAG, "status: $msg")
        notify(msg)
        statusCallback?.invoke(msg)
    }

    private fun startHid() {
        val port = prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT)
        val h = HidMouseController(this) { msg -> emit(msg) }
        hid = h
        h.register()

        server = BridgeServer(
            portProvider = { prefs.getInt(MainActivity.KEY_PORT, MainActivity.DEFAULT_PORT) },
            hid = h,
            onLog = { msg -> emit(msg) },
            onClient = { ip -> emit("PC connected from $ip") },
        ).also { it.start() }

        emit("Service started. HID registering...")
    }

    /** Called by the Activity's touchpad so it works even without any PC. */
    fun sendMove(dx: Int, dy: Int): Boolean {
        return hid?.sendReport(0, dx, dy, 0) ?: false
    }

    fun isHidConnected(): Boolean = hid?.isReady ?: false

    private fun stopEverything() {
        try { server?.stop() } catch (_: Exception) {}
        server = null
        try { hid?.unregister() } catch (_: Exception) {}
        hid = null
        emit("Stopped")
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID, "BtAimBridge", NotificationManager.IMPORTANCE_LOW
            )
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

    private fun notify(text: String) {
        try {
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .notify(NOTIF_ID, buildNotification(text))
        } catch (_: Exception) {}
    }

    override fun onDestroy() {
        stopEverything()
        super.onDestroy()
    }
}
