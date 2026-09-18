// 微信桥接密钥配置页（一机多主收尾，2026-09-18）。
// 仅独立主账号可进入（入口在 plugin_card 已按 isAdmin 门控；后端二次校验）。
// 安全口径：GET 只回脱敏值；明文密钥仅在「生成/手动输入 → 保存成功」时于本页完整展示一次，
// 之后不再可查（需重新生成/轮换）。
import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';
import "package:ai_companion/theme/tokens.dart";

class WechatBridgeSecretScreen extends StatefulWidget {
  const WechatBridgeSecretScreen({super.key});

  @override
  State<WechatBridgeSecretScreen> createState() => _WechatBridgeSecretScreenState();
}

class _WechatBridgeSecretScreenState extends State<WechatBridgeSecretScreen> {
  bool _loading = true;
  bool _saving = false;
  bool _hasSecret = false;
  String _masked = '';
  final TextEditingController _ctrl = TextEditingController();
  String? _errText;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final r = await ApiClient().getWechatBridgeSecret();
      if (!mounted) return;
      setState(() {
        _hasSecret = r['has_secret'] == true;
        _masked = (r['masked'] ?? '').toString();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      _toast('$e');
    }
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  /// 32 字节随机 → URL-safe base64（去填充，约 43 字符），满足后端 ≥16 字符。
  String _generateRandom() {
    final rnd = Random.secure();
    final bytes = List<int>.generate(32, (_) => rnd.nextInt(256));
    return base64Url.encode(bytes).replaceAll('=', '');
  }

  void _fillRandom() {
    setState(() {
      _ctrl.text = _generateRandom();
      _errText = null;
    });
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    final secret = _ctrl.text.trim();
    if (secret.length < 16) {
      setState(() => _errText = l10n.channelSecretMinLen);
      return;
    }
    setState(() => _saving = true);
    try {
      await ApiClient().putWechatBridgeSecret(secret);
      if (!mounted) return;
      _ctrl.clear();
      await _load();
      if (!mounted) return;
      await _showPlaintextOnceDialog(secret);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  /// 明文仅展示一次：保存成功后弹出，供复制到 openclaw 网关配置。
  Future<void> _showPlaintextOnceDialog(String secret) async {
    final l10n = AppLocalizations.of(context)!;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.channelSecretOnceTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.channelSecretOnceHint, style: const TextStyle(fontSize: 12, color: AppColors.textSecondary)),
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Theme.of(ctx).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(10),
              ),
              child: SelectableText(secret, style: const TextStyle(fontSize: 12, fontFamily: 'monospace')),
            ),
          ],
        ),
        actions: [
          TextButton.icon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: secret));
              if (!ctx.mounted) return;
              Navigator.pop(ctx);
              _toast(l10n.channelSecretCopied);
            },
            icon: const Icon(Icons.copy, size: 16),
            label: Text(l10n.channelSecretCopy),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text(l10n.channelSecretClose),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmDelete() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.channelSecretDelete),
        content: Text(l10n.channelSecretDeleteConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.channelSecretCancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.channelSecretDelete)),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _saving = true);
    try {
      await ApiClient().deleteWechatBridgeSecret();
      if (!mounted) return;  // Codex 复核补：await 后 widget 可能已被 dispose，
      // 此时 _ctrl 已释放，再调 clear() 会抛「TextEditingController used after being disposed」
      _ctrl.clear();
      await _load();
      _toast(l10n.channelSecretDeleted);
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.channelSecretTitle)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                // 状态卡
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.4),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Row(
                    children: [
                      Icon(_hasSecret ? Icons.verified_user_outlined : Icons.lock_open_outlined,
                          size: 22, color: AppColors.textSecondary),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              _hasSecret ? l10n.channelSecretStatusConfigured : l10n.channelSecretStatusNone,
                              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                            ),
                            if (_hasSecret && _masked.isNotEmpty) ...[
                              const SizedBox(height: 2),
                              Text(_masked,
                                  style: const TextStyle(fontSize: 12, fontFamily: 'monospace', color: AppColors.textSecondary)),
                            ],
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                Text(l10n.channelSecretGuide, style: const TextStyle(fontSize: 12, color: AppColors.textSecondary)),
                const SizedBox(height: 16),
                // 输入
                TextField(
                  controller: _ctrl,
                  minLines: 2,
                  maxLines: 4,
                  style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
                  decoration: InputDecoration(
                    labelText: l10n.channelSecretInput,
                    errorText: _errText,
                    border: const OutlineInputBorder(),
                  ),
                  onChanged: (_) {
                    if (_errText != null) setState(() => _errText = null);
                  },
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    TextButton.icon(
                      onPressed: _saving ? null : _fillRandom,
                      icon: const Icon(Icons.casino_outlined, size: 16),
                      label: Text(l10n.channelSecretGenerate, style: const TextStyle(fontSize: 12)),
                    ),
                    const Spacer(),
                    FilledButton(
                      onPressed: _saving ? null : _save,
                      child: _saving
                          ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                          : Text(l10n.channelSecretSave),
                    ),
                  ],
                ),
                if (_hasSecret) ...[
                  const Divider(height: 28),
                  TextButton.icon(
                    onPressed: _saving ? null : _confirmDelete,
                    icon: const Icon(Icons.delete_outline, size: 18, color: Colors.redAccent),
                    label: Text(l10n.channelSecretDelete,
                        style: const TextStyle(fontSize: 13, color: Colors.redAccent)),
                  ),
                ],
              ],
            ),
    );
  }
}
