package com.valaim.btaimbridge

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.format.Formatter
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

class MainActivity : AppCompatActivity() {

    companion object {
        const val PREFS = "btaimbridge"
        const val KEY_PORT = "port"
        const val DEFAULT_PORT = 47800
        private const val REQ_BT = 1
        private const val REQ_NOTIF = 2
    }

    private lateinit var prefs: SharedPreferences
    private lateinit var statusView: TextView
    private lateinit var ipView: TextView
    private lateinit var portView: EditText
    private var running = false

    private val statusReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val s = intent.getStringExtra(BridgeService.EXTRA_STATUS) ?: return
            statusView.text = s
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        statusView = findViewById(R.id.status)
        ipView = findViewById(R.id.ip)
        portView = findViewById(R.id.port)

        portView.setText(prefs.getInt(KEY_PORT, DEFAULT_PORT).toString())
        ipView.text = getLocalIp()

        findViewById<Button>(R.id.btnStart).setOnClickListener {
            val port = portView.text.toString().toIntOrNull() ?: DEFAULT_PORT
            prefs.edit().putInt(KEY_PORT, port).apply()
            requestPermissionsThenStart()
        }
        findViewById<Button>(R.id.btnStop).setOnClickListener {
            stopBridge()
        }
        findViewById<Button>(R.id.btnBattery).setOnClickListener {
            openBatterySettings()
        }
        findViewById<Button>(R.id.btnRefreshIp).setOnClickListener {
            ipView.text = getLocalIp()
        }

        requestNotifPermission()
    }

    override fun onResume() {
        super.onResume()
        val filter = IntentFilter(BridgeService.ACTION_STATUS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(statusReceiver, filter)
        }
        ipView.text = getLocalIp()
    }

    override fun onPause() {
        super.onPause()
        try { unregisterReceiver(statusReceiver) } catch (_: Exception) {}
    }

    private fun requestPermissionsThenStart() {
        val needed = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            needed += Manifest.permission.BLUETOOTH_CONNECT
            needed += Manifest.permission.BLUETOOTH_SCAN
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), REQ_BT)
        } else {
            startBridge()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_BT) {
            val btOk = grantResults.all { it == PackageManager.PERMISSION_GRANTED }
            if (btOk) startBridge()
            else {
                Toast.makeText(this, "Bluetooth permission denied", Toast.LENGTH_LONG).show()
                statusView.text = "Permission denied"
            }
        }
    }

    private fun startBridge() {
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null || !adapter.isEnabled) {
            Toast.makeText(this, "Turn Bluetooth on first", Toast.LENGTH_LONG).show()
            return
        }
        val intent = Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent)
        else startService(intent)
        running = true
        statusView.text = "Starting bridge..."
    }

    private fun stopBridge() {
        val intent = Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_STOP)
        startService(intent)
        running = false
        statusView.text = "Stopped"
    }

    private fun requestNotifPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIF
            )
        }
    }

    private fun openBatterySettings() {
        try {
            val intent = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            startActivity(intent)
        } catch (_: Exception) {
            Toast.makeText(this, "Open Settings > Apps > BtAimBridge > Battery > Unrestricted",
                Toast.LENGTH_LONG).show()
        }
    }

    private fun getLocalIp(): String {
        return try {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            @Suppress("DEPRECATION")
            val ip = wm.connectionInfo.ipAddress
            if (ip == 0) "No WiFi" else Formatter.formatIpAddress(ip)
        } catch (_: Exception) {
            "Unknown"
        }
    }
}
