import "dart:async";
import "dart:io";
import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "package:image_picker/image_picker.dart";
import "package:provider/provider.dart";
import "../../providers/settings_provider.dart";
import "../../services/api_client.dart";
import "../../services/home_tab_controller.dart";
import "../../services/notification_service.dart";
import "../../services/background_polling_service.dart";
import "character_list_screen.dart";
import "../social/moments_screen.dart";
import "profile_screen.dart";
import "../settings/dnd_settings_screen.dart";
import "../settings/api_config_screen.dart";
import "../settings/permission_settings_screen.dart";
import "../phone/phone_perception_screen.dart";
import "../phone/ai_interaction_screen.dart";
import "../settings/appearance_screen.dart";
import "../plugin/extensions_screen.dart";
import "../settings/support_screen.dart";
import "../settings/update_announcement_screen.dart";
import "../auth/user_agreement_screen.dart";
import "../life/home_visual_screen.dart";
import "../weave/weave_library_screen.dart";
import "../../providers/pets_provider.dart";

class AppDrawerController {
  /// 全局抽屉开关：HomeScreen 监听此值渲染抽屉覆盖层，
  /// 好友/朋友圈/宠物页通过 open/toggle 呼出，避免静态回调耦合。
  static final ValueNotifier<bool> isOpen = ValueNotifier<bool>(false);
  static void open() => isOpen.value = true;
  static void close() => isOpen.value = false;
  static void toggle() => isOpen.value = !isOpen.value;
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with SingleTickerProviderStateMixin {
  int _currentIndex = 0;
  int _momentUnread = 0;
  Timer? _momentTimer;
  bool _drawerOpen = false;
  bool _dragging = false;
  double _dragStartValue = 0;
  late final PageController _pageController = PageController();
  late final AnimationController _drawerCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 260),
  );
  final _pages = <Widget>[
    const CharacterListScreen(),
    const MomentsScreen(),
    const AiInteractionScreen(),
    const HomeVisualScreen(),
  ];

  @override
  void initState() {
    super.initState();
    _momentTimer = Timer.periodic(const Duration(seconds: 30), (_) => _refreshMomentUnread());
    _refreshMomentUnread();
    NotificationService().startPolling();
    // 【已修复 2026-08-04】后台常驻服务正式启用：根因=服务 channel 未创建导致 startForeground 崩溃（CannotPostForegroundServiceNotificationException），已在 ensureConfigured 中补建 channel
    BackgroundPollingService.start();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final settings = context.read<SettingsProvider>();
      await settings.load();
      if (settings.isLoggedIn) settings.syncProfileFromServer();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) { context.read<PetsProvider>().loadPets(); });
    AppDrawerController.isOpen.addListener(_onDrawerChanged);
    HomeTabController.index.addListener(_onTabChanged);
    _drawerCtrl.addListener(() {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    AppDrawerController.isOpen.removeListener(_onDrawerChanged);
    HomeTabController.index.removeListener(_onTabChanged);
    _momentTimer?.cancel();
    _pageController.dispose();
    _drawerCtrl.dispose();
    NotificationService().stopPolling();
    super.dispose();
  }

  void _onDrawerChanged() {
    setState(() => _drawerOpen = AppDrawerController.isOpen.value);
    if (_dragging) return;
    if (AppDrawerController.isOpen.value) {
      _drawerCtrl.forward();
    } else {
      _drawerCtrl.reverse();
    }
  }

  void _closeDrawer() {
    if (_drawerOpen) AppDrawerController.close();
  }

  /// 抽屉拖动（遮罩/左边缘热区共用）：位移换算成进度，跟手滑动
  void _onDrawerDragStart(DragStartDetails d) {
    _dragging = true;
    _dragStartValue = _drawerCtrl.value;
  }

  void _onDrawerDragUpdate(DragUpdateDetails d) {
    final dw2 = MediaQuery.of(context).size.width * 0.72;
    final v = (_dragStartValue + (d.primaryDelta ?? 0) / dw2).clamp(0.0, 1.0);
    _drawerCtrl.value = v;
  }

  void _onDrawerDragEnd(DragEndDetails d) {
    _dragging = false;
    final v = _drawerCtrl.value;
    final vel = d.primaryVelocity ?? 0;
    final open = vel > 300 || (vel >= -300 && v >= 0.5);
    AppDrawerController.isOpen.value = open;
  }

  void _onDrawerDragCancel() {
    _dragging = false;
    AppDrawerController.isOpen.value = _drawerCtrl.value >= 0.5;
  }

  /// 左边缘热区开始拖动：先激活抽屉覆盖层（不触发动画打断），再跟手
  void _onEdgeDragStart(DragStartDetails d) {
    _dragging = true;
    AppDrawerController.isOpen.value = true;
    _dragStartValue = _drawerCtrl.value;
  }

  /// 朋友圈"回复我的"未读数（30s 轮询刷新，红点角标）
  Future<void> _refreshMomentUnread() async {
    try {
      final n = await ApiClient().getUnreadComments();
      if (mounted && n != _momentUnread) setState(() => _momentUnread = n);
    } catch (_) {}
  }

  void _markMomentsRead() {
    ApiClient().markMomentsRead().catchError((_) {});
    _momentUnread = 0;
  }

  /// 手机感知序列切换首页 tab（0=好友列表 / 1=朋友圈 / 2=手机 / 3=宠物）
  void _onTabChanged() {
    if (!mounted) return;
    _closeDrawer();
    _pageController.animateToPage(
      HomeTabController.index.value.clamp(0, _pages.length - 1),
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeOutCubic,
    );
  }

  /// PageView 切页副作用：同步选中项、关抽屉、朋友圈红点、前台屏幕
  void _onPageChanged(int i) {
    if (!mounted) return;
    setState(() => _currentIndex = i);
    _closeDrawer();
    if (i == 1) _markMomentsRead();
    NotificationService().setActiveScreen(
      i == 0 ? ActiveScreen.characterList : ActiveScreen.other,
    );
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final l10n = AppLocalizations.of(context)!;
    final dw = MediaQuery.of(context).size.width * 0.72;

    return Stack(children: [
      Scaffold(
        body: Stack(
          children: [
            PageView(
              controller: _pageController,
              onPageChanged: _onPageChanged,
              children: _pages,
            ),
            // 左边缘热区：手指右滑拉出抽屉（只响应横向拖动）
            Positioned(
              left: 0,
              top: 0,
              bottom: 0,
              width: 22,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onHorizontalDragStart: _onEdgeDragStart,
                onHorizontalDragUpdate: _onDrawerDragUpdate,
                onHorizontalDragEnd: _onDrawerDragEnd,
                onHorizontalDragCancel: _onDrawerDragCancel,
              ),
            ),
          ],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _currentIndex,
          onDestinationSelected: (i) {
            _closeDrawer();
            _pageController.animateToPage(
              i,
              duration: const Duration(milliseconds: 300),
              curve: Curves.easeOutCubic,
            );
          },
          destinations: [
            NavigationDestination(icon: const Icon(Icons.people), label: l10n.tabFriends),
            NavigationDestination(
              icon: _MomentTabIcon(unread: _momentUnread),
              label: l10n.tabMoments,
            ),
            NavigationDestination(icon: const Icon(Icons.forum_outlined), label: l10n.tabAiInteraction),
            NavigationDestination(
              icon: const Icon(Icons.home_outlined),
              label: '小家',
            ),
          ],
        ),
      ),
      IgnorePointer(
        ignoring: !_drawerOpen,
        child: AnimatedBuilder(
          animation: _drawerCtrl,
          builder: (context, _) {
            final v = _drawerCtrl.value;
            return Opacity(
              opacity: v,
              child: GestureDetector(
                onTap: _closeDrawer,
                onHorizontalDragStart: _onDrawerDragStart,
                onHorizontalDragUpdate: _onDrawerDragUpdate,
                onHorizontalDragEnd: _onDrawerDragEnd,
                onHorizontalDragCancel: _onDrawerDragCancel,
                child: SizedBox.expand(
                  child: Stack(children: [
                    Container(color: Colors.black.withValues(alpha: 0.45 * v)),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Transform.translate(
                        offset: Offset(-dw * (1 - v), 0),
                        child: SizedBox(
                          width: dw,
                          child: Material(elevation: 8, child: SafeArea(child: _drawerContent(settings, context))),
                        ),
                      ),
                    ),
                  ]),
                ),
              ),
            );
          },
        ),
      ),
    ]);
  }

  Widget _drawerContent(SettingsProvider s, BuildContext c) {
    final l10n = AppLocalizations.of(c)!;
    const textColor = Color(0xFF1C1C1E);
    const subColor = Color(0xFF8E8E93);
    const chevColor = Color(0xFFC6C6C8);

    Widget group(String title, List<Widget> rows) {
      return Padding(
        padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(left: 16, bottom: 6),
              child: Text(title,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: subColor)),
            ),
            Container(
              decoration: BoxDecoration(
                color: Theme.of(c).colorScheme.surface,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(children: rows),
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
      Widget? trailing,
      VoidCallback? onTap,
    }) {
      return InkWell(
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 52),
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: Row(children: [
            Icon(icon, size: 20, color: const Color(0xFF007AFF)),
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
          margin: const EdgeInsets.only(left: 46),
          color: Theme.of(c).dividerColor,
        );

    return ListView(padding: EdgeInsets.zero, children: [
      // 顶部用户信息：点击头像区域进入个人主页
      InkWell(
        onTap: () {
          _closeDrawer();
          Navigator.push(c, MaterialPageRoute(builder: (_) => const ProfileScreen()));
        },
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 24, 16, 16),
          decoration: BoxDecoration(color: Theme.of(c).colorScheme.primaryContainer),
          child: Row(children: [
            GestureDetector(
              onTap: () => _changeAvatar(c, s),
              child: Stack(
                children: [
                  CircleAvatar(
                    radius: 28,
                    backgroundColor: Theme.of(c).colorScheme.secondaryContainer,
                    child: s.avatarUrl.isNotEmpty
                        ? ClipOval(
                            child: Image.network(ApiClient().resolveUrl(s.avatarUrl), width: 56, height: 56, fit: BoxFit.cover,
                              errorBuilder: (context, error, stack) => Text(s.nickname.isNotEmpty ? s.nickname[0] : "?", style: const TextStyle(fontSize: 22)),
                            ),
                          )
                        : Text(s.nickname.isNotEmpty ? s.nickname[0] : "?", style: const TextStyle(fontSize: 22)),
                  ),
                  Positioned(
                    right: 0, bottom: 0,
                    child: Container(
                      padding: const EdgeInsets.all(3),
                      decoration: BoxDecoration(color: Theme.of(c).colorScheme.primary, shape: BoxShape.circle),
                      child: Icon(Icons.photo_camera, size: 12, color: Theme.of(c).colorScheme.onPrimary),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 14),
            Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(s.nickname, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              const SizedBox(height: 2),
              Text('${l10n.userId}: ${s.userId}', style: TextStyle(fontSize: 13, color: Colors.grey.shade600)),
            ])),
            const Icon(Icons.chevron_right, size: 20, color: Colors.grey),
          ]),
        ),
      ),
      const SizedBox(height: 16),
      // 连接组：服务器地址 / 连接状态
      group('连接', [
        row(
          icon: Icons.computer_outlined,
          title: l10n.serverAddress,
          subtitle: s.serverUrl,
          onTap: () {
            _closeDrawer();
            _editUrl(c, s);
          },
        ),
        divider(),
        row(
          icon: Icons.wifi_outlined,
          title: l10n.connectionStatus,
          subtitle: s.isConnected ? l10n.connected : l10n.disconnected,
          trailing: Switch(
            value: s.isConnected,
            onChanged: (_) async {
              _closeDrawer();
              await s.testConnection();
              if (c.mounted) {
                ScaffoldMessenger.of(c).showSnackBar(
                  SnackBar(content: Text(s.isConnected ? l10n.connectSuccess : l10n.connectFail)),
                );
              }
            },
          ),
        ),
      ]),
      // 体验组：体验设置（手机感知/免打扰/扩展/应用容貌 收纳于此）+ API 配置
      group('体验', [
        ExpansionTile(
          leading: const Icon(Icons.tune, size: 20, color: Color(0xFF007AFF)),
          title: const Text('体验设置', style: TextStyle(fontSize: 15, color: textColor)),
          subtitle: const Text('手机感知 / 免打扰 / 扩展 / 应用容貌', style: TextStyle(fontSize: 11, color: subColor)),
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
              leading: const Icon(Icons.visibility_outlined, size: 20, color: Color(0xFF007AFF)),
              title: Text(l10n.phonePerception, style: const TextStyle(fontSize: 14)),
              subtitle: Text(l10n.phonePerceptionHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                _closeDrawer();
                Navigator.push(c, MaterialPageRoute(builder: (_) => const PhonePerceptionScreen()));
              },
            ),
            ListTile(
              leading: const Icon(Icons.do_not_disturb, size: 20, color: Color(0xFF007AFF)),
              title: Text(l10n.dnd, style: const TextStyle(fontSize: 14)),
              subtitle: Text(l10n.dndHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                _closeDrawer();
                Navigator.push(c, MaterialPageRoute(builder: (_) => const DndSettingsScreen()));
              },
            ),
            ListTile(
              leading: const Icon(Icons.extension_outlined, size: 20, color: Color(0xFF007AFF)),
              title: Text(l10n.extensions, style: const TextStyle(fontSize: 14)),
              subtitle: Text(l10n.extensionsHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                _closeDrawer();
                Navigator.push(c, MaterialPageRoute(builder: (_) => const ExtensionsScreen()));
              },
            ),
            ListTile(
              leading: const Icon(Icons.palette_outlined, size: 20, color: Color(0xFF007AFF)),
              title: Text(l10n.appearanceTitle, style: const TextStyle(fontSize: 14)),
              subtitle: Text(l10n.appearanceHint, style: const TextStyle(fontSize: 11, color: subColor)),
              trailing: const Icon(Icons.chevron_right, size: 18, color: chevColor),
              onTap: () {
                _closeDrawer();
                Navigator.push(c, MaterialPageRoute(builder: (_) => const AppearanceScreen()));
              },
            ),
          ],
        ),
        divider(),
        row(
          icon: Icons.blur_circular,
          title: '织库',
          subtitle: '全景记忆 · 编织成球',
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const WeaveLibraryScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.admin_panel_settings_outlined,
          title: '权限管理',
          subtitle: 'AI 能力权限（生图 / 识图 / 语音 / 浏览器等）',
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const PermissionSettingsScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.key_outlined,
          title: l10n.apiConfig,
          subtitle: l10n.apiConfigHint,
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const ApiConfigScreen()));
          },
        ),
      ]),
      // 系统组：支持作者 / 更新公告 / 用户协议
      group('系统', [
        row(
          icon: Icons.favorite_outline,
          title: l10n.supportAuthor,
          subtitle: l10n.supportAuthorHint,
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const SupportScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.campaign_outlined,
          title: '更新公告',
          subtitle: '最近更新内容，按天查看',
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const UpdateAnnouncementScreen()));
          },
        ),
        divider(),
        row(
          icon: Icons.description_outlined,
          title: '用户协议',
          subtitle: '协议内容后续补充',
          onTap: () {
            _closeDrawer();
            Navigator.push(c, MaterialPageRoute(builder: (_) => const UserAgreementScreen()));
          },
        ),
      ]),
      // 关于组：关于/版本 + 退出登录
      group('关于', [
        row(icon: Icons.info_outline, title: l10n.about, subtitle: l10n.version),
        if (s.isLoggedIn) divider(),
        if (s.isLoggedIn)
          row(
            icon: Icons.logout,
            title: l10n.logout,
            titleColor: Colors.red,
            onTap: () async {
              _closeDrawer();
              await BackgroundPollingService.stop();
              await s.logout();
              if (c.mounted) Navigator.pushReplacementNamed(c, '/');
            },
          ),
      ]),
      const SizedBox(height: 8),
    ]);
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
        ScaffoldMessenger.of(c).showSnackBar(SnackBar(content: Text(l10n.avatarUpdateFailed)));
      }
    }
  }

  void _editUrl(BuildContext c, SettingsProvider s) {
    final l10n = AppLocalizations.of(c)!;
    var ct = TextEditingController(text: s.serverUrl);
    showDialog(context: c, builder: (ctx) => AlertDialog(
      title: Text(l10n.serverAddress), content: TextField(controller: ct, decoration: const InputDecoration(hintText: 'http://192.168.x.x:8000')),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)), FilledButton(onPressed: () { s.setServerUrl(ct.text); Navigator.pop(ctx); }, child: Text(l10n.save))],
    ));
  }

}

class _MomentTabIcon extends StatelessWidget {
  final int unread;
  const _MomentTabIcon({required this.unread});

  @override
  Widget build(BuildContext context) {
    return Stack(
      clipBehavior: Clip.none,
      children: [
        const Icon(Icons.people_outline),
        if (unread > 0)
          Positioned(
            right: -8,
            top: -4,
            child: Container(
              constraints: const BoxConstraints(minWidth: 16, minHeight: 16),
              padding: const EdgeInsets.symmetric(horizontal: 4),
              decoration: BoxDecoration(color: Colors.red, borderRadius: BorderRadius.circular(9)),
              child: Center(
                child: Text(unread > 99 ? "99+" : "$unread", style: const TextStyle(fontSize: 10, color: Colors.white, height: 1)),
              ),
            ),
          ),
      ],
    );
  }
}

