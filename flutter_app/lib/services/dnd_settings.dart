import "package:shared_preferences/shared_preferences.dart";
import "api_client.dart";
import "unread_engine.dart";

/// 免打扰（DND）设置：读写 SharedPreferences + 时段判断。
/// 纯静态工具，供 Flutter 层通知弹窗与免打扰设置页共用。
class DndSettings {
  static Future<Map<String, dynamic>> get() async {
    final prefs = await SharedPreferences.getInstance();
    return {
      "enabled": prefs.getBool("dnd_enabled") ?? false,
      "notificationsEnabled": prefs.getBool("notifications_enabled") ?? true,
      "startHour": prefs.getInt("dnd_start_hour") ?? 22,
      "startMinute": prefs.getInt("dnd_start_minute") ?? 0,
      "endHour": prefs.getInt("dnd_end_hour") ?? 8,
      "endMinute": prefs.getInt("dnd_end_minute") ?? 0,
    };
  }

  static Future<void> set(Map<String, dynamic> settings) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool("dnd_enabled", settings["enabled"] as bool);
    if (settings["notificationsEnabled"] != null) {
      await prefs.setBool(
          "notifications_enabled", settings["notificationsEnabled"] as bool);
    }
    await prefs.setInt("dnd_start_hour", settings["startHour"] as int);
    await prefs.setInt("dnd_start_minute", settings["startMinute"] as int);
    await prefs.setInt("dnd_end_hour", settings["endHour"] as int);
    await prefs.setInt("dnd_end_minute", settings["endMinute"] as int);
  }

  /// 同步免打扰设置到服务端（供后端状态触发等主动行为在免打扰时段内不打扰）。
  /// 失败静默（本地设置仍生效，下次保存再同步）。
  static Future<void> syncToServer() async {
    try {
      final settings = await get();
      await ApiClient().dio.put("/api/v1/auth/dnd", data: {
        "dnd_enabled": settings["enabled"],
        "notifications_enabled": settings["notificationsEnabled"],
        "start_hour": settings["startHour"],
        "start_minute": settings["startMinute"],
        "end_hour": settings["endHour"],
        "end_minute": settings["endMinute"],
      });
    } catch (_) {
      // 静默失败：下次保存时重试
    }
  }

  static bool isInDndPeriod(Map<String, dynamic> settings) =>
      UnreadEngine.isInDndPeriod(settings);
}
