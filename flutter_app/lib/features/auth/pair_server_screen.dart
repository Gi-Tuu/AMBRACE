import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../services/server_identity.dart';

/// 服务器身份配对页（批 0-3 M0-b，2026-09-27；方案 §4.3.4）。
///
/// 与首启引导页分开：本页只做「输入桌面控制台显示的 12 位配对码 → 对照屏幕确认指纹 →
/// 落盘身份密钥」。指纹**必须**由用户在电脑屏幕上前确认后写入（带外确认），
/// 不允许改成从 `/health` 的 `fp_hint` 读取——那个字段本身在明文信道上可被替换。
class PairServerScreen extends StatefulWidget {
  const PairServerScreen({super.key});

  @override
  State<PairServerScreen> createState() => _PairServerScreenState();
}

class _PairServerScreenState extends State<PairServerScreen> {
  final _codeCtrl = TextEditingController();
  final _urlCtrl = TextEditingController();
  bool _loading = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    final identity = ServerIdentity.instance;
    identity.ensureLoaded().then((_) {
      if (!mounted) return;
      setState(() {});
    });
    _urlCtrl.text = ApiClient().baseUrl;
  }

  @override
  void dispose() {
    _codeCtrl.dispose();
    _urlCtrl.dispose();
    super.dispose();
  }

  String _failureText(AppLocalizations l10n, String? failure) {
    switch (failure) {
      case 'no_code':
        return l10n.serverIdentityNoCode;
      case 'network':
        return l10n.serverIdentityNetworkFail;
      default:
        return l10n.serverIdentityFailedBody;
    }
  }

  Future<void> _startPairing() async {
    final l10n = AppLocalizations.of(context)!;
    final code = _codeCtrl.text.trim().toUpperCase();
    final url = _urlCtrl.text.trim();
    if (!ServerIdentity.isPairingCodeValid(code) || url.isEmpty) {
      setState(() => _message = l10n.serverIdentityCodeInvalid);
      return;
    }
    setState(() {
      _loading = true;
      _message = l10n.serverIdentityPairing;
    });
    final result = await ServerIdentity.instance.pair(code, url);
    if (!mounted) return;
    setState(() => _loading = false);
    if (!result.ok) {
      setState(() => _message = _failureText(l10n, result.failure));
      return;
    }
    // 带外确认：用户必须对照电脑屏幕上的指纹点头，否则撤销刚落盘的密钥
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        final t = AppLocalizations.of(ctx)!;
        return AlertDialog(
          title: Text(t.serverIdentityTitle),
          content: Text(t.serverIdentityFingerprintConfirm(
              result.serverName.isEmpty ? '-' : result.serverName,
              result.fingerprintDisplay)),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text(t.serverIdentityConfirmNo),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: Text(t.serverIdentityConfirmYes),
            ),
          ],
        );
      },
    );
    if (confirmed != true) {
      await ServerIdentity.instance.unpair();
    }
    if (!mounted) return;
    setState(() {
      _codeCtrl.clear();
      _message = confirmed == true
          ? ServerIdentity.instance.fingerprintDisplay
          : l10n.serverIdentityRevoked;
    });
  }

  Future<void> _unpair() async {
    final l10n = AppLocalizations.of(context)!;
    await ServerIdentity.instance.unpair();
    if (!mounted) return;
    setState(() => _message = l10n.serverIdentityRevoked);
  }

  Future<void> _setMode(String mode) async {
    await ServerIdentity.instance.saveVerifyMode(mode);
    if (!mounted) return;
    setState(() {});
  }

  Widget _statusCard(AppLocalizations l10n, ServerIdentity identity) {
    final paired = identity.isPaired;
    final counters = identity.verifiedCount + identity.mismatchCount + identity.unsignedCount;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(paired
                ? l10n.serverIdentityStatusPaired(identity.fingerprintDisplay)
                : l10n.serverIdentityStatusUnpaired),
            if (paired && counters > 0)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(l10n.serverIdentityUnverifiedWarn(identity.verifiedCount,
                    identity.mismatchCount, identity.unsignedCount)),
              ),
            if (paired)
              Row(
                children: [
                  Text(l10n.serverIdentityModeLabel),
                  const SizedBox(width: 8),
                  DropdownButton<String>(
                    value: identity.verifyMode,
                    onChanged: (v) => v == null ? null : _setMode(v),
                    items: [
                      DropdownMenuItem(
                          value: 'off', child: Text(l10n.serverIdentityModeOff)),
                      DropdownMenuItem(value: 'shadow',
                          child: Text(l10n.serverIdentityModeShadow)),
                      DropdownMenuItem(value: 'enforce',
                          child: Text(l10n.serverIdentityModeEnforce)),
                    ],
                  ),
                ],
              ),
            if (paired)
              TextButton(
                onPressed: _unpair,
                child: Text(l10n.serverIdentityUnpair),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final identity = ServerIdentity.instance;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.serverIdentityTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _statusCard(l10n, identity),
          const SizedBox(height: 12),
          TextField(
            controller: _urlCtrl,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: l10n.serverAddress,
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _codeCtrl,
            textCapitalization: TextCapitalization.characters,
            maxLength: ServerIdentity.codeLength,
            inputFormatters: [
              FilteringTextInputFormatter.allow(
                  RegExp('[${ServerIdentity.codeAlphabet}a-z]')),
              UpperCaseFormatter(),
            ],
            decoration: InputDecoration(
              labelText: l10n.serverIdentityPair,
              hintText: l10n.serverIdentityCodeHint,
              counterText: '',
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          Text(l10n.serverIdentityNote,
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _loading ? null : _startPairing,
            child: Text(_loading
                ? l10n.serverIdentityPairing
                : l10n.serverIdentityPair),
          ),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_message!,
                  style: Theme.of(context).textTheme.bodyMedium),
            ),
        ],
      ),
    );
  }
}

/// 配对码统一按大写处理（后端派生前也会 `upper()`，此处只为显示一致）
class UpperCaseFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
      TextEditingValue oldValue, TextEditingValue newValue) {
    return newValue.copyWith(text: newValue.text.toUpperCase());
  }
}
