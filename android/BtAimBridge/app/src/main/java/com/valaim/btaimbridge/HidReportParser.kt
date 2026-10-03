package com.valaim.btaimbridge

/** Bit offsets (absolute in wire report, incl. leading report-id byte) of mouse fields. */
class HidLayout(val id: Int) {
    var bits = 0            // accumulated input bits (data portion)
    var xBit = -1
    var yBit = -1
    var wheelBit = -1
    var btnBase = -1        // absolute bit of button usage 1
    var btnCount = 0

    val wireLen: Int
        get() {
            val idB = if (id != 0) 8 else 0
            return (idB + bits + 7) / 8
        }
}

object HidReportParser {

    fun parse(desc: ByteArray): Map<Int, HidLayout> {
        val layouts = HashMap<Int, HidLayout>()
        fun lay(id: Int): HidLayout = layouts.getOrPut(id) { HidLayout(id) }

        var i = 0
        var page = 0
        var size = 0
        var count = 0
        var id = 0
        val usages = ArrayList<Int>()
        var usageMin = 0
        var usageMax = 0
        var hasRange = false
        val stack = ArrayList<IntArray>()
        val SZ = intArrayOf(0, 1, 2, 4)

        while (i < desc.size) {
            val hdr = desc[i].toInt() and 0xFF
            val n = SZ[hdr and 3]
            val typ = (hdr shr 2) and 3
            val tag = hdr shr 4
            if (i + 1 + n > desc.size) break
            var data = 0
            for (k in 0 until n) data = data or ((desc[i + 1 + k].toInt() and 0xFF) shl (8 * k))
            i += 1 + n

            when {
                typ == 1 -> when (tag) {   // global
                    0 -> page = data
                    7 -> size = data
                    8 -> { id = data; lay(id) }
                    9 -> count = data
                    16 -> stack.add(intArrayOf(page, size, count, id))
                    17 -> {
                        val p = if (stack.isNotEmpty()) stack.removeAt(stack.size - 1) else null
                        if (p != null) { page = p[0]; size = p[1]; count = p[2]; id = p[3] }
                    }
                }
                typ == 2 -> when (tag) {   // local
                    0 -> { usages.add(data); hasRange = false }
                    1 -> { usageMin = data; hasRange = true; usages.clear() }
                    2 -> usageMax = data
                }
                typ == 0 && tag == 8 -> {   // INPUT: the only main item we need
                    val bits = if (size == 0 || count == 0) 0 else size * count
                    val l = lay(id)
                    val isVar = (data and 0x02) != 0
                    val absStart = (if (id != 0) 8 else 0) + l.bits
                    if (isVar && bits > 0) {
                        if (page == 0x09) {
                            val first = if (hasRange) usageMin else (usages.firstOrNull() ?: 0)
                            if (first in 1..32) {
                                if (l.btnBase < 0) l.btnBase = absStart + (first - 1)
                                l.btnCount = maxOf(l.btnCount, count)
                            }
                        } else if (page == 1) {
                            var k = 0
                            while (k < count) {
                                val u = if (hasRange) usageMin + k else (usages.getOrNull(k) ?: usages.lastOrNull() ?: 0)
                                when (u) {
                                    0x30 -> l.xBit = absStart + k * size
                                    0x31 -> l.yBit = absStart + k * size
                                    0x38 -> l.wheelBit = absStart + k * size
                                }
                                k++
                            }
                        }
                    }
                    l.bits += bits
                    usages.clear()
                }
                typ == 0 && (tag == 10 || tag == 12) -> usages.clear()
            }
        }
        return layouts
    }

    /** signed bit-field reader over a report wire buffer */
    fun sfield(rep: ByteArray, bit: Int, size: Int): Int {
        if (bit < 0 || size <= 0) return 0
        var v = 0
        for (k in 0 until size) {
            val bi = bit + k
            val b = rep[bi / 8].toInt() and 0xFF
            val s = (b shr (bi % 8)) and 1
            v = v or (s shl k)
        }
        if (size < 32 && (v shr (size - 1)) and 1 == 1) v = v - (1 shl size)
        return v
    }
}
