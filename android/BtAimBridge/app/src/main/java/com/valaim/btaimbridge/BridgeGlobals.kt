package com.valaim.btaimbridge

/** Shared handles for non-activity components (mouse proxy screen). */
object BridgeGlobals {
    @Volatile var hid: HidMouseController? = null
    @Volatile var server: BridgeServer? = null

    fun clientAlive(): Boolean = server?.isAlive() == true
}
