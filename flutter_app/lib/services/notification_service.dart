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
/// 【通知双轨收敛 2026-08-05】网络轮询以后台前台服务（BackgroundPollingService）为主源。
/// 【订正 2026-10-11 A54】此前这段注释写的是「每 5 秒读快照刷新红点＋心跳超时 150s 兜底」，
/// 而实现里 150s 那条判定**从来不存在**，且本文件 :73-74 自己写着「SharedPreferences 跨 isolate
/// 单例缓存读不到后台 isolate 写入的值」⇒ **前台红点的真实数据源是每 30 秒那次自轮询（_fallbackPoll），
/// 5 秒那个定时器基本空转**。这就是「红点要过很久／重进一次才消失」的量化解释，本次按此修：
/// ① 读快照前 `reload()`，让 5s 通道真的能看见后台写的值；
/// ② AI 回复落地那一刻由聊天链路主动标记已读（不等 30s，见 ChatProvider._noteAiReplyVisible）；
/// ③ 标记后立即做一次**网络权威**校正（_fallbackPoll），不再只刷本地快照；
/// ④ 顺序改为「先吸收、后应用快照」，消掉红点亮一下又灭；
/// ⑤ 抑制改带 TTL 的纯状态机（ReadSuppressor），不再无限期压住红点。
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

  /// 本地已读抑制（A54 起为纯状态机，规则与 TTL 见 unread_engine.ReadSuppressor，可单测）。
  final ReadSuppressor _suppressor = ReadSuppressor();
  /// 最近一次「用户正看着这个角色的聊天页」的会话/角色，用于离开页面与切后台时补一次已读。
  int? _visibleSessionId;
  int? _visibleCharacterId;
  /// 可见期标记已读的节流戳（AI 连发多条时不必每条都打一次 /read）
  int _lastVisibleMarkMs = 0;

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
    final wasChat = _activeScreen == ActiveScreen.chat;
    _activeScreen = screen;
    if (screen == ActiveScreen.chat) {
      _chatCharacterId = characterId;
    } else if (wasChat) {
      // 离开聊天页（被别的页面盖住／返回上层）⇒ 刚才看着的那条会话补标一次已读。
      // 不补就会退化成「红点等到下次 30s 轮询或重进会话才消失」。
      _flushVisibleRead();
    }
    notifyListeners();
  }

  /// app 前后台切换（AppLifecycleListener 调用）：
  /// 写 SharedPreferences（兼容旧逻辑）+ invoke 可靠通道通知后台 isolate
  /// （prefs 跨 isolate 单例缓存读不到对方写入值，后台标志必须走 invoke）
  Future<void> setAppInForeground(bool value) async {
    if (!value && _activeScreen == ActiveScreen.chat) {
      // 退后台/息屏前补一次已读：用户刚才就在看这个会话
      _flushVisibleRead();
    }
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
      // A54：后台 isolate 写的是同一份原生 SharedPreferences，但 Dart 侧每个 isolate 有自己的
      // 单例缓存——不 reload 就读到旧值（这正是 :73-74 那句注释承认「读不到」的原因）。
      try {
        await prefs.reload();
      } catch (_) {}
      if (_lastEventAt == 0) _lastEventAt = prefs.getInt("unread_last_event_at") ?? 0;

      // 1) 先消费新增未读事件：正在看的角色会被吸收（并登记抑制）
      final eventAt = prefs.getInt(NotifyPrefs.newEventAtKey) ?? 0;
      if (eventAt > _lastEventAt) {
        final events = await NotifyPrefs.decodeEvent(prefs.getString(NotifyPrefs.newEventKey));
        _lastEventAt = eventAt;
        await prefs.setInt("unread_last_event_at", eventAt);
        for (final event in events) {
          await _handleNewUnread(event);
        }
      }

      // 2) 再应用快照 → 红点。顺序反过来就会「先亮后灭」（用户体感的红点闪一下）：
      //    旧顺序是先用未过滤的快照点亮，之后才吸收，观感上就是回弹。
      final snapshot =
          NotifyPrefs.decodeSnapshot(prefs.getString(NotifyPrefs.snapshotKey));
      _applySnapshot(snapshot);
    } catch (_) {}
  }

  /// 应用快照（抑制规则集中在 ReadSuppressor：TTL／归零／计数变化都会解除）
  void _applySnapshot(Map<int, int> snapshot) {
    final cleaned = _suppressor.filter(snapshot);
    final prev = _unreadCounts;
    _unreadCounts = cleaned;
    // 这个快照是每 5 秒读一次 SharedPreferences 的轮询喂进来的（:78）。
    // 不加这句比较就是"每 5 秒无条件 notifyListeners"，而好友列表的监听回调
    // 是一句裸 setState ⇒ 未读没变也整页重建（列表一长就变成周期性掉帧）。
    // 已核：标已读/清除那两条路径各自都带 notify（:222 / :236），不靠这里兜底。
    var changed = prev.length != cleaned.length;
    if (!changed) {
      for (final e in cleaned.entries) {
        if (prev[e.key] != e.value) {
          changed = true;
          break;
        }
      }
    }
    if (changed) notifyListeners();
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

      // A54：先把「正在看的角色」吸收掉，再应用快照（否则红点先亮后灭）；
      // 其余角色的事件排在 _applySnapshot 之后处理，保证角标与横幅同时序。
      final deferred = <UnreadEvent>[];
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
        deferred.add(UnreadEvent(
          characterId: diff.characterId,
          sessionId: sessionId,
          count: diff.count,
          title: title,
          content: msg.content,
        ));
      }
      _applySnapshot(newCounts);
      for (final e in deferred) {
        await _handleNewUnread(e);
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

  /// 标记某会话已读。
  ///
  /// ⚠ A54 修掉一条**恒不生效的死路径**：原来本地清红点靠 `_sessionCharMap.remove(sessionId)`，
  /// 而这张表只有 `_absorbUnread` 会写 ⇒ 进会话那唯一一次 markRead 永远拿到 null，红点只能等
  /// 后面的刷新兜。所以 `characterId` 现在由调用方（聊天页知道自己在看谁）传进来。
  Future<void> markRead(int sessionId, {int? characterId}) async {
    try {
      await _api.markSessionRead(sessionId);
      final charId = characterId ?? _sessionCharMap.remove(sessionId);
      _rememberVisible(sessionId, charId);
      if (charId != null) {
        _suppressor.note(charId, _unreadCounts[charId] ?? 0);
        _unreadCounts.remove(charId);
        notifyListeners();
      }
      // 权威校正走网络（本地快照可能还是旧的），失败退回刷快照
      await _fallbackPoll();
    } catch (_) {
      _refreshFromPrefs();
    }
  }

  /// 记住「用户此刻在看哪个会话／角色」，供离开页面与切后台时补标一次。
  void _rememberVisible(int? sessionId, int? charId) {
    if (sessionId != null) _visibleSessionId = sessionId;
    if (charId != null) _visibleCharacterId = charId;
  }

  /// 补一次已读（离开聊天页／切后台前）。静默失败：这只是兜底，不打断 UI。
  void _flushVisibleRead() {
    final sid = _visibleSessionId;
    if (sid == null) return;
    unawaited(markRead(sid, characterId: _visibleCharacterId));
  }

  /// AI 回复落地那一刻由聊天链路调用（**不等 30 秒轮询**）：
  /// 只有确实停在该角色的聊天页才标已读，并按 2 秒节流避免连发多条时反复打 /read。
  Future<void> onVisibleReply(int? sessionId, int? charId) async {
    if (sessionId == null || charId == null) return;
    if (_activeScreen != ActiveScreen.chat || _chatCharacterId != charId) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    if (now - _lastVisibleMarkMs < 2000) {
      _rememberVisible(sessionId, charId);
      return;
    }
    _lastVisibleMarkMs = now;
    await markRead(sessionId, characterId: charId);
  }

  /// 聊天页内消息已实时可见：吸收未读并调用后端标记已读（联动同角色会话）
  Future<void> _absorbUnread(int sessionId, int charId) async {
    try {
      await _api.markSessionRead(sessionId);
      _sessionCharMap[sessionId] = charId;
      _rememberVisible(sessionId, charId);
      _suppressor.note(charId, _unreadCounts[charId] ?? 0);
      _unreadCounts.remove(charId);
      notifyListeners();
    } catch (_) {}
  }
}