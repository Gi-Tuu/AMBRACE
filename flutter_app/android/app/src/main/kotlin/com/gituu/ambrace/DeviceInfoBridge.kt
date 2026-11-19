package com.gituu.ambrace.ai_companion

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Log

/**
 * 去 Shizuku 后的系统信息桥：全部走 Android 公开 API。
 * 电量/网络/勿扰/屏幕/设备信息无系统级权限；前台应用优先 UsageStats（需使用情况访问），
 * 拿不到时由无障碍服务维护的 currentPackage 兜底。
 */
object DeviceInfoBridge {
    private const val TAG = "DeviceInfoBridge"

    fun isAccessibilityEnabled(ctx: Context): Boolean {
        val expected = "${ctx.packageName}/${ctx.packageName}.PhonePerceptionAccessibilityService"
        val enabled = Settings.Secure.getString(
            ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ).orEmpty()
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }

    fun status(ctx: Context): Map<String, Any> {
        val on = isAccessibilityEnabled(ctx)
        return mapOf("serverRunning" to on, "permissionGranted" to on)
    }

    fun systemSnapshot(ctx: Context): Map<String, Any> {
        val out = LinkedHashMap<String, Any>()
        try {
            val pm = ctx.getSystemService(Context.POWER_SERVICE) as? PowerManager
            out["screenOn"] = pm?.isInteractive ?: false
        } catch (_: Exception) {}
        try {
            val bm = ctx.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
            bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)?.let {
                if (it in 0..100) out["batteryLevel"] = it
            }
            val intent = ctx.registerReceiver(null, android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            out["batteryCharging"] =
                status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        } catch (_: Exception) {}
        try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val caps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
            out["network"] = when {
                caps == null -> "NONE"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WIFI"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "CELLULAR"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ETHERNET"
                else -> "OTHER"
            }
        } catch (_: Exception) {}
        try {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
            val filter = nm?.currentInterruptionFilter ?: NotificationManager.INTERRUPTION_FILTER_ALL
            out["dnd"] = filter != NotificationManager.INTERRUPTION_FILTER_ALL
        } catch (_: Exception) {}
        try {
            PhonePerceptionAccessibilityService.currentPackage?.let { out["foregroundApp"] = it }
        } catch (_: Exception) {}
        val device = listOf(Build.MANUFACTURER, Build.MODEL).filter { it.isNotBlank() }.joinToString(" ")
        if (device.isNotBlank()) out["device"] = device
        out["androidVersion"] = Build.VERSION.RELEASE
        Log.i(TAG, "snapshot fields=${out.keys}")
        return mapOf("ok" to true, "data" to out, "steps_ok" to out.size, "steps_total" to out.size, "error" to "")
    }

    fun listAppsDetailed(ctx: Context): Map<String, Any> {
        return try {
            val pm = ctx.packageManager
            val apps = pm.getInstalledApplications(android.content.pm.PackageManager.ApplicationInfoFlags.of(0L))
            if (apps.isEmpty()) {
                mapOf("ok" to false, "apps" to emptyList<Map<String, String>>(), "error" to "应用列表为空（请确认 QUERY_ALL_PACKAGES 已生效）")
            } else {
                val list = apps.map {
                    mapOf(
                        "package" to it.packageName,
                        "label" to (pm.getApplicationLabel(it)?.toString() ?: it.packageName),
                    )
                }.sortedBy { (it["label"] as String).lowercase() }
                mapOf("ok" to true, "apps" to list)
            }
        } catch (e: Exception) {
            Log.w(TAG, "listAppsDetailed failed: ${e.message}")
            mapOf("ok" to false, "apps" to emptyList<Map<String, String>>(), "error" to (e.message ?: "无法读取应用列表"))
        }
    }

    fun listPackages(ctx: Context): Map<String, Any> {
        return try {
            val pm = ctx.packageManager
            val pkgs = pm.getInstalledApplications(android.content.pm.PackageManager.ApplicationInfoFlags.of(0L))
                .filter { it.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM == 0 }
                .map { it.packageName }.sorted()
            mapOf("ok" to true, "packages" to pkgs, "error" to "")
        } catch (e: Exception) {
            mapOf("ok" to false, "packages" to emptyList<String>(), "error" to (e.message ?: "无法读取应用列表"))
        }
    }
}
