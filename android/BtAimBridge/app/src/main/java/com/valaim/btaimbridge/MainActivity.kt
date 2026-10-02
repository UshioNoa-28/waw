package com.valaim.btaimbridge

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Process
import java.io.File
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.format.Formatter
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

@SuppressLint("SetTextI18n")
class MainActivity : AppCompatActivity() {

    companion object {
        const val PREFS = "btaimbridge"
        const val KEY_PORT = "port"
        const val DEFAULT_PORT = 47800
        private const val REQ_PERMS = 1
    }

    private lateinit var prefs: SharedPreferences
    private lateinit var hid: HidMouseController
    private var server: BridgeServer? = null
    private val main = Handler(Looper.getMainLooper())
    private var running = false

    private lateinit var tvIp: TextView
    private lateinit var tvStatus: TextView
    private lateinit var etPort: EditText
    private lateinit var btnToggle: Button

    override fun onResume() {
        super.onResume()
        CrashLog.log("MainActivity resume, tail: ${CrashLog.tail(3)}")
    }

        override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CrashLog.init(this)
        CrashLog.log("MainActivity created")
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        tvIp = findViewById(R.id.tvIp)
        tvStatus = findViewById(R.id.tvStatus)
        etPort = findViewById(R.id.etPort)
        btnToggle = findViewById(R.id.btnToggle)

        etPort.setText(prefs.getInt(KEY_PORT, DEFAULT_PORT).toString())
        tvIp.text = "PC IP: ${localIp()}"

        hid = HidMouseController(this) { msg -> setStatus(msg) }
        BridgeGlobals.hid = hid

        btnToggle.setOnClickListener {
            if (running) stopAll() else requestThenStart()
        }
        findViewById<Button>(R.id.btnDiscover).setOnClickListener { makeDiscoverable() }
        findViewById<Button>(R.id.btBtSettings).setOnClickListener {
            try {
                startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
            } catch (_: Exception) {
                Toast.makeText(this, "无法打开蓝牙设置", Toast.LENGTH_SHORT).show()
            }
        }
        findViewById<Button>(R.id.btnMouseProxy).setOnClickListener {
            try { startActivity(android.content.Intent(this, MouseUsbProxyActivity::class.java)) }
            catch (e: Exception) { setStatus("打开代理页失败: ${e.javaClass.simpleName} ${e.message}") }
        }
        findViewById<Button>(R.id.btnMouseTest).setOnClickListener {
            startActivity(android.content.Intent(this, MouseTestActivity::class.java))
        }
        findViewById<Button>(R.id.btnLock).setOnClickListener {
            server?.sendLockToggle()
            Toast.makeText(this, "已发送锁定切换", Toast.LENGTH_SHORT).show()
        }
        findViewById<Button>(R.id.btnResetHid).setOnClickListener {
            hid.forceReRegister()
            setStatus("Resetting HID registration...")
        }
        findViewById<Button>(R.id.btnRefresh).setOnClickListener {
            tvIp.text = "PC IP: ${localIp()}"
        }

        setupTouchpad()

        // Auto-start once permissions are granted: HID registration must be
        // live BEFORE pairing, and nobody remembers to press START first.
        if (hasAllPerms()) {
            main.postDelayed({ if (!running) startAll() }, 300)
        }
    }

    private fun hasAllPerms(): Boolean {
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            need += Manifest.permission.BLUETOOTH_CONNECT
            need += Manifest.permission.BLUETOOTH_SCAN
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        return need.all { checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }
    }

    private fun setupTouchpad() {
        val pad = findViewById<android.view.View>(R.id.touchpad)
        var lastX = 0f
        var lastY = 0f
        pad.setOnTouchListener { _, ev ->
            when (ev.actionMasked) {
                android.view.MotionEvent.ACTION_DOWN -> {
                    lastX = ev.x
                    lastY = ev.y
                }
                android.view.MotionEvent.ACTION_MOVE -> {
                    val dx = (ev.x - lastX).toInt()
                    val dy = (ev.y - lastY).toInt()
                    lastX = ev.x
                    lastY = ev.y
                    if (dx != 0 || dy != 0) {
                        val ok = hid.sendReport(0, dx, dy, 0)
                        if (!ok) setStatus("HID not connected - tap Reconnect")
                    }
                }
            }
            true
        }
    }

    private fun setStatus(s: String) = main.post { tvStatus.text = s }

    private fun requestThenStart() {
        if (BridgeGlobals.server != null && running) {
            // lifecycle re-create (rotation/process warmup) - keep the existing server,
            // re-binding a second one is what caused the mid-session bind failures
            return
        }
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            need += Manifest.permission.BLUETOOTH_CONNECT
            need += Manifest.permission.BLUETOOTH_SCAN
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        val missing = need.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQ_PERMS)
        } else {
            startAll()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_PERMS) {
            if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                startAll()
            } else {
                setStatus("Permission denied - needed for Bluetooth")
            }
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

        // Start the keep-alive foreground service first: it puts the app in the
        // foreground state so the HID registration below survives the game
        // taking focus.
        startKeepAlive()

        setStatus("Registering HID...")
        hid.register()

        server = BridgeServer(
            portProvider = { prefs.getInt(KEY_PORT, DEFAULT_PORT) },
            hid = hid,
            onLog = { msg -> setStatus(msg) },
            onClient = { ip -> setStatus("PC connected from $ip") },
        ).also { it.start() }
        BridgeGlobals.server = server

        running = true
        btnToggle.text = "STOP"
        setStatus("Started. Now pair \"BtAimBridge\" on the PC.")
    }

    private fun startKeepAlive() {
        val intent = Intent(this, KeepAliveService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent)
        else startService(intent)
    }

    private fun stopKeepAlive() {
        stopService(Intent(this, KeepAliveService::class.java))
    }

    private fun stopAll() {
        server?.stop()
        server = null
        hid.unregister()
        running = false
        stopKeepAlive()
        btnToggle.text = "START"
        setStatus("Stopped")
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
        val intent = Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).apply {
            putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, 300)
        }
        try {
            startActivity(intent)
        } catch (_: Exception) {
            Toast.makeText(this, "Could not open discoverable dialog", Toast.LENGTH_SHORT).show()
        }
        setStatus("Discoverable for 5 minutes - pair from the PC now")
    }

    private fun localIp(): String = try {
        val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION")
        val ip = wm.connectionInfo.ipAddress
        if (ip == 0) "no wifi" else Formatter.formatIpAddress(ip)
    } catch (_: Exception) {
        "unknown"
    }

    override fun onDestroy() {
        stopAll()
        super.onDestroy()
    }
}
