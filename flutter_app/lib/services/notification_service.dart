import "dart:async";
import "package:flutter/material.dart";
import "package:flutter_background_service/flutter_background_service.dart";
import "package:shared_preferences/shared_preferences.dart";
import "api_client.dart";
import "dnd_settings.dart";
import "notification_displayer.dart";
import "unread_engine.dart";
import "../utils/app_lang.dart";

/// 当前所在页面：聊天页/好友列表页不弹（可直接看到消息来源），其他页面弹
enum ActiveScreen { other, characterList, chat }

/// 消息通知服务：前台展示通道（红点 + 顶部横幅 + 前台系统通知）。
/// 【通知双轨收敛 2026-08-05】不再自行网络轮询：
/// 网络轮询统一由后台前台服务（BackgroundPollingService）执行，结果写入 SharedPreferences，
/// 本服务每 5 秒读快照刷新红点、消费新增未读事件弹横幅；
/// 兜底：后台服务心跳超时（150s）时前台自行单次轮询，防止服务被杀后通知失效。
class NotificationService extends ChangeNotifier {
  static final NotificationService _instance = NotificationService._internal();
  factory NotificationService() => _instance;
  NotificationService._internal();

  final _api = ApiClient();
  Map<int, int> _unreadCounts = {}; // character_id -> count（已过滤本地已读抑制）
  final Map<int, int> _sessionCharMap = {}; // session_id -> character_id（兜底轮询时填充）
  ActiveScreen _activeScreen = ActiveScreen.other;
  int? _chatCharacterId; // 当前聊天页角色
  bool _isAppInForeground = true; // app 是否在前台（后台时由前台服务弹系统通知）
  Timer? _prefsTimer;
  Timer? _fallbackTimer;

  /// 本地已读抑制：markRead/聊天页吸收时记录 (charId -> 当时的 count)，
  /// 快照读到相同 count 时过滤（红点不回弹）；count 增大或归零后解除。
  final Map<int, int> _suppressedCounts = {};

  /// 已消费的事件时间戳（持久化，重启不重复弹）
  int _lastEventAt = 0;
  final NotifyDebouncer _debouncer = NotifyDebouncer();

  /// 兜底轮询的本地基线（与后台 isolate 互不干扰）
  Map<int, int> _fallbackLastCounts = {};
  bool _fallbackBaselineSet = false;

  Map<int, int> get unreadCounts => Map.unmodifiable(_unreadCounts);
  ActiveScreen get activeScreen => _activeScreen;

  /// 页面主动上报当前所在页面（聊天页/好友列表页用于抑制弹窗）
  void setActiveScreen(ActiveScreen screen, {int? characterId}) {
    _activeScreen = screen;
    if (screen == ActiveScreen.chat) _chatCharacterId = characterId;
    notifyListeners();
  }

  /// app 前后台切换（AppLifecycleListener 调用）：
  /// 写 SharedPreferences（兼容旧逻辑）+ invoke 可靠通道通知后台 isolate
  /// （prefs 跨 isolate 单例缓存读不到对方写入值，后台标志必须走 invoke）
  Future<void> setAppInForeground(bool value) async {
    _isAppInForeground = value;
    try {
      FlutterBackgroundService().invoke("setAppForeground", {"value": value});
    } catch (_) {}
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool("app_in_foreground", value);
  }

  /// 初始化系统通知插件（main 启动时调用一次）
  Future<void> init() async {
    await NotificationDisplayer().init();
  }

  /// 启动轮询（登录后调用）：立即轮询一次 + 每 5 秒读本地快照 + 每 30 秒网络自轮询。
  /// 注意：SharedPreferences 跨 isolate 单例缓存读不到后台 isolate 写入的事件/快照，
  /// 因此前台红点/横幅数据源为自轮询（_fallbackPoll），后台 isolate 只负责后台时的系统通知。
  void startPolling() {
    _prefsTimer?.cancel();
    _fallbackTimer?.cancel();
    _prefsTimer = Timer.periodic(const Duration(seconds: 5), (_) => _refreshFromPrefs());
    _fallbackTimer = Timer.periodic(const Duration(seconds: 30), (_) => _fallbackPoll());
    _refreshFromPrefs();
    _fallbackPoll();
  }

  void stopPolling() {
    _prefsTimer?.cancel();
    _fallbackTimer?.cancel();
    _prefsTimer = null;
    _fallbackTimer = null;
  }

  /// 读 SharedPreferences 快照：刷新红点 + 消费新增未读事件
  Future<void> _refreshFromPrefs() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (_lastEventAt == 0) _lastEventAt = prefs.getInt("unread_last_event_at") ?? 0;

      // 1) 快照 → 红点（应用本地已读抑制）
      final snapshot =
          NotifyPrefs.decodeSnapshot(prefs.getString(NotifyPrefs.snapshotKey));
      _applySnapshot(snapshot);

