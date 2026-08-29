import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../services/api_client.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/ios_card_group.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 账号关联页（#68 P3）：独立主账号视图（家庭信息/生成受邀码/复制/踢出）+ 子账号视图（兑换/主账号昵称/解除关联）。
class AccountLinkingScreen extends StatefulWidget {
  const AccountLinkingScreen({super.key});

  @override
  State<AccountLinkingScreen> createState() => _AccountLinkingScreenState();
}

class _AccountLinkingScreenState extends State<AccountLinkingScreen> {
  final TextEditingController _redeemCtrl = TextEditingController();
  Map<String, dynamic>? _family;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  String? _inviteCode;

  bool get _isSub => _family?['is_sub'] == true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _redeemCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final f = await ApiClient().getFamily();
      if (!mounted) return;
      setState(() {
        _family = f;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    }
  }

  Future<void> _generateInvite() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().generateInvite();
      if (!mounted) return;
      setState(() {
        _inviteCode = r['code'] as String?;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
    }
  }

  Future<void> _copyInvite() async {
    final code = _inviteCode;
    if (code == null || code.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: code));
    if (!mounted) return;
    final l10n = AppLocalizations.of(context)!;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.copied)));
  }

  Future<void> _redeem() async {
    if (_busy) return;
    final code = _redeemCtrl.text.trim();
    if (code.isEmpty) return;
    setState(() => _busy = true);
    final l10n = AppLocalizations.of(context)!;
    final sp = context.read<SettingsProvider>(); // 在异步前捕获，避免跨 gap 使用 context
    try {
      await ApiClient().redeemInvite(code);
      if (!mounted) return;
      _redeemCtrl.clear();
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.redeemSuccess)));
      await _load();
      // 更新本地身份：子账号已关联
      await sp.syncProfileFromServer();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.redeemFailed(e.toString()))));
    }
  }

  Future<void> _unlink({int? targetUserId}) async {
    if (_busy) return;
    final l10n = AppLocalizations.of(context)!;
    final sp = context.read<SettingsProvider>(); // 在异步前捕获，避免跨 gap 使用 context
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.unlink),
        content: Text(targetUserId == null ? l10n.accountSubHint : l10n.kickSubConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await ApiClient().unlink(targetUserId: targetUserId);
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.unlinkSuccess)));
      await _load();
      await sp.syncProfileFromServer();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.unlinkFailed(e.toString()))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.accountLinking)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 12),
                      FilledButton.tonal(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : _isSub
                  ? _subView(l10n)
                  : _mainView(l10n),
    );
  }

  /// 独立主账号视图。
  Widget _mainView(AppLocalizations l10n) {
    final members = (_family?['member_count'] as num?)?.toInt() ?? 0;
    final subs = ((_family?['sub_accounts'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final mainId = (_family?['root_id'] as num?)?.toInt();
    final settings = context.watch<SettingsProvider>();

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 8),
      children: [
        IosCardGroup(
          title: l10n.family,
          children: [
            ListTile(
              leading: Icon(Icons.groups_outlined, color: Theme.of(context).colorScheme.primary),
              title: Text(l10n.familyMemberCount(members)),
              subtitle: Text(l10n.accountMainHint),
            ),
          ],
        ),
        IosCardGroup(
          title: l10n.inviteCode,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(
                children: [
                  Expanded(
                    child: _inviteCode == null
                        ? Text(l10n.inviteCodeValidity,
                            style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle))
                        : SelectableText(
                            _inviteCode!,
                            style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold, letterSpacing: 2),
                          ),
                  ),
                  if (_inviteCode != null)
                    IconButton(
                      tooltip: l10n.copyInvite,
                      icon: const Icon(Icons.copy),
                      onPressed: _copyInvite,
                    ),
                  if (_busy)
                    const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  else
                    FilledButton.tonal(
                      onPressed: _generateInvite,
                      child: Text(_inviteCode == null ? l10n.generateInvite : l10n.copyInvite),
                    ),
                ],
              ),
            ),
            if (_inviteCode != null)
              const IosCardDivider(),
            if (_inviteCode != null)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: Text(l10n.inviteCodeValidity,
                    style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
              ),
          ],
        ),
        IosCardGroup(
          title: l10n.subAccount,
          children: [
            if (subs.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                child: Text(l10n.noSubAccounts, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
              )
            else
              for (var i = 0; i < subs.length; i++) ...[
                if (i > 0) const IosCardDivider(),
                ListTile(
                  leading: CircleAvatar(
                    backgroundColor: Colors.blue.withValues(alpha: 0.2),
                    child: Text((subs[i]['nickname'] as String?)?.isNotEmpty == true
                        ? ((subs[i]['nickname'] as String).substring(0, 1))
                        : '?'),
                  ),
                  title: Text(subs[i]['nickname'] as String? ?? ''),
                  subtitle: Text(subs[i]['username'] as String? ?? ''),
                  trailing: (subs[i]['id'] == settings.userId || subs[i]['id'] == mainId)
                      ? null
                      : TextButton(
                          onPressed: _busy ? null : () => _unlink(targetUserId: (subs[i]['id'] as num).toInt()),
                          child: Text(l10n.kickSub),
                        ),
                ),
              ],
          ],
        ),
      ],
    );
  }

  /// 子账号视图。
  Widget _subView(AppLocalizations l10n) {
    final main = (_family?['main_account'] as Map<String, dynamic>?) ?? const {};
    final mainName = main['nickname'] as String? ?? '';

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 8),
      children: [
        IosCardGroup(
          title: l10n.mainAccount,
          children: [
            ListTile(
              leading: CircleAvatar(
                backgroundColor: Colors.blue.withValues(alpha: 0.2),
                child: Text(mainName.isNotEmpty ? mainName.substring(0, 1) : '?'),
              ),
              title: Text(mainName),
              subtitle: Text(main['username'] as String? ?? ''),
            ),
            const IosCardDivider(),
            ListTile(
              leading: Icon(Icons.link_off, color: Theme.of(context).colorScheme.primary),
              title: Text(l10n.unlink),
              subtitle: Text(l10n.accountSubHint),
              trailing: IconButton(
                icon: const Icon(Icons.chevron_right),
                onPressed: _busy ? null : () => _unlink(),
              ),
            ),
          ],
        ),
        IosCardGroup(
          title: l10n.enterInviteCode,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: _redeemCtrl,
                    style: const TextStyle(letterSpacing: 2, fontSize: 18, fontWeight: FontWeight.bold),
                    textAlign: TextAlign.center,
                    autofocus: false,
                    decoration: const InputDecoration(
                      hintText: 'XXXX0000',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton(
                      onPressed: _busy ? null : _redeem,
                      child: _busy
                          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : Text(l10n.redeem),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ],
    );
  }
}
