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
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChanged(device: BluetoothDevice, state: Int) {
            when (state) {
                BluetoothProfile.STATE_CONNECTED -> {
                    hostDevice = device
                    connected = true
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
                    onState("PC disconnected")
                }
            }
        }

        override fun onAppStatusChanged(pluggedDevice: BluetoothDevice?, isRegistered: Boolean) {
            registered = isRegistered
            if (isRegistered) {
                onState("HID registered - now pair \"BtAimBridge\" on the PC")
            } else {
                connected = false
                onState("HID unregistered")
            }
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
        val a = adapter ?: run { onState("No Bluetooth"); return }
        if (!a.isEnabled) { onState("Turn Bluetooth ON"); return }
        if (!hasPermission()) { onState("Grant Nearby devices permission"); return }
        onState("Connecting HID profile...")
        val ok = a.getProfileProxy(context, serviceListener, BluetoothProfile.HID_DEVICE)
        if (!ok) onState("getProfileProxy failed")
    }

    @SuppressLint("MissingPermission")
    fun unregister() {
        val dev = hidDevice
        try { dev?.unregisterApp() } catch (_: Exception) {}
        hidDevice = null
        hostDevice = null
        registered = false
        connected = false
        if (dev != null) adapter?.closeProfileProxy(BluetoothProfile.HID_DEVICE, dev)
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
