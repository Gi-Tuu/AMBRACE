
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../services/notification_service.dart';
import '../../global_keys.dart';
import '../../providers/chat_provider.dart';
import '../../widgets/ai_avatar.dart';
import '../character/character_edit_screen.dart';
import '../chat/chat_group_list_screen.dart';
import '../chat/chat_screen.dart';
import '../settings/douyin_approvals_screen.dart';
import 'home_screen.dart';


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
  bool _loading = true;
  String _query = '';

  List<AICharacter> get _filtered => _query.isEmpty
      ? _characters
      : _characters
          .where((c) => c.name.toLowerCase().contains(_query.toLowerCase()))
          .toList();

  @override
  void initState() {
    super.initState();
    NotificationService().setActiveScreen(ActiveScreen.characterList);
    _loadCharacters();
    NotificationService().addListener(_onUnreadChanged);
    DouyinApprovalBadge.refresh();
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
      });
    } catch (e) {
      if (mounted) setState(() => _loading = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('加载失败: ')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(icon: const Icon(Icons.menu), onPressed: () => AppDrawerController.toggle(), tooltip: '菜单'),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('AI 好友'),
            const SizedBox(width: 8),
            ValueListenableBuilder<int>(
              valueListenable: DouyinApprovalBadge.count,
              builder: (context, count, _) => GestureDetector(
                onTap: () async {
                  await Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const DouyinApprovalsScreen()),
                  );
                  DouyinApprovalBadge.refresh();
                },
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Icon(
                      count > 0 ? Icons.mail : Icons.mail_outline,
                      size: 21,
                      color: count > 0 ? Colors.red : Colors.grey,
                    ),
                    if (count > 0)
                      Positioned(
                        right: -5,
                        top: -5,
                        child: Container(
                          padding: const EdgeInsets.all(2),
                          constraints: const BoxConstraints(minWidth: 14, minHeight: 14),
                          decoration: BoxDecoration(color: Colors.red, shape: BoxShape.circle),
                          child: Text(
                            '$count',
                            textAlign: TextAlign.center,
                            style: const TextStyle(fontSize: 8, color: Colors.white, height: 1),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ),
        actions: [
          // UI 2.0：iOS 风格圆形填充添加按钮
          IconButton(
            tooltip: '创建好友',
            onPressed: () async {
              final result = await Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const CharacterEditScreen()),
              );
              if (result == true) _loadCharacters();
            },
            style: IconButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.primaryContainer,
              foregroundColor: Theme.of(context).colorScheme.onPrimaryContainer,
            ),
            icon: const Icon(Icons.add, size: 22),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: TextField(
              onChanged: (v) => setState(() => _query = v.trim()),
              decoration: InputDecoration(
                hintText: '搜索 AI 好友',
                prefixIcon: const Icon(Icons.search, size: 20),
                isDense: true,
                filled: true,
                fillColor: Theme.of(context).colorScheme.surface,
                contentPadding: const EdgeInsets.symmetric(vertical: 10),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(12),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
            child: Card(
              margin: EdgeInsets.zero,
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                leading: Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.primaryContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Icon(Icons.groups, color: Colors.white, size: 24),
                ),
                title: const Text('家庭群聊', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                subtitle: const Text('和你的 AI 角色们一起聊天', style: TextStyle(fontSize: 12)),
                trailing: const Icon(Icons.chevron_right),
                onTap: () async {
                  await Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const ChatGroupListScreen()),
                  );
                },
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _query.isNotEmpty && _filtered.isEmpty
                    ? const Center(child: Text('没有匹配的好友'))
                    : _characters.isEmpty
                        ? const Center(child: Text('还没有AI好友，点击右上角创建'))
                        : ListView.builder(
                            padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                            itemCount: _filtered.length,
                            itemBuilder: (context, index) {
                              final char = _filtered[index];
                              return Card(
                                margin: const EdgeInsets.only(bottom: 8),
                                child: ListTile(
                                  contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                                  leading: AIAvatar(name: char.name, size: 44, imageUrl: char.avatarUrl),
                                  title: Text(char.name, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                                  subtitle: Text(char.personality ?? '',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(fontSize: 12)),
                                  trailing: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      if (NotificationService().unreadCounts.containsKey(char.id))
                                        Container(
                                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                          decoration: BoxDecoration(
                                            color: Colors.red,
                                            borderRadius: BorderRadius.circular(10),
                                          ),
                                          child: Text(
                                            "${NotificationService().unreadCounts[char.id]}",
                                            style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
                                          ),
                                        ),
                                      const SizedBox(width: 4),
                                      const Icon(Icons.chevron_right),
                                    ],
                                  ),
                                  onTap: () async {
                                    context.read<ChatProvider>().setCharacter(char);
                                    await Future.delayed(const Duration(milliseconds: 200));
                                    if (context.mounted) {
                                      Navigator.push(
                                        context,
                                        MaterialPageRoute(
                                          builder: (_) => const ChatScreen(),
                                        ),
                                      );
                                    }
                                  },
                                  onLongPress: () async {
                                    final result = await Navigator.push(
                                      context,
                                      MaterialPageRoute(
                                        builder: (_) => CharacterEditScreen(character: char),
                                      ),
                                    );
                                    if (result == true) _loadCharacters();
                                  },
                                ),
                              );
                            },
                          ),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    appRouteObserver.unsubscribe(this);
    NotificationService().removeListener(_onUnreadChanged);
    NotificationService().setActiveScreen(ActiveScreen.other);
    super.dispose();
  }
}
