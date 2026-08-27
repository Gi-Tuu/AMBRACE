import 'package:flutter/foundation.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../screens/weave/weave_view_mode.dart';
import '../theme/skins/skin_registry.dart';

class SettingsProvider extends ChangeNotifier {
  String _serverUrl = '';
  String _nickname = '用户';
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

  bool get needsOnboarding => !_onboardingDone && _serverUrl.isEmpty;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    _serverUrl = prefs.getString('server_url') ?? '';
    _nickname = prefs.getString('nickname') ?? '用户';
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
}