      // 2) 新增未读事件 → 弹横幅/系统通知（仅处理比上次新的）
      final eventAt = prefs.getInt(NotifyPrefs.newEventAtKey) ?? 0;
      if (eventAt > _lastEventAt) {
        final events = await NotifyPrefs.decodeEvent(prefs.getString(NotifyPrefs.newEventKey));
        _lastEventAt = eventAt;
        await prefs.setInt("unread_last_event_at", eventAt);
        for (final event in events) {
          await _handleNewUnread(event);
        }
      }
    } catch (_) {}
  }

  /// 应用快照（过滤本地已读抑制；count 增大/归零后解除抑制）
  void _applySnapshot(Map<int, int> snapshot) {
    final cleaned = <int, int>{};
    _suppressedCounts.removeWhere((charId, count) {
      final cur = snapshot[charId] ?? 0;
      return cur == 0 || cur != count; // 服务器已清零或又有新增 → 解除
    });
    for (final e in snapshot.entries) {
      if (_suppressedCounts.containsKey(e.key)) continue;
      cleaned[e.key] = e.value;
    }
    _unreadCounts = cleaned;
    notifyListeners();
  }

  /// 前台兜底单次网络轮询（复用共享引擎，逻辑与后台 isolate 一致）
  Future<void> _fallbackPoll() async {
    try {
      final data = await _api.getUnreadCounts();
      final list = data["unread"] as List? ?? [];
      final newCounts = UnreadEngine.parseUnread(list);
      final diffs = _fallbackBaselineSet
          ? UnreadEngine.diffNewUnread(_fallbackLastCounts, newCounts)
          : <UnreadDiff>[];
      _fallbackLastCounts = newCounts;
      _fallbackBaselineSet = true;

      _applySnapshot(newCounts);
      for (final diff in diffs) {
        final sessionId = _findSessionIdByList(list, diff.characterId);
        if (sessionId == null) continue;
        // 正在该角色的聊天页：消息已实时可见，吸收未读，不弹
        if (_activeScreen == ActiveScreen.chat && _chatCharacterId == diff.characterId) {
          _absorbUnread(sessionId, diff.characterId);
          continue;
        }
        // 组装事件所需内容（前台兜底时这里会拉一次消息）
        final msgs = await _api.getMessages(sessionId, limit: 1);
        if (msgs.isEmpty) continue;
        final msg = msgs.last;
        if (msg.senderType != 'ai') continue;
        final title = await _fetchCharacterName(diff.characterId);
        await _handleNewUnread(UnreadEvent(
          characterId: diff.characterId,
          sessionId: sessionId,
          count: diff.count,
          title: title,
          content: msg.content,
        ));
      }
    } catch (_) {}
  }

  int? _findSessionIdByList(List<dynamic> list, int charId) {
    for (final item in list) {
      if (item is! Map) continue;
      final sid = item["session_id"] as int?;
      final cid = item["character_id"] as int?;
      if (cid == charId && sid != null) return sid;
    }
    return null;
  }

  Future<void> _handleNewUnread(UnreadEvent event) async {
    final charId = event.characterId;
    // app 在后台/息屏时由前台服务弹系统通知，Flutter 层不再弹
    if (!_isAppInForeground) return;
    // 通知总开关：关闭后横幅与系统通知都不弹（红点仍更新）
    final settings = await DndSettings.get();
    if (!(settings["notificationsEnabled"] as bool? ?? true)) return;
    // 页面抑制：好友列表页不弹；聊天页且是该角色不弹（吸收未读）
    if (_activeScreen == ActiveScreen.characterList) return;
    if (_activeScreen == ActiveScreen.chat && _chatCharacterId == charId) {
      await _absorbUnread(event.sessionId, charId);
      return;
    }

    // 防抖：同角色 10 秒内不重复弹
    if (!_debouncer.shouldAllow(charId)) return;

    // 系统通知（含免打扰检查；2026-08-18：去掉 App 内顶部横幅，只保留系统通知——避免双弹窗）
    
    await NotificationDisplayer().showSystemNotification(
        id: charId, title: event.title, body: event.content);
  }

  Future<String> _fetchCharacterName(int charId) async {
    final fallback = await appLang() == 'en' ? 'AI Friend' : 'AI 好友';
    try {
      final chars = await _api.getCharacters();
      for (final c in chars) {
        if (c.id == charId) return c.name;
      }
      return fallback;
    } catch (_) {
      return fallback;
    }
  }

  Future<void> markRead(int sessionId) async {
    try {
      await _api.markSessionRead(sessionId);
      final charId = _sessionCharMap.remove(sessionId);
      if (charId != null) {
        final cur = _unreadCounts[charId] ?? 0;
        _suppressedCounts[charId] = cur;
        _unreadCounts.remove(charId);
        notifyListeners();
      }
      _refreshFromPrefs();
    } catch (_) {}
  }

  /// 聊天页内消息已实时可见：吸收未读并调用后端标记已读（联动同角色会话）
  Future<void> _absorbUnread(int sessionId, int charId) async {
    try {
      await _api.markSessionRead(sessionId);
      _sessionCharMap[sessionId] = charId;
      final cur = _unreadCounts[charId] ?? 0;
      _suppressedCounts[charId] = cur;
      _unreadCounts.remove(charId);
      notifyListeners();
    } catch (_) {}
  }
}