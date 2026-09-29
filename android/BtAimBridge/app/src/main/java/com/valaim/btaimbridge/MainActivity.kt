package com.valaim.btaimbridge

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.text.format.Formatter
import android.view.MotionEvent
import android.view.View
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
        private const val REQ_PERMS = 1
    }

    private lateinit var prefs: SharedPreferences
    private var service: BridgeService? = null
    private var bound = false

    private lateinit var tvIp: TextView
    private lateinit var tvStatus: TextView
    private lateinit var etPort: EditText
    private lateinit var btnToggle: Button

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val b = binder as? BridgeService.LocalBinder ?: return
            service = b.service()
            bound = true
            service?.setStatusCallback { s -> runOnUiThread { tvStatus.text = s } }
            if (service?.hid != null) tvStatus.text = "Service running"
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            bound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        tvIp = findViewById(R.id.tvIp)
        tvStatus = findViewById(R.id.tvStatus)
        etPort = findViewById(R.id.etPort)
        btnToggle = findViewById(R.id.btnToggle)

        etPort.setText(prefs.getInt(KEY_PORT, DEFAULT_PORT).toString())
        tvIp.text = "PC IP: ${localIp()}"

        btnToggle.setOnClickListener {
            if (service?.hid != null) stopAll() else requestThenStart()
        }
        findViewById<Button>(R.id.btnDiscover).setOnClickListener { makeDiscoverable() }
        findViewById<Button>(R.id.btnRefresh).setOnClickListener {
            tvIp.text = "PC IP: ${localIp()}"
        }

        setupTouchpad()
        bindService(Intent(this, BridgeService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    private fun setupTouchpad() {
        val pad = findViewById<View>(R.id.touchpad)
        var lastX = 0f
        var lastY = 0f
        pad.setOnTouchListener { _, ev ->
            when (ev.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    lastX = ev.x
                    lastY = ev.y
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (ev.x - lastX).toInt()
                    val dy = (ev.y - lastY).toInt()
                    lastX = ev.x
                    lastY = ev.y
                    if (dx != 0 || dy != 0) {
                        val ok = service?.sendMove(dx, dy) ?: false
                        if (!ok) tvStatus.text = "touchpad: HID not connected"
                    }
                }
            }
            true
        }
    }

    private fun requestThenStart() {
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            need += Manifest.permission.BLUETOOTH_CONNECT
            need += Manifest.permission.BLUETOOTH_SCAN
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        val missing = need.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQ_PERMS)
        } else startAll()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_PERMS) {
            if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                startAll()
            } else tvStatus.text = "Permission denied"
        }
    }

    private fun startAll() {
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null || !adapter.isEnabled) {
            Toast.makeText(this, "Turn Bluetooth ON first", Toast.LENGTH_LONG).show()
            return
        }
        val port = etPort.text.toString().toIntOrNull() ?: DEFAULT_PORT
        prefs.edit().putInt(KEY_PORT, port).apply()

        val intent = Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent)
        else startService(intent)

        btnToggle.text = "STOP"
        tvStatus.text = "Starting service..."
    }

    private fun stopAll() {
        val intent = Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_STOP)
        startService(intent)
        btnToggle.text = "START"
    }

    @SuppressLint("MissingPermission")
    private fun makeDiscoverable() {
        val adapter = BluetoothAdapter.getDefaultAdapter() ?: return
        if (!adapter.isEnabled) {
            Toast.makeText(this, "Turn Bluetooth ON first", Toast.LENGTH_SHORT).show()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Grant Nearby devices permission first", Toast.LENGTH_SHORT).show()
            return
        }
        try {
            startActivity(Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).apply {
                putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, 300)
            })
        } catch (_: Exception) {}
    }

    private fun localIp(): String = try {
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        val ip = wm.connectionInfo.ipAddress
        if (ip == 0) "no wifi" else Formatter.formatIpAddress(ip)
    } catch (_: Exception) "unknown"

    override fun onDestroy() {
        if (bound) {
            service?.setStatusCallback(null)
            unbindService(connection)
            bound = false
        }
        super.onDestroy()
    }
}
