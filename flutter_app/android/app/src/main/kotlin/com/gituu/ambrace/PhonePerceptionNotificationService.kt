package com.gituu.ambrace.ai_companion

import android.app.Notification
import android.content.ComponentName
import android.content.pm.PackageManager
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.text.TextUtils
import android.util.Base64
import android.util.Log
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
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

        /** 通道状态时间戳：ISO8601 UTC（Dart DateTime.tryParse 可解析；不用 java.time，其需 API 26） */
        private val UTC_TS = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
            .apply { timeZone = TimeZone.getTimeZone("UTC") }

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
            if (pkg == "com.gituu.ambrace.ai_companion") return
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
                    if (pkg == "com.gituu.ambrace.ai_companion") continue
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

    /** 响应签名的防重放原料：16 字节随机数 → base64url（无填充、无换行），与 App 侧同形态 */
    private fun identityNonce(): String {
        val bytes = ByteArray(16)
        java.security.SecureRandom().nextBytes(bytes)
        return Base64.encodeToString(
            bytes,
            Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP
        )
    }

    /**
     * 验签（批 0-3 M0-b，口径同 backend/app/server_identity.py::verify_proof）：
     * canonical = nonce + "\n" + status + "\n" + sha256(body).hex + "\n" + ts，
     * 密钥是配对时解包 wrapped_key 得到的身份密钥（site），不是配对码本身。
     *
     * 本上报路径不在后端 SIGN_PATHS 白名单内 ⇒ 正常响应**不出签**：没有 X-Ambrace-Proof
     * 属预期，判为通过（不阻断上报）。一旦带了签名就必须对得上，对不上即视为伪造。
     */
    private fun verifyIdentityProof(
        identityKeyB64: String,
        nonce: String,
        status: Int,
        body: ByteArray,
        proof: String?,
    ): Boolean {
        val header = (proof ?: "").trim()
        if (header.isEmpty()) return true
        val site = try {
            Base64.decode(identityKeyB64, Base64.DEFAULT)
        } catch (_: Exception) {
            return false
        }
        if (site.size != 32) return false
        val parts = header.split(" ")
        if (parts.size != 3 || parts[0] != "v1") return false
        val bodyHex = MessageDigest.getInstance("SHA-256").digest(body)
            .joinToString("") { "%02x".format(it) }
        val canonical = "${nonce.take(128)}\n$status\n$bodyHex\n${parts[1]}"
        val expect = Mac.getInstance("HmacSHA256").apply {
            init(SecretKeySpec(site, "HmacSHA256"))
        }.doFinal(canonical.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
        // 定长十六进制串；isEqual 不做逐字节短路比较
        return MessageDigest.isEqual(expect.toByteArray(), parts[2].toByteArray())
    }

    /** P1 通道状态：验签失败写 chan_status_perception_upload（Dart 侧 ChannelStatusTracker 读同一 key） */
    private fun recordIdentityMismatch(detail: String) {
        try {
            val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
            val key = "flutter.chan_status_perception_upload"
            val prev = try {
                JSONObject(prefs.getString(key, "{}") ?: "{}")
            } catch (_: Exception) {
                JSONObject()
            }
            val state = JSONObject()
                .put("code", "identityMismatch")
                .put("retriable", false)
                .put("lastOkAt", prev.optString("lastOkAt", ""))
                .put("lastErrorAt", UTC_TS.format(Date()))
                .put("failCount", prev.optInt("failCount", 0) + 1)
                .put("detail", detail.take(200))
            prefs.edit().putString(key, state.toString()).apply()
        } catch (e: Exception) {
            Log.w("PhonePerception", "identity status write failed: ${e.message}")
        }
    }

    /** 事件驱动上报：把通知缓存 POST 到自家服务器 /perception/auto（开关/地址/token 读 Flutter 预置，同进程无缓存问题） */
    private fun reportToServer() {
        try {
            val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
            if (!prefs.getBoolean("flutter.pp_auto_notify", false)) return
            val token = prefs.getString("flutter.auth_token", "") ?: ""
            val baseUrl = prefs.getString("flutter.server_url", "") ?: ""
            // 批 0-3 M0-b：身份密钥副本（App 配对成功后写入同一份 Flutter prefs，
            // 与 flutter.auth_token / flutter.server_url 同口径）。本路径不在后端 SIGN_PATHS
            // 白名单内（响应正常不出签）；一旦带签名就必须验，验不过即丢弃并记通道状态。
            val identityKey = prefs.getString("flutter.server_identity_key", "") ?: ""
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
                    val nonce = if (identityKey.isNotEmpty()) identityNonce() else ""
                    if (nonce.isNotEmpty()) {
                        conn.setRequestProperty("X-Ambrace-Challenge", nonce)
                    }
                    conn.doOutput = true
                    conn.outputStream.use { it.write(payload.toByteArray(Charsets.UTF_8)) }
                    val code = conn.responseCode
                    // 验签摘要必须打在服务器返回的原始字节上，故整段读回
                    val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                    val body = stream?.use { it.readBytes() } ?: ByteArray(0)
                    if (identityKey.isNotEmpty() && !verifyIdentityProof(
                            identityKey, nonce, code, body,
                            conn.getHeaderField("X-Ambrace-Proof"))
                    ) {
                        // 签名不符＝来源可疑：丢弃该响应（不认这次上报成功），并落 P1 通道状态
                        Log.w("PhonePerception", "identity proof mismatch on http $code, dropped")
                        recordIdentityMismatch("native perception/auto proof mismatch (http $code)")
                        conn.disconnect()
                        return@Thread
                    }
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
