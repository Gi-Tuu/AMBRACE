import 'dart:io';

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';

import '../providers/settings_provider.dart';
import '../services/api_client.dart';
import '../services/background_polling_service.dart';
import '../services/fcm_push_service.dart';
import '../theme/aurora_tokens.dart';
import '../theme/tokens.dart';
import 'app_page_route.dart';
import '../screens/home/profile_screen.dart';
import '../screens/settings/dnd_settings_screen.dart';
import '../screens/settings/api_config_screen.dart';
import '../screens/settings/permission_admin_screen.dart';
import '../screens/phone/phone_perception_screen.dart';
import '../screens/settings/appearance_screen.dart';
import '../screens/settings/account_linking_screen.dart';
import '../screens/plugin/extensions_screen.dart';
import '../screens/settings/support_screen.dart';
import '../screens/settings/backup_screen.dart';
import '../screens/settings/update_announcement_screen.dart';
import '../screens/auth/user_agreement_screen.dart';
import '../screens/auth/onboarding_screen.dart';

/// 全局抽屉开关：HomeScreen 监听此值渲染抽屉覆盖层，
/// 好友/朋友圈/宠物页通过 open/toggle 呼出，避免静态回调耦合。
class AppDrawerController {
  static final ValueNotifier<bool> isOpen = ValueNotifier<bool>(false);
  static void open() => isOpen.value = true;
  static void close() => isOpen.value = false;
  static void toggle() => isOpen.value = !isOpen.value;
}

/// 首页侧抽屉内容（Aurora Phase 2 B1 视觉升级）。
///
/// 从 home_screen.dart 抽出为独立组件，便于测试与复用；交互逻辑
/// （各入口跳转/退出登录/改头像/测连接）与抽屉控制器保持不变。
///
/// 视觉：顶部用户信息区 aurora 渐变 + 头像白描边环 + 昵称 18px w700；
/// 分组标题左侧 3px 主题色圆点；行图标 22px 置于 8px 圆角主题色 0.08 容器；
/// 退出登录行图标与文字红色。
class HomeDrawer extends StatelessWidget {
  // 包信息全进程只取一次，避免 build 内联 Future 导致版本号闪空/重复平台调用
  static final Future<PackageInfo> _packageInfo = PackageInfo.fromPlatform();

  const HomeDrawer({
    super.key,
    required this.settings,
    required this.onClose,
  });

  final SettingsProvider settings;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final s = settings;
    final l10n = AppLocalizations.of(context)!;
    const textColor = AppColors.textPrimary;
    const subColor = AppColors.textSecondary;
    const chevColor = AppColors.separator;
    final scheme = Theme.of(context).colorScheme;

