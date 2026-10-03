package com.valaim.btaimbridge

/**
 * HID report descriptor + serialization for a 5-button relative mouse.
 *
 * Layout (little endian bit order):
 *   byte 0: buttons (bit0 left, bit1 right, bit2 middle, bit3 b4, bit4 b5)
 *   byte 1: dx  (int8, -127..127)
 *   byte 2: dy  (int8, -127..127)
 *   byte 3: wheel (int8)
 */
object MouseReport {

    val DESCRIPTOR: ByteArray = byteArrayOf(
        0x05.toByte(), 0x01.toByte(),       // Usage Page (Generic Desktop)
        0x09.toByte(), 0x02.toByte(),       // Usage (Mouse)
        0xA1.toByte(), 0x01.toByte(),       // Collection (Application)
        0xA1.toByte(), 0x00.toByte(),       //   Collection (Physical)
        0x05.toByte(), 0x09.toByte(),       //     Usage Page (Button)
        0x19.toByte(), 0x01.toByte(),       //     Usage Minimum (1)
        0x29.toByte(), 0x05.toByte(),       //     Usage Maximum (5)
        0x15.toByte(), 0x00.toByte(),       //     Logical Minimum (0)
        0x25.toByte(), 0x01.toByte(),       //     Logical Maximum (1)
        0x95.toByte(), 0x05.toByte(),       //     Report Count (5)
        0x75.toByte(), 0x01.toByte(),       //     Report Size (1)
        0x81.toByte(), 0x02.toByte(),       //     Input (Data,Var,Abs)
        0x95.toByte(), 0x03.toByte(),       //     Report Count (3) padding
        0x75.toByte(), 0x01.toByte(),       //     Report Size (1)
        0x81.toByte(), 0x03.toByte(),       //     Input (Cnst,Var,Abs)
        0x05.toByte(), 0x01.toByte(),       //     Usage Page (Generic Desktop)
        0x09.toByte(), 0x30.toByte(),       //     Usage (X)
        0x09.toByte(), 0x31.toByte(),       //     Usage (Y)
        0x15.toByte(), 0x81.toByte(),       //     Logical Minimum (-127)
        0x25.toByte(), 0x7F.toByte(),       //     Logical Maximum (127)
        0x75.toByte(), 0x08.toByte(),       //     Report Size (8)
        0x95.toByte(), 0x02.toByte(),       //     Report Count (2)
        0x81.toByte(), 0x06.toByte(),       //     Input (Data,Var,Rel)
        0x09.toByte(), 0x38.toByte(),       //     Usage (Wheel)
        0x15.toByte(), 0x81.toByte(),       //     Logical Minimum (-127)
        0x25.toByte(), 0x7F.toByte(),       //     Logical Maximum (127)
        0x75.toByte(), 0x08.toByte(),       //     Report Size (8)
        0x95.toByte(), 0x01.toByte(),       //     Report Count (1)
        0x81.toByte(), 0x06.toByte(),       //     Input (Data,Var,Rel)
        0xC0.toByte(),                      //   End Collection
        0xC0.toByte(),                      // End Collection

    )    )

    const val BTN_LEFT = 0x01
    const val BTN_RIGHT = 0x02
    const val BTN_MIDDLE = 0x04

    /** Report is submitted with reportId 0, so it is exactly 4 bytes. */
    fun build(buttons: Int, dx: Int, dy: Int, wheel: Int): ByteArray {
        val bx = dx.coerceIn(-127, 127).toByte()
        val by = dy.coerceIn(-127, 127).toByte()
        val bw = wheel.coerceIn(-127, 127).toByte()
        return byteArrayOf(
            (buttons and 0x1F).toByte(),
            bx,
            by,
            bw,
        )
    }
}
