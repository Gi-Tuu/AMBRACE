package com.aicompanion.ai_companion

import android.app.Notification
import android.content.ComponentName
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.text.TextUtils
import android.util.Log
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONArray
import org.json.JSONObject

/**
 * 手机感知·通知监听服务（AI 走出沙箱 Phase 2）
 * 缓存用户手机收到的消息通知（包名/应用名/标题/文本/时间），供聊天时注入上下文。
 * 隐私护栏：跳过本 app 自己通知；跳过验证码/密码/支付类关键词；只保留文本不进图片。
 */
class PhonePerceptionNotificationService : NotificationListenerService() {

    companion object {
        @Volatile
        var lastNotifications: List<Map<String, String>> = emptyList()
            private set

        @Volatile
        var instance: PhonePerceptionNotificationService? = null
            private set

        private const val MAX_KEEP = 20

        // 验证码/密码/支付类关键词：整条丢弃，保护隐私
        private val SENSITIVE_KEYWORDS = listOf(
            "验证码", "动态码", "校验码", "安全码", "密码", "支付", "转账", "银行", "余额",
            "card", "code", "password", "otp", "verify"
        )

        fun isSensitive(title: String, text: String): Boolean {
            val t = (title + " " + text).lowercase()
            return SENSITIVE_KEYWORDS.any { t.contains(it) }
        }

        private const val PREFS = "phone_perception"
        private const val KEY_WHITELIST = "notif_whitelist"
        private const val KEY_CACHE = "notif_cache_json"

        /** 把当前缓存写入 SharedPreferences，供后台 isolate 定时主动上报（后台 isolate 无 MethodChannel） */
        fun persistToPrefs(context: android.content.Context) {
            try {
                val arr = JSONArray()
                for (n in lastNotifications) {
                    arr.put(JSONObject(n as Map<*, *>))
                }
                context.getSharedPreferences(PREFS, MODE_PRIVATE)
                    .edit().putString(KEY_CACHE, arr.toString()).apply()
        // 同步写入 FlutterSharedPreferences（flutter. 前缀），供 Dart 前台/新 isolate 读取
        context.getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
                    .edit().putString("flutter." + KEY_CACHE, arr.toString()).apply()
            } catch (_: Exception) {}
        }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        try {
            val pkg = sbn.packageName ?: return
            if (pkg == "com.aicompanion.ai_companion") return
            // 通知白名单：空 = 全部允许；非空 = 只缓存勾选的 app
            // 白名单由 Flutter 设置页写入 FlutterSharedPreferences（key=flutter.pp_notif_whitelist）
            val wl = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
                .getStringSet("flutter." + KEY_WHITELIST, emptySet()) ?: emptySet()
            if (wl.isNotEmpty() && pkg !in wl) return
            val extras = sbn.notification?.extras ?: return
            val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
            val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
                ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
            if (TextUtils.isEmpty(title) && TextUtils.isEmpty(text)) return
            if (isSensitive(title, text)) return

            val entry = mapOf(
                "app" to resolveAppName(pkg),
                "package" to pkg,
                "title" to title.take(80),
                "text" to text.take(200),
                "time" to System.currentTimeMillis().toString(),
            )
            val list = ArrayList(lastNotifications.filter { it["package"] != pkg })
            list.add(0, entry)
            lastNotifications = list.take(MAX_KEEP)
            persistToPrefs(this)
            debouncedReportToServer()
        } catch (e: Exception) {
            Log.w("PhonePerception", "notification capture failed: ${e.message}")
        }
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification?) {
        // 保留历史，不删除（聊天时读最近几条即可）
    }

    override fun onListenerConnected() {
        instance = this
        Log.i("PhonePerception", "notification listener connected")
        // 进程重启后缓存为空：把系统当前活跃通知同步进来，避免错过停机期间的通知
        try {
            val active = activeNotifications
            if (active.isNotEmpty()) {
                val list = ArrayList(lastNotifications)
                for (sbn in active) {
                    val pkg = sbn.packageName ?: continue
                    if (pkg == "com.aicompanion.ai_companion") continue
                    val extras = sbn.notification?.extras ?: continue
                    val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
                    val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
                        ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
                    if (TextUtils.isEmpty(title) && TextUtils.isEmpty(text)) continue
                    if (isSensitive(title, text)) continue
                    val entry = mapOf(
                        "app" to resolveAppName(pkg),
                        "package" to pkg,
                        "title" to title.take(80),
                        "text" to text.take(200),
                        "time" to System.currentTimeMillis().toString(),
                    )
                    list.removeAll { it["package"] == pkg }
                    list.add(0, entry)
                }
                lastNotifications = list.take(MAX_KEEP)
                persistToPrefs(this)
                Log.i("PhonePerception", "synced active notifications: " + lastNotifications.size)
                reportToServer()
            }
        } catch (e: Exception) {
            Log.w("PhonePerception", "sync active notifications failed: " + e.message)
        }
    }

    /** R2：监听断开时主动请求重绑；部分 ROM 会拦截，失败由健康检测引导用户重开 */
    override fun onListenerDisconnected() {
        instance = null
        Log.w("PhonePerception", "notification listener disconnected")
        try {
            requestRebind(ComponentName(this, PhonePerceptionNotificationService::class.java))
        } catch (e: Exception) {
            Log.w("PhonePerception", "requestRebind failed: ${e.message}")
        }
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    private fun resolveAppName(pkg: String): String {
        return try {
            val ai = packageManager.getApplicationInfo(pkg, 0)
            packageManager.getApplicationLabel(ai).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            pkg
        }
    }

    private var lastReportAt = 0L

    /** 捕获新通知后去抖上报（60s），避免高频通知刷请求；服务器端另有 30 分钟节流 */
    private fun debouncedReportToServer() {
        val now = System.currentTimeMillis()
        if (now - lastReportAt < 60_000L) return
        lastReportAt = now
        reportToServer()
    }

    /** 事件驱动上报：把通知缓存 POST 到自家服务器 /perception/auto（开关/地址/token 读 Flutter 预置，同进程无缓存问题） */
    private fun reportToServer() {
        try {
            val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
            if (!prefs.getBoolean("flutter.pp_auto_notify", false)) return
            val token = prefs.getString("flutter.auth_token", "") ?: ""
            val baseUrl = prefs.getString("flutter.server_url", "") ?: ""
            if (token.isEmpty() || baseUrl.isEmpty()) return
            if (lastNotifications.isEmpty()) return
            val arr = JSONArray()
            for (n in lastNotifications) {
                arr.put(JSONObject(n as Map<*, *>))
            }
            val payload = JSONObject().put("notifications", arr).toString()
            val url = URL(baseUrl.trimEnd('/') + "/api/v1/phone/perception/auto")
            Thread {
                try {
                    val conn = url.openConnection() as HttpURLConnection
                    conn.requestMethod = "POST"
                    conn.connectTimeout = 5000
                    conn.readTimeout = 10000
                    conn.setRequestProperty("Content-Type", "application/json")
                    conn.setRequestProperty("Authorization", "Bearer $token")
                    conn.doOutput = true
                    conn.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
                    val code = conn.responseCode
                    Log.i("PhonePerception", "auto report sent: " + code)
                    conn.disconnect()
                } catch (e: Exception) {
                    Log.w("PhonePerception", "auto report failed: " + e.message)
                }
            }.start()
        } catch (e: Exception) {
            Log.w("PhonePerception", "reportToServer error: " + e.message)
        }
    }
}
