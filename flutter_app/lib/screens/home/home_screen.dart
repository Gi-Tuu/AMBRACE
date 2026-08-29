import "dart:async";
import "dart:ui";
import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "package:provider/provider.dart";
import "../../providers/settings_provider.dart";
import "../../services/api_client.dart";
import "../../services/home_tab_controller.dart";
import "../../services/notification_service.dart";
import "../../services/background_polling_service.dart";
import "../../theme/aurora_tokens.dart";
import "../../widgets/home_bottom_bar.dart";
import "../../widgets/home_drawer.dart";
import "character_list_screen.dart";
import "../social/moments_screen.dart";
import "../phone/ai_interaction_screen.dart";
import "../life/home_visual_screen.dart";
import "../../providers/pets_provider.dart";

// AppDrawerController 由 home_drawer.dart 提供（Phase 2 B1 抽屉提取），
// 此处 re-export 保持既有页面（好友/朋友圈/宠物/小手机/小家）的 import 路径不变。
export "../../widgets/home_drawer.dart" show AppDrawerController;

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with SingleTickerProviderStateMixin {
  /// 抽屉宽度系数（Phase 2 B1：72% → 76%）。
  static const double _drawerWidthFactor = 0.76;

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
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final settings = context.read<SettingsProvider>();
      await settings.load();
      // #55 后台保活开关：默认开；登录态且开启时启动前台服务（App 此刻在前台，满足 Android 14+ 前台服务启动限制）
      if (settings.isLoggedIn && settings.backgroundKeepalive) {
        BackgroundPollingService.start();
      }
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
    final dw2 = MediaQuery.of(context).size.width * _drawerWidthFactor;
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
      duration: AppMotion.normal,
      curve: AppMotion.emphasized,
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
    final dw = MediaQuery.of(context).size.width * _drawerWidthFactor;
    // reduceMotion 与系统「减少动效」等效：关闭持续动画与视差缩放，保留必要转场
    final reduceMotion =
        settings.reduceMotion || MediaQuery.disableAnimationsOf(context);

    return Stack(children: [
      Scaffold(
        body: Stack(
          children: [
            // Aurora 视差纵深：抽屉滑出时主页缩放 0.92 + 圆角 24（reduceMotion 时只平移抽屉不缩放）
            AnimatedBuilder(
              animation: _drawerCtrl,
              builder: (context, child) {
                final v = _drawerCtrl.value;
                final scale = reduceMotion ? 1.0 : 1.0 - 0.08 * v;
                return Transform.scale(
                  scale: scale,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(24 * v),
                    child: child,
                  ),
                );
              },
              child: PageView(
                controller: _pageController,
                onPageChanged: _onPageChanged,
                children: _pages,
              ),
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
            // 抽屉拖拽时屏幕左缘 3px 主题色光条（静态跟随进度，非持续动画）
            Positioned(
              left: 0,
              top: 0,
              bottom: 0,
              width: 3,
              child: AnimatedBuilder(
                animation: _drawerCtrl,
                builder: (context, _) {
                  final v = _drawerCtrl.value;
                  return IgnorePointer(
                    child: Opacity(
                      opacity: v,
                      child: Container(
                        color: Theme.of(context).colorScheme.primary,
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
        bottomNavigationBar: HomeBottomBar(
          selectedIndex: _currentIndex,
          onSelected: (i) {
            _closeDrawer();
            _pageController.animateToPage(
              i,
              duration: AppMotion.normal,
              curve: AppMotion.emphasized,
            );
          },
          items: [
            HomeBottomBarItem(icon: const Icon(Icons.people), label: l10n.tabFriends),
            HomeBottomBarItem(
              icon: _MomentTabIcon(unread: _momentUnread),
              label: l10n.tabMoments,
            ),
            HomeBottomBarItem(icon: const Icon(Icons.forum_outlined), label: l10n.tabAiInteraction),
            HomeBottomBarItem(
              icon: const Icon(Icons.home_outlined),
              label: l10n.homeTitle,
            ),
          ],
        ),
      ),
      KeyedSubtree(
        key: const Key('homeDrawerOverlay'),
        child: IgnorePointer(
        ignoring: !_drawerOpen,
        child: AnimatedBuilder(
          animation: _drawerCtrl,
          builder: (context, _) {
            final v = _drawerCtrl.value;
            // #13 抽屉玻璃模糊强度走全局换算（不硬编码 sigma）
            final sigma = AppGlass.effectiveBlur(AppGlass.blurHeavy,
                reduceBlur: settings.reduceBlur);
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
                          child: Material(
                            elevation: 8,
                            // #13 毛玻璃皮肤：抽屉面板做高斯模糊+半透明底；其余皮肤不透明、不模糊
                            color: AppGlass.isGlassSkin(context)
                                ? Colors.transparent
                                : null,
                            child: AppGlass.isGlassSkin(context)
                                ? ClipRect(
                                    child: BackdropFilter(
                                      filter: ImageFilter.blur(
                                          sigmaX: sigma, sigmaY: sigma),
                                      child: Container(
                                        color: Theme.of(context).brightness ==
                                                Brightness.dark
                                            ? Colors.black
                                                .withValues(alpha: 0.45)
                                            : Colors.white
                                                .withValues(alpha: 0.60),
                                        child: SafeArea(
                                          child: HomeDrawer(
                                              settings: settings,
                                              onClose: _closeDrawer),
                                        ),
                                      ),
                                    ),
                                  )
                                : SafeArea(
                                    child: HomeDrawer(
                                        settings: settings,
                                        onClose: _closeDrawer),
                                  ),
                          ),
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
      ),
    ]);
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
            // 呼吸扩散圈在 PulsingBadge 内部（reduceMotion 时只剩静态红点）
            child: PulsingBadge(
              child: Container(
                constraints: const BoxConstraints(minWidth: 16, minHeight: 16),
                padding: const EdgeInsets.symmetric(horizontal: 4),
                decoration: BoxDecoration(color: Colors.red, borderRadius: BorderRadius.circular(9)),
                child: Center(
                  child: Text(unread > 99 ? "99+" : "$unread", style: const TextStyle(fontSize: 10, color: Colors.white, height: 1)),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
