package com.gituu.ambrace.ai_companion

import android.Manifest
import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Process
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.PowerManager
import android.util.Log
import android.provider.MediaStore
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityManager
import androidx.core.app.NotificationManagerCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import org.json.JSONArray
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 手机感知 MethodChannel：读屏文本 / 相册最近列表 / 权限与设置引导（AI 走出沙箱 Phase 1） */
class MainActivity : FlutterActivity() {
    companion object {
        const val CHANNEL = "com.gituu.ambrace/phone_perception"
        private const val REQ_MEDIA = 6601
        private const val REQ_MEDIA_FILES = 6602
        private var pendingMediaResult: MethodChannel.Result? = null
        private var pendingMediaFilesResult: MethodChannel.Result? = null
    }

    private var channel: MethodChannel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 通知监听自恢复：app 进程重启后系统可能不自动重绑监听服务，主动请求重绑
        try {
            if (NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)) {
                // 监听自恢复：系统省电/强杀后可能不再自动重绑监听服务，主动请求重绑（公开 API 24+）
                NotificationListenerService.requestRebind(
                    ComponentName(this, PhonePerceptionNotificationService::class.java)
                )
            }
        } catch (_: Exception) {}
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        channel?.setMethodCallHandler { call, result ->
            when (call.method) {
                "getScreenText" -> result.success(
                    mapOf(
                        "text" to PhonePerceptionAccessibilityService.lastScreenText,
                        "capturedAt" to PhonePerceptionAccessibilityService.lastCapturedAt,
                        "serviceEnabled" to isAccessibilityEnabled()
                    )
                )
                "getNodeTreeRaw" -> {
                    val svc = PhonePerceptionAccessibilityService.instance
                    result.success(svc?.captureNodeTreeJson() ?: "")
                }
                "getNodeTree" -> {
                    val svc = PhonePerceptionAccessibilityService.instance
                    // 实时采集当前屏幕（不依赖事件缓存，避免点选器拿到空/过期节点）
                    val liveJson = svc?.captureNodeTreeJson() ?: ""
                    result.success(
                        mapOf(
                            "text" to PhonePerceptionAccessibilityService.lastScreenText,
                            "nodes" to decodeNodeTree(liveJson),
                            "externalNodes" to decodeNodeTree(
                                PhonePerceptionAccessibilityService.lastExternalNodeTreeJson
                            ),
                            "externalPackage" to PhonePerceptionAccessibilityService.lastExternalPackage,
                            "capturedAt" to System.currentTimeMillis(),
                            "serviceEnabled" to (svc != null),
                            // 系统设置里是否已启用读屏服务（区分「未开启」与「已开启但未连接」）
                            "systemEnabled" to isAccessibilityEnabled()
                        )
                    )
                }
                "performAction" -> {
                    val action = call.argument<String>("action") ?: "click"
                    val target = call.argument<String>("target") ?: ""
                    val svc = PhonePerceptionAccessibilityService.instance
                    if (svc == null) {
                        result.success(mapOf("ok" to false, "message" to "无障碍服务未连接，请先开启“读屏（无障碍）”"))
                    } else {
                        val r = svc.performNodeAction(action, target)
                        result.success(mapOf("ok" to r.ok, "message" to r.message))
                    }
                }
                "performActionByPath" -> {
                    val action = call.argument<String>("action") ?: "click"
                    val path = call.argument<String>("path") ?: ""
                    val text = call.argument<String>("text") ?: ""
                    val svc = PhonePerceptionAccessibilityService.instance
                    if (svc == null) {
                        result.success(mapOf("ok" to false, "message" to "无障碍服务未连接，请先开启“读屏（无障碍）”"))
                    } else {
                        val r = svc.performNodeActionByPath(action, path, text)
                        result.success(mapOf("ok" to r.ok, "message" to r.message))
                    }
                }
                "openApp" -> {
                    val pkg = call.argument<String>("package") ?: ""
                    val svc = PhonePerceptionAccessibilityService.instance
                    if (svc == null) result.success(mapOf("ok" to false, "message" to "无障碍服务未连接"))
                    else { val r = svc.openApp(pkg); result.success(mapOf("ok" to r.ok, "message" to r.message)) }
                }
                "setText" -> {
                    val text = call.argument<String>("text") ?: ""
                    val svc = PhonePerceptionAccessibilityService.instance
                    if (svc == null) {
                        result.success(mapOf("ok" to false, "message" to "无障碍服务未连接，请先开启“读屏（无障碍）”"))
                    } else {
                        val r = svc.performSetText(text)
                        result.success(mapOf("ok" to r.ok, "message" to r.message))
                    }
                }
                "getRecentPhotos" -> {
                    val list = queryRecentPhotos(call.argument("limit") ?: 10)
                    result.success(list)
                }
                "getRecentMediaFiles" -> {
                    val type = call.argument<String>("type") ?: "video"
                    val limit = call.argument<Int>("limit") ?: 8
                    result.success(queryRecentMediaFiles(type, limit))
                }
                "openAppSettings" -> {
                    runCatching {
                        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                    }
                    result.success(true)
                }
                "requestMediaPermission" -> requestMediaPermission(result)
                "requestMediaFilesPermission" -> requestMediaFilesPermission(result)
                "openAllFilesAccessSettings" -> {
                    runCatching {
                        startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName")))
                    }
                    result.success(true)
                }
                "getNotifications" -> result.success(PhonePerceptionNotificationService.lastNotifications)
                "isNotificationAccessEnabled" -> result.success(isNotificationAccessEnabled())
                "openNotificationSettings" -> {
                    result.success(openNotificationListenerSettings())
                }
                "getUsageStatsEnabled" -> result.success(isUsageAccessEnabled())
                "openUsageAccessSettings" -> {
                    runCatching { startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)) }
                    result.success(true)
                }
                "getUsageStats" -> {
                    val top = call.argument<Int>("top") ?: 8
                    result.success(queryUsageStats(top))
                }
                "shizukuStatus" -> result.success(DeviceInfoBridge.status(this))
                "shizukuRequestPermission" -> {
                    try {
                        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    } catch (_: Exception) {}
                    result.success(true)
                }
                "shizukuRunShell" -> result.success(
                    mapOf(
                        "ok" to false, "stdout" to "", "code" to "unsupported", "retriable" to false,
                        "stderr" to "Shizuku 已移除：请使用无障碍动作（AmBraceClick / 节点动作）",
                    )
                )
                "accessibilityTap" -> {
                    val x = (call.argument<Double>("x") ?: 0.0).toFloat()
                    val y = (call.argument<Double>("y") ?: 0.0).toFloat()
                    val svc = PhonePerceptionAccessibilityService.instance
                    result.success(mapOf("ok" to (svc?.tapAt(x, y) ?: false)))
                }
                "accessibilitySwipe" -> {
                    val svc = PhonePerceptionAccessibilityService.instance
                    val ms = (call.argument<Int>("ms") ?: 300).toLong()
                    val direction = (call.argument<String>("direction") ?: "").lowercase()
                    var x1 = (call.argument<Double>("x1") ?: 0.0).toFloat()
                    var y1 = (call.argument<Double>("y1") ?: 0.0).toFloat()
                    var x2 = (call.argument<Double>("x2") ?: 0.0).toFloat()
                    var y2 = (call.argument<Double>("y2") ?: 0.0).toFloat()
                    if (direction.isNotEmpty()) {
                        val point = android.graphics.Point()
                        try {
                            val dmgr = getSystemService(android.hardware.display.DisplayManager::class.java)
                            val disp = dmgr?.getDisplay(android.view.Display.DEFAULT_DISPLAY)
                            @Suppress("DEPRECATION")
                            disp?.getRealSize(point)
                        } catch (_: Exception) {}
                        val w = if (point.x > 0) point.x.toFloat() else resources.displayMetrics.widthPixels.toFloat()
                        val h = if (point.y > 0) point.y.toFloat() else resources.displayMetrics.heightPixels.toFloat()
                        when (direction) {
                            "up" -> { x1 = w / 2; y1 = h * 0.75f; x2 = w / 2; y2 = h * 0.25f }
                            "down" -> { x1 = w / 2; y1 = h * 0.25f; x2 = w / 2; y2 = h * 0.75f }
                            "left" -> { x1 = w * 0.75f; y1 = h / 2; x2 = w * 0.25f; y2 = h / 2 }
                            "right" -> { x1 = w * 0.25f; y1 = h / 2; x2 = w * 0.75f; y2 = h / 2 }
                        }
                    }
                    if (svc == null) {
                        result.success(mapOf("ok" to false))
                    } else {
                        val replied = java.util.concurrent.atomic.AtomicBoolean(false)
                        svc.swipeTo(x1, y1, x2, y2, ms) { ok ->
                            if (replied.compareAndSet(false, true)) {
                                runOnUiThread { result.success(mapOf("ok" to ok)) }
                            }
                        }
                    }
                }
                "accessibilityBack" -> result.success(mapOf("ok" to (PhonePerceptionAccessibilityService.instance?.backPress() ?: false)))
                "accessibilityHome" -> result.success(mapOf("ok" to (PhonePerceptionAccessibilityService.instance?.homePress() ?: false)))
                "shizukuGetAppList" -> result.success(DeviceInfoBridge.listPackages(this))
                "shizukuSystemSnapshot" -> result.success(DeviceInfoBridge.systemSnapshot(this))
                "openAccessibilitySettings" -> {
                    // 跳系统无障碍设置（App 更新后读屏服务可能被系统重置，需重新开启）
                    try {
                        startActivity(
                            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                        result.success(true)
                    } catch (e: Exception) {
                        result.success(false)
                    }
                }
                "getAppListDetailed" -> {
                    Thread {
                        runOnUiThread { result.success(DeviceInfoBridge.listAppsDetailed(this)) }
                    }.start()
                }
                "getScreenSize" -> {
                    // 屏幕物理像素（工作流坐标点击范围提示用）
                    try {
                        val dm = resources.displayMetrics
                        result.success(mapOf("width" to dm.widthPixels, "height" to dm.heightPixels))
                    } catch (e: Exception) {
                        result.success(mapOf("width" to 0, "height" to 0, "error" to (e.message ?: "")))
                    }
                }
                // R5：统一健康检测
                "getServiceHealth" -> {
                    result.success(getServiceHealth())
                }
                // R4：电池优化白名单
                "isIgnoringBatteryOptimizations" -> {
                    result.success(isIgnoringBatteryOptimizations())
                }
                "requestIgnoreBatteryOptimizations" -> {
                    result.success(requestIgnoreBatteryOptimizations())
                }
                // R11：导出感知日志
                "exportPerceptionLog" -> {
                    result.success(exportPerceptionLog())
                }
                else -> result.notImplemented()
            }
        }
    }

    private fun isUsageAccessEnabled(): Boolean {
        // 检查系统「使用情况访问」授权（特殊权限，需用户在系统设置中手动开启）
        val appOpsAllowed = try {
            val appOps = getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            val mode = if (Build.VERSION.SDK_INT >= 29) {
                appOps.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), packageName)
            } else {
                @Suppress("DEPRECATION")
                appOps.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), packageName)
            }
            mode == AppOpsManager.MODE_ALLOWED
        } catch (_: Exception) {
            false
        }
        if (appOpsAllowed) return true
        // 兜底：部分 ROM（vivo/OPPO/小米等）appops 状态同步有延迟，授权后仍能查到前台时长数据即视为已授权
        return try {
            val usm = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val end = System.currentTimeMillis()
            val start = end - 24 * 60 * 60 * 1000L
            val stats = usm.queryUsageStats(UsageStatsManager.INTERVAL_BEST, start, end)
            stats?.any { it.totalTimeInForeground > 0 } == true
        } catch (_: Exception) {
            false
        }
    }

    /** 查询最近 24 小时各应用前台使用时长（按时长降序 Top N） */
    private fun queryUsageStats(top: Int): List<Map<String, Any>> {
        val usm = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val end = System.currentTimeMillis()
        val start = end - 24 * 60 * 60 * 1000L
        val stats = try {
            usm.queryUsageStats(UsageStatsManager.INTERVAL_BEST, start, end)
        } catch (_: Exception) {
            emptyList()
        } ?: emptyList()
        // 按包名聚合（INTERVAL_BEST 可能跨多个时间桶返回重复包）
        val agg = HashMap<String, Long>()
        for (s in stats) {
            if (s.totalTimeInForeground <= 0 || s.packageName == packageName) continue
            agg[s.packageName] = (agg[s.packageName] ?: 0L) + s.totalTimeInForeground
        }
        val pm = packageManager
        return agg.entries
            .sortedByDescending { it.value }
            .take(top)
            .map { (pkg, totalMs) ->
                val appName = try {
                    val ai = pm.getApplicationInfo(pkg, 0)
                    pm.getApplicationLabel(ai).toString()
                } catch (_: Exception) {
                    pkg
                }
                mapOf(
                    "package" to pkg,
                    "app_name" to appName,
                    "total_ms" to totalMs,
                )
            }
    }

    private fun openNotificationListenerSettings(): Boolean {
        // 标准 action 优先；部分 ROM（vivo/iQOO）解析失败时回退到直接打开组件
        return try {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            true
        } catch (_: Exception) {
            try {
                val comp = ComponentName(
                    "com.android.settings",
                    "com.android.settings.Settings\$NotificationAccessSettingsActivity"
                )
                startActivity(Intent().setComponent(comp))
                true
            } catch (_: Exception) {
                false
            }
        }
    }

    private fun isNotificationAccessEnabled(): Boolean {
        return NotificationManagerCompat.getEnabledListenerPackages(this).contains(packageName)
    }

    private fun decodeNodeTree(json: String): List<Map<String, Any>> {
        if (json.isBlank()) return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                mapOf(
                    "text" to (o.optString("text") ?: ""),
                    "clickable" to o.optBoolean("clickable", false),
                    "editable" to o.optBoolean("editable", false),
                    "className" to (o.optString("className") ?: ""),
                    "x" to o.optInt("x", 0),
                    "y" to o.optInt("y", 0)
                )
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun isAccessibilityEnabled(): Boolean {
        val am = getSystemService(ACCESSIBILITY_SERVICE) as? AccessibilityManager ?: return false
        return am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            .any { it.resolveInfo?.serviceInfo?.packageName == packageName }
    }

    private fun requestMediaPermission(result: MethodChannel.Result) {
        val perms = if (Build.VERSION.SDK_INT >= 33) {
            arrayOf(Manifest.permission.READ_MEDIA_IMAGES)
        } else {
            arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        val need = perms.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (need.isEmpty()) {
            result.success(true)
            return
        }
        pendingMediaResult = result
        requestPermissions(need.toTypedArray(), REQ_MEDIA)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MEDIA) {
            pendingMediaResult?.success(grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED })
            pendingMediaResult = null
        } else if (requestCode == REQ_MEDIA_FILES) {
            pendingMediaFilesResult?.success(grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED })
            pendingMediaFilesResult = null
        }
    }

    private fun requestMediaFilesPermission(result: MethodChannel.Result) {
        val perms = if (Build.VERSION.SDK_INT >= 33) {
            arrayOf(Manifest.permission.READ_MEDIA_VIDEO, Manifest.permission.READ_MEDIA_AUDIO)
        } else {
            arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        val need = perms.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (need.isEmpty()) {
            result.success(true)
            return
        }
        pendingMediaFilesResult = result
        requestPermissions(need.toTypedArray(), REQ_MEDIA_FILES)
    }

    private fun queryRecentPhotos(limit: Int): List<Map<String, Any>> {
        val list = mutableListOf<Map<String, Any>>()
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES) != PackageManager.PERMISSION_GRANTED
        ) return list
        if (Build.VERSION.SDK_INT < 33 &&
            checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED
        ) return list

        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA)
        contentResolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            arrayOf(
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.SIZE,
                MediaStore.Images.Media.DATE_TAKEN,
                MediaStore.Images.Media.DATE_ADDED
            ),
            null,
            null,
            MediaStore.Images.Media.DATE_ADDED + " DESC"
        )?.use { c ->
            var n = 0
            while (c.moveToNext() && n < limit) {
                val id = c.getLong(0)
                val name = c.getString(1) ?: ""
                val size = c.getLong(2)
                val dateTaken = c.getLong(3)
                val dateAdded = c.getLong(4)
                val takenMs = if (dateTaken > 0) dateTaken else dateAdded * 1000L
                list.add(
                    mapOf(
                        "id" to id,
                        "uri" to (MediaStore.Images.Media.EXTERNAL_CONTENT_URI.toString() + "/" + id),
                        "name" to name,
                        "size" to size,
                        "date" to fmt.format(Date(takenMs)),
                        "type" to "image"
                    )
                )
                n++
            }
        }
        return list
    }

    private fun queryRecentMediaFiles(type: String, limit: Int): List<Map<String, Any>> {
        val list = mutableListOf<Map<String, Any>>()
        val uri: Uri
        val proj: Array<String>
        val needPermission: Array<String>
        when (type) {
            "video" -> {
                uri = MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                proj = arrayOf(
                    MediaStore.Video.Media._ID, MediaStore.Video.Media.DISPLAY_NAME,
                    MediaStore.Video.Media.SIZE, MediaStore.Video.Media.DURATION, MediaStore.Video.Media.DATE_ADDED
                )
                needPermission = if (Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.READ_MEDIA_VIDEO) else arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
            "audio" -> {
                uri = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
                proj = arrayOf(
                    MediaStore.Audio.Media._ID, MediaStore.Audio.Media.DISPLAY_NAME,
                    MediaStore.Audio.Media.SIZE, MediaStore.Audio.Media.DURATION, MediaStore.Audio.Media.DATE_ADDED
                )
                needPermission = if (Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.READ_MEDIA_AUDIO) else arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
            else -> { // document
                uri = MediaStore.Files.getContentUri("external")
                proj = arrayOf(
                    MediaStore.Files.FileColumns._ID, MediaStore.Files.FileColumns.DISPLAY_NAME,
                    MediaStore.Files.FileColumns.SIZE, MediaStore.Files.FileColumns.DATE_ADDED
                )
                needPermission = if (Build.VERSION.SDK_INT >= 30) emptyArray() else arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
        }
        // 权限检查：文档类在 Android 11+ 需要“所有文件访问”，未授予则返回空
        if (type == "document" && Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) {
            return list
        }
        for (p in needPermission) {
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) return list
        }
        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA)
        val selection = if (type == "document") {
            MediaStore.Files.FileColumns.MEDIA_TYPE + "=" + MediaStore.Files.FileColumns.MEDIA_TYPE_NONE +
                " AND (" + MediaStore.Files.FileColumns.MIME_TYPE + " IS NULL OR " +
                MediaStore.Files.FileColumns.MIME_TYPE + " LIKE 'application/%' OR " +
                MediaStore.Files.FileColumns.MIME_TYPE + " LIKE 'text/%')"
        } else null
        contentResolver.query(uri, proj, selection, null, MediaStore.Files.FileColumns.DATE_ADDED + " DESC")?.use { c ->
            var n = 0
            while (c.moveToNext() && n < limit) {
                val id = c.getLong(0)
                val name = c.getString(1) ?: ""
                val size = c.getLong(2)
                val dateAdded = c.getLong(3)
                val extra = if (type == "document") "" else ("（时长 " + (c.getLong(3) / 1000) + "s）")
                list.add(
                    mapOf(
                        "id" to id,
                        "uri" to (uri.toString() + "/" + id),
                        "name" to name,
                        "size" to size,
                        "date" to fmt.format(Date(dateAdded * 1000L)),
                        "type" to type + extra
                    )
                )
                n++
            }
        }
        return list
    }

    // ================= R5：统一健康检测 =================

    private fun getServiceHealth(): Map<String, Any> {
        val accSystemEnabled = isAccessibilityEnabled()
        val accInstanceAlive = PhonePerceptionAccessibilityService.instance != null
        val notifEnabled = isNotificationAccessEnabled()
        val notifConnected = PhonePerceptionNotificationService.instance != null
        // 去 Shizuku：保留旧健康键但恒为 false，避免调用方崩溃
        val shizukuRunning = false
        val shizukuGranted = false
        val usageEnabled = isUsageAccessEnabled()
        val batteryOk = isIgnoringBatteryOptimizations()

        return mapOf(
            "accessible" to (accSystemEnabled && accInstanceAlive),
            "accessibleSystemEnabled" to accSystemEnabled,
            "accessibleInstanceAlive" to accInstanceAlive,
            "notification" to notifEnabled,
            "notificationConnected" to (notifEnabled && notifConnected),
            "shizuku" to (shizukuRunning && shizukuGranted),
            "shizukuRunning" to shizukuRunning,
            "shizukuGranted" to shizukuGranted,
            "usageStats" to usageEnabled,
            "batteryOptimized" to !batteryOk,
        )
    }

    // ================= R4：电池优化白名单 =================

    private fun isIgnoringBatteryOptimizations(): Boolean {
        return try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            pm.isIgnoringBatteryOptimizations(packageName)
        } catch (_: Exception) {
            false
        }
    }

    private fun requestIgnoreBatteryOptimizations(): Boolean {
        return try {
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
            intent.data = Uri.parse("package:$packageName")
            startActivity(intent)
            true
        } catch (_: Exception) {
            try {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                true
            } catch (_: Exception) {
                false
            }
        }
    }

    // ================= R11：导出感知日志 =================

    private fun exportPerceptionLog(): Map<String, Any> {
        return try {
            val logFile = java.io.File(getExternalFilesDir(null) ?: filesDir, "logs/phone_perception.log")
            if (logFile.exists()) {
                mapOf("ok" to true, "path" to logFile.absolutePath, "content" to logFile.readText().take(50_000))
            } else {
                mapOf("ok" to true, "path" to "", "content" to "")
            }
        } catch (e: Exception) {
            mapOf("ok" to false, "error" to (e.message ?: ""))
        }
    }
}
