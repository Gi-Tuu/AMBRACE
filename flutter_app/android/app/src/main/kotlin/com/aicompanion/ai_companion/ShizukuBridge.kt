package com.aicompanion.ai_companion

import android.content.pm.PackageManager
import android.util.Log
import rikka.shizuku.Shizuku
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Shizuku 权限通道（2026-08-12）：ADB/root 启动 Shizuku server 后，本 app 获得系统级能力
 *  （应用列表 / 系统设置 / 模拟操作等普通权限拿不到的 API）。
 *  授权流程：Shizuku app 启动服务 → 本 app requestPermission 弹窗 → 授权后执行 shell。
 *  说明：ADB 通道权限有限（不同系统版本不同），v1 以 `pm list packages` 等只读命令为主。
 */
object ShizukuBridge {
    const val CHANNEL_TAG = "ShizukuBridge"
    const val REQUEST_CODE = 20260812

    private val executor = Executors.newSingleThreadExecutor()

    /** Shizuku 服务是否在运行（需先在 Shizuku app 或 ADB 启动） */
    fun isServerRunning(): Boolean = try {
        Shizuku.pingBinder()
    } catch (_: Exception) {
        false
    }

    /** 本 app 是否已获得 Shizuku 授权 */
    fun isPermissionGranted(): Boolean = try {
        Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
    } catch (_: Exception) {
        false
    }

    /** 弹出 Shizuku 授权请求；返回 true 表示已发起（结果经 Shizuku 回调通知） */
    fun requestPermission(): Boolean = try {
        if (isServerRunning() && !isPermissionGranted()) {
            Shizuku.requestPermission(REQUEST_CODE)
            true
        } else {
            false
        }
    } catch (e: Exception) {
        Log.w(CHANNEL_TAG, "requestPermission failed: ${e.message}")
        false
    }

            /** 在 Shizuku 授权下执行 shell 命令（如 "pm list packages -3"），异步回调结果
     *  前置检查服务/授权并给出可操作提示；连接异常自动重置 binder 重试一次（覆盖安装/服务重启后常见）；
     *  超时与连接异常转成用户可读信息（2026-08-14）。 */
    fun runShell(command: String, timeoutMs: Long = 15000L, callback: (Map<String, Any>) -> Unit) {
        executor.execute {
            var attempts = 0
            while (true) {
                attempts++
                val out = runShellOnce(command, timeoutMs)
                val err = (out["stderr"] as? String).orEmpty()
                if (attempts < 2 && (err.contains("process hasn't exited") || err.contains("DeadObject"))) {
                    // Shizuku binder 连接失效（App 更新/服务重启后常见）：稍等重试一次
                    Thread.sleep(800)
                    continue
                }
                callback(out)
                break
            }
        }
    }

            private fun runShellOnce(command: String, timeoutMs: Long): Map<String, Any> {
        val out = mutableMapOf<String, Any>("ok" to false, "stdout" to "", "stderr" to "")
        try {
            // 前置检查：服务与授权（避免在未就绪时反射调用触发 "process hasn't exited"）
            if (!isServerRunning()) {
                out["stderr"] = "Shizuku 服务未运行：请打开 Shizuku 应用启动服务（或重新用无线调试启动）"
                return out
            }
            if (!isPermissionGranted()) {
                out["stderr"] = "未获得 Shizuku 授权：请在 Shizuku 应用中为本应用开启授权"
                return out
            }
            val args = command.trim().split(Regex("\\s+")).toTypedArray()
            // Shizuku 13.1.5：newProcess 为 @ShizukuService 生成的私有方法（cmd/env/dir），反射调用
            val method = Shizuku::class.java.getDeclaredMethod(
                "newProcess",
                Array<String>::class.java, Array<String>::class.java, String::class.java,
            )
            method.isAccessible = true
            val process = method.invoke(null, args, null, null) as Process
            val sbOut = StringBuilder()
            val sbErr = StringBuilder()
            val tOut = Thread {
                BufferedReader(InputStreamReader(process.inputStream)).forEachLine { sbOut.appendLine(it) }
            }
            val tErr = Thread {
                BufferedReader(InputStreamReader(process.errorStream)).forEachLine { sbErr.appendLine(it) }
            }
            tOut.start()
            tErr.start()
            // 等待进程退出：轮询 exitValue()（Shizuku 的 RemoteProcess 未覆盖 waitFor(timeout)，
            // JDK 默认实现循环调 exitValue()，而 Shizuku binder 抛 IllegalArgumentException 会逃逸，
            // 导致误报 "process hasn't exited" —— 必须手动轮询）
            val deadline = System.currentTimeMillis() + timeoutMs
            var exited = false
            while (System.currentTimeMillis() < deadline) {
                try {
                    process.exitValue()
                    exited = true
                    break
                } catch (_: Exception) {
                    Thread.sleep(100)
                }
            }
            tOut.join(2000)
            tErr.join(2000)
            val stdout = sbOut.toString().trim()
            val stderr = sbErr.toString().trim()
            if (!exited) {
                // 超时未退出：命令卡死/服务异常，销毁进程并提示
                try { process.destroy() } catch (_: Exception) {}
                out["stderr"] = if (stderr.isNotBlank()) {
                    stderr
                } else {
                    "命令执行超时（${timeoutMs}ms），Shizuku 服务可能异常，请重启 Shizuku 后重试"
                }
            } else {
                out["ok"] = true
                out["stdout"] = stdout
                out["stderr"] = stderr
            }
        } catch (e: Exception) {
            val msg = e.message ?: e.toString()
            Log.e(CHANNEL_TAG, "runShell failed cmd=" + command, e)
            // Shizuku 常见连接异常：给用户可操作的提示而非原始报错
            out["stderr"] = when {
                msg.contains("process hasn't exited") || msg.contains("DeadObject") ->
                    "Shizuku 连接异常：请先重启 Shizuku 服务，再回到本页重试"
                msg.contains("Permission") || msg.contains("denied") ->
                    "Shizuku 权限不足：请在 Shizuku 应用中为本应用开启授权"
                else -> msg
            }
        }
        return out
    }

