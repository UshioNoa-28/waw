package com.valaim.btaimbridge

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothHidDevice
import android.bluetooth.BluetoothHidDeviceAppQosSettings
import android.bluetooth.BluetoothHidDeviceAppSdpSettings
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

/**
 * Registers the phone as a standard Bluetooth HID mouse.
 *
 * Simple and synchronous: call [register] once, check [isReady], then use
 * [sendReport]. No service, no lifecycle tricks - matches how well-behaved
 * HID apps (Dotori / PhonePad) do it.
 */
class HidMouseController(
    private val context: Context,
    private val onState: (String) -> Unit,
) {
    companion object {
        private const val TAG = "HidMouse"
        private val APP_EXECUTOR = Executors.newSingleThreadExecutor { r ->
            Thread(r, "hid-cmd").apply { isDaemon = true }
        }
        private val QOS = BluetoothHidDeviceAppQosSettings(
            BluetoothHidDeviceAppQosSettings.SERVICE_BEST_EFFORT,
            800, 9, 0, 11250, BluetoothHidDeviceAppQosSettings.MAX
        )
    }

    private val adapter: BluetoothAdapter? =
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    private var hidDevice: BluetoothHidDevice? = null
    private var hostDevice: BluetoothDevice? = null

    @Volatile private var registered = false
    @Volatile private var connected = false

    val isReady: Boolean get() = connected

    private val callback = object : BluetoothHidDevice.Callback() {
        override fun onSetReport(device: BluetoothDevice, type: Byte, id: Byte, data: ByteArray) {
            if (id.toInt() == 1) BridgeGlobals.onVendorFrame?.invoke(data)
        }

        @SuppressLint("MissingPermission")
        override fun onConnectionStateChanged(device: BluetoothDevice, state: Int) {
            when (state) {
                BluetoothProfile.STATE_CONNECTED -> {
                    hostDevice = device
                    connected = true
                    lastConnectedAt = System.currentTimeMillis()
                    lastHostMac = device.address
                    onState("CONNECTED to PC - releasing buttons")
                    // The host may remember a stuck button state from a last
                    // session that ended abruptly; clear it immediately.
                    try {
                        hidDevice?.sendReport(device, 0, MouseReport.build(0, 0, 0, 0))
                    } catch (_: SecurityException) {
                    }
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    if (hostDevice == device) hostDevice = null
                    connected = false
                    val dt = System.currentTimeMillis() - lastConnectedAt
                    if (lastConnectedAt > 0 && dt < 8_000) {
                        flapCount++
                        if (flapCount >= 3) {
                            autoConnectPaused = true
                            onState("链路抖动(连上即断 x$flapCount) - 已暂停自动重连,点【重置HID注册】或手机蓝牙里取消配对后重配")
                        } else {
                            onState("PC disconnected (flap $flapCount/3)")
                        }
                    } else {
                        flapCount = 0
                        onState("PC disconnected")
                    }
                }
            }
        }

        override fun onAppStatusChanged(pluggedDevice: BluetoothDevice?, isRegistered: Boolean) {
            registered = isRegistered
            if (isRegistered) {
                onState("HID registered")
                // Registration is not connection: after the app was killed and
                // restarted, the host link must be re-established actively.
                if (!connected && !manualStop) startReconnectLoop()
            } else {
                connected = false
                hostDevice = null
                onState("HID unregistered")
                // The watchdog re-registers within a few seconds.
            }
        }

        // --- control-channel replies: the host needs these during/after
        // --- connection, otherwise it drops the link right after pairing. ---

        @SuppressLint("MissingPermission")
        override fun onGetReport(device: BluetoothDevice, type: Byte, id: Byte, bufferSize: Int) {
            try {
                val payload = if (type == BluetoothHidDevice.REPORT_TYPE_INPUT && id == 0.toByte())
                    MouseReport.build(0, 0, 0, 0) else ByteArray(0)
                hidDevice?.replyReport(device, type, id, payload)
            } catch (_: Exception) {
            }
        }

        override fun onSetProtocol(device: BluetoothDevice, protocol: Byte) {}

        override fun onVirtualCableUnplug(device: BluetoothDevice) {
            if (hostDevice == device) hostDevice = null
            connected = false
            onState("PC unplugged the virtual cable - remove & re-pair if needed")
        }
    }

    @Volatile private var manualStop = false
    @Volatile private var reconnecting = false

    /**
     * Re-establish the host link from scratch: the source of truth is the
     * system bond list (Windows paired as "BtAimBridge"), never app memory.
     */
    @SuppressLint("MissingPermission")
    private fun startReconnectLoop() {
        if (reconnecting) return
        reconnecting = true
        Thread {
            repeat(8) {
                if (connected || manualStop) {
                    reconnecting = false
                    return@Thread
                }
                tryConnectBonded()
                try { Thread.sleep(1500) } catch (_: InterruptedException) { return@Thread }
            }
            reconnecting = false
            if (!connected) {
                onState("Not connected - pair \"BtAimBridge\" in Windows Bluetooth once")
            }
        }.start()
    }

    // Connect candidates: this session's last host, or bonded devices whose
    // name looks like a computer (DESKTOP-, LAPTOP-, etc), or the only bonded
    // device if there is exactly one. Never spray connect() at earbuds etc.
    @SuppressLint("MissingPermission")
    private fun tryConnectBonded() {
        if (autoConnectPaused) return
        val hd = hidDevice ?: return
        val bonded = try {
            adapter?.bondedDevices?.toList()
        } catch (_: SecurityException) {
            null
        } ?: return
        if (!bondedListReported && !connected) {
            bondedListReported = true
            val names = bonded.joinToString(", ") {
                try { it.name ?: it.address } catch (_: SecurityException) { it.address }
            }
            onState("手机已配对: ${if (names.isEmpty()) "无 - 请在 Windows 配对" else names}")
        }
        val candidates = bonded.filter { dev ->
            val mac = dev.address
            if (lastHostMac != null && mac.equals(lastHostMac, true)) return@filter true
            val nm = try { dev.name } catch (_: SecurityException) { null } ?: ""
            PC_NAME.containsMatchIn(nm) || (bonded.size == 1)
        }
        for (dev in candidates) {
            try {
                hd.connect(dev)
            } catch (_: Exception) {
            }
        }
    }

    @Volatile private var bondedListReported = false
    @Volatile private var lastHostMac: String? = null       // this session only
    @Volatile private var autoConnectPaused = false
    @Volatile private var lastConnectedAt = 0L
    @Volatile private var flapCount = 0
    private val PC_NAME = Regex("(?i)(DESKTOP|LAPTOP|THINKPAD|XPS|MATEBOOK|PC[-_]|WIN[-_]|-PC$)")

    /** Hard reset of the app-side registration without touching pairing data. */
    @SuppressLint("MissingPermission")
    fun forceReRegister() {
        bondedListReported = false
        autoConnectPaused = false
        flapCount = 0
        lastConnectedAt = 0
        val hd = hidDevice
        val host = hostDevice
        if (hd != null && host != null) {
            try { hd.disconnect(host) } catch (_: Exception) {}
        }
        try { hd?.unregisterApp() } catch (_: Exception) {}
        registered = false
        connected = false
        hostDevice = null
        manualStop = false
        onState("HID 已重置,重新注册中...")
        Thread {
            try { Thread.sleep(600) } catch (_: InterruptedException) {}
            reregister()
            // After re-register, try the host we had this session (or scan again).
            try { Thread.sleep(1200) } catch (_: InterruptedException) {}
            if (!connected) startReconnectLoop()
        }.start()
    }

    /**
     * Self-healing watchdog. Every few seconds: re-register if the app got
     * unregistered, reconnect if the host link dropped. No user action ever.
     */
    @Volatile private var watchRunning = false

    private fun startWatchdog() {
        if (watchRunning) return
        watchRunning = true
        Thread {
            while (watchRunning) {
                try { Thread.sleep(4000) } catch (_: InterruptedException) { break }
                if (!watchRunning || manualStop) break
                if (!registered) {
                    reregister()
                    continue
                }
                if (!connected) {
                    tryConnectBonded()
                }
            }
        }.start()
    }

    private fun stopWatchdog() {
        watchRunning = false
    }

    @SuppressLint("MissingPermission")
    private fun reregister() {
        onState("Watchdog re-registering HID...")
        val dev = hidDevice ?: run { register(); return }
        val sdp = BluetoothHidDeviceAppSdpSettings(
            "BtAimBridge", "Bluetooth mouse bridge", "ValAim",
            BluetoothHidDevice.SUBCLASS1_MOUSE, MouseReport.DESCRIPTOR,
        )
        try {
            dev.registerApp(sdp, null, QOS, APP_EXECUTOR, callback)
        } catch (_: Exception) {
        }
    }

    private val serviceListener = object : BluetoothProfile.ServiceListener {
        @SuppressLint("MissingPermission")
        override fun onServiceConnected(profile: Int, proxy: BluetoothProfile) {
            if (profile != BluetoothProfile.HID_DEVICE) return
            hidDevice = proxy as BluetoothHidDevice
            val sdp = BluetoothHidDeviceAppSdpSettings(
                "BtAimBridge",
                "Bluetooth mouse bridge",
                "ValAim",
                BluetoothHidDevice.SUBCLASS1_MOUSE,
                MouseReport.DESCRIPTOR,
            )
            try {
                val ok = hidDevice?.registerApp(sdp, null, QOS, APP_EXECUTOR, callback) ?: false
                if (!ok) onState("registerApp failed")
            } catch (e: SecurityException) {
                onState("Permission denied: ${e.message}")
            }
        }

        override fun onServiceDisconnected(profile: Int) {
            if (profile != BluetoothProfile.HID_DEVICE) return
            hidDevice = null
            registered = false
            connected = false
            onState("HID service lost")
        }
    }

    fun hasPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
        return ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.BLUETOOTH_CONNECT
        ) == PackageManager.PERMISSION_GRANTED
    }

    @SuppressLint("MissingPermission")
    fun register() {
        manualStop = false
        startWatchdog()
        val a = adapter ?: run { onState("No Bluetooth"); return }
        if (!a.isEnabled) { onState("Turn Bluetooth ON"); return }
        if (!hasPermission()) { onState("Grant Nearby devices permission"); return }
        onState("Connecting HID profile...")
        val ok = a.getProfileProxy(context, serviceListener, BluetoothProfile.HID_DEVICE)
        if (!ok) onState("getProfileProxy failed")
    }

    @SuppressLint("MissingPermission")
    fun unregister() {
        manualStop = true
        stopWatchdog()
        val dev = hidDevice
        try { dev?.unregisterApp() } catch (_: Exception) {}
        hidDevice = null
        hostDevice = null
        registered = false
        connected = false
        if (dev != null) adapter?.closeProfileProxy(BluetoothProfile.HID_DEVICE, dev)
    }

    @SuppressLint("MissingPermission")
    fun sendVendor(frame: ByteArray): Boolean {
        val dev = hostDevice ?: return false
        val f = if (frame.size >= 8) frame.copyOfRange(0, 8) else frame.copyOf(8)
        return try { hidDevice?.sendReport(dev, 1, f) ?: false } catch (_: SecurityException) { false }
    }

    @SuppressLint("MissingPermission")
    fun sendReport(buttons: Int, dx: Int, dy: Int, wheel: Int): Boolean {
        val dev = hostDevice ?: return false
        val report = MouseReport.build(buttons, dx, dy, wheel)
        return try {
            hidDevice?.sendReport(dev, 0, report) ?: false
        } catch (_: SecurityException) {
            false
        }
    }
}
