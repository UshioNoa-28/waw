package com.valaim.btaimbridge

import android.Manifest
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
 * Wraps the Android BluetoothHidDevice profile and exposes a tiny API:
 * [register], [sendReport], [isReady].
 *
 * The phone becomes a standard Bluetooth HID mouse to the host. No software
 * is needed on the PC side.
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

    @Volatile
    private var ready = false

    val isReady: Boolean get() = ready

    private val callback = object : BluetoothHidDevice.Callback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChanged(device: BluetoothDevice, state: Int) {
            when (state) {
                BluetoothProfile.STATE_CONNECTED -> {
                    hostDevice = device
                    ready = true
                    onState("Connected to ${device.name ?: device.address}")
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    if (hostDevice == device) {
                        hostDevice = null
                        ready = false
                    }
                    onState("Host disconnected")
                }
            }
        }

        override fun onAppStatusChanged(pluggedDevice: BluetoothDevice?, registered: Boolean) {
            if (registered) {
                onState("HID ready - pair \"BtAimBridge\" in Windows Bluetooth settings")
            } else {
                ready = false
                onState("HID app unregistered")
            }
        }
    }

    private val serviceListener = object : BluetoothProfile.ServiceListener {
        @SuppressLint("MissingPermission")
        override fun onServiceConnected(profile: Int, proxy: BluetoothProfile) {
            if (profile != BluetoothProfile.HID_DEVICE) return
            hidDevice = proxy as BluetoothHidDevice
            Log.i(TAG, "HID service connected")

            val sdp = BluetoothHidDeviceAppSdpSettings(
                "BtAimBridge",
                "Virtual Bluetooth mouse",
                "ValAim",
                BluetoothHidDevice.SUBCLASS1_MOUSE,
                MouseReport.DESCRIPTOR,
            )

            try {
                val ok = hidDevice?.registerApp(sdp, null, QOS, APP_EXECUTOR, callback) ?: false
                if (!ok) onState("HID registerApp returned false")
            } catch (e: SecurityException) {
                onState("Missing Bluetooth permission: ${e.message}")
            }
        }

        override fun onServiceDisconnected(profile: Int) {
            if (profile != BluetoothProfile.HID_DEVICE) return
            hidDevice = null
            ready = false
            onState("HID service disconnected")
        }
    }

    @SuppressLint("MissingPermission")
    fun register() {
        val a = adapter ?: run { onState("No Bluetooth adapter"); return }
        if (!a.isEnabled) {
            onState("Bluetooth is off")
            return
        }
        if (!hasConnectPermission()) {
            onState("Bluetooth permission not granted")
            return
        }
        val ok: Boolean = a.getProfileProxy(
            context, serviceListener, BluetoothProfile.HID_DEVICE
        )
        if (!ok) onState("Could not get HID profile proxy")
    }

    @SuppressLint("MissingPermission")
    fun unregister() {
        val dev = hidDevice
        try {
            dev?.unregisterApp()
        } catch (_: Exception) {
        }
        hidDevice = null
        hostDevice = null
        ready = false
        if (dev != null) {
            adapter?.closeProfileProxy(BluetoothProfile.HID_DEVICE, dev)
        }
    }

    @SuppressLint("MissingPermission")
    fun sendReport(buttons: Int, dx: Int, dy: Int, wheel: Int): Boolean {
        val dev = hostDevice ?: return false
        val report = MouseReport.build(buttons, dx, dy, wheel)
        return try {
            hidDevice?.sendReport(dev, 0, report) ?: false
        } catch (e: SecurityException) {
            false
        }
    }

    fun hasConnectPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
        return ContextCompat.checkSelfPermission(
            context, Manifest.permission.BLUETOOTH_CONNECT
        ) == PackageManager.PERMISSION_GRANTED
    }
}
