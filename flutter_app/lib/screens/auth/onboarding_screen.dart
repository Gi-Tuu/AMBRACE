import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../global_keys.dart';
import '../../models/character.dart';
import '../../providers/chat_provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../services/fcm_push_service.dart';
import '../../theme/tokens.dart';
import '../../widgets/app_page_route.dart';
import '../chat/chat_screen.dart';
import '../settings/api_config_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// AMBRACE 首次使用引导（Onboarding，2026-08-24）。
///
/// 3-4 步引导全新用户：连接服务器 → 账号（登录/注册）→ 创建角色 → 内嵌 API Key 配置。
/// 完成后自动创建角色会话并发送首条消息，进入聊天页。
/// 触发时机由 [SettingsProvider.needsOnboarding] 决定（仅全新设备，不打扰已配置用户）。
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  static const int _stepCount = 4;

  int _step = 0;

  // ── 步骤 1：服务器 ──
  final _serverUrlCtrl = TextEditingController();
  bool _testingServer = false;
  bool _serverConnected = false;
  String _connStatus = '';

  // ── 步骤 2：账号（登录/注册）──
  bool _isRegister = false;
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();
  bool _busyAccount = false;
  bool _accountDone = false;

  // ── 步骤 3：创建角色 ──
  final _charNameCtrl = TextEditingController();
  final _charPersonalityCtrl = TextEditingController();
  bool _busyCharacter = false;
  AICharacter? _createdCharacter;
  String _generatedGreeting = ''; // 创建后生成的开场白（有值则作为首条消息）

  // ── 步骤 4：API Key ──
  final _apiBaseUrlCtrl = TextEditingController();
  final _apiKeyCtrl = TextEditingController();
  final _apiModelCtrl = TextEditingController();
  final _apiProviderCtrl = TextEditingController();
  bool _savingApi = false;
  bool _testingApi = false;

  @override
  void initState() {
    super.initState();
    _serverUrlCtrl.text = context.read<SettingsProvider>().serverUrl;
  }

  @override
  void dispose() {
    _serverUrlCtrl.dispose();
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    _nicknameCtrl.dispose();
    _charNameCtrl.dispose();
    _charPersonalityCtrl.dispose();
    _apiBaseUrlCtrl.dispose();
    _apiKeyCtrl.dispose();
    _apiModelCtrl.dispose();
    _apiProviderCtrl.dispose();
    super.dispose();
  }

  // ───────────────────────── 步骤 1：连接服务器 ─────────────────────────

  Future<void> _testServer() async {
    final l10n = AppLocalizations.of(context)!;
    final url = _serverUrlCtrl.text.trim();
    if (url.isEmpty) return;
    setState(() {
      _testingServer = true;
      _connStatus = l10n.checking;
      _serverConnected = false;
    });
    final settings = context.read<SettingsProvider>();
    try {
      // 复用既有 ApiClient baseUrl 配置与登录页连接测试逻辑（GET /api/v1/system/health）
      ApiClient().updateBaseUrl(url);
      final r = await ApiClient().dio.get(
            '/api/v1/system/health',
            options: Options(connectTimeout: const Duration(seconds: 3)),
          );
      final ok = r.statusCode == 200;
      if (!mounted) return;
      setState(() {
        _serverConnected = ok;
        _connStatus = ok ? l10n.connectSuccess : l10n.connectFailed;
      });
      if (ok) await settings.setServerUrl(url);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _serverConnected = false;
        _connStatus = l10n.connectFail;
      });
    } finally {
      if (mounted) setState(() => _testingServer = false);
    }
  }

  // ───────────────────────── 步骤 2：账号 ─────────────────────────

  Future<void> _submitAccount() async {
    final l10n = AppLocalizations.of(context)!;
    final username = _usernameCtrl.text.trim();
    final password = _passwordCtrl.text;
    if (username.isEmpty || password.isEmpty) {
      _toast(l10n.onboardingWarningUsername);
      return;
    }
    setState(() => _busyAccount = true);
    final settings = context.read<SettingsProvider>();
    try {
      final dio = ApiClient().dio;
      final path = _isRegister ? '/api/v1/auth/register' : '/api/v1/auth/login';
      final body = {'username': username, 'password': password};
      if (_isRegister) {
        body['nickname'] = _nicknameCtrl.text.trim().isEmpty
            ? username
            : _nicknameCtrl.text.trim();
      }
      final r = await dio.post(path, data: body,
          options: Options(connectTimeout: const Duration(seconds: 5)));
      final data = r.data as Map<String, dynamic>;
      final token = data['access_token'] as String;
      final userId = data['user_id'] as int;
      final nickname = data['nickname'] as String;
      await settings.setAuth(token, userId, nickname);
      ApiClient().configure(baseUrl: settings.serverUrl, token: token);
      await FcmPushService.instance.init();
      await settings.syncProfileFromServer();
      if (!mounted) return;
      setState(() => _accountDone = true);
      // 登录老用户（已有角色）→ 跳过创建角色/API 配置，直接进主页
      if (!_isRegister && await _hasCharacters()) {
        await settings.setOnboardingDone(true);
        if (!mounted) return;
        _enterApp(autoChat: false, greeting: '');
        return;
      }
      _next();
    } on DioException catch (e) {
      if (mounted) _toast(e.response?.data?['detail']?.toString() ?? l10n.loginFailed);
    } catch (_) {
      if (mounted) _toast(l10n.connectFailed);
    } finally {
      if (mounted) setState(() => _busyAccount = false);
    }
  }

  /// 判断该账号是否已有角色：复用现有角色列表接口（GET /api/v1/characters）。
  /// 有任意角色返回 true；接口报错或无角色返回 false（按新用户走）。
  Future<bool> _hasCharacters() async {
    try {
      final chars = await ApiClient().getCharacters();
      return chars.isNotEmpty;
    } catch (_) {
      return false;
    }
  }

  // ───────────────────────── 步骤 3：创建角色 ─────────────────────────

  Future<void> _createCharacter() async {
    final l10n = AppLocalizations.of(context)!;
    final name = _charNameCtrl.text.trim();
    if (name.isEmpty) {
      _toast(l10n.nameRequired);
      return;
    }
    setState(() => _busyCharacter = true);
    try {
      final data = <String, dynamic>{
        'name': name,
        'personality': _charPersonalityCtrl.text.trim(),
      };
      final char = await ApiClient().createCharacter(data);
      if (!mounted) return;
      setState(() => _createdCharacter = char);
      _toast(l10n.onboardingCharacterCreated);
      // 创建后一次性询问生成问候语（与编辑页同一套，落库 greeting_message）
      await _maybeGenerateGreeting(char);
      if (!mounted) return;
      _next();
    } catch (_) {
      if (mounted) _toast(l10n.saveFailed);
    } finally {
      if (mounted) setState(() => _busyCharacter = false);
    }
  }

  /// 创建角色成功后一次性询问「是否生成问候语？」；点生成则 LLM 生成并写回 greeting_message。
  Future<void> _maybeGenerateGreeting(AICharacter char) async {
    final l10n = AppLocalizations.of(context)!;
    final want = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.generateGreetingAsk),
        content: Text(l10n.generateGreetingDesc),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(l10n.generateGreetingSkip),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.generateGreetingDo),
          ),
        ],
      ),
    );
    if (want != true || !mounted) return;
    try {
      final res = await ApiClient().generateGreeting(char.id);
      if (mounted) {
        _generatedGreeting = res['greeting_message']?.toString() ?? '';
        _toast(l10n.generateGreetingDone);
      }
    } catch (_) {
      if (mounted) _toast(l10n.generateGreetingFail);
    }
  }

  /// 跳过创建角色：不建角色也可继续到下一步（后续在主页补充），无副作用。
  void _skipCharacter() {
    if (_busyCharacter) return;
    _next();
  }

  // ───────────────────────── 步骤 4：API Key ─────────────────────────

  Map<String, dynamic> _apiBody() => {
        'enabled': true,
        'base_url': _apiBaseUrlCtrl.text.trim(),
        'model': _apiModelCtrl.text.trim(),
        'provider': _apiProviderCtrl.text.trim(),
        if (_apiKeyCtrl.text.trim().isNotEmpty) 'api_key': _apiKeyCtrl.text.trim(),
      };

  Future<void> _testApi() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _testingApi = true);
    try {
      final r = await ApiClient().testApiConnection(_apiBody());
      if (!mounted) return;
      final ok = r['ok'] == true;
      _toast(ok ? l10n.onboardingApiTestOk : l10n.onboardingApiTestFail);
    } catch (_) {
      if (mounted) _toast(l10n.onboardingApiTestFail);
    } finally {
      if (mounted) setState(() => _testingApi = false);
    }
  }

  Future<bool> _saveApiConfig() async {
    final l10n = AppLocalizations.of(context)!;
    if (_apiBaseUrlCtrl.text.trim().isEmpty || _apiKeyCtrl.text.trim().isEmpty) {
      _toast(l10n.onboardingApiKeyEmpty);
      return false;
    }
    setState(() => _savingApi = true);
    try {
      final settings = context.read<SettingsProvider>();
      // 主账号写服务器级 LLM 配置（onboarding 第一个账号即主账号）；否则写用户级 BYOK。
      if (settings.isAdmin) {
        await ApiClient().updateServerApiConfig(_apiBody());
      } else {
        await ApiClient().updateApiConfig(_apiBody());
      }
      if (mounted) _toast(l10n.onboardingApiKeySaved);
      return true;
    } catch (_) {
      if (mounted) _toast(l10n.saveFailed);
      return false;
    } finally {
      if (mounted) setState(() => _savingApi = false);
    }
  }

  // ───────────────────────── 完成 / 跳过 ─────────────────────────

  /// 完成引导（已配置 API Key）：写完成标志 → 进入主页 → 进入聊天页并自动发送首条消息。
  Future<void> _finish() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await _saveApiConfig();
    if (!ok || !mounted) return;
    await context.read<SettingsProvider>().setOnboardingDone(true);
    if (!mounted) return;
    _enterApp(
      autoChat: true,
      greeting: _generatedGreeting.isNotEmpty ? _generatedGreeting : l10n.onboardingFirstMessage,
    );
  }

  /// 跳过 API Key（稍后设置）：写完成标志 → 进入主页 → 引导到设置页（API 配置）。
  Future<void> _skipApi() async {
    await context.read<SettingsProvider>().setOnboardingDone(true);
    if (!mounted) return;
    _enterApp(autoChat: false, greeting: '', guideToApi: true);
  }

  /// 进入应用：替换到主页；按需推入聊天页并自动发送首条消息。
  /// [guideToApi] 仅在跳过 API 配置时使用：引导到设置页（API 配置），主页在下一层可返回；
  /// 否则留在主页，由主页优雅处理未建角色/未配 API 的情况。
  void _enterApp({required bool autoChat, required String greeting, bool guideToApi = false}) {
    final nav = appNavigatorKey.currentState;
    if (nav == null) return;
    nav.pushReplacementNamed('/home');
    if (autoChat && _createdCharacter != null) {
      final chat = context.read<ChatProvider>();
      chat.setCharacter(_createdCharacter!);
      chat.setInitialGreeting(greeting);
      nav.push(AppPageRoute(builder: (_) => const ChatScreen()));
    } else if (guideToApi) {
      nav.push(AppPageRoute(builder: (_) => const ApiConfigScreen()));
    }
  }

  // ───────────────────────── 通用导航 ─────────────────────────

  bool get _stepDone {
    switch (_step) {
      case 0:
        return _serverConnected;
      case 1:
        return _accountDone;
      case 2:
        return _createdCharacter != null;
      case 3:
        return true;
    }
    return false;
  }

  void _next() {
    if (_step >= _stepCount - 1) return;
    setState(() => _step++);
  }

  void _back() {
    if (_step <= 0) return;
    setState(() => _step--);
  }

  void _toast(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  // ───────────────────────── UI ─────────────────────────

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            _header(l10n),
            Expanded(
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 250),
                child: KeyedSubtree(
                  key: ValueKey(_step),
                  child: _buildStep(l10n),
                ),
              ),
            ),
            _footer(l10n),
          ],
        ),
      ),
    );
  }

  Widget _header(AppLocalizations l10n) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.sm, AppSpacing.sm, AppSpacing.sm, 0),
      child: Column(
        children: [
          Row(
            children: [
              if (_step > 0)
                IconButton(
                  onPressed: _back,
                  icon: const Icon(Icons.arrow_back),
                  tooltip: l10n.back,
                )
              else
                const SizedBox(width: 48),
              Expanded(
                child: Text(
                  l10n.onboardingTitle,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: AppTypography.titleSize,
                    fontWeight: AppTypography.titleWeight,
                  ),
                ),
              ),
              const SizedBox(width: 48),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            l10n.onboardingSubtitle,
            style: TextStyle(fontSize: AppTypography.helperSize, color: scheme.onSurfaceVariant),
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var i = 0; i < _stepCount; i++) ...[
                if (i > 0) _connector(i),
                _stepColumn(l10n, i),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _connector(int index) {
    final done = index <= _step;
    final color = done ? Theme.of(context).colorScheme.primary : AppColors.dividerDark;
    return Container(
      width: 14,
      height: 2,
      margin: const EdgeInsets.only(top: 5, left: 3, right: 3),
      color: color.withValues(alpha: done ? 1 : 0.4),
    );
  }

  Widget _stepColumn(AppLocalizations l10n, int index) {
    final active = index == _step;
    final done = index < _step;
    final scheme = Theme.of(context).colorScheme;
    final color = (active || done) ? scheme.primary : scheme.surfaceContainerHighest;
    final label = switch (index) {
      0 => l10n.onboardingStepServer,
      1 => l10n.onboardingStepAccount,
      2 => l10n.onboardingStepCharacter,
      _ => l10n.onboardingStepApiKey,
    };
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: active ? 12 : 10,
          height: active ? 12 : 10,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            border: active ? Border.all(color: scheme.onPrimary, width: 2) : null,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          label,
          style: TextStyle(
            fontSize: AppTypography.captionSize,
            color: active ? scheme.primary : scheme.onSurfaceVariant,
            fontWeight: active ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ],
    );
  }

  Widget _buildStep(AppLocalizations l10n) {
    switch (_step) {
      case 0:
        return _stepServer(l10n);
      case 1:
        return _stepAccount(l10n);
      case 2:
        return _stepCharacter(l10n);
      case 3:
        return _stepApiKey(l10n);
    }
    return const SizedBox();
  }

  Widget _scroll({required List<Widget> children}) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.lg, AppSpacing.lg, AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: children,
      ),
    );
  }

  Widget _title(AppLocalizations l10n, String title, String desc) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 6),
        Text(desc, style: TextStyle(fontSize: AppTypography.bodySize, color: scheme.onSurfaceVariant)),
        const SizedBox(height: AppSpacing.lg),
      ],
    );
  }

  Widget _field(TextEditingController ctrl,
      {required String label, String? hint, bool obscure = false}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: TextField(
        controller: ctrl,
        obscureText: obscure,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surface,
          contentPadding: const EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: 12),
        ),
      ),
    );
  }

  Widget _primaryButton({required String text, required VoidCallback? onPressed, bool loading = false}) {
    return SizedBox(
      width: double.infinity,
      child: FilledButton(
        onPressed: loading ? null : onPressed,
        style: FilledButton.styleFrom(
          padding: const EdgeInsets.symmetric(vertical: 14),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AppRadius.md)),
        ),
        child: loading
            ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
            : Text(text),
      ),
    );
  }

  Widget _outlineButton({required String text, required VoidCallback? onPressed}) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton(
        onPressed: onPressed,
        style: OutlinedButton.styleFrom(
          padding: const EdgeInsets.symmetric(vertical: 12),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AppRadius.md)),
        ),
        child: Text(text),
      ),
    );
  }

  Widget _stepServer(AppLocalizations l10n) {
    return _scroll(children: [
      _title(l10n, l10n.onboardingServerTitle, l10n.onboardingServerDesc),
      _field(_serverUrlCtrl, label: l10n.serverAddress, hint: 'http://192.168.1.100:8000'),
      Align(
        alignment: Alignment.centerLeft,
        child: Text(
          _connStatus.isNotEmpty ? _connStatus : l10n.serverAddressHint,
          style: TextStyle(
            fontSize: AppTypography.helperSize,
            color: _serverConnected
                ? AppColors.success
                : (_connStatus.isNotEmpty ? AppColors.error : Theme.of(context).colorScheme.onSurfaceVariant),
          ),
        ),
      ),
      const SizedBox(height: AppSpacing.sm),
      _outlineButton(
        text: l10n.testConnection,
        onPressed: _testingServer ? null : _testServer,
      ),
      const SizedBox(height: AppSpacing.lg),
    ]);
  }

  Widget _stepAccount(AppLocalizations l10n) {
    final scheme = Theme.of(context).colorScheme;
    return _scroll(children: [
      _title(l10n, l10n.onboardingAccountTitle, l10n.onboardingAccountDesc),
      SegmentedButton<bool>(
        segments: [
          ButtonSegment(value: false, label: Text(l10n.login), icon: const Icon(Icons.login)),
          ButtonSegment(value: true, label: Text(l10n.register), icon: const Icon(Icons.person_add)),
        ],
        selected: {_isRegister},
        onSelectionChanged: (s) => setState(() {
          _isRegister = s.first;
          _accountDone = false;
        }),
      ),
      const SizedBox(height: AppSpacing.md),
      _field(_usernameCtrl, label: l10n.username),
      if (_isRegister) _field(_nicknameCtrl, label: l10n.nicknameOptional),
      _field(_passwordCtrl, label: l10n.password, obscure: true),
      _primaryButton(
        text: _isRegister ? l10n.register : l10n.login,
        onPressed: _submitAccount,
        loading: _busyAccount,
      ),
      if (_accountDone)
        Padding(
          padding: const EdgeInsets.only(top: AppSpacing.md),
          child: Row(
            children: [
              Icon(Icons.check_circle, color: scheme.primary, size: 18),
              const SizedBox(width: 6),
              Text(l10n.onboardingAccountDone, style: TextStyle(color: scheme.primary)),
            ],
          ),
        ),
      const SizedBox(height: AppSpacing.lg),
    ]);
  }

  Widget _stepCharacter(AppLocalizations l10n) {
    return _scroll(children: [
      _title(l10n, l10n.onboardingCharacterTitle, l10n.onboardingCharacterDesc),
      _field(_charNameCtrl, label: l10n.name),
      _field(_charPersonalityCtrl, label: l10n.onboardingCharacterPersonalityLabel, hint: l10n.onboardingCharacterPersonalityHint),
      _primaryButton(
        text: l10n.onboardingCharacterCreate,
        onPressed: _createCharacter,
        loading: _busyCharacter,
      ),
      const SizedBox(height: AppSpacing.sm),
      _outlineButton(
        text: l10n.onboardingCharacterSkip,
        onPressed: _busyCharacter ? null : _skipCharacter,
      ),
      if (_createdCharacter != null)
        Padding(
          padding: const EdgeInsets.only(top: AppSpacing.md),
          child: Row(
            children: [
              const Icon(Icons.check_circle, color: AppColors.success, size: 18),
              const SizedBox(width: 6),
              Text('${l10n.onboardingCharacterCreated}: ${_createdCharacter!.name}',
                  style: const TextStyle(color: AppColors.success)),
            ],
          ),
        ),
      const SizedBox(height: AppSpacing.lg),
    ]);
  }

  Widget _stepApiKey(AppLocalizations l10n) {
    return _scroll(children: [
      _title(l10n, l10n.onboardingApiKeyTitle, l10n.onboardingApiKeyHint),
      DropdownButtonFormField<String>(
        initialValue: null,
        isExpanded: true,
        decoration: InputDecoration(
          labelText: l10n.onboardingApiKeyPreset,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
            borderSide: BorderSide.none,
          ),
          filled: true,
          fillColor: Theme.of(context).colorScheme.surface,
        ),
        items: [
          for (final e in kLlmPresets.entries)
            DropdownMenuItem(value: e.key, child: Text(e.key)),
        ],
        onChanged: (v) {
          if (v == null) return;
          final p = kLlmPresets[v]!;
          setState(() {
            _apiBaseUrlCtrl.text = p['base_url'] ?? '';
            _apiModelCtrl.text = p['model'] ?? '';
            _apiProviderCtrl.text = p['provider'] ?? '';
          });
        },
      ),
      const SizedBox(height: AppSpacing.md),
      _field(_apiBaseUrlCtrl, label: 'Base URL', hint: 'https://api.deepseek.com/v1'),
      _field(_apiModelCtrl, label: l10n.model, hint: 'deepseek-chat'),
      _field(_apiProviderCtrl, label: l10n.provider, hint: 'deepseek'),
      _field(_apiKeyCtrl, label: l10n.apiKeyKeep, hint: 'sk-...', obscure: true),
      const SizedBox(height: AppSpacing.xs),
      Row(
        children: [
          Expanded(
            child: _outlineButton(
              text: l10n.testConnection,
              onPressed: _testingApi ? null : _testApi,
            ),
          ),
        ],
      ),
      const SizedBox(height: AppSpacing.sm),
      Text(
        l10n.onboardingApiKeySkipTip,
        style: TextStyle(fontSize: AppTypography.helperSize, color: Theme.of(context).colorScheme.onSurfaceVariant),
      ),
      const SizedBox(height: AppSpacing.lg),
    ]);
  }

  Widget _footer(AppLocalizations l10n) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.xs, AppSpacing.lg, AppSpacing.md),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_step < _stepCount - 1)
            _primaryButton(
              text: l10n.onboardingNext,
              onPressed: _stepDone ? _next : null,
            )
          else ...[
            _primaryButton(
              text: l10n.onboardingApiKeySaveDone,
              onPressed: _savingApi ? null : _finish,
              loading: _savingApi,
            ),
            const SizedBox(height: AppSpacing.sm),
            TextButton(
              onPressed: _skipApi,
              child: Text(l10n.onboardingApiKeySkip, style: TextStyle(color: scheme.onSurfaceVariant)),
            ),
          ],
        ],
      ),
    );
  }
}
