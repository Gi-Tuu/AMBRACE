package com.aicompanion.ai_companion

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.graphics.Path
import android.graphics.Rect
import android.util.Log
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * 手机感知·无障碍读屏服务（AI 走出沙箱 Phase 1~3）
 * Phase 1：聚合当前窗口文字，缓存“最近非本 app 页面”文本，供聊天时注入上下文。
 * Phase 3：升级为“节点树快照”（可点击/长按/输入节点 + 屏幕坐标），并执行
 * 单步动作（点击/长按/滚动/输入文本）。动作目标必须来自节点树，敏感节点硬拒绝。
 * 隐私护栏：跳过密码输入节点；跳过常见银行/支付类包名；只缓存文本不采集图片内容。
 */
class PhonePerceptionAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: PhonePerceptionAccessibilityService? = null
            private set
        @Volatile
        var lastScreenText: String = ""
            private set
        @Volatile
        var lastCapturedAt: Long = 0L
            private set
        @Volatile
        var lastNodeTreeJson: String = ""
            private set
        @Volatile
        var lastTreeAt: Long = 0L
            private set
        // 最近一次外部应用页面的节点树（工作流点选器可回看；本 app 页面不覆盖）
        @Volatile
        var lastExternalNodeTreeJson: String = ""
            private set
        @Volatile
        var lastExternalPackage: String = ""
            private set

        // 常见银行/支付类包名关键词（整页跳过，降低隐私风险）
        private val SENSITIVE_PACKAGE_KEYWORDS = listOf(
            "bank", "unionpay", "alipay" /* 支付宝页内表单多，保守跳过 */, "pay",
            "cred", "citibank", "cmb", "ccb", "icbc", "abc", "boc", "cib"
        )
        // 输入法（IME）包名关键词：输入法弹窗事件会混入“当前应用窗口”文本，导致读到本 app 页面
        private val IME_PACKAGE_KEYWORDS = listOf(
            "inputmethod", "sogou", "baidu.input", "iflytek", "qq.pinyin",
            "ime.", ".ime", "gboard", "keyboard"
        )
        // 本 app 页面固定 UI 特征文本：整页文本全为此类特征时视为“本 app 页面”，丢弃缓存
        private val APP_UI_FEATURES = listOf("输入消息", "手机感知", "免打扰", "消息通知", "角色生活")
        // 敏感动作拒绝：出现在节点文本/输入内容中时拒绝执行（支付/密码/验证码等）
        private val SENSITIVE_NODE_KEYWORDS = listOf(
            "password", "passwd", "pin", "支付", "付款", "钱包", "银行", "信用卡",
            "验证码", "登录密码", "支付密码", "转账", "余额", "安全键盘", "银行卡"
        )
        private const val MAX_TEXT = 2000
        private const val MAX_NODES = 40

        fun isSensitivePackage(pkg: String): Boolean {
            val p = pkg.lowercase()
            return SENSITIVE_PACKAGE_KEYWORDS.any { p.contains(it) }
        }

        fun isImePackage(pkg: String): Boolean {
            val p = pkg.lowercase()
            return IME_PACKAGE_KEYWORDS.any { p.contains(it) }
        }

        /** 文本是否整页都是本 app UI 特征（聊天输入框占位/抽屉入口等），无用户信息价值 */
        fun isAppUiOnly(text: String): Boolean {
            val t = text.replace("[输入框]", "").trim()
            if (t.isEmpty()) return true
            val stripped = APP_UI_FEATURES.fold(t) { acc, f -> acc.replace(f, "") }.trim()
            return stripped.isEmpty()
        }

        /** 敏感护栏：目标文本/输入内容含支付密码类关键词 → 拒绝操作 */
        fun isSensitiveText(t: String): Boolean {
            val s = t.lowercase()
            return SENSITIVE_NODE_KEYWORDS.any { s.contains(it) }
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        // 前台保活（2026-08-14）：无障碍服务启动前台通知，进程获前台保护，
        // 退出 App/划掉任务后 vivo 不再冻结杀进程，服务保持连接
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                val channel = NotificationChannel(
                    "ai_companion_service", "拥爱后台服务",
                    NotificationManager.IMPORTANCE_LOW,
                )
                nm.createNotificationChannel(channel)
            }
            val builder = if (Build.VERSION.SDK_INT >= 26) {
                Notification.Builder(this, "ai_companion_service")
            } else {
                @Suppress("DEPRECATION")
                Notification.Builder(this)
            }
            val notif = builder
                .setContentTitle("拥爱运行中")
                .setContentText("正在感知手机屏幕")
                .setSmallIcon(R.mipmap.ic_launcher)
                .setOngoing(true)
                .build()
            startForeground(8888, notif)
        } catch (ex: Exception) {
            Log.w("PhonePerceptionAcc", "startForeground failed: " + (ex.message ?: ""))
        }
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        try {
            onAccessibilityEventSafe(event)
        } catch (ex: Exception) {
            // 节点树操作偶发 IllegalStateException（节点已回收等），绝不能崩服务（崩溃会被系统禁用）
            Log.w("PhonePerceptionAcc", "onAccessibilityEvent error: " + (ex.message ?: ""))
        }
    }

    private fun onAccessibilityEventSafe(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
            event.eventType != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) return

        val pkg = event.packageName?.toString() ?: return
        // 本 app 页面：不缓存文本（避免“在聊天页问读到的是聊天页自己”），
        // 但仍刷新节点树（Phase 3 演示/操作自家 UI 需要目标节点）
        if (pkg == "com.aicompanion.ai_companion") {
            val ownRoot = rootInActiveWindow ?: return
            lastNodeTreeJson = buildNodeTreeJson(ownRoot)
            lastTreeAt = System.currentTimeMillis()
            return
        }
        // 敏感包名与输入法弹窗事件整页跳过（既缓存也拒绝操作）
        if (isSensitivePackage(pkg)) return
        if (isImePackage(pkg)) return

        val root = rootInActiveWindow ?: return
        val text = extractText(root)
        if (text.isBlank()) return
        // 整页只有本 app UI 特征文本（如聊天页输入框占位）→ 丢弃，避免读到“聊天页自己”
        if (isAppUiOnly(text)) return

        // 节流：文本变化或距上次捕获 ≥5 秒才更新
        val now = System.currentTimeMillis()
        if (text == lastScreenText && now - lastCapturedAt < 5000) return
        lastScreenText = text.take(MAX_TEXT)
        lastCapturedAt = now
        // Phase 3：同步刷新可操作节点树（供“指认按钮/执行动作”）
        val treeJson = buildNodeTreeJson(root)
        lastNodeTreeJson = treeJson
        lastTreeAt = now
        // 外部页面缓存：点选器回看“最近打开的应用”目标用
        lastExternalNodeTreeJson = treeJson
        lastExternalPackage = pkg
    }

    override fun onInterrupt() {}

    // ================= 读屏文本（Phase 1，保持不变） =================

    private fun extractText(node: AccessibilityNodeInfo): String {
        val sb = StringBuilder()
        collectText(node, sb)
        return sb.toString().trim()
    }

    private fun collectText(node: AccessibilityNodeInfo, sb: StringBuilder) {
        if (node.isPassword) return
        val t = nodeLabel(node) ?: ""
        if (t.isNotBlank() && t.length > 1 && !isGarbageText(t)) {
            sb.append(t).append("；")
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                collectText(child, sb)
            } finally {
                try { child.recycle() } catch (_: Exception) {}
            }
        }
    }

    private fun isGarbageText(t: String): Boolean {
        // 占位标签（[输入框]/[图标]）是语义标记，放行
        if (t == "[输入框]" || t == "[图标]") return false
        if (t.length >= 3 && t.toSet().size <= 1) return true
        val hasWord = t.any { c -> c.isLetterOrDigit() }
        return !hasWord
    }

    /** 取节点可读标签：text > contentDescription > tooltipText
     *  （Flutter 语义标签走 content-desc、tooltip 走 tooltipText，均不在 text 里） */
    private fun nodeLabel(node: AccessibilityNodeInfo): String? {
        val t = node.text?.toString()?.trim()
        if (!t.isNullOrBlank()) return t
        val d = node.contentDescription?.toString()?.trim()
        if (!d.isNullOrBlank()) return d
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            val tt = node.tooltipText?.toString()?.trim()
            if (!tt.isNullOrBlank()) return tt
        }
        return null
    }

    // ================= Phase 3：节点树快照 =================

    private fun buildNodeTreeJson(root: AccessibilityNodeInfo): String {
        val arr = JSONArray()
        collectClickable(root, arr)
        return arr.toString()
    }

    /** 实时采集当前窗口节点树（工作流点选器等需要最新屏幕时调用）；服务未连接/无窗口返回空串 */
    fun captureNodeTreeJson(): String {
        val root = rootInActiveWindow ?: return ""
        return try {
            val arr = JSONArray()
            collectClickable(root, arr)
            arr.toString()
        } catch (_: Exception) {
            ""
        }
    }

    private fun collectClickable(node: AccessibilityNodeInfo, arr: JSONArray) {
        if (arr.length() >= MAX_NODES) return
        if (node.isPassword) return
        val t = nodeLabel(node)
        val cls = node.className?.toString()?.lowercase() ?: ""
        val isEdit = cls.contains("edittext")
        val clickable = node.isClickable || node.isLongClickable || isEdit
        if (clickable) {
            // 空输入框以占位文本 [输入框] 暴露；无标签图标/图像节点给占位 [图标]（带坐标，供点选定位）
            val label = when {
                t.isNullOrBlank() && isEdit -> "[输入框]"
                t.isNullOrBlank() -> "[图标]"
                else -> t
            }
            if (!label.isNullOrBlank() && !isSensitiveText(label) && !isGarbageText(label)) {
                val rect = Rect()
                node.getBoundsInScreen(rect)
                if (rect.width() > 0 && rect.height() > 0) {
                    val obj = JSONObject()
                    obj.put("text", label.take(50))
                    obj.put("clickable", node.isClickable || node.isLongClickable)
                    obj.put("editable", isEdit)
                    obj.put("className", cls)
                    obj.put("x", rect.centerX())
                    obj.put("y", rect.centerY())
                    arr.put(obj)
                }
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                collectClickable(child, arr)
            } finally {
                try { child.recycle() } catch (_: Exception) {}
            }
        }
    }

    // ================= Phase 3：动作执行（点击/长按/滚动/输入） =================

    data class ActionResult(val ok: Boolean, val message: String)

    /** 执行单步动作。target 必须匹配当前窗口节点文本；敏感文本/密码节点拒绝。 */
    fun performNodeAction(action: String, target: String): ActionResult {
        if (action !in setOf("click", "long_click", "scroll")) {
            return ActionResult(false, "不支持的动作类型")
        }
        if (target.isBlank()) return ActionResult(false, "缺少操作目标")
        if (isSensitiveText(target)) return ActionResult(false, "敏感节点已拒绝操作")
        val root = rootInActiveWindow ?: return ActionResult(false, "无障碍服务未连接或当前无可操作页面")
        val node = if (target == "[输入框]") {
            findEditableNode(root)
        } else {
            findNodeByText(root, target)
        } ?: return ActionResult(false, "未在当前屏幕找到目标节点“$target”")
        if (node.isPassword) {
            try { node.recycle() } catch (_: Exception) {}
            return ActionResult(false, "密码节点已拒绝操作")
        }
        val result = when (action) {
            "click" -> performClick(node)
            "long_click" -> performLongClick(node)
            else -> performScroll(node)
        }
        try { node.recycle() } catch (_: Exception) {}
        return result
    }

    /** 输入文本到当前聚焦输入框（长度 ≤50，敏感词拒绝）。 */
    fun performSetText(text: String): ActionResult {
        if (text.isBlank()) return ActionResult(false, "输入内容为空")
        if (text.length > 50) return ActionResult(false, "输入内容过长（最多 50 字）")
        if (isSensitiveText(text)) return ActionResult(false, "敏感内容拒绝输入")
        val root = rootInActiveWindow ?: return ActionResult(false, "无障碍服务未连接")
        val focused = findFocusedEdit(root)
            ?: return ActionResult(false, "未找到当前聚焦的输入框")
        if (focused.isPassword) {
            try { focused.recycle() } catch (_: Exception) {}
            return ActionResult(false, "密码输入框已拒绝操作")
        }
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        val ok = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        try { focused.recycle() } catch (_: Exception) {}
        return if (ok) ActionResult(true, "已输入文本（${text.length} 字）")
        else ActionResult(false, "输入失败：目标输入框不支持写入")
    }

    private fun findEditableNode(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (root.isEditable && !root.isPassword) return root
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val r = findEditableNode(child)
            if (r != null) return r
        }
        return null
    }

    private fun findFocusedEdit(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val cls = root.className?.toString()?.lowercase() ?: ""
        if (root.isFocused && (cls.contains("edittext") || root.isEditable)) return root
        if (root.isEditable) return root
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val r = findFocusedEdit(child)
            if (r != null) return r
        }
        return null
    }

    /** 先精确匹配，再模糊包含匹配：避免“发布”误中“发布动态”这类包含关系节点。 */
    private fun findNodeByText(root: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        findNodeByTextExact(root, text)?.let { return it }
        return findNodeByTextContains(root, text)
    }

    private fun findNodeByTextExact(root: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        val t = nodeLabel(root)
        if (!t.isNullOrBlank() && !root.isPassword && !isSensitiveText(t) && t == text) return root
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val r = findNodeByTextExact(child, text)
            if (r != null) return r
        }
        return null
    }

    private fun findNodeByTextContains(root: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        val t = nodeLabel(root)
        if (!t.isNullOrBlank() && !root.isPassword && !isSensitiveText(t)) {
            val match = t == text || t.contains(text) || text.contains(t)
            if (match) return root
        }
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val r = findNodeByTextContains(child, text)
            if (r != null) return r
        }
        return null
    }

    private fun performClick(node: AccessibilityNodeInfo): ActionResult {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        val cx = rect.centerX().toFloat()
        val cy = rect.centerY().toFloat()
        val label = node.text?.toString()?.trim()?.take(20) ?: "目标"
        // 手势优先（对不可直接 ACTION_CLICK 的节点更稳），失败降级为节点点击
        if (dispatchTap(cx, cy)) return ActionResult(true, "已点击“$label”")
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        return if (ok) ActionResult(true, "已点击“$label”")
        else ActionResult(false, "点击“$label”失败：节点不可操作")
    }

    private fun performLongClick(node: AccessibilityNodeInfo): ActionResult {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        val cx = rect.centerX().toFloat()
        val cy = rect.centerY().toFloat()
        val label = node.text?.toString()?.trim()?.take(20) ?: "目标"
        if (dispatchLongPress(cx, cy)) return ActionResult(true, "已长按“$label”")
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
        return if (ok) ActionResult(true, "已长按“$label”")
        else ActionResult(false, "长按“$label”失败")
    }

    private fun performScroll(node: AccessibilityNodeInfo): ActionResult {
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
        return if (ok) ActionResult(true, "已向下滚动")
        else ActionResult(false, "滚动失败：目标不支持滚动")
    }

    private fun dispatchTap(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 100L))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    private fun dispatchLongPress(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 600L))
            .build()
        return dispatchGesture(gesture, null, null)
    }
}
