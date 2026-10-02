package com.valaim.btaimbridge

import android.content.Context
import java.io.File

/** Poor-man flight recorder: survives silent OEM restarts; readable via adb. */
object CrashLog {
    private var file: File? = null

    fun init(ctx: Context) {
        if (file != null) return
        file = File(ctx.filesDir, "crash.txt")
        Thread.setDefaultUncaughtExceptionHandler { _, e ->
            log("UNCAUGHT: ${e.javaClass.name}: ${e.message}\n" +
                e.stackTrace.take(12).joinToString("\n"))
            try { Thread.sleep(200) } catch (_: Exception) {}
            android.os.Process.killProcess(android.os.Process.myPid())
        }
    }

    fun tailAll(): String {
        return try { file?.takeIf { it.exists() }?.readText()?.takeLast(4000) ?: "(无日志)" } catch (e: Exception) { "err ${e.message}" }
    }

    fun tail(n: Int = 6): String {
        return try {
            val f = file ?: return "-"
            if (!f.exists()) return "(空)"
            f.readLines().takeLast(n).joinToString(" | ")
        } catch (e: Exception) { "read fail ${e.message}" }
    }

    fun log(msg: String) {
        try {
            file?.appendText("${System.currentTimeMillis()} $msg\n")
        } catch (_: Exception) {
        }
    }
}
