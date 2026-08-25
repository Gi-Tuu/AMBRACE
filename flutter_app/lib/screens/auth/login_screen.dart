import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import 'package:dio/dio.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../global_keys.dart';
import 'register_screen.dart';
import 'onboarding_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  bool _isConnected = false;
  String _connStatus = '';
  final _serverUrlCtrl = TextEditingController();
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  bool _loading = false;
  String? _error;

  Future<void> _testConnection() async {
    final l10n = AppLocalizations.of(context)!;
    final url = _serverUrlCtrl.text.trim();
    if (url.isEmpty) return;
    setState(() { _connStatus = l10n.checking; _isConnected = false; });
    final settings = context.read<SettingsProvider>();
    try {
      final r = await Dio().get('$url/api/v1/system/health',
        options: Options(connectTimeout: Duration(seconds: 3)));
      setState(() {
        _isConnected = r.statusCode == 200;
        _connStatus = _isConnected ? l10n.connectSuccess : l10n.connectFailed;
      });
      // 自动保存服务器地址
      await settings.setServerUrl(url);
    } catch (e) {
      if (!mounted) return;
      setState(() { _isConnected = false; _connStatus = l10n.connectFail; });
    }
  }

  Future<void> _login() async {
    final l10n = AppLocalizations.of(context)!;
    final username = _usernameCtrl.text.trim();
    final password = _passwordCtrl.text.trim();
    if (username.isEmpty || password.isEmpty) return;

    setState(() { _loading = true; _error = null; });
    final settings = context.read<SettingsProvider>();
    await settings.setServerUrl(_serverUrlCtrl.text.trim());

    try {
      final r = await Dio().post(
        '${settings.serverUrl}/api/v1/auth/login',
        data: {'username': username, 'password': password},
        options: Options(connectTimeout: Duration(seconds: 5)),
      );
      final data = r.data as Map<String, dynamic>;
      final token = data['access_token'] as String;
      await settings.setAuth(
        token,
        data['user_id'] as int,
        data['nickname'] as String,
      );
      ApiClient().configure(baseUrl: settings.serverUrl, token: token);
      await settings.syncProfileFromServer();
      appNavigatorKey.currentState?.pushReplacementNamed('/home');
    } on DioException catch (e) {
      final msg = e.response?.data?['detail'] ?? l10n.loginFailed;
      setState(() { _error = msg.toString(); _loading = false; });
    } catch (e) {
      setState(() { _error = l10n.connectFailed; _loading = false; });
    }
  }

  /// 忘记密码（本地部署）：无需旧密码，仅用户名+新密码直接重置
  Future<void> _forgotPassword(BuildContext ctx) async {
    final l10n = AppLocalizations.of(ctx)!;
    final settings = context.read<SettingsProvider>();
    final uCtrl = TextEditingController();
    final pCtrl = TextEditingController();
    final cCtrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: ctx,
      builder: (dialogCtx) => AlertDialog(
        title: Text(l10n.loginForgotPassword),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: uCtrl, decoration: InputDecoration(labelText: l10n.username)),
            const SizedBox(height: 8),
            TextField(controller: pCtrl, obscureText: true, decoration: InputDecoration(labelText: l10n.loginNewPassword)),
            const SizedBox(height: 8),
            TextField(controller: cCtrl, obscureText: true, decoration: InputDecoration(labelText: l10n.loginConfirmNewPassword)),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(dialogCtx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(dialogCtx, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (ok != true || !ctx.mounted) return;
    final username = uCtrl.text.trim();
    final newPassword = pCtrl.text;
    if (username.isEmpty || newPassword.isEmpty || newPassword != cCtrl.text) {
      if (ctx.mounted) {
        ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.loginResetInvalid)));
      }
      return;
    }
    try {
      await Dio().post(
        '${settings.serverUrl}/api/v1/auth/forgot-password',
        data: {'username': username, 'new_password': newPassword},
        options: Options(connectTimeout: Duration(seconds: 5)),
      );
      if (ctx.mounted) {
        ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.loginResetOk)));
      }
    } catch (_) {
      if (ctx.mounted) {
        ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.loginResetFail)));
      }
    }
  }

  @override
  void initState() {
    super.initState();
    // P1-7：服务器地址为空时实时显示引导提示（随输入即时刷新）
    _serverUrlCtrl.addListener(() {
      if (mounted) setState(() {});
    });
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final settings = context.read<SettingsProvider>();
      await settings.load();
      if (!context.mounted) return;
      _serverUrlCtrl.text = settings.serverUrl;
      if (settings.isLoggedIn) {
        ApiClient().configure(baseUrl: settings.serverUrl, token: settings.token);
        appNavigatorKey.currentState?.pushReplacementNamed('/home');
        return;
      }
      // 首次使用引导（Onboarding）：全新设备（无服务器地址）先走引导，不打扰已配置用户
      if (settings.needsOnboarding) {
        appNavigatorKey.currentState?.pushReplacement(
          MaterialPageRoute(builder: (_) => const OnboardingScreen()),
        );
        return;
      }
      // \u81ea\u52a8\u68c0\u6d4b\u8fde\u63a5
      if (settings.serverUrl.isNotEmpty) {
        _testConnection();
      }
    });
  }

  @override
  void dispose() {
    _serverUrlCtrl.dispose();
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Image.asset('assets/ic_launcher.png', width: 80, height: 80),
              const SizedBox(height: 16),
              Text(l10n.appName, style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
              const SizedBox(height: 24),
              TextField(
                controller: _serverUrlCtrl,
                decoration: InputDecoration(
                  labelText: l10n.serverAddress,
                  border: const OutlineInputBorder(
                                borderRadius: BorderRadius.all(Radius.circular(12)),
                                borderSide: BorderSide.none,
                              ),
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surface,
                  prefixIcon: Icon(Icons.computer),
                ),
              ),
              const SizedBox(height: 8),
              if (_serverUrlCtrl.text.trim().isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Text(
                    l10n.serverAddressHint,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                  ),
                ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _connStatus.isNotEmpty ? _connStatus : l10n.tapToTest,
                      style: TextStyle(
                        fontSize: 12,
                        color: _isConnected ? Colors.green : (_connStatus.isNotEmpty ? Colors.red : Colors.grey),
                      ),
                    ),
                  ),
                  TextButton.icon(
                    icon: Icon(Icons.wifi_find, size: 16),
                    label: Text(l10n.testConnection, style: const TextStyle(fontSize: 12)),
                    onPressed: _testConnection,
                    style: TextButton.styleFrom(padding: EdgeInsets.symmetric(horizontal: 8)),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _usernameCtrl,
                decoration: InputDecoration(
                  labelText: l10n.username, border: const OutlineInputBorder(
                                borderRadius: BorderRadius.all(Radius.circular(12)),
                                borderSide: BorderSide.none,
                              ),
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surface, prefixIcon: const Icon(Icons.person),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _passwordCtrl,
                obscureText: true,
                decoration: InputDecoration(
                  labelText: l10n.password, border: const OutlineInputBorder(
                                borderRadius: BorderRadius.all(Radius.circular(12)),
                                borderSide: BorderSide.none,
                              ),
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surface, prefixIcon: const Icon(Icons.lock),
                ),
                onSubmitted: (_) => _login(),
              ),
              if (_error != null) Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.red)),
              ),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _loading ? null : _login,
                  child: _loading ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)) : Text(l10n.login),
                ),
              ),
              const SizedBox(height: 12),
              TextButton(
                onPressed: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const RegisterScreen())),
                child: Text(l10n.noAccountRegister),
              ),
              const SizedBox(height: 4),
              TextButton(
                onPressed: () => _forgotPassword(context),
                child: const Text('忘记密码？修改', style: TextStyle(fontSize: 13, color: Colors.grey)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}