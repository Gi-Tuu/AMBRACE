
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'dart:async';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../home/home_screen.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../widgets/privacy_lock_view.dart';
import '../../widgets/shimmer.dart';
import '../../features/phone/phone_desktop_view.dart';
import '../../features/phone/phone_tiles.dart';

/// Aurora P3 统一玻璃 AppBar（手机内页模式：半透明底 + 0.5px 描边，无 BackdropFilter）
class AiInteractionScreen extends StatefulWidget {
  const AiInteractionScreen({super.key});

  @override
  State<AiInteractionScreen> createState() => _AiInteractionScreenState();
}

class _AiInteractionScreenState extends State<AiInteractionScreen>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;
  List<AIChat> _chats = [];
  List<AICharacter> _characters = [];
  // AI 此刻（Phase D，2026-08-14）：角色卡片显示精简状态（phase+mood）
  Map<int, Map<String, dynamic>> _present = {};
  bool _loading = true;
  String? _error;
  bool _socialEnabled = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final chars = await ApiClient().getCharacters();
      final chats = await ApiClient().getAiChats(limit: 200);
      bool social = true;
      try {
        final p = await ApiClient().dio.get('/api/v1/auth/profile');
        social = p.data['ai_social_enabled'] as bool? ?? true;
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _characters = chars;
        _chats = chats;
        _socialEnabled = social;
        _loading = false;
      });
      _loadPresent();
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.loadFailed;
        _loading = false;
      });
    }
  }

  /// 并行加载每个角色的此刻状态（生活时段 + 八维心情；失败静默保留默认值）
  Future<void> _loadPresent() async {
    final map = <int, Map<String, dynamic>>{};
    await Future.wait([
      for (final c in _characters)
        () async {
          try {
            final ls = await ApiClient().getLifeState(c.id);
            final cs = await ApiClient().getCharacterStates(c.id);
            map[c.id] = {'phase': ls['phase'] as String? ?? '', 'mood': cs.mood};
          } catch (_) {
            map[c.id] = {'phase': '', 'mood': 50};
          }
        }(),
    ]);
    if (!mounted) return;
    setState(() => _present = map);
  }

  Future<void> _setSocialEnabled(bool v) async {
    setState(() => _socialEnabled = v);
    try {
      await ApiClient().updateProfile({'ai_social_enabled': v});
    } catch (_) {
      if (!mounted) return;
      setState(() => _socialEnabled = !v);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(AppLocalizations.of(context)!.switchSaveFail)),
      );
    }
  }

  /// 打开角色小手机：先检查隐私上锁（target=phone）→ 锁定则走申请流程 → 通过后进入内置微信
  Future<void> _openPhone(AICharacter c) async {
    try {
      final status = await ApiClient().getPrivacyStatus(c.id, 'phone');
      final enabled = status['enabled'] == true;
      final unlockUntil = status['unlock_until'] as String?;
      var unlocked = false;
      if (unlockUntil != null && unlockUntil.isNotEmpty) {
        try {
          final until = DateTime.parse(unlockUntil.replaceAll(' ', 'T'))
              .toUtc()
              .add(const Duration(hours: 8));
          unlocked = until.isAfter(DateTime.now());
        } catch (_) {}
      }
      if (enabled && !unlocked) {
        if (!mounted) return;
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => Scaffold(
              appBar: AppBar(title: Text(AppLocalizations.of(context)!.phoneOf(c.name))),
              body: PrivacyLockView(
                characterId: c.id,
                target: 'phone',
                contentName: AppLocalizations.of(context)!.phoneShort,
                onUnlocked: () {
                  if (!mounted) return;
                  Navigator.of(context).pop(); // 关闭锁屏页
                  _pushDesktop(c);
                },
              ),
            ),
          ),
        );
        return;
      }
    } catch (_) {}
    if (!mounted) return;
    _pushDesktop(c);
  }

  void _pushDesktop(AICharacter c) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => PhoneDesktopScreen(
          character: c,
          chats: _chatsByChar()[c.id] ?? [],
          charMap: _charMap,
        ),
      ),
    );
  }

  /// 每个角色参与的对话（时间正序）
  Map<int, List<AIChat>> _chatsByChar() {
    final m = <int, List<AIChat>>{};
    for (final c in _chats) {
      m.putIfAbsent(c.characterAId, () => []).add(c);
      m.putIfAbsent(c.characterBId, () => []).add(c);
    }
    return m;
  }

  Map<int, AICharacter> get _charMap => {for (final c in _characters) c.id: c};

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;
    final isDark = theme.brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P2 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
        backgroundColor: isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55),
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          bottom: BorderSide(
            color: isDark
                ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                : Colors.black.withValues(alpha: AppGlass.borderAlpha),
            width: 0.5,
          ),
        ),
        leading: IconButton(
          icon: const Icon(Icons.menu),
          onPressed: () => AppDrawerController.toggle(),
          tooltip: l10n.menu,
        ),
        title: Text(l10n.phoneShort),
        actions: [
          IconButton(
            icon: const Icon(Icons.smartphone),
            tooltip: l10n.myPhone,
            onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(l10n.myPhoneComingSoon)),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          // Aurora P2：私聊开关卡 IosCardGroup → AuroraCard
          // （组内透明 Material：SwitchListTile 需要 Material 祖先，防 debug 断言）
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: AuroraCard(
              padding: EdgeInsets.zero,
              child: Material(
                type: MaterialType.transparency,
                child: SwitchListTile(
                  secondary: const Icon(Icons.forum_outlined),
                  title: Text(l10n.aiPrivateChat),
                  subtitle: Text(l10n.aiPrivateChatHint),
                  value: _socialEnabled,
                  onChanged: _setSocialEnabled,
                ),
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const AiInteractionSkeleton()
                : _error != null
                    ? _ErrorView(message: _error!, onRetry: _load)
                    : RefreshIndicator(
                        onRefresh: _load,
                        child: _characters.isEmpty
                            ? _EmptyView(theme: theme)
                            : GridView.builder(
                                physics: const AlwaysScrollableScrollPhysics(),
                                padding: const EdgeInsets.all(12),
                                gridDelegate:
                                    const SliverGridDelegateWithFixedCrossAxisCount(
                                  crossAxisCount: 2,
                                  mainAxisSpacing: 12,
                                  crossAxisSpacing: 12,
                                  childAspectRatio: 0.62,
                                ),
                                itemCount: _characters.length,
                                itemBuilder: (context, i) {
                                  final c = _characters[i];
                                  final myChats = _chatsByChar()[c.id] ?? [];
                                  return PhoneTile(
                                    character: c,
                                    chats: myChats,
                                    present: _present[c.id],
                                    onTap: () => _openPhone(c),
                                  );
                                },
                              ),
                      ),
          ),
        ],
      ),
    );
  }
}