    Widget group(String title, List<Widget> rows) {
      return Padding(
        padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(left: 16, bottom: 6),
              child: Row(
                children: [
                  Container(
                    width: 3,
                    height: 3,
                    decoration:
                        BoxDecoration(color: scheme.primary, shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 6),
                  Text(title,
                      style: const TextStyle(
                          fontSize: 12, fontWeight: FontWeight.w600, color: subColor)),
                ],
              ),
            ),
            Container(
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surface,
                borderRadius: BorderRadius.circular(12),
              ),
              // 透明 Material：组内 ListTile 需要最近的 Material 祖先，
              // 否则 debug 断言报「背景色/墨水效果被 DecoratedBox 遮挡」
              child: Material(
                type: MaterialType.transparency,
                child: Column(children: rows),
              ),
            ),
          ],
        ),
      );
    }

    Widget row({
      required IconData icon,
      required String title,
      String? subtitle,
      Color? titleColor,
      Color? iconColor,
      Widget? trailing,
      VoidCallback? onTap,
    }) {
      return InkWell(
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 52),
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: Row(children: [
            _RowIcon(icon: icon, color: iconColor),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(title,
                      style: TextStyle(fontSize: 15, color: titleColor ?? textColor)),
                  if (subtitle != null) ...[
                    const SizedBox(height: 1),
                    Text(subtitle,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 11, color: subColor)),
                  ],
                ],
              ),
            ),
            if (trailing != null)
              trailing
            else
              const Icon(Icons.chevron_right, size: 18, color: chevColor),
          ]),
        ),
      );
    }

    Widget divider() => Container(
          height: 0.5,
          margin: const EdgeInsets.only(left: 58),
          color: Theme.of(context).dividerColor,
        );

    return ListView(padding: EdgeInsets.zero, children: [
      // 顶部用户信息：aurora 渐变背景，点击头像区域进入个人主页
      InkWell(
        onTap: () {
          onClose();
          Navigator.push(context, AppPageRoute(builder: (_) => const ProfileScreen()));
        },
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 24, 16, 16),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: AppGradient.aurora(
                primary: scheme.primary,
                secondary: scheme.secondary,
                surface: scheme.surface,
              ),
            ),
          ),
          child: Row(children: [
            GestureDetector(
              onTap: () => _changeAvatar(context, s),
              child: Stack(
                children: [
                  // 头像白色描边环 2px
                  Container(
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white, width: 2),
                    ),
                    padding: const EdgeInsets.all(2),
                    child: CircleAvatar(
                      radius: 28,
                      backgroundColor: scheme.secondaryContainer,
                      child: s.avatarUrl.isNotEmpty
                          ? ClipOval(
                              child: Image.network(
                                  ApiClient().resolveUrl(s.avatarUrl),
                                  width: 56,
                                  height: 56,
                                  fit: BoxFit.cover,
                                  errorBuilder: (context, error, stack) => Text(
                                      s.nickname.isNotEmpty ? s.nickname[0] : "?",
                                      style: const TextStyle(fontSize: 22))),
                            )
                          : Text(s.nickname.isNotEmpty ? s.nickname[0] : "?",
                              style: const TextStyle(fontSize: 22)),
                    ),
                  ),
                  Positioned(
                    right: 0,
                    bottom: 0,
                    child: Container(
                      padding: const EdgeInsets.all(3),
                      decoration:
                          BoxDecoration(color: scheme.primary, shape: BoxShape.circle),
                      child: Icon(Icons.photo_camera, size: 12, color: scheme.onPrimary),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 14),
            Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(s.nickname,
                  style:
                      const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              const SizedBox(height: 2),
              Text('${l10n.userId}: ${s.userId}',
                  style: TextStyle(fontSize: 13, color: Colors.grey.shade600)),
            ])),
            const Icon(Icons.chevron_right, size: 20, color: Colors.grey),
          ]),
        ),
      ),
      const SizedBox(height: 16),
      // 连接组：服务器地址 / 连接状态
      group(l10n.groupConnection, [
        row(
          icon: Icons.computer_outlined,
          title: l10n.serverAddress,
          subtitle: s.serverUrl,
          onTap: () {
            onClose();
            _editUrl(context, s);
          },
        ),
        divider(),
        row(
          icon: Icons.wifi_outlined,
          title: l10n.connectionStatus,
          subtitle: s.isConnected ? l10n.connected : l10n.disconnected,
          onTap: () => _checkConnection(context, s),
          trailing: TextButton(
            onPressed: () => _checkConnection(context, s),
            child: Text(l10n.testConnection),
          ),
        ),
      ]),
      // 体验组：体验设置（手机感知/免打扰/扩展/应用容貌 收纳于此）+ API 配置
      group(l10n.groupExperience, [
        ExpansionTile(
          leading: const _RowIcon(icon: Icons.tune),
          title: Text(l10n.experienceSettingsTitle,
              style: const TextStyle(fontSize: 15, color: textColor)),
          subtitle: Text(l10n.experienceSettingsSubtitle,
              style: const TextStyle(fontSize: 11, color: subColor)),
          tilePadding: const EdgeInsets.symmetric(horizontal: 14),
          childrenPadding: const EdgeInsets.only(left: 8, right: 8, bottom: 8),
          shape: const Border(),
          collapsedShape: const Border(),
          iconColor: chevColor,
          collapsedIconColor: chevColor,
          backgroundColor: Colors.transparent,
          collapsedBackgroundColor: Colors.transparent,
          children: [
            ListTile(
              leading: const _RowIcon(icon: Icons.visibility_outlined),
              title: Text(l10n.phonePerception, style: const TextStyle(fontSize: 14)),
              subtitle:
                  Text(l10n.phonePerceptionHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                onClose();
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const PhonePerceptionScreen()));
              },
            ),
            ListTile(
              leading: const _RowIcon(icon: Icons.do_not_disturb),
              title: Text(l10n.dnd, style: const TextStyle(fontSize: 14)),
              subtitle: Text(l10n.dndHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                onClose();
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const DndSettingsScreen()));
              },
            ),
            ListTile(
              leading: const _RowIcon(icon: Icons.extension_outlined),
              title: Text(l10n.extensions, style: const TextStyle(fontSize: 14)),
              subtitle:
                  Text(l10n.extensionsHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                onClose();
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const ExtensionsScreen()));
              },
            ),
            ListTile(
              leading: const _RowIcon(icon: Icons.palette_outlined),
              title: Text(l10n.appearanceTitle, style: const TextStyle(fontSize: 14)),
              subtitle:
                  Text(l10n.appearanceSubtitle, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                onClose();
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const AppearanceScreen()));
              },
            ),
            ListTile(
              leading: const _RowIcon(icon: Icons.family_restroom),
              title: Text(l10n.accountLinking, style: const TextStyle(fontSize: 14)),
              subtitle:
                  Text(l10n.accountLinkingHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                onClose();
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const AccountLinkingScreen()));
              },
            ),
          ],
        ),
        divider(),
        // #11 织库总入口从侧抽屉移除（已并入角色详情页 / 首页工具箱）
        row(
          icon: Icons.admin_panel_settings_outlined,
          title: l10n.permissionManagementTitle,
          subtitle: l10n.permissionManagementHint,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const PermissionAdminScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.key_outlined,
          title: l10n.apiConfig,
          subtitle: l10n.apiConfigHint,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const ApiConfigScreen()));
          },
        ),
      ]),
      // 系统组：更新公告 / 数据备份 / 支持作者
      group(l10n.groupSystem, [
        row(
          icon: Icons.campaign_outlined,
          title: l10n.updateAnnouncement,
          subtitle: l10n.updateAnnouncementHint,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const UpdateAnnouncementScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.backup_outlined,
          title: l10n.backupTitle,
          subtitle: l10n.backupSubtitle,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const BackupScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.favorite_outline,
          title: l10n.supportAuthor,
          subtitle: l10n.supportAuthorHint,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const SupportScreen()));
          },
        ),
      ]),
      // 关于组：关于/版本 + 退出登录
      group(l10n.groupAbout, [
        row(
          icon: Icons.info_outline,
          title: l10n.about,
          trailing: FutureBuilder<PackageInfo>(
            future: _packageInfo,
            builder: (context, snap) {
              final info = snap.data;
              final ver = info == null
                  ? ''
                  : 'v${info.version}'
                      '${info.buildNumber.isNotEmpty ? ' (${info.buildNumber})' : ''}';
              return Text(
                ver,
                style: const TextStyle(fontSize: 12, color: AppColors.textSecondary),
              );
            },
          ),
        ),
        divider(),
        row(
          icon: Icons.description_outlined,
          title: l10n.userAgreementTitle,
          subtitle: l10n.userAgreementHint,
          onTap: () {
            onClose();
            Navigator.push(
                context, AppPageRoute(builder: (_) => const UserAgreementScreen()));
          },
        ),
        if (s.isLoggedIn) divider(),
        if (s.isLoggedIn)
          row(
            icon: Icons.restart_alt,
            title: l10n.onboardingReRun,
            onTap: () async {
              onClose();
              final ok = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: Text(l10n.onboardingReRun),
                  content: Text(l10n.onboardingReRunConfirm),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(ctx, false),
                        child: Text(l10n.cancel)),
                    FilledButton(
                        onPressed: () => Navigator.pop(ctx, true),
                        child: Text(l10n.confirm)),
                  ],
                ),
              );
              if (ok == true && context.mounted) {
                Navigator.push(
                    context, AppPageRoute(builder: (_) => const OnboardingScreen()));
              }
            },
          ),
        if (s.isLoggedIn) divider(),
        if (s.isLoggedIn)
          row(
            icon: Icons.logout,
            iconColor: Colors.red,
            title: l10n.logout,
            titleColor: Colors.red,
            onTap: () async {
              onClose();
              await BackgroundPollingService.stop();
              // FCM 离线推送注销（失败不影响登出）
              try {
                await FcmPushService.instance.unregister();
              } catch (_) {}
              await s.logout();
              if (context.mounted) Navigator.pushReplacementNamed(context, '/');
            },
          ),
      ]),
      const SizedBox(height: 8),
    ]);
  }

  Future<void> _checkConnection(BuildContext c, SettingsProvider s) async {
    final l10n = AppLocalizations.of(c)!;
    onClose();
    await s.testConnection();
    if (c.mounted) {
      ScaffoldMessenger.of(c).showSnackBar(
        SnackBar(content: Text(s.isConnected ? l10n.connectSuccess : l10n.connectFail)),
      );
    }
  }

  Future<void> _changeAvatar(BuildContext c, SettingsProvider s) async {
    final l10n = AppLocalizations.of(c)!;
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1024,
      maxHeight: 1024,
      imageQuality: 85,
    );
    if (picked == null || !c.mounted) return;
    try {
      final up = await ApiClient().uploadAvatar(File(picked.path));
      final url = up["url"] as String? ?? "";
      if (url.isEmpty) throw Exception("empty url");
      await ApiClient().updateProfile({"avatar_url": url});
      await s.setAvatarUrl(url);
      if (c.mounted) {
        ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text(l10n.avatarUpdated)));
      }
    } catch (e) {
      if (c.mounted) {
        ScaffoldMessenger.of(c)
            .showSnackBar(SnackBar(content: Text(l10n.avatarUpdateFailed)));
      }
    }
  }

  void _editUrl(BuildContext c, SettingsProvider s) {
    final l10n = AppLocalizations.of(c)!;
    var ct = TextEditingController(text: s.serverUrl);
    showDialog(
        context: c,
        builder: (ctx) => AlertDialog(
              title: Text(l10n.serverAddress),
              content: TextField(
                  controller: ct,
                  decoration:
                      const InputDecoration(hintText: 'http://192.168.x.x:8000')),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
                FilledButton(
                    onPressed: () {
                      s.setServerUrl(ct.text);
                      Navigator.pop(ctx);
                    },
                    child: Text(l10n.save)),
              ],
            )).then((_) => ct.dispose());
  }
}

/// 行图标：22px 图标置于 8px 圆角、主题色 0.08 透明度背景的容器内。
class _RowIcon extends StatelessWidget {
  const _RowIcon({required this.icon, this.color});

  final IconData icon;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 32,
      height: 32,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: scheme.primary.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Icon(icon, size: 22, color: color ?? scheme.primary),
    );
  }
}
