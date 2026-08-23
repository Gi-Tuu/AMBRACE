
import 'package:flutter/foundation.dart';
import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';

class SettingsProvider extends ChangeNotifier {
  String _serverUrl = '';
  String _nickname = '用户';
  String _token = '';
  String _avatarUrl = '';
  int _userId = 0;
  bool _isConnected = false;
  bool _isLoggedIn = false;
  bool _isAdmin = false; // #46 主账号（选择型）：登录后从 profile 同步
  int _themeModeIndex = 0; // 0=跟随系统 1=浅色 2=深色
  int _seedColorIndex = 0; // 主题色索引
  String _localeCode = 'system'; // system=跟随系统 zh=简体中文 en=English
  bool _backgroundKeepalive = true; // 后台保活（#55，默认开）

  String get serverUrl => _serverUrl;
  String get nickname => _nickname;
  String get token => _token;
  String get avatarUrl => _avatarUrl;
  int get userId => _userId;
  bool get isConnected => _isConnected;
  bool get isLoggedIn => _isLoggedIn;
  bool get isAdmin => _isAdmin;
  int get themeModeIndex => _themeModeIndex;
  int get seedColorIndex => _seedColorIndex;
  String get localeCode => _localeCode;
  bool get backgroundKeepalive => _backgroundKeepalive;

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
    _localeCode = prefs.getString('locale_code') ?? 'system';
    _backgroundKeepalive = prefs.getBool('background_keepalive') ?? true;
    notifyListeners();
  }

  Future<void> setThemeModeIndex(int index) async {
    _themeModeIndex = index;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('theme_mode_index', index);
    notifyListeners();
  }

  Future<void> setLocale(String code) async {
    _localeCode = code;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('locale_code', code);
    notifyListeners();
  }

  Future<void> setSeedColorIndex(int index) async {
    _seedColorIndex = index;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt('seed_color_index', index);
    notifyListeners();
  }

  Future<void> setBackgroundKeepalive(bool value) async {
    _backgroundKeepalive = value;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('background_keepalive', value);
    notifyListeners();
  }

  Future<void> setServerUrl(String url) async {
    _serverUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
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

  /// 登录态下从服务器同步用户资料（头像/昵称）。
  /// 退出登录会清空本地 avatar_url，登录/启动/进个人主页时调用可回填。
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
    } catch (_) {
      // 网络失败静默跳过，下次登录/进主页再同步
    }
  }

  Future<void> logout() async {
    _token = '';
    _userId = 0;
    _isLoggedIn = false;
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
