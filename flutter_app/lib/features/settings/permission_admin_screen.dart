import 'package:flutter/material.dart';

import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/theme/tokens.dart';

import 'permission_settings_screen.dart';
import 'account_admin_screen.dart';
import 'feature_flags_screen.dart';

/// 权限管理统一入口（2026-08-24）：
/// 「权限管理」/「主账号管理」/「服务器功能管理」合并为内部 3 tab——
/// AI 能力权限 / 主账号管理 / 服务器功能管理。
/// 三个原子页各自复用自身 body（去掉独立 AppBar/Scaffold，showAppBar=false 内嵌）；
/// 非主账号进入「主账号管理」tab 由 AccountAdminScreen 内建 isAdmin 判定显示「仅主账号可管理」占位。
class PermissionAdminScreen extends StatelessWidget {
  const PermissionAdminScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        backgroundColor: AppColors.bgLight,
        appBar: AppBar(
          title: Text(l10n.permissionManagementTitle),
          bottom: TabBar(
            tabs: [
              Tab(text: l10n.permTitle),
              Tab(text: l10n.accountAdminTitle),
              Tab(text: l10n.featureFlagsTitle),
            ],
          ),
        ),
        body: const TabBarView(
          children: [
            PermissionSettingsScreen(showAppBar: false),
            AccountAdminScreen(showAppBar: false),
            FeatureFlagsScreen(showAppBar: false),
          ],
        ),
      ),
    );
  }
}
