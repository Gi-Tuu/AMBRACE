
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../theme/tokens.dart';
import '../../theme/aurora_tokens.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../services/notification_service.dart';
import '../../global_keys.dart';
import '../../providers/chat_provider.dart';
import '../../providers/settings_provider.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/character_list_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/group_list_card.dart';
import '../character/character_edit_screen.dart';
import '../chat/chat_group_chat_screen.dart';
import '../chat/create_group_dialog.dart';
import '../chat/chat_screen.dart';
import '../settings/douyin_approvals_screen.dart';
import '../weave/weave_library_screen.dart';
import 'home_screen.dart';
import "package:ai_companion/widgets/app_page_route.dart";
import '../../widgets/shimmer.dart';
import 'package:ai_companion/l10n/app_localizations.dart';


class CharacterListScreen extends StatefulWidget {
  const CharacterListScreen({super.key});

  @override
  State<CharacterListScreen> createState() => _CharacterListScreenState();
}

class _CharacterListScreenState extends State<CharacterListScreen>
    with RouteAware, AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;
  List<AICharacter> _characters = [];
  List<Map<String, dynamic>> _groups = [];
  bool _loading = true;
  bool _loadError = false; // 加载失败标记（区别于真空列表）
  String _query = '';
  final _searchFocus = FocusNode();
  // B2 列表入场动效：只在首次加载/重试成功后播一次，下拉刷新不重播
  bool _entrancePlayed = false;
  bool _entranceActive = false;
  Timer? _entranceEndTimer;

  List<AICharacter> get _filtered => _query.isEmpty
      ? _characters
      : _characters
          .where((c) => c.name.toLowerCase().contains(_query.toLowerCase()))
          .toList();

  List<Map<String, dynamic>> get _filteredGroups {
    bool matchGroup(Map<String, dynamic> g) {
      if (_query.isEmpty) return true;
      final name = (g['name'] as String? ?? '').toLowerCase();
      final members = ((g['members'] as List?) ?? const [])
          .map((m) => (m as Map)['name']?.toString().toLowerCase() ?? '')
          .join(' ');
      return name.contains(_query.toLowerCase()) || members.contains(_query.toLowerCase());
    }
    return _groups.where(matchGroup).toList();
  }

  @override
  void initState() {
    super.initState();
    NotificationService().setActiveScreen(ActiveScreen.characterList);
    _loadCharacters();
    _loadGroups();
    NotificationService().addListener(_onUnreadChanged);
    DouyinApprovalBadge.refresh();
    _searchFocus.addListener(() {
      if (mounted) setState(() {});
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final route = ModalRoute.of(context);
    if (route != null) appRouteObserver.subscribe(this, route);
  }

  @override
  void didPush() {
    NotificationService().setActiveScreen(ActiveScreen.characterList);
  }

  @override
  void didPopNext() {
    // 从聊天页/其他页面返回好友列表时恢复抑制
    NotificationService().setActiveScreen(ActiveScreen.characterList);
  }

  void _onUnreadChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _loadCharacters() async {
    final api = ApiClient();
    try {
      final chars = await api.getCharacters();
      if (!mounted) return;
      setState(() {
        _characters = chars;
        _loading = false;
        _loadError = false; // 加载成功，清除错误标记
        _maybeStartEntrance();
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _loadError = true; // 标记加载失败
      });
      final l10n = AppLocalizations.of(context)!;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.charListLoadFailed)),
      );
    }
  }

  /// 下拉/重试：好友 + 群一并刷新。
  Future<void> _loadAll() async {
    await Future.wait([_loadCharacters(), _loadGroups()]);
  }

  Future<void> _loadGroups() async {
    final api = ApiClient();
    try {
      final groups = await api.getChatGroups();
      if (!mounted) return;
      setState(() => _groups = groups);
    } catch (_) {
      // 群加载失败不阻塞好友列表；静默（好友列表自身已有错误态）
    }
  }

  /// 首次加载/重试成功后开启一次错峰入场；下拉刷新不重播。
  /// reduceMotion（或系统 disableAnimations）时不开启。
  void _maybeStartEntrance() {
    if (_entrancePlayed) return;
    _entrancePlayed = true;
    final settings = context.read<SettingsProvider>();
    final disableAnimations =
        settings.reduceMotion || MediaQuery.disableAnimationsOf(context);
    if (disableAnimations) return;
    _entranceActive = true;
    _entranceEndTimer?.cancel();
    _entranceEndTimer = Timer(
      AppMotion.normal +
          const Duration(milliseconds: StaggeredEntrance.staggerMs * StaggeredEntrance.maxItems),
      () {
        if (mounted) setState(() => _entranceActive = false);
      },
    );
  }

  Future<void> _createCharacter() async {
    final result = await Navigator.push(
      context,
      AppPageRoute(builder: (_) => const CharacterEditScreen()),
    );
    if (result == true) _loadCharacters();
  }

  /// #16/#17 右上角工具箱：总织库 / 抖音批准请求（带红点）/ 创建好友。
  Widget _buildToolbox(BuildContext context, AppLocalizations l10n) {
    return ValueListenableBuilder<int>(
      valueListenable: DouyinApprovalBadge.count,
      builder: (context, count, _) {
        PopupMenuItem<String> item(
            String value, IconData icon, String label) {
          return PopupMenuItem<String>(
            value: value,
            child: Row(children: [
              Icon(icon, size: 19, color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: 12),
              Expanded(child: Text(label)),
            ]),
          );
        }

        return PopupMenuButton<String>(
          tooltip: l10n.toolboxTitle,
          position: PopupMenuPosition.under,
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          icon: Stack(
            clipBehavior: Clip.none,
            children: [
              const Icon(Icons.apps_rounded),
              if (count > 0)
                Positioned(
                  right: -4,
                  top: -4,
                  child: Container(
                    padding: const EdgeInsets.all(2),
                    constraints:
                        const BoxConstraints(minWidth: 14, minHeight: 14),
                    decoration: const BoxDecoration(
                        color: AppColors.error, shape: BoxShape.circle),
                    child: Text(
                      '$count',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 8, color: Colors.white, height: 1),
                    ),
                  ),
                ),
            ],
          ),
          onSelected: (value) async {
            switch (value) {
              case 'weave':
                Navigator.push(context,
                    AppPageRoute(builder: (_) => const WeaveLibraryScreen()));
                break;
              case 'douyin':
                await Navigator.push(
                  context,
                  AppPageRoute(builder: (_) => const DouyinApprovalsScreen()),
                );
                DouyinApprovalBadge.refresh();
                break;
              case 'create_group':
                final created = await showCreateGroupDialog(context);
                if (created == true) _loadGroups();
                break;
              case 'create':
                _createCharacter();
                break;
            }
          },
          itemBuilder: (_) => [
            item('weave', Icons.blur_circular, l10n.weaveLibraryTitle),
            item('douyin', Icons.mail_outline,
                count > 0 ? '${l10n.dyApprovalsTitle} ($count)' : l10n.dyApprovalsTitle),
            item('create_group', Icons.group_add_outlined, l10n.createGroup),
            item('create', Icons.person_add_alt_1, l10n.createFriend),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(
        leading: IconButton(icon: const Icon(Icons.menu), onPressed: () => AppDrawerController.toggle(), tooltip: l10n.menu),
        title: Text(l10n.aiFriendTitle),
        actions: [_buildToolbox(context, l10n)],
        // Aurora 玻璃顶栏：半透明背景 + 0.5px 描边（不加模糊，底栏已占 1 个 BackdropFilter）
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
        titleTextStyle: TextStyle(
          fontSize: 22,
          fontWeight: FontWeight.w700,
          color: scheme.onSurface,
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(AppSpacing.sm, AppSpacing.xs, AppSpacing.sm, AppSpacing.xxs),
            child: AnimatedContainer(
              key: const Key('searchBox'),
              duration: _searchFocus.hasFocus ? AppMotion.fast : Duration.zero,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: _searchFocus.hasFocus
                      ? scheme.primary
                      : Colors.transparent,
                  width: 1.5,
                ),
              ),
              child: TextField(
                key: const Key('searchField'),
                focusNode: _searchFocus,
                onChanged: (v) => setState(() => _query = v.trim()),
                decoration: InputDecoration(
                  hintText: l10n.searchAiFriend,
                  prefixIcon: const Icon(Icons.search, size: 20),
                  isDense: true,
                  filled: true,
                  fillColor: scheme.surface,
                  contentPadding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const CharacterListSkeleton()
                : RefreshIndicator(
                    onRefresh: _loadAll, // 下拉同时刷新好友与群
                    child: (_characters.isEmpty && _filteredGroups.isEmpty)
                        ? _buildEmptyState(l10n)
                        : _buildUnifiedList(l10n),
                  ),
          )
        ],
      ),
    );
  }

  /// 空状态/错误状态（EmptyState 统一渲染；包在 ListView 里保证可下拉刷新）
  Widget _buildEmptyState(AppLocalizations l10n) {
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        SizedBox(height: MediaQuery.of(context).size.height * 0.22),
        EmptyState(
          icon: _loadError ? Icons.cloud_off_rounded : Icons.people_outline_rounded,
          title: _loadError ? l10n.loadFailedCheckServer : l10n.noAiFriend,
          action: _loadError
              ? TextButton.icon(
                  onPressed: _loadAll,
                  icon: const Icon(Icons.refresh_rounded, size: 18),
                  label: Text(l10n.retry),
                )
              : null,
        ),
      ],
    );
  }

  /// 群聊卡 + 好友卡同构混排（同一 ListView，不再有独立置顶层）。
  Widget _buildUnifiedList(AppLocalizations l10n) {
    final groups = _filteredGroups;
    final chars = _filtered;
    if (_query.isNotEmpty && groups.isEmpty && chars.isEmpty) {
      return ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        children: [
          SizedBox(height: MediaQuery.of(context).size.height * 0.22),
          EmptyState(icon: Icons.search_off_rounded, title: l10n.noMatchingFriend),
        ],
      );
    }

    // 默认顺序：群在前、好友在后（群通常很少）。如想好友在前，交换两个 for 的顺序即可。
    final groupCount = groups.length;
    final total = groupCount + chars.length;
    // 好友加载失败但有群时，列表顶部补一条可点重试的错误条，避免错误态被群列表掩盖。
    final showErrorBanner = _loadError && chars.isEmpty;
    final bannerCount = showErrorBanner ? 1 : 0;

    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(AppSpacing.sm, AppSpacing.xxs, AppSpacing.sm, 96),
      itemCount: total + bannerCount,
      itemBuilder: (context, index) {
        if (showErrorBanner && index == 0) {
          return Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: AuroraCard(
              onTap: _loadAll,
              child: Row(children: [
                Icon(Icons.cloud_off_rounded, size: 18, color: Theme.of(context).colorScheme.error),
                const SizedBox(width: 8),
                Expanded(child: Text(l10n.loadFailedCheckServer, style: const TextStyle(fontSize: 13))),
                Icon(Icons.refresh_rounded, size: 18, color: Theme.of(context).colorScheme.primary),
              ]),
            ),
          );
        }
        final i = index - bannerCount;
        if (i < groupCount) {
          final g = groups[i];
          final name = g['name'] as String? ?? l10n.groupTitle;
          final subtitle = ((g['members'] as List?) ?? const [])
              .map((m) => (m as Map)['name']?.toString() ?? '')
              .where((s) => s.isNotEmpty)
              .join('、');
          return Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: StaggeredEntrance(
              index: i,
              enabled: _entranceActive,
              child: GroupListCard(
                name: name,
                subtitle: subtitle,
                onTap: () => Navigator.push(
                  context,
                  AppPageRoute(
                    builder: (_) => ChatGroupChatScreen(
                      groupId: g['id'] as int,
                      groupName: name,
                    ),
                  ),
                ),
                onLongPress: () => _deleteGroup(g, l10n),
              ),
            ),
          );
        }

        final char = chars[i - groupCount];
        final unread = NotificationService().unreadCounts[char.id];
        return Padding(
          padding: const EdgeInsets.only(bottom: AppSpacing.xs),
          child: StaggeredEntrance(
            index: i,
            enabled: _entranceActive,
            child: CharacterListCard(
              character: char,
              unread: unread,
              onTap: () async {
                context.read<ChatProvider>().setCharacter(char);
                await Future.delayed(const Duration(milliseconds: 200));
                if (context.mounted) {
                  Navigator.push(
                    context,
                    AppPageRoute(builder: (_) => const ChatScreen()),
                  );
                }
              },
              onLongPress: () async {
                final result = await Navigator.push(
                  context,
                  AppPageRoute(builder: (_) => CharacterEditScreen(character: char)),
                );
                if (result == true) _loadCharacters();
              },
            ),
          ),
        );
      },
    );
  }

  /// 长按群卡：删除群（替代原群管理列表页的删除入口）。
  Future<void> _deleteGroup(Map<String, dynamic> g, AppLocalizations l10n) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteGroup),
        content: Text(l10n.deleteGroupConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.delete)),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ApiClient().deleteChatGroup(g['id'] as int);
      _loadGroups();
    } catch (_) {}
  }


  @override
  void dispose() {
    _entranceEndTimer?.cancel();
    _searchFocus.dispose();
    appRouteObserver.unsubscribe(this);
    NotificationService().removeListener(_onUnreadChanged);
    NotificationService().setActiveScreen(ActiveScreen.other);
    super.dispose();
  }
}
