import "dart:convert";
import "../utils/service_l10n.dart";
import "../utils/app_lang.dart";

/// 通知引擎：前后台共用的纯逻辑（无 UI / 无网络依赖）。
/// 收敛目标：轮询解析、增量比对、防抖、DND 判断只实现一份，
/// 前台（NotificationService）与后台（BackgroundPollingService）共享。
class UnreadEngine {
  /// 解析服务端 unread 列表 → character_id -> count（count>0）
  static Map<int, int> parseUnread(List<dynamic> list) {
    final result = <int, int>{};
    for (final item in list) {
      if (item is! Map) continue;
      final cid = item["character_id"] as int?;
      final count = item["count"] as int? ?? 0;
      if (cid != null && count > 0) result[cid] = count;
    }
    return result;
  }

  /// 增量比对：返回新增未读的 (character_id, 新count) 列表（cur 中比 prev 多的）
  static List<UnreadDiff> diffNewUnread(Map<int, int> prev, Map<int, int> cur) {
    final result = <UnreadDiff>[];
    for (final entry in cur.entries) {
      final prevCount = prev[entry.key] ?? 0;
      if (entry.value > prevCount) result.add(UnreadDiff(entry.key, entry.value));
    }
    return result;
  }

  /// 免打扰时段判断（与 DndSettings 同口径，集中实现避免双轨偏差）
  static bool isInDndPeriod(Map<String, dynamic> settings) {
    if (!(settings["enabled"] as bool? ?? false)) return false;
    final now = DateTime.now();
    final currentMinutes = now.hour * 60 + now.minute;
    final startMinutes =
        (settings["startHour"] as int) * 60 + (settings["startMinute"] as int);
    final endMinutes =
        (settings["endHour"] as int) * 60 + (settings["endMinute"] as int);
    if (startMinutes <= endMinutes) {
      return currentMinutes >= startMinutes && currentMinutes < endMinutes;
    }
    return currentMinutes >= startMinutes || currentMinutes < endMinutes;
  }
}

/// 一次新增未读（角色 + 最新未读数）
class UnreadDiff {
  final int characterId;
  final int count;
  const UnreadDiff(this.characterId, this.count);
}

/// 新增未读事件（后台 isolate 组装完整信息，前台直接消费，零网络）
class UnreadEvent {
  final int characterId;
  final int sessionId;
  final int count;
  final String title;
  final String content;
  const UnreadEvent({
    required this.characterId,
    required this.sessionId,
    required this.count,
    required this.title,
    required this.content,
  });

  Map<String, dynamic> toJson() => {
        "characterId": characterId,
        "sessionId": sessionId,
        "count": count,
        "title": title,
        "content": content,
      };

  static UnreadEvent? fromJson(Object? raw, ServiceL10n l10n) {
    if (raw is! Map) return null;
    final cid = raw["characterId"] as int?;
    final sid = raw["sessionId"] as int?;
    final cnt = raw["count"] as int?;
    final title = raw["title"] as String? ?? l10n.aiFriend;
    final content = raw["content"] as String? ?? "";
    if (cid == null || sid == null || cnt == null) return null;
    return UnreadEvent(
        characterId: cid, sessionId: sid, count: cnt, title: title, content: content);
  }
}

/// 同角色弹窗防抖（10 秒内不重复），前后台共用
class NotifyDebouncer {
  final Duration _interval;
  final Map<int, DateTime> _lastNotifiedAt = {};

  NotifyDebouncer({Duration interval = const Duration(seconds: 10)})
      : _interval = interval;

  bool shouldAllow(int charId) {
    final now = DateTime.now();
    final last = _lastNotifiedAt[charId];
    if (last != null && now.difference(last) < _interval) return false;
    _lastNotifiedAt[charId] = now;
    return true;
  }

  void clear(int charId) => _lastNotifiedAt.remove(charId);
}

/// prefs 键统一管理：后台 isolate 写、前台主 isolate 读（跨 isolate 通信）。
class NotifyPrefs {
  static const String snapshotKey = "unread_snapshot";
  static const String snapshotAtKey = "unread_snapshot_at";
  static const String newEventKey = "unread_new";
  static const String newEventAtKey = "unread_new_at";
  static const String lastPollAtKey = "unread_last_poll_at";

  /// 快照 → JSON 字符串（charId -> count）。
  /// 注意：jsonEncode 要求 Map key 为 String，int key 会抛
  /// "Converting object to an encodable object failed"，必须显式转 String。
  static String encodeSnapshot(Map<int, int> counts) =>
      jsonEncode(counts.map((k, v) => MapEntry(k.toString(), v)));

  static Map<int, int> decodeSnapshot(String? raw) {
    if (raw == null || raw.isEmpty) return {};
    try {
      final map = jsonDecode(raw) as Map<String, dynamic>;
      final result = <int, int>{};
      for (final e in map.entries) {
        result[int.parse(e.key)] = (e.value as num).toInt();
      }
      return result;
    } catch (_) {
      return {};
    }
  }

  /// 新增未读事件列表 → JSON 字符串
  static String encodeEvent(List<UnreadEvent> events) =>
      jsonEncode(events.map((e) => e.toJson()).toList());

  static Future<List<UnreadEvent>> decodeEvent(String? raw) async {
    if (raw == null || raw.isEmpty) return [];
    try {
      final l10n = ServiceL10n(await appLang());
      final list = jsonDecode(raw) as List<dynamic>;
      return list.map((j) => UnreadEvent.fromJson(j, l10n)).whereType<UnreadEvent>().toList();
    } catch (_) {
      return [];
    }
  }
}