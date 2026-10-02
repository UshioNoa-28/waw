package com.valaim.btaimbridge

/** Shared handles for non-activity components (mouse proxy screen). */
object BridgeGlobals {
    @Volatile var hid: HidMouseController? = null
    @Volatile var server: BridgeServer? = null
    @Volatile var btnMask = 0
    @Volatile var onVendorFrame: ((ByteArray) -> Unit)? = null

    fun clientAlive(): Boolean = server?.isAlive() == true
}
