import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/tokens.dart';

import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../widgets/app_page_route.dart';
import '../../widgets/ios_card_group.dart';
import 'account_linking_screen.dart';

/// 家庭管理员（#46 选择型，2026-08-24；A6 收口 2026-09-24 统一命名口径）
///
/// 家庭管理员 = users.is_admin=1 的账号集合，权限范围只在**本家庭内**
/// （后端 GET /admin/accounts 按家庭过滤，非管理员 403）。
/// 入口有两条：抽屉独立入口（按 isAdmin 隐藏）与本页面内占位（按服务端 403 兜底）。
/// 2026-09-24 起本页还承接「账号关联」入口（原抽屉项，用户拍板收进本页）。
class AccountAdminScreen extends StatefulWidget {
  const AccountAdminScreen({super.key, this.showAppBar = true});

  /// 是否渲染独立 AppBar/Scaffold；作为「权限管理」合并页 tab body 时传 false。
  final bool showAppBar;

  @override
  State<AccountAdminScreen> createState() => _AccountAdminScreenState();
}

class _AccountAdminScreenState extends State<AccountAdminScreen> {
  bool _loading = true;
  bool _isAdmin = false;
  List<Map<String, dynamic>> _accounts = [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _isAdmin = context.read<SettingsProvider>().isAdmin;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final accounts = await ApiClient().listAccounts();
      if (!mounted) return;
      setState(() {
        _accounts = accounts;
        _isAdmin = true; // 列表拉取成功即本账号是本家庭管理员（服务端权威）
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        if (e is DioException && e.response?.statusCode == 403) {
          _isAdmin = false;
        } else {
          _error = e.toString();
        }
      });
    }
  }

  Future<void> _toggle(Map<String, dynamic> acc, bool value) async {
    final l10n = AppLocalizations.of(context)!;
    final prev = acc['is_admin'] as bool;
    setState(() => acc['is_admin'] = value);
    try {
      final id = acc['id'] as int;
      await ApiClient().setAccountAdmin(id, value);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.accountAdminSaved)));
      // 以后端为准重拉列表（后端可能按规则改写，乐观状态不足以代表最终结果）
      await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => acc['is_admin'] = prev);
      String msg = l10n.accountAdminFailed;
      if (e is DioException) {
        final status = e.response?.statusCode;
        final rawDetail = e.response?.data?['detail'];
        // 后端 detail 原文仅在「非空字符串」时直接展示，否则回落本地文案
        final detail =
            (rawDetail is String && rawDetail.isNotEmpty) ? rawDetail : null;
        if (status == 400) {
          // 区分两种 400：权限不足/至少保留一个 → 复用保留一个文案；其余展示服务端 detail
          if (detail == 'main_account_manage_only' || detail == 'admin_keep_one') {
            msg = l10n.accountAdminKeepOne;
          } else {
            msg = detail ?? l10n.accountAdminFailed;
          }
        } else if (status == 403) {
          // 优先展示后端 detail 原文（后端按请求语言下发原因），为空才回落本地文案
          msg = detail ?? l10n.accountAdminOnly;
        } else if (detail != null) {
          msg = detail;
        }
      }
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(msg)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final body = _body(l10n);
    if (!widget.showAppBar) return body;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        title: Text(l10n.accountAdminTitle),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _load,
            tooltip: l10n.refresh,
          ),
        ],
      ),
      body: body,
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.cloud_off, size: 48, color: Colors.grey),
              const SizedBox(height: 12),
              Text(l10n.accountAdminLoadFailed, textAlign: TextAlign.center),
              const SizedBox(height: 16),
              OutlinedButton(
                onPressed: _load,
                child: Text(l10n.retry),
              ),
            ],
          ),
        ),
      );
    }
    // 非家庭管理员（含子账号）：占位照旧，但**家庭工具卡要保留**——子账号的
    // 「兑换受邀码 / 解除关联」只在这里和抽屉里能进，抽屉那条按 isAdmin 收走了。
    if (!_isAdmin) {
      return ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 32),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(32, 32, 32, 8),
            child: Column(
              children: [
                const Icon(Icons.lock_outline, size: 48, color: Colors.grey),
                const SizedBox(height: 12),
                Text(l10n.accountAdminOnly, textAlign: TextAlign.center),
                const SizedBox(height: 8),
                Text(
                  l10n.accountAdminOnlyHint,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                      fontSize: 12,
                      color: AppColors.textSecondary,
                      height: 1.4),
                ),
              ],
            ),
          ),
          _familyToolsCard(l10n),
        ],
      );
    }
    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 32),
      children: [
        _familyToolsCard(l10n),
        IosCardGroup(
          title: l10n.accountAdminListTitle,
          children: [
            for (final acc in _accounts) _accountRow(acc),
          ],
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 0),
          child: Text(
            l10n.accountAdminHint,
            textAlign: TextAlign.center,
            style: const TextStyle(
                fontSize: 11, color: AppColors.textSecondary, height: 1.4),
          ),
        ),
      ],
    );
  }

  /// 家庭工具卡（2026-09-24 用户拍板）：把抽屉里的「账号关联」收进家庭管理员页。
  ///
  /// 管理员看到的「账号关联」＝生成受邀码 / 踢出子账号；子账号看到的＝兑换受邀码 /
  /// 解除关联。两边都要能进，所以本卡在**管理员与非管理员分支里都渲染**。
  Widget _familyToolsCard(AppLocalizations l10n) {
    return IosCardGroup(
      title: l10n.accountAdminTools,
      children: [
        ListTile(
          leading: const Icon(Icons.family_restroom, size: 20),
          title: Text(
            l10n.accountLinking,
            style: const TextStyle(fontSize: 15, color: AppColors.textPrimary),
          ),
          subtitle: Text(
            l10n.accountLinkingHint,
            style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
          ),
          trailing: const Icon(Icons.chevron_right, size: 18),
          onTap: () => Navigator.push(
              context, AppPageRoute(builder: (_) => const AccountLinkingScreen())),
        ),
      ],
    );
  }

  Widget _accountRow(Map<String, dynamic> acc) {
    final l10n = AppLocalizations.of(context)!;
    final id = acc['id'] as int? ?? 0;
    final username = acc['username'] as String? ?? '';
    final nickname = acc['nickname'] as String? ?? '';
    final avatarUrl = acc['avatar_url'] as String?;
    final displayName = nickname.isNotEmpty ? nickname : username;
    final isAdmin = (acc['is_admin'] as bool?) ?? false;
    final isSelf = (acc['is_self'] as bool?) ?? false;
    final parentId = acc['parent_id'] as int?;
    final isSubAccount = parentId != null;

    // 副标题：自己（家庭管理员 + 我）/ 子账号 / 其它独立账号
    String subtitle;
    if (isSelf) {
      subtitle = l10n.accountMainLabel;
    } else if (isSubAccount) {
      subtitle = '${l10n.accountSubLabel} · $username · #$id';
    } else {
      subtitle = '$username · #$id';
    }

    return SwitchListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      secondary: _avatar(avatarUrl, displayName),
      title: Text(
        displayName,
        style: const TextStyle(fontSize: 15, color: AppColors.textPrimary),
      ),
      subtitle: Text(
        subtitle,
        style: const TextStyle(fontSize: 11, color: AppColors.textSecondary),
      ),
      value: isAdmin,
      activeThumbColor: AppColors.accent,
      // 自己的开关禁用（不能取消自己）；家庭内其它账号可由本家庭管理员切换
      onChanged: isSelf ? null : (value) => _toggle(acc, value),
    );
  }

  Widget _avatar(String? url, String name) {
    final resolved = ApiClient().resolveUrl(url);
    if (resolved.isNotEmpty) {
      return CircleAvatar(
        radius: 18,
        backgroundImage: NetworkImage(resolved),
        onBackgroundImageError: (_, __) {},
      );
    }
    return CircleAvatar(
      radius: 18,
      backgroundColor: AppColors.accentBlue.withValues(alpha: 0.15),
      child: Text(
        name.isNotEmpty ? name.characters.first : '?',
        style: const TextStyle(fontSize: 14, color: AppColors.accent),
      ),
    );
  }
}
