import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/tokens.dart';

import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 家庭管理员（#46 选择型，2026-08-24；A6 收口 2026-09-24 统一命名口径）
///
/// 家庭管理员 = users.is_admin=1 的账号集合，权限范围只在**本家庭内**
/// （后端 GET /admin/accounts 按家庭过滤，非管理员 403）。
/// 入口有两条：抽屉独立入口（按 isAdmin 隐藏）与本页面内占位（按服务端 403 兜底）。
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
    if (!_isAdmin) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
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
      );
    }
    return ListView(
      padding: const EdgeInsets.only(top: 8, bottom: 32),
      children: [
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