    /** 已安装的第三方应用列表（pm list packages -3 解析） */
    fun getInstalledPackages(callback: (Map<String, Any>) -> Unit) {
        runShell("pm list packages -3", 15000L) { result ->
            val ok = result["ok"] == true
            val out = (result["stdout"] as? String).orEmpty()
            val packages = if (ok) {
                out.lineSequence()
                    .map { it.trim() }
                    .filter { it.startsWith("package:") }
                    .map { it.removePrefix("package:").trim() }
                    .filter { it.isNotBlank() }
                    .sorted()
                    .toList()
            } else {
                emptyList()
            }
            callback(mapOf("ok" to ok, "packages" to packages, "error" to (result["stderr"] ?: "")))
        }
    }

    /** 系统状态快照（只读，手机感知联动）：前台应用/屏幕/电池/网络/勿扰/设备 */
    fun getSystemSnapshot(callback: (Map<String, Any>) -> Unit) {
        val data = LinkedHashMap<String, String>()
        val steps = listOf(
            "activity" to "dumpsys activity activities",
            "power" to "dumpsys power",
            "battery" to "dumpsys battery",
            "connectivity" to "dumpsys connectivity",
            "zen" to "settings get global zen_mode",
            "manufacturer" to "getprop ro.product.manufacturer",
            "model" to "getprop ro.product.model",
            "android" to "getprop ro.build.version.release",
        )

        fun next(i: Int) {
            if (i >= steps.size) {
                callback(mapOf("ok" to true, "data" to parseSystemSnapshot(data)))
                return
            }
            val (key, cmd) = steps[i]
            runShell(cmd, 8000L) { r ->
                // 超时/非零退出也保留 stdout（dumpsys 输出大，读线程可能未完成）
                val out = (r["stdout"] as? String).orEmpty().trim()
                if (out.isNotEmpty()) data[key] = out
                next(i + 1)
            }
        }
        next(0)
    }

    private fun parseSystemSnapshot(data: Map<String, String>): Map<String, Any> {
        val out = LinkedHashMap<String, Any>()
        // 前台应用：dumpsys activity activities 的 topResumedActivity
        val act = data["activity"] ?: ""
        val focus = Regex("topResumedActivity=ActivityRecord\\{[^}]*?\\s+([^\\s/]+)/").find(act)
        if (focus != null) out["foregroundApp"] = focus.groupValues[1]
        // 屏幕：mWakefulness=Awake|Asleep
        val wake = Regex("mWakefulness=([A-Za-z]+)").find(data["power"] ?: "")?.groupValues?.get(1)
        if (wake != null) out["screenOn"] = wake == "Awake" || wake == "On"
        // 亮屏时长：mScreenOnTime=xxx (ms)
        val onTime = Regex("mScreenOnTime=([0-9]+)").find(data["power"] ?: "")?.groupValues?.get(1)?.toLongOrNull()
        if (onTime != null) out["screenOnMs"] = onTime
        // 电池：level / status（2=充电中 5=充满）
        val bat = data["battery"] ?: ""
        val level = Regex("(?m)^\\s*level: (\\d+)").find(bat)?.groupValues?.get(1)?.toIntOrNull()
        if (level != null) out["batteryLevel"] = level
        val status = Regex("(?m)^\\s*status: (\\d+)").find(bat)?.groupValues?.get(1)
        if (status != null) out["batteryCharging"] = status == "2" || status == "5"
        // 网络：Transports: CELLULAR/WIFI
        val net = Regex("Transports: ([A-Z_]+)").find(data["connectivity"] ?: "")
        if (net != null) out["network"] = net.groupValues[1].uppercase()
        // 勿扰：settings get global zen_mode
        val zen = data["zen"]?.trim()
        if (zen != null) out["dnd"] = zen.isNotEmpty() && zen != "0"
        // 设备
        val mfr = (data["manufacturer"] ?: "").trim()
        val model = (data["model"] ?: "").trim()
        val device = listOf(mfr, model).filter { it.isNotBlank() && it != "unknown" }.joinToString(" ")
        if (device.isNotBlank()) out["device"] = device
        val ver = (data["android"] ?: "").trim()
        if (ver.isNotBlank()) out["androidVersion"] = ver
        return out
    }
}
