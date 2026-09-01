import 'package:flutter/foundation.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:ui' as ui;

import '../screens/weave/weave_view_mode.dart';
import '../theme/skins/skin_registry.dart';
import '../services/api_client.dart';

class SettingsProvider extends ChangeNotifier {
  String _serverUrl = '';
  String _nickname =
      ui.PlatformDispatcher.instance.locale.languageCode.startsWith('en') ? 'User' : '用户';
  String _token = '';
  String _avatarUrl = '';
  int _userId = 0;
  bool _isConnected = false;
  bool _isLoggedIn = false;
  bool _isAdmin = false;
  int? _parentId; // #68 P3 账号关联：父账号 id（NULL=独立主账号）
  bool _isSub = false; // #68 P3 账号关联：是否为子账号
  int _themeModeIndex = 0; // 0=跟随系统 1=浅色 2=深色
  int _seedColorIndex = 0; // 强调色索引
  String _skinId = SkinRegistry.defaultSkinId; // ⭐ 新增：当前皮肤 ID
  String _localeCode = 'system';
  bool _backgroundKeepalive = true;
  WeaveViewMode _weaveViewMode = WeaveViewMode.auto;
  bool _onboardingDone = false;
  // ⭐ 毛玻璃背景设置（仅 glass 皮肤生效）
  String? _glassBackgroundPath; // null = 渐变模式
  double _glassBlur = 15; // 背景图模糊度 0-30
  double _glassDim = 0.1; // 背景压暗 0-0.6
  int? _glassAuroraColor1; // 渐变起（Color 值，null=跟随 seedColor）
  int? _glassAuroraColor2; // 渐变终
  // ⭐ 全局动效/模糊开关（Phase 1，D2）：默认关闭，用户可在外观设置中开启
  bool _reduceMotion = false; // 关闭持续循环动效（浮动/脉冲等），保留必要转场与按压反馈
  bool _reduceBlur = false; // 所有 BackdropFilter 的 sigma 减半（最低 4）

  String get serverUrl => _serverUrl;
  String get nickname => _nickname;
  String get token => _token;
  String get avatarUrl => _avatarUrl;
  int get userId => _userId;
  bool get isConnected => _isConnected;
  bool get isLoggedIn => _isLoggedIn;
  bool get isAdmin => _isAdmin;
  int? get parentId => _parentId;
  bool get isSub => _isSub;
  int get themeModeIndex => _themeModeIndex;
  int get seedColorIndex => _seedColorIndex;
  String get skinId => _skinId; // ⭐ 新增
  String get localeCode => _localeCode;
  bool get backgroundKeepalive => _backgroundKeepalive;
  WeaveViewMode get weaveViewMode => _weaveViewMode;
  bool get onboardingDone => _onboardingDone;
  String? get glassBackgroundPath => _glassBackgroundPath;
  double get glassBlur => _glassBlur;
  double get glassDim => _glassDim;
  int? get glassAuroraColor1 => _glassAuroraColor1;
  int? get glassAuroraColor2 => _glassAuroraColor2;
  bool get reduceMotion => _reduceMotion;
  bool get reduceBlur => _reduceBlur;

