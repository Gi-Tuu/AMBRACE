import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import 'package:dio/dio.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _usernameCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();
  bool _loading = false;
  String? _error;

  Future<void> _register() async {
    final l10n = AppLocalizations.of(context)!;
    final username = _usernameCtrl.text.trim();
    final password = _passwordCtrl.text.trim();
    if (username.isEmpty || password.isEmpty) return;

    setState(() { _loading = true; _error = null; });
    final settings = context.read<SettingsProvider>();

    try {
      final r = await Dio().post(
        '${settings.serverUrl}/api/v1/auth/register',
        data: {
          'username': username,
          'password': password,
          'nickname': _nicknameCtrl.text.trim().isEmpty ? username : _nicknameCtrl.text.trim(),
        },
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
      if (mounted) Navigator.pushReplacementNamed(context, '/home');
    } on DioException catch (e) {
      final msg = e.response?.data?['detail'] ?? l10n.registerFailed;
      setState(() { _error = msg.toString(); _loading = false; });
    } catch (e) {
      setState(() { _error = l10n.connectFailed; _loading = false; });
    }
  }

  @override
  void dispose() {
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    _nicknameCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.register)),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(32),
          child: Column(
            children: [
              TextField(
                controller: _usernameCtrl,
                decoration: InputDecoration(labelText: l10n.username, border: const OutlineInputBorder(
                                borderRadius: BorderRadius.all(Radius.circular(12)),
                                borderSide: BorderSide.none,
                              ),
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surface, prefixIcon: const Icon(Icons.person)),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _nicknameCtrl,
                decoration: InputDecoration(
                  labelText: l10n.nicknameOptional,
                  border: const OutlineInputBorder(
                    borderRadius: BorderRadius.all(Radius.circular(12)),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surface,
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _passwordCtrl,
                obscureText: true,
                decoration: InputDecoration(labelText: l10n.password, border: const OutlineInputBorder(
                                borderRadius: BorderRadius.all(Radius.circular(12)),
                                borderSide: BorderSide.none,
                              ),
                              filled: true,
                              fillColor: Theme.of(context).colorScheme.surface, prefixIcon: const Icon(Icons.lock)),
                onSubmitted: (_) => _register(),
              ),
              if (_error != null) Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.red)),
              ),
              const SizedBox(height: 24),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _loading ? null : _register,
                  child: _loading ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white)) : Text(l10n.register),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}