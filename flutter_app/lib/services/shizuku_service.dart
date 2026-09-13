import "dart:io";
import "package:flutter/services.dart";
import "package:shared_preferences/shared_preferences.dart";

/// Shizuku 权限通道（2026-08-12）：ADB/root 启动 Shizuku 后授权，可执行系统级 shell
/// （应用列表 / 系统设置 / 模拟操作前置）。v1 提供：状态查询 / 授权请求 / shell 执行 / 应用列表。
class ShizukuService {
  static const MethodChannel _channel = MethodChannel("com.gituu.ambrace/phone_perception");

  static const String enabledKey = "pp_shizuku_enabled";

  static Future<bool> isEnabled() async =>
      (await SharedPreferences.getInstance()).getBool(enabledKey) ?? false;

  static Future<void> setEnabled(bool v) async {
    final p = await SharedPreferences.getInstance();
    await p.setBool(enabledKey, v);
  }

  /// {serverRunning, permissionGranted}
  static Future<Map<String, dynamic>> status() async {
    if (!Platform.isAndroid) return {"serverRunning": false, "permissionGranted": false};
    try {
      final r = await _channel.invokeMethod("shizukuStatus") as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (_) {
      return {"serverRunning": false, "permissionGranted": false};
    }
  }

  /// 发起授权请求（系统弹窗），返回是否已发起
  static Future<bool> requestPermission() async {
    try {
      return await _channel.invokeMethod("shizukuRequestPermission") as bool? ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 授权下执行 shell 命令 → {ok, stdout, stderr}
  static Future<Map<String, dynamic>> runShell(String command, {int timeoutMs = 15000}) async {
    try {
      final r = await _channel.invokeMethod(
              "shizukuRunShell", {"command": command, "timeout_ms": timeoutMs}) as Map? ??
          {};
      return Map<String, dynamic>.from(r);
    } catch (e) {
      return {"ok": false, "stdout": "", "stderr": "$e"};
    }
  }

  /// 已安装第三方应用列表 → {ok, packages, error}
  static Future<Map<String, dynamic>> getAppList() async {
    try {
      final r = await _channel.invokeMethod("shizukuGetAppList") as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (e) {
      return {"ok": false, "packages": <String>[], "error": "$e"};
    }
  }

  /// 已安装第三方应用（含中文名，工作流选应用用）→ {ok, apps: [{package, label}], error}
  /// 带超时：PackageManager 异常或 vivo 降级走 Shizuku shell 时耗时可能较长，避免界面一直转圈
  static Future<Map<String, dynamic>> getAppListDetailed() async {
    try {
      final r = await _channel
          .invokeMethod("getAppListDetailed")
          .timeout(const Duration(seconds: 25)) as Map? ??
          {};
      return Map<String, dynamic>.from(r);
    } catch (e) {
      return {"ok": false, "apps": <Map<String, dynamic>>[], "error": "$e"};
    }
  }

  /// 系统状态快照（只读，手机感知联动）→ {ok, data, error}
  /// data: {foregroundApp, screenOn, screenOnMs, batteryLevel, batteryCharging,
  ///        network, dnd, device, androidVersion}
  static Future<Map<String, dynamic>> getSystemSnapshot() async {
    try {
      final r = await _channel.invokeMethod("shizukuSystemSnapshot") as Map? ?? {};
      return Map<String, dynamic>.from(r);
    } catch (e) {
      return {"ok": false, "data": <String, dynamic>{}, "error": "$e"};
    }
  }

  /// 快照数据 → 自然语言文本（注入 AI 上下文用）
  /// isEn：服务层无 BuildContext，沿用 appLang() 判定（与 utils/service_l10n.dart 的 isEn 分支同口径）
  static String formatSnapshot(Map<String, dynamic> d, {required bool isEn}) {
    final parts = <String>[];
    final screenOn = d['screenOn'] == true;
    parts.add(screenOn ? (isEn ? 'Screen on' : '屏幕亮起') : (isEn ? 'Screen off' : '屏幕熄灭'));
    final onMs = d['screenOnMs'];
    if (onMs is num && onMs > 0) {
      final min = (onMs / 60000).round();
      parts.add(isEn ? 'On for $min min' : '已亮 $min 分钟');
    }
    final fg = d['foregroundApp'] as String? ?? '';
    if (fg.isNotEmpty) parts.add(isEn ? 'Foreground app: $fg' : '前台应用：$fg');
    final level = d['batteryLevel'];
    if (level is num) {
      final charging = d['batteryCharging'] == true ? (isEn ? ' charging' : ' 充电中') : '';
      parts.add(isEn ? 'Battery ${level.toInt()}%$charging' : '电池 ${level.toInt()}%$charging');
    }
    final net = d['network'] as String? ?? '';
    if (net.isNotEmpty) parts.add(isEn ? 'Network: $net' : '网络：$net');
    parts.add(isEn
        ? 'DND: ${d['dnd'] == true ? 'on' : 'off'}'
        : '勿扰：${d['dnd'] == true ? '开启' : '关闭'}');
    final dev = d['device'] as String? ?? '';
    final ver = d['androidVersion'] as String? ?? '';
    if (dev.isNotEmpty || ver.isNotEmpty) {
      parts.add(isEn
          ? 'Device: ${[dev, ver].where((e) => e.isNotEmpty).join(' / ')}'
          : '设备：${[dev, ver].where((e) => e.isNotEmpty).join(' / ')}');
    }
    return isEn
        ? 'Phone status: ${parts.join('; ')}'
        : '手机状态：${parts.join('；')}';
  }
}