  bool get needsOnboarding => !_onboardingDone && _serverUrl.isEmpty;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    _serverUrl = prefs.getString('server_url') ?? '';
    _nickname = prefs.getString('nickname') ??
        (ui.PlatformDispatcher.instance.locale.languageCode.startsWith('en') ? 'User' : '用户');
    _avatarUrl = prefs.getString('avatar_url') ?? '';
    _token = prefs.getString('auth_token') ?? '';
    _userId = prefs.getInt('user_id') ?? 0;
    _isLoggedIn = _token.isNotEmpty;
    _isAdmin = prefs.getBool('is_admin') ?? false;
    _themeModeIndex = prefs.getInt('theme_mode_index') ?? 0;
    _seedColorIndex = prefs.getInt('seed_color_index') ?? 0;
    _skinId = prefs.getString('skin_id') ?? SkinRegistry.defaultSkinId; // ⭐ 读取
    _localeCode = prefs.getString('locale_code') ?? 'system';
    _backgroundKeepalive = prefs.getBool('background_keepalive') ?? true;
    _weaveViewMode = WeaveViewMode.fromStorageValue(
        prefs.getString(WeaveViewMode.storageKey));
    _onboardingDone = prefs.getBool('onboarding_done') ?? false;
    _glassBackgroundPath = prefs.getString('glass_background_path');
    _glassBlur = prefs.getDouble('glass_blur') ?? 15;
    _glassDim = prefs.getDouble('glass_dim') ?? 0.1;
    _glassAuroraColor1 = prefs.getInt('glass_aurora1');
    _glassAuroraColor2 = prefs.getInt('glass_aurora2');
    _reduceMotion = prefs.getBool('reduce_motion') ?? false;
    _reduceBlur = prefs.getBool('reduce_blur') ?? false;
    notifyListeners();
  }

  Future<void> setThemeModeIndex(int index) async {
    _themeModeIndex = index;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('theme_mode_index', index);
    notifyListeners();
  }

  Future<void> setSeedColorIndex(int index) async {
    _seedColorIndex = index;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('seed_color_index', index);
    notifyListeners();
  }

  /// ⭐ 新增：切换皮肤
  Future<void> setSkinId(String id) async {
    if (_skinId == id) return;
    _skinId = id;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('skin_id', id);
    notifyListeners();
  }

  Future<void> setLocale(String code) async {
    _localeCode = code;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('locale_code', code);
    notifyListeners();
  }

  Future<void> setBackgroundKeepalive(bool value) async {
    _backgroundKeepalive = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('background_keepalive', value);
    notifyListeners();
  }

  Future<void> setWeaveViewMode(WeaveViewMode mode) async {
    if (_weaveViewMode == mode) return;
    _weaveViewMode = mode;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(WeaveViewMode.storageKey, mode.storageValue);
    notifyListeners();
  }

  Future<void> setServerUrl(String url) async {
    _serverUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
    notifyListeners();
  }

  Future<void> setOnboardingDone(bool value) async {
    if (_onboardingDone == value) return;
    _onboardingDone = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('onboarding_done', value);
    notifyListeners();
  }

  Future<void> setNickname(String name) async {
    _nickname = name;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('nickname', name);
    notifyListeners();
  }

  Future<void> setAvatarUrl(String url) async {
    _avatarUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('avatar_url', url);
    notifyListeners();
  }

  Future<void> setAuth(String token, int userId, String nickname) async {
    _token = token;
    _userId = userId;
    _nickname = nickname;
    _isLoggedIn = true;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('auth_token', token);
    await prefs.setInt('user_id', userId);
    await prefs.setString('nickname', nickname);
    notifyListeners();
  }

  Future<void> syncProfileFromServer() async {
    if (_token.isEmpty || _serverUrl.isEmpty) return;
    try {
      final r = await Dio().get(
        '$_serverUrl/api/v1/auth/profile',
        options: Options(
          headers: {"Authorization": "Bearer $_token"},
          connectTimeout: const Duration(seconds: 5),
        ),
      );
      final data = r.data as Map<String, dynamic>;
      final av = data['avatar_url'] as String? ?? "";
      if (av.isNotEmpty && av != _avatarUrl) {
        await setAvatarUrl(av);
      }
      final nick = data['nickname'] as String? ?? "";
      if (nick.isNotEmpty && nick != _nickname) {
        await setNickname(nick);
      }
      final admin = data['is_admin'];
      if (admin is bool) {
        await _setAdmin(admin);
      }
      final pid = data['parent_id'];
      if (pid == null || pid is int) {
        _parentId = pid as int?;
      }
      final isSub = data['is_sub'];
      if (isSub is bool) {
        _isSub = isSub;
      }
      notifyListeners();
    } catch (_) {}
  }

  Future<void> logout() async {
    _token = '';
    _userId = 0;
    _isLoggedIn = false;
    _parentId = null;
    _isSub = false;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('auth_token');
    await prefs.remove('user_id');
    await prefs.remove('avatar_url');
    // B4（2026-09-01 审查）：登出时同步清掉 ApiClient 单例残留的认证头
    try {
      ApiClient().clearAuth();
    } catch (_) {}
    notifyListeners();
  }

  void setConnected(bool value) {
    _isConnected = value;
    notifyListeners();
  }

  Future<void> _setAdmin(bool value) async {
    if (_isAdmin == value) return;
    _isAdmin = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('is_admin', value);
    notifyListeners();
  }

  Future<bool> testConnection() async {
    try {
      final r = await Dio().get('$_serverUrl/api/v1/system/health',
          options: Options(connectTimeout: Duration(seconds: 3)));
      _isConnected = r.statusCode == 200;
    } catch (_) {
      _isConnected = false;
    }
    notifyListeners();
    return _isConnected;
  }

  /// 设置毛玻璃背景图路径（null = 渐变模式）。
  Future<void> setGlassBackgroundPath(String? path) async {
    _glassBackgroundPath = path;
    final prefs = await SharedPreferences.getInstance();
    if (path == null) {
      await prefs.remove('glass_background_path');
    } else {
      await prefs.setString('glass_background_path', path);
    }
    notifyListeners();
  }

  /// 设置毛玻璃背景图模糊度（0-30，实时预览）。
  Future<void> setGlassBlur(double value) async {
    _glassBlur = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('glass_blur', value);
    notifyListeners();
  }

  /// 设置毛玻璃背景压暗（0-0.6，实时预览）。
  Future<void> setGlassDim(double value) async {
    _glassDim = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('glass_dim', value);
    notifyListeners();
  }

  /// 设置毛玻璃渐变配色（c1=起色，c2=终色；null=跟随 seedColor，取色器逐个保存时保留另一个）。
  Future<void> setGlassAuroraColors({ui.Color? c1, ui.Color? c2}) async {
    if (c1 != null) _glassAuroraColor1 = c1.toARGB32();
    if (c2 != null) _glassAuroraColor2 = c2.toARGB32();
    final prefs = await SharedPreferences.getInstance();
    if (c1 != null) {
      await prefs.setInt('glass_aurora1', _glassAuroraColor1!);
    } else if (_glassAuroraColor1 == null) {
      await prefs.remove('glass_aurora1');
    }
    if (c2 != null) {
      await prefs.setInt('glass_aurora2', _glassAuroraColor2!);
    } else if (_glassAuroraColor2 == null) {
      await prefs.remove('glass_aurora2');
    }
    notifyListeners();
  }

  /// 重置毛玻璃背景设置为默认（渐变模式 + 默认模糊/压暗/跟随 seedColor）。
  Future<void> resetGlassBackground() async {
    _glassBackgroundPath = null;
    _glassBlur = 15;
    _glassDim = 0.1;
    _glassAuroraColor1 = null;
    _glassAuroraColor2 = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('glass_background_path');
    await prefs.remove('glass_blur');
    await prefs.remove('glass_dim');
    await prefs.remove('glass_aurora1');
    await prefs.remove('glass_aurora2');
    notifyListeners();
  }

  /// 设置全局「减少动效」开关（关闭持续循环动效，保留必要转场/按压反馈）。
  Future<void> setReduceMotion(bool value) async {
    if (_reduceMotion == value) return;
    _reduceMotion = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('reduce_motion', value);
    notifyListeners();
  }

  /// 设置全局「降低模糊」开关（所有 BackdropFilter 的 sigma 减半，最低 4）。
  Future<void> setReduceBlur(bool value) async {
    if (_reduceBlur == value) return;
    _reduceBlur = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('reduce_blur', value);
    notifyListeners();
  }
}
