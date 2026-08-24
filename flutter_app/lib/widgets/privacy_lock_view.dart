import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_client.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// AI 隐私上锁通用视图：锁屏态 → 向 AI 申请 → 提示框（AI 回复 + 是否同意）→ 同意后出现「查看」
class PrivacyLockView extends StatefulWidget {
  /// 0 = 小手机（服务端按最近互动角色解析）
  final int characterId;
  /// diary / phone
  final String target;
  /// 展示名：日记 / 手机
  final String contentName;
  /// 同意并点击「查看」后回调（父页面加载/显示内容）
  final VoidCallback? onUnlocked;

  const PrivacyLockView({
    super.key,
    required this.characterId,
    required this.target,
    required this.contentName,
    this.onUnlocked,
  });

  @override
  State<PrivacyLockView> createState() => _PrivacyLockViewState();
}

class _PrivacyLockViewState extends State<PrivacyLockView> {
  final _api = ApiClient();
  Map<String, dynamic>? _status;
  bool _applying = false;
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _refresh();
    // 冷却倒计时刷新
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final s = await _api.getPrivacyStatus(widget.characterId, widget.target);
      if (mounted) {
        setState(() => _status = s);
      }
    } catch (_) {}
  }

  int get _cooldown => ((_status?['cooldown_remaining'] as num?) ?? 0).toInt();

  Future<void> _apply() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _applying = true);
    try {
      final res = await _api.requestPrivacyAccess(widget.characterId, widget.target);
      final approved = res['approved'] == true;
      final reply = (res['ai_reply'] as String?) ?? '';
      if (!mounted) return;
      setState(() {
        _applying = false;
        _status = {
          'enabled': res['privacy_lock_enabled'] ?? true,
          'locked': !approved,
          'cooldown_remaining': res['cooldown_remaining'] ?? 120,
          'unlock_until': res['unlock_until'],
        };
      });
      if (approved) {
        await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Row(children: [
              const Icon(Icons.celebration, color: Colors.green),
              const SizedBox(width: 8),
              Text(l10n.privacyApproved),
            ]),
            content: Text(reply, style: const TextStyle(fontSize: 14, height: 1.6)),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(l10n.privacyLater),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(l10n.privacyView),
              ),
            ],
          ),
        );
        widget.onUnlocked?.call();
      } else {
        await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Row(children: [
              const Icon(Icons.sentiment_dissatisfied, color: Colors.orange),
              const SizedBox(width: 8),
              Text(l10n.privacyRejected),
            ]),
            content: Text(reply, style: const TextStyle(fontSize: 14, height: 1.6)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.privacyGotIt)),
            ],
          ),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _applying = false);
      if (mounted) {
        final msg = e.toString();
        final friendly = msg.contains('冷却')
            ? l10n.privacyTooFrequent
            : l10n.privacyApplyFailed;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(friendly)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.grey.shade200,
                shape: BoxShape.circle,
              ),
              child: Icon(Icons.lock_outline, size: 40, color: Colors.grey.shade600),
            ),
            const SizedBox(height: 16),
            Text(
              l10n.privacyLockedBy(widget.contentName),
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            Text(
              l10n.privacyApplyHint,
              style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
            ),
            const SizedBox(height: 20),
            if (_cooldown > 0)
              Text(
                l10n.privacyCooldown(_cooldown),
                style: TextStyle(fontSize: 13, color: Colors.orange.shade700),
              )
            else
              FilledButton.icon(
                onPressed: _applying ? null : _apply,
                icon: _applying
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.mark_email_unread_outlined, size: 18),
                label: Text(_applying ? l10n.privacyApplying : l10n.privacyApplyButton),
              ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: _refresh,
              icon: const Icon(Icons.refresh, size: 16),
              label: Text(l10n.privacyRefreshStatus),
            ),
          ],
        ),
      ),
    );
  }
}
