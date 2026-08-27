import "dart:async";
import "package:flutter/material.dart";
import "package:geolocator/geolocator.dart";
import "../../services/notification_service.dart";
import "../../utils/beijing_time.dart";
import "../../services/phone_perception_service.dart";
import "../../services/api_client.dart";
import "../../widgets/privacy_lock_view.dart";
import "../settings/notification_whitelist_screen.dart";
import "shizuku_screen.dart";
import "workflow_screen.dart";
import "../../services/shizuku_service.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "package:ai_companion/theme/tokens.dart";

/// 手机感知设置页（AI 走出沙箱 Phase 1）：总开关 + 逐项授权 + 权限引导 + 历史记录
class PhonePerceptionScreen extends StatefulWidget {
  const PhonePerceptionScreen({super.key});

  @override
  State<PhonePerceptionScreen> createState() => _PhonePerceptionScreenState();
}

class _PhonePerceptionScreenState extends State<PhonePerceptionScreen> with WidgetsBindingObserver {
  bool _enabled = false;
  bool _screenOn = false;
  bool _clipboardOn = false;
  bool _mediaOn = false;
  bool _mediaFilesOn = false;
  bool _notificationOn = false;
  bool _autoNotifyOn = false;
  bool _actionsOn = false;
  bool _serviceEnabled = false;
  bool _notifServiceEnabled = false;
  bool _historyLoading = false;
  List<Map<String, dynamic>> _history = [];
  bool _showHistory = false;
  Map<String, dynamic>? _privacyStatus; // 小手机锁状态（characterId=0 由服务端按最近互动角色解析）
  bool _phoneUnlocked = false;
  bool _locationEnabled = false;
  bool _locationGpsEnabled = false;
  bool _locationFollow = false;
  String _userLocation = "";
  String _aiLocation = "";
  double? _locationLat;
  double? _locationLng;
  String _locationCity = "";
  bool _usageStatsOn = false;
  bool _usageStatsGranted = false;
  bool _pendingUsageGrant = false; // 跳转系统「使用情况访问」等待用户授权后自动重查
  Timer? _usageTimer;
  bool _shizukuServer = false;
  bool _shizukuGranted = false;
  bool _shizukuBusy = false;
  String _shizukuSnapshot = "";
  bool _expandScreen = false; // 读屏子项（剪贴板/相册/媒体文件）
  bool _expandActions = false; // 模拟操作子项（查看节点）
  bool _expandNotif = false; // 通知读取子项（主动提及/白名单）
  bool _expandLocation = false; // 位置信息子项
  Map<String, dynamic> _health = {}; // R5：统一健康检测
  bool _batteryOk = false; // R4：电池白名单

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    NotificationService().setActiveScreen(ActiveScreen.other);
    _load();
    _loadPrivacyStatus();
    _reportTimezone();
    _loadLocation();
    _loadUsageStatsState();
    _loadShizukuState();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _usageTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 从系统「使用情况访问」授权页返回时自动重查并开启，无需用户再点一次开关
    if (state == AppLifecycleState.resumed && _pendingUsageGrant) {
      _autoEnableUsageStatsAfterReturn();
    }
    // R4：从电池优化设置返回时刷新状态
    if (state == AppLifecycleState.resumed) {
      _loadHealth();
    }
  }

  /// 小手机隐私上锁状态（无角色上下文：服务端按最近互动角色解析）
  Future<void> _loadPrivacyStatus() async {
    try {
      final s = await ApiClient().getPrivacyStatus(0, "phone");
      if (mounted) setState(() => _privacyStatus = s);
    } catch (_) {}
  }

  Future<void> _load() async {
    final prefsEnabled = await PhonePerceptionService.isEnabled();
    final screen = await PhonePerceptionService.subEnabled(PhonePerceptionService.screenKey);
    final clip = await PhonePerceptionService.subEnabled(PhonePerceptionService.clipboardKey);
    final media = await PhonePerceptionService.subEnabled(PhonePerceptionService.mediaKey);
    final mediaFiles = await PhonePerceptionService.subEnabled(PhonePerceptionService.mediaFilesKey);
    final notif = await PhonePerceptionService.subEnabled(PhonePerceptionService.notificationKey);
    final autoNotif = await PhonePerceptionService.subEnabled(PhonePerceptionService.autoNotifyKey);
    final actions = await PhonePerceptionService.isActionsEnabled();
    final status = await PhonePerceptionService.getScreenStatus();
    final notifOk = await PhonePerceptionService.isNotificationAccessEnabled();
    if (!mounted) return;
    setState(() {
      _enabled = prefsEnabled;
      _screenOn = screen;
      _clipboardOn = clip;
      _mediaOn = media;
      _mediaFilesOn = mediaFiles;
      _notificationOn = notif;
      _autoNotifyOn = autoNotif;
      _actionsOn = actions;
      _serviceEnabled = (status["serviceEnabled"] as bool? ?? false);
      _notifServiceEnabled = notifOk;
    });
    // R5/R4：健康检测 + 电池白名单
    _loadHealth();
  }

  Future<void> _loadHealth() async {
    final health = await PhonePerceptionService.getServiceHealth();
    final batteryOk = await PhonePerceptionService.isIgnoringBatteryOptimizations();
    if (mounted) {
      setState(() {
        _health = health;
        _batteryOk = batteryOk;
      });
    }
  }

  bool get _shizukuReady => _shizukuServer && _shizukuGranted;

  Future<void> _loadShizukuState() async {
    final st = await ShizukuService.status();
    if (mounted) {
      setState(() {
        _shizukuServer = st["serverRunning"] == true;
        _shizukuGranted = st["permissionGranted"] == true;
      });
    }
  }

  /// Shizuku 授权下采集系统状态（前台应用/屏幕/电池/网络/勿扰/设备）并上报 AI
  Future<void> _collectShizuku() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _shizukuBusy = true);
    final r = await ShizukuService.getSystemSnapshot();
    final data = Map<String, dynamic>.from(r["data"] as Map? ?? {});
    final text = ShizukuService.formatSnapshot(data);
    final ok = await PhonePerceptionService.uploadSnapshot(text, "shizuku_system");
    if (!mounted) return;
    setState(() {
      _shizukuBusy = false;
      _shizukuSnapshot = ok ? text : l10n.ppShizukuUploadFailed(text);
    });
  }

  Widget _shizukuDot(bool ok, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: ok ? AppColors.success : AppColors.separator,
          ),
        ),
        const SizedBox(width: 5),
        Text(label, style: const TextStyle(fontSize: 12, color: AppColors.textMuted)),
      ],
    );
  }

  Future<void> _toggleEnabled(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _enabled = v);
    await PhonePerceptionService.setEnabled(v);
    // 关闭总开关时同时停用全部子项，避免残留授权
    if (!v) {
      setState(() {
        _screenOn = false;
        _clipboardOn = false;
        _mediaOn = false;
        _mediaFilesOn = false;
        _notificationOn = false;
      });
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.screenKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.clipboardKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.notificationKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.autoNotifyKey, false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
      _usageTimer?.cancel();
      setState(() {
        _autoNotifyOn = false;
        _usageStatsOn = false;
      });
    }
    _showSnack(v ? l10n.ppEnabledOn : l10n.ppEnabledOff);
  }

  Future<void> _toggleScreen(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      await PhonePerceptionService.openAccessibilitySettings();
      _showSnack(l10n.ppOpenAccessibility);
    }
    setState(() => _screenOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.screenKey, v);
    await _refreshServiceState();
  }

  Future<void> _loadUsageStatsState() async {
    final on = await PhonePerceptionService.subEnabled(PhonePerceptionService.usageStatsKey);
    final granted = await PhonePerceptionService.isUsageStatsEnabled();
    if (mounted) {
      setState(() {
        _usageStatsOn = on;
        _usageStatsGranted = granted;
      });
    }
    if (on) {
      _startUsageTimer();
    }
  }

  void _startUsageTimer() {
    _usageTimer?.cancel();
    _usageTimer = Timer.periodic(const Duration(minutes: 30), (_) {
      PhonePerceptionService.uploadUsageStats();
    });
  }

  /// 从系统授权页返回后调用：已授权则自动开启并上报
  Future<void> _autoEnableUsageStatsAfterReturn() async {
    final l10n = AppLocalizations.of(context)!;
    _pendingUsageGrant = false;
    if (!mounted || !_enabled || _usageStatsOn) return;
    final granted = await PhonePerceptionService.isUsageStatsEnabled();
    if (!granted || !mounted) {
      if (mounted) _showSnack(l10n.ppUsageNotGranted);
      return;
    }
    setState(() {
      _usageStatsOn = true;
      _usageStatsGranted = true;
    });
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, true);
    final content = await PhonePerceptionService.uploadUsageStats();
    _startUsageTimer();
    if (mounted) {
      _showSnack(content != null ? l10n.ppUsageGrantedWith(content) : l10n.ppUsageGrantedEmpty);
    }
  }

  Future<void> _toggleUsageStats(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.isUsageStatsEnabled();
      if (!granted) {
        // 系统「使用情况访问」特殊权限：引导跳转，返回时自动重查（_pendingUsageGrant）
        _pendingUsageGrant = true;
        await PhonePerceptionService.openUsageAccessSettings();
        _showSnack(l10n.ppUsageOpenSettings);
        if (mounted) setState(() => _usageStatsOn = false);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
        return;
      }
      setState(() {
        _usageStatsOn = true;
        _usageStatsGranted = true;
      });
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, true);
      final content = await PhonePerceptionService.uploadUsageStats();
      _showSnack(content != null ? l10n.ppUsageEnabledWith(content) : l10n.ppUsageEnabledEmpty);
      _startUsageTimer();
    } else {
      _usageTimer?.cancel();
      setState(() => _usageStatsOn = false);
      await PhonePerceptionService.setSubEnabled(PhonePerceptionService.usageStatsKey, false);
      _showSnack(l10n.ppUsageDisabled);
    }
  }

  Future<void> _toggleNotification(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      await PhonePerceptionService.openNotificationSettings();
      _showSnack(l10n.ppOpenNotification);
    }
    setState(() => _notificationOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.notificationKey, v);
    final ok = await PhonePerceptionService.isNotificationAccessEnabled();
    if (mounted) setState(() => _notifServiceEnabled = ok);
  }

  Future<void> _toggleMedia(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.requestMediaPermission();
      if (!granted && mounted) {
        setState(() => _mediaOn = false);
        _showSnack(l10n.ppMediaDenied);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, false);
        await PhonePerceptionService.openAppSettings();
        return;
      }
    }
    setState(() => _mediaOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaKey, v);
  }

  Future<void> _toggleMediaFiles(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final granted = await PhonePerceptionService.requestMediaFilesPermission();
      if (!granted && mounted) {
        setState(() => _mediaFilesOn = false);
        _showSnack(l10n.ppMediaFilesDenied);
        await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, false);
        await PhonePerceptionService.openAppSettings();
        return;
      }
    }
    setState(() => _mediaFilesOn = v);
    await PhonePerceptionService.setSubEnabled(PhonePerceptionService.mediaFilesKey, v);
  }

  Future<void> _refreshServiceState() async {
    final status = await PhonePerceptionService.getScreenStatus();
    if (!mounted) return;
    setState(() => _serviceEnabled = (status["serviceEnabled"] as bool? ?? false));
  }

  bool _isPhoneLocked() {
    final s = _privacyStatus;
    if (s == null) return false;
    if (_phoneUnlocked) return false;
    return s["enabled"] == true && s["locked"] == true;
  }

  Future<void> _loadHistory() async {
    setState(() => _historyLoading = true);
    final list = await PhonePerceptionService.fetchHistory();
    if (!mounted) return;
    setState(() {
      _history = list;
      _historyLoading = false;
    });
  }

  Future<void> _collectNow() async {
    final l10n = AppLocalizations.of(context)!;
    final r = await PhonePerceptionService.collectAndUpload();
    if (!mounted) return;
    final content = (r["content"] as String? ?? "").trim();
    final preview = content.length > 40 ? "${content.substring(0, 40)}..." : content;
    final msg = switch (r["status"]) {
      "ok" => l10n.ppCollectedWith(preview),
      "disabled" => l10n.ppCollectDisabled,
      "no_sources" => l10n.ppCollectNoSources,
      "empty" => l10n.ppCollectEmpty,
      "network_error" => l10n.ppCollectNetworkError,
      _ => l10n.ppCollectDone,
    };
    _showSnack(msg);
  }

  Future<void> _clearAll() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await PhonePerceptionService.clearAll();
    _showSnack(ok ? l10n.ppClearedAll : l10n.ppClearFailed);
    if (ok) {
      setState(() => _history = []);
    }
  }


  String get _userLocationDisplay {
    final l10n = AppLocalizations.of(context)!;
    if (_locationGpsEnabled) {
      if (_locationCity.isNotEmpty) return l10n.ppLocCityLocated(_locationCity);
      if (_locationLat != null && _locationLng != null) {
        return l10n.ppLocCoordsLocated(_locationLat!.toStringAsFixed(4), _locationLng!.toStringAsFixed(4));
      }
      return l10n.ppLocLocating;
    }
    return _userLocation.isEmpty ? l10n.ppLocUnset : _userLocation;
  }

  String get _aiLocationDisplay {
    final l10n = AppLocalizations.of(context)!;
    if (_locationFollow) {
      return l10n.ppLocFollowUser(_userLocation.isEmpty ? l10n.ppLocNotSet : _userLocation);
    }
    return _aiLocation.isEmpty ? l10n.ppLocUnset : _aiLocation;
  }

  String get _locationSubtitle {
    final l10n = AppLocalizations.of(context)!;
    final parts = <String>[];
    final uloc = _locationCity.isNotEmpty ? _locationCity : _userLocation;
    if (uloc.isNotEmpty) parts.add(l10n.ppLocUser(uloc));
    if (_aiLocation.isNotEmpty) parts.add(l10n.ppLocAi(_aiLocation));
    if (_locationFollow) parts.add(l10n.ppLocFollow);
    if (_locationGpsEnabled) parts.add(l10n.ppLocGpsOn);
    return parts.isEmpty ? l10n.ppLocUnsetExpand : parts.join(" · ");
  }

  /// 上报手机本地时区（分钟偏移，如 480=UTC+8），供角色时间感知；失败静默
  Future<void> _reportTimezone() async {
    try {
      final off = DateTime.now().timeZoneOffset.inMinutes;
      await ApiClient().updateUserLocation(timezoneOffsetMinutes: off);
    } catch (_) {}
  }

  Future<void> _loadLocation() async {
    try {
      final loc = await ApiClient().getUserLocation();
      if (!mounted) return;
      setState(() {
        _locationEnabled = loc["location_enabled"] == true;
        _locationGpsEnabled = loc["location_gps_enabled"] == true;
        _userLocation = (loc["user_location"] as String? ?? "").trim();
        _aiLocation = (loc["ai_location"] as String? ?? "").trim();
        _locationFollow = loc["location_follow"] == true;
        _locationLat = (loc["location_lat"] as num?)?.toDouble();
        _locationLng = (loc["location_lng"] as num?)?.toDouble();
        _locationCity = (loc["location_city"] as String? ?? "").trim();
      });
      // GPS 已开启但尚无坐标（旧版本开启的开关）：自动尝试定位一次
      if (_locationGpsEnabled && _locationLat == null && _locationLng == null) {
        _locateAndReport();
      }
    } catch (_) {}
  }

  Future<void> _toggleLocation(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _locationEnabled = v);
    await ApiClient().updateUserLocation(locationEnabled: v);
    if (!v) {
      setState(() {
        _locationGpsEnabled = false;
        _locationFollow = false;
        _userLocation = "";
        _aiLocation = "";
      });
    }
    _showSnack(v ? l10n.ppLocEnabledOn : l10n.ppLocEnabledOff);
  }

  Future<void> _toggleLocationGps(bool v) async {
    final l10n = AppLocalizations.of(context)!;
    if (v) {
      final ok = await _locateAndReport();
      if (!ok) {
        setState(() => _locationGpsEnabled = false);
        await ApiClient().updateUserLocation(locationGpsEnabled: false);
        return;
      }
      setState(() => _locationGpsEnabled = true);
      _showSnack(l10n.ppLocGpsEnabledWith(_userLocationDisplay));
    } else {
      setState(() => _locationGpsEnabled = false);
      await ApiClient().updateUserLocation(locationGpsEnabled: false);
      _showSnack(l10n.ppLocGpsDisabled);
    }
  }

  /// 请求定位权限 → 获取经纬度 → 上报后端（后端反查城市名）；成功 true
  Future<bool> _locateAndReport() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final enabled = await Geolocator.isLocationServiceEnabled();
      if (!enabled) {
        _showSnack(l10n.ppLocServiceOff);
        return false;
      }
      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
        _showSnack(perm == LocationPermission.deniedForever
            ? l10n.ppLocDeniedForever
            : l10n.ppLocNoPermission);
        return false;
      }
      // 优先 Android 原生 LocationManager（GPS+网络基站定位，不依赖 Google 定位服务），
      // 避免国行机型无 Google 网络时 FusedLocationProvider 超时导致定位失败
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: AndroidSettings(
          accuracy: LocationAccuracy.medium,
          timeLimit: Duration(seconds: 20),
          forceLocationManager: true,
        ),
      );
      await ApiClient().updateUserLocation(
        locationGpsEnabled: true,
        locationLat: pos.latitude,
        locationLng: pos.longitude,
      );
      await _loadLocation();
      return true;
    } catch (e) {
      _showSnack(l10n.ppLocFailed('$e'));
      return false;
    }
  }

  Future<void> _toggleLocationFollow(bool v) async {
    setState(() {
      _locationFollow = v;
      if (v) _aiLocation = _userLocation; // 位置跟随：AI 位置与用户相同
    });
    await ApiClient().updateUserLocation(
      locationFollow: v,
      aiLocation: v ? _userLocation : _aiLocation,
    );
  }

  Future<void> _editLocation({required bool isUser}) async {
    final l10n = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: isUser ? _userLocation : _aiLocation);
    final value = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(isUser ? l10n.ppLocSetUser : l10n.ppLocSetAi),
        content: TextField(
          controller: controller,
          maxLength: 50,
          decoration: InputDecoration(hintText: l10n.ppLocHint, counterText: ""),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: Text(l10n.save),
          ),
        ],
      ),
    );
    if (value == null || value.isEmpty) return;
    if (isUser) {
      setState(() {
        _userLocation = value;
        if (_locationFollow) _aiLocation = value;
      });
      await ApiClient().updateUserLocation(
        userLocation: value,
        aiLocation: _locationFollow ? value : null,
      );
    } else {
      setState(() => _aiLocation = value);
      await ApiClient().updateUserLocation(aiLocation: value);
    }
  }

  void _showSnack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  String _shortTime(String raw) {
    final r = formatBeijingTime(raw).replaceAll("T", " ").trim();
    return r.length >= 16 ? r.substring(0, 16) : r;
  }

  String _sourceLabel(String s) {
    final l10n = AppLocalizations.of(context)!;
    switch (s) {
      case "accessibility":
        return l10n.ppSourceScreen;
      case "clipboard":
        return l10n.ppSourceClipboard;
      case "media":
        return l10n.ppSourceMedia;
      case "notification":
        return l10n.ppSourceNotification;
      default:
        return s;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    const subColor = AppColors.textSecondary;
    const iconColor = AppColors.accent;

    Widget sw({
      required IconData icon,
      required String title,
      required String subtitle,
      required bool value,
      required ValueChanged<bool>? onChanged,
      Color? color,
    }) {
      final enabled = onChanged != null;
      return SwitchListTile(
        secondary: Icon(icon,
            size: 22,
            color: enabled ? (color ?? iconColor) : scheme.onSurface.withValues(alpha: 0.38)),
        title: Text(title,
            style: TextStyle(
                fontSize: 15,
                color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: subColor)),
        value: value,
        onChanged: onChanged,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14),
      );
    }

    Widget nav({
      required IconData icon,
      required String title,
      required String subtitle,
      VoidCallback? onTap,
      bool enabled = true,
      Color? color,
      Widget? trailing,
    }) {
      return ListTile(
        leading: Icon(icon, size: 22, color: color ?? iconColor),
        title: Text(title, style: TextStyle(fontSize: 15, color: scheme.onSurface)),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: subColor)),
        enabled: enabled,
        trailing: trailing ?? const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
        onTap: onTap,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14),
      );
    }

    /// 可折叠父项：点击行展开/收起子项，右侧开关独立控制
    Widget foldParent({
      required IconData icon,
      required String title,
      required String subtitle,
      required bool value,
      required ValueChanged<bool>? onChanged,
      required bool expanded,
      required VoidCallback onToggle,
      Color? color,
    }) {
      final enabled = onChanged != null;
      return ListTile(
        leading: Icon(icon,
            size: 22,
            color: enabled ? (color ?? iconColor) : scheme.onSurface.withValues(alpha: 0.38)),
        title: Text(title,
            style: TextStyle(
                fontSize: 15,
                color: enabled ? scheme.onSurface : scheme.onSurface.withValues(alpha: 0.38))),
        subtitle: Text(subtitle, style: const TextStyle(fontSize: 11, color: subColor)),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(expanded ? Icons.expand_less : Icons.expand_more,
                size: 20, color: AppColors.separator),
            Switch(value: value, onChanged: onChanged),
          ],
        ),
        onTap: onToggle,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14),
      );
    }

    Widget group(String? title, List<Widget> children) {
      return Padding(
        padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (title != null)
              Padding(
                padding: const EdgeInsets.only(left: 16, bottom: 6),
                child: Text(title,
                    style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: subColor)),
              ),
            Container(
              decoration: BoxDecoration(
                color: scheme.surface,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(children: children),
            ),
          ],
        ),
      );
    }

    Widget div() => Container(
          height: 0.5,
          margin: const EdgeInsets.only(left: 46),
          color: Theme.of(context).dividerColor,
        );

    /// R5：健康状态灯 tile
    Widget healthTile(IconData icon, String title, bool ok,
        {String? sub, VoidCallback? onTap}) {
      return ListTile(
        leading: Icon(icon, size: 22, color: ok ? Colors.green : Colors.orange),
        title: Text(title, style: TextStyle(fontSize: 15, color: scheme.onSurface)),
        subtitle: sub != null
            ? Text(sub, style: const TextStyle(fontSize: 11, color: subColor))
            : null,
        trailing: onTap != null
            ? const Icon(Icons.chevron_right, size: 18, color: AppColors.separator)
            : Icon(ok ? Icons.check_circle : Icons.warning_amber,
                size: 18, color: ok ? Colors.green : Colors.orange),
        onTap: onTap,
        contentPadding: const EdgeInsets.symmetric(horizontal: 14),
      );
    }

    return Scaffold(
      appBar: AppBar(title: Text(l10n.phonePerception)),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 24),
        children: [
          // 总开关
          group(null, [
            sw(
              icon: Icons.visibility_outlined,
              title: l10n.phonePerception,
              subtitle: _enabled ? l10n.ppSubtitleOn : l10n.ppSubtitleOff,
              value: _enabled,
              onChanged: _toggleEnabled,
            ),
          ]),
          // R5：服务健康状态灯
          if (_health.isNotEmpty)
            group('运行状态', [
              healthTile(Icons.accessibility_new, '无障碍服务',
                  _health['accessible'] == true,
                  sub: _health['accessibleInstanceAlive'] == true ? '已连接' : '系统已开但服务未连接'),
              div(),
              healthTile(Icons.notifications_outlined, '通知读取',
                  _health['notification'] == true,
                  sub: _health['notificationConnected'] == true ? '已连接' : '系统已开但未连接'),
              div(),
              healthTile(Icons.shield_outlined, 'Shizuku',
                  _health['shizuku'] == true,
                  sub: _health['shizukuRunning'] == true
                      ? (_health['shizukuGranted'] == true ? '已授权' : '未授权')
                      : '未运行'),
              div(),
              healthTile(Icons.bar_chart, '使用情况访问',
                  _health['usageStats'] == true),
              div(),
              healthTile(Icons.battery_saver, '电池优化白名单',
                  _batteryOk,
                  sub: _batteryOk ? '已加入' : '未加入（可能导致后台断开）',
                  onTap: _batteryOk ? null : () async {
                    await PhonePerceptionService.requestIgnoreBatteryOptimizations();
                    Future.delayed(const Duration(seconds: 2), _loadHealth);
                  }),
            ]),
          // 采集项
          group(l10n.ppGroupSources, [
            foldParent(
              icon: Icons.screen_share_outlined,
              title: l10n.ppScreenTitle,
              subtitle: _serviceEnabled ? l10n.ppScreenRunning : l10n.ppScreenOff,
              value: _screenOn,
              onChanged: _enabled ? _toggleScreen : null,
              expanded: _expandScreen,
              onToggle: () => setState(() => _expandScreen = !_expandScreen),
            ),
            if (_expandScreen) ...[
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.content_paste,
                  title: l10n.ppClipboard,
                  subtitle: l10n.ppClipboardSub,
                  value: _clipboardOn,
                  onChanged: _enabled
                      ? (v) async {
                          setState(() => _clipboardOn = v);
                          await PhonePerceptionService.setSubEnabled(PhonePerceptionService.clipboardKey, v);
                        }
                      : null,
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.photo_library_outlined,
                  title: l10n.ppMediaTitle,
                  subtitle: l10n.ppMediaSub,
                  value: _mediaOn,
                  onChanged: _enabled ? _toggleMedia : null,
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.video_library_outlined,
                  title: l10n.ppMediaFilesTitle,
                  subtitle: l10n.ppMediaFilesSub,
                  value: _mediaFilesOn,
                  onChanged: _enabled ? _toggleMediaFiles : null,
                ),
              ),
            ],
            div(),
            sw(
              icon: Icons.bar_chart_outlined,
              title: l10n.ppUsageStatsTitle,
              subtitle: _usageStatsGranted
                  ? l10n.ppUsageStatsGranted
                  : l10n.ppUsageStatsNotGranted,
              value: _usageStatsOn,
              onChanged: _enabled ? _toggleUsageStats : null,
            ),
            div(),
            foldParent(
              icon: Icons.touch_app_outlined,
              title: l10n.ppActionsTitle,
              subtitle: _actionsOn
                  ? l10n.ppActionsOn
                  : l10n.ppActionsOff,
              value: _actionsOn,
              onChanged: _enabled && _screenOn
                  ? (v) async {
                      setState(() => _actionsOn = v);
                      await PhonePerceptionService.setActionsEnabled(v);
                    }
                  : null,
              expanded: _expandActions,
              onToggle: () => setState(() => _expandActions = !_expandActions),
            ),
            if (_expandActions) ...[
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: nav(
                  icon: Icons.account_tree_outlined,
                  title: l10n.ppWorkflowTitle,
                  subtitle: l10n.ppWorkflowSub,
                  enabled: _enabled,
                  onTap: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => const WorkflowScreen(),
                    ));
                  },
                ),
              ),
            ],
            div(),
            foldParent(
              icon: Icons.notifications_outlined,
              title: l10n.ppNotificationTitle,
              subtitle: _notifServiceEnabled
                  ? l10n.ppNotifRunning
                  : l10n.ppNotifOff,
              value: _notificationOn,
              onChanged: _enabled ? _toggleNotification : null,
              expanded: _expandNotif,
              onToggle: () => setState(() => _expandNotif = !_expandNotif),
            ),
            if (_expandNotif) ...[
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.auto_awesome_outlined,
                  title: l10n.ppAutoNotifyTitle,
                  subtitle: l10n.ppAutoNotifySub,
                  value: _autoNotifyOn,
                  onChanged: _enabled && _notificationOn
                      ? (v) async {
                          setState(() => _autoNotifyOn = v);
                          await PhonePerceptionService.setSubEnabled(PhonePerceptionService.autoNotifyKey, v);
                        }
                      : null,
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: nav(
                  icon: Icons.notifications_none_outlined,
                  title: l10n.ppWhitelistTitle,
                  subtitle: l10n.ppWhitelistSub,
                  enabled: _enabled && _notificationOn,
                  onTap: () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => const NotificationWhitelistScreen()),
                    );
                  },
                ),
              ),
            ],
            div(),
            nav(
              icon: Icons.security_outlined,
              title: l10n.ppShizukuTitle,
              subtitle: l10n.ppShizukuSub,
              enabled: _enabled,
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const ShizukuScreen()),
              ),
            ),
            div(),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 4, 14, 4),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      _shizukuDot(_shizukuServer, l10n.ppShizukuServer),
                      const SizedBox(width: 14),
                      _shizukuDot(_shizukuGranted, l10n.ppShizukuGranted),
                      const Spacer(),
                      Text(
                        _shizukuReady ? l10n.ppReady : l10n.ppNotReady,
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                          color: _shizukuReady ? AppColors.success : subColor,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _enabled && _shizukuReady && !_shizukuBusy
                          ? _collectShizuku
                          : null,
                      icon: const Icon(Icons.sensors, size: 18),
                      label: Text(_shizukuBusy ? l10n.ppCollecting : l10n.ppCollectShizuku),
                    ),
                  ),
                  if (_shizukuSnapshot.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        _shizukuSnapshot,
                        style: const TextStyle(fontSize: 12, color: subColor, height: 1.5),
                      ),
                    ),
                ],
              ),
            ),
          ]),
          // 位置
          group(l10n.ppGroupLocation, [
            foldParent(
              icon: Icons.location_on_outlined,
              title: l10n.ppLocationTitle,
              subtitle: _locationEnabled
                  ? (_locationSubtitle.isNotEmpty
                      ? _locationSubtitle
                      : l10n.ppLocSubtitleOn)
                  : l10n.ppLocSubtitleOff,
              value: _locationEnabled,
              onChanged: _toggleLocation,
              expanded: _expandLocation,
              onToggle: () => setState(() => _expandLocation = !_expandLocation),
            ),
            if (_expandLocation) ...[
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.gps_fixed,
                  title: l10n.ppLocGpsTitle,
                  subtitle: _locationGpsEnabled ? l10n.ppLocGpsOnSub : l10n.ppLocGpsOffSub,
                  value: _locationGpsEnabled,
                  onChanged: _locationEnabled ? _toggleLocationGps : null,
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: nav(
                  icon: Icons.person_pin_circle_outlined,
                  title: l10n.ppLocUserTitle,
                  subtitle: _userLocationDisplay,
                  enabled: _locationEnabled && !_locationGpsEnabled,
                  trailing: const Icon(Icons.edit_outlined, size: 20, color: AppColors.separator),
                  onTap: () => _editLocation(isUser: true),
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: nav(
                  icon: Icons.smart_toy_outlined,
                  title: l10n.ppLocAiTitle,
                  subtitle: _aiLocationDisplay,
                  enabled: _locationEnabled && !_locationFollow,
                  trailing: const Icon(Icons.edit_outlined, size: 20, color: AppColors.separator),
                  onTap: () => _editLocation(isUser: false),
                ),
              ),
              div(),
              Padding(
                padding: const EdgeInsets.only(left: 24),
                child: sw(
                  icon: Icons.sync_alt,
                  title: l10n.ppLocFollowTitle,
                  subtitle: _locationFollow ? l10n.ppLocFollowOnSub : l10n.ppLocFollowOffSub,
                  value: _locationFollow,
                  onChanged: _locationEnabled ? _toggleLocationFollow : null,
                ),
              ),
            ],
          ]),
          // 隐私说明
          group(l10n.ppGroupPrivacy, [
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
              child: Text(
                l10n.ppPrivacyNote,
                style: const TextStyle(fontSize: 12, color: AppColors.textMuted, height: 1.6),
              ),
            ),
          ]),
          // 操作与记录
          group(l10n.ppGroupActions, [
            nav(
              icon: Icons.my_location,
              title: l10n.ppCollectNowTitle,
              subtitle: l10n.ppCollectNowSub,
              color: AppColors.success,
              onTap: _collectNow,
            ),
            div(),
            nav(
              icon: Icons.history,
              title: l10n.ppHistoryTitle,
              subtitle: _history.isEmpty ? l10n.ppNoSnapshots : l10n.ppRecentCount('${_history.length}'),
              trailing: _historyLoading
                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.chevron_right, size: 18, color: AppColors.separator),
              onTap: () {
                setState(() => _showHistory = !_showHistory);
                // 小手机上锁时先展示申请面板，不直接加载历史内容
                if (_showHistory && !_isPhoneLocked()) _loadHistory();
              },
            ),
            if (_showHistory && _isPhoneLocked())
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: PrivacyLockView(
                  characterId: 0,
                  target: "phone",
                  contentName: l10n.ppLockContentName,
                  onUnlocked: () {
                    setState(() => _phoneUnlocked = true);
                    _loadHistory();
                  },
                ),
              ),
            if (_showHistory && !_isPhoneLocked())
              for (final s in _history)
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.phone_android, size: 18, color: subColor),
                  title: Text(
                    s["content"] ?? "",
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12),
                  ),
                  subtitle: Text(
                    "${_sourceLabel(s["source"] ?? "")} · ${_shortTime(s["created_at"]?.toString() ?? "")}",
                    style: const TextStyle(fontSize: 11, color: subColor),
                  ),
                  contentPadding: const EdgeInsets.symmetric(horizontal: 14),
                ),
            if (_history.isNotEmpty || _showHistory) div(),
            ListTile(
              leading: const Icon(Icons.delete_outline, color: AppColors.error),
              title: Text(l10n.ppClearAll, style: const TextStyle(fontSize: 15, color: AppColors.error)),
              contentPadding: const EdgeInsets.symmetric(horizontal: 14),
              onTap: _clearAll,
            ),
          ]),
        ],
      ),
    );
  }
}