/// 小手机桌面应用（图标 + 位置 + 状态）

class _EmptyView extends StatelessWidget {
  const _EmptyView({required this.theme});
  final ThemeData theme;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    // Aurora P2：EmptyState 统一渲染；包 ListView 保留下拉刷新
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        SizedBox(height: MediaQuery.of(context).size.height * 0.18),
        EmptyState(
          icon: Icons.phone_android_rounded,
          title: l10n.noCharacters,
          subtitle: l10n.createRoleHint,
        ),
      ],
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    // Aurora P2：EmptyState 统一渲染 + 重试按钮；包 ListView 使下拉刷新可用
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        SizedBox(height: MediaQuery.of(context).size.height * 0.18),
        EmptyState(
          icon: Icons.cloud_off_rounded,
          title: message,
          action: TextButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh_rounded, size: 18),
            label: Text(l10n.retry),
          ),
        ),
      ],
    );
  }
}

// ── Aurora P2 私有组件 / 助手 ──

/// App 图标按压缩放：按下 0.9、抬起回 1.0（AppMotion.fast + emphasized）。
/// 用 Listener 读指针事件（不参与手势 Arena，不与外层 onTap/LongPress 冲突）；
/// `enabled=false`（编辑模式 / reduceMotion / 系统 disableAnimations）时不缩放。
