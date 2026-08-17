import 'dart:async';
import 'package:flutter/material.dart';
import '../services/api_client.dart';

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
            title: const Row(children: [
              Icon(Icons.celebration, color: Colors.green),
              SizedBox(width: 8),
              Text('TA 同意了'),
            ]),
            content: Text(reply, style: const TextStyle(fontSize: 14, height: 1.6)),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('稍后再看'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(ctx),
                child: const Text('查看'),
              ),
            ],
          ),
        );
        widget.onUnlocked?.call();
      } else {
        await showDialog<void>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Row(children: [
              Icon(Icons.sentiment_dissatisfied, color: Colors.orange),
              SizedBox(width: 8),
              Text('TA 拒绝了'),
            ]),
            content: Text(reply, style: const TextStyle(fontSize: 14, height: 1.6)),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('知道了')),
            ],
          ),
        );
      }
    } catch (e) {
      if (mounted) setState(() => _applying = false);
      if (mounted) {
        final msg = e.toString();
        final friendly = msg.contains('冷却')
            ? '申请太频繁啦，2 分钟后再试试'
            : '申请失败，请稍后再试';
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(friendly)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
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
              'TA 把${widget.contentName}锁起来了',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 6),
            Text(
              '想看看就向 TA 申请吧',
              style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
            ),
            const SizedBox(height: 20),
            if (_cooldown > 0)
              Text(
                '申请冷却中 $_cooldown 秒',
                style: TextStyle(fontSize: 13, color: Colors.orange.shade700),
              )
            else
              FilledButton.icon(
                onPressed: _applying ? null : _apply,
                icon: _applying
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.mark_email_unread_outlined, size: 18),
                label: Text(_applying ? '申请中…' : '向 TA 申请查看'),
              ),
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: _refresh,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('刷新状态'),
            ),
          ],
        ),
      ),
    );
  }
}
