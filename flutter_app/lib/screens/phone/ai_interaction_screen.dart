import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'dart:async';
import 'package:intl/intl.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../chat/chat_group_chat_screen.dart';
import 'phone_app_screens.dart';
import 'phone_pet_screen.dart';
import '../../widgets/ios_card_group.dart';
import '../../services/api/phone_desktop_api.dart';
import '../home/home_screen.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/privacy_lock_view.dart';
import '../../widgets/shimmer.dart';

/// 小手机：角色"手机"方块网格，点开进入该角色的手机桌面（目前仅「畅聊」应用）
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
        builder: (_) => _PhoneDesktopScreen(
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
    return Scaffold(
      appBar: AppBar(
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
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
            child: IosCardGroup(
              children: [
                SwitchListTile(
                  secondary: const Icon(Icons.forum_outlined),
                  title: Text(l10n.aiPrivateChat),
                  subtitle: Text(l10n.aiPrivateChatHint),
                  value: _socialEnabled,
                  onChanged: _setSocialEnabled,
                ),

              ],
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
                                  return _PhoneTile(
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
class _PhoneApp {
  _PhoneApp({
    required this.key,
    required this.label,
    required this.icon,
    required this.c1,
    required this.c2,
    required this.deletable,
    this.plugin,
    required this.pos,
    this.hidden = false,
  });

  final String key;
  final String label;
  final IconData icon;
  final int c1;
  final int c2;
  final bool deletable;
  final String? plugin; // 扩展附属产品（如 browser_mcp）：关闭扩展不显示
  int pos;
  bool hidden;
}

/// 角色小手机桌面：模拟手机桌面（拖拽换位 / 长按编辑删除 / 小组件 / 应用集）
class _PhoneDesktopScreen extends StatefulWidget {
  const _PhoneDesktopScreen({
    required this.character,
    required this.chats,
    required this.charMap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  final Map<int, AICharacter> charMap;

  @override
  State<_PhoneDesktopScreen> createState() => _PhoneDesktopScreenState();
}

class _PhoneDesktopScreenState extends State<_PhoneDesktopScreen> {
  static const _defColors = [Color(0xFF1C1C3A), Color(0xFF3A2E5C), Color(0xFF6C4E7E)];
  static const _wallpapers = <String, List<Color>>{
    '': [Color(0xFF1C1C3A), Color(0xFF3A2E5C), Color(0xFF6C4E7E)],
    'aurora': [Color(0xFF0F3443), Color(0xFF34E89E)],
    'sunset': [Color(0xFFC33764), Color(0xFF1D2671)],
    'ocean': [Color(0xFF2193B0), Color(0xFF6DD5ED)],
    'cherry': [Color(0xFFF7B3C6), Color(0xFF6A5ACD)],
    'coffee': [Color(0xFF3E2723), Color(0xFFB8860B)],
  };
  static const _appMeta = <String, ({String label, IconData icon, int c1, int c2, bool deletable, String? plugin})>{
    'chat': (label: '畅聊', icon: Icons.chat_bubble, c1: 0xFF5B7CFA, c2: 0xFF00CEC9, deletable: false, plugin: null),
    'album': (label: '相册', icon: Icons.photo_library, c1: 0xFF00B4DB, c2: 0xFF11998E, deletable: false, plugin: null),
    'market': (label: '应用市场', icon: Icons.storefront, c1: 0xFF7F00FF, c2: 0xFFE100FF, deletable: false, plugin: null),
    'calendar': (label: '日历', icon: Icons.calendar_month, c1: 0xFFFF512F, c2: 0xFFF09819, deletable: false, plugin: null),
    'browser': (label: '浏览器', icon: Icons.public, c1: 0xFF2193B0, c2: 0xFF6DD5ED, deletable: true, plugin: 'browser_mcp'),
    'theme': (label: '主题', icon: Icons.palette, c1: 0xFFF953C6, c2: 0xFFB91D73, deletable: false, plugin: null),
    'settings': (label: '设置', icon: Icons.settings, c1: 0xFF616161, c2: 0xFF9E9E9E, deletable: false, plugin: null),
    'memo': (label: '备忘录', icon: Icons.sticky_note_2, c1: 0xFFFFB75E, c2: 0xFFED8F03, deletable: false, plugin: null),
    'pets': (label: '宠物', icon: Icons.pets, c1: 0xFF43A047, c2: 0xFF8BC34A, deletable: false, plugin: null),
  };

  /// 应用名国际化（_appMeta 保持 const，label 运行时映射）
  String _appLabel(_PhoneApp app, AppLocalizations l10n) => switch (app.key) {
        'chat' => l10n.appChat,
        'album' => l10n.appAlbum,
        'market' => l10n.appMarket,
        'calendar' => l10n.appCalendar,
        'browser' => l10n.appBrowser,
        'theme' => l10n.appTheme,
        'settings' => l10n.appSettings,
        'memo' => l10n.appMemo,
        'pets' => l10n.appPets,
        _ => app.label,
      };

  final List<_PhoneApp> _apps = [];
  List<Map<String, dynamic>> _catalog = [];
  String? _wallpaper;
  bool _loading = true;
  bool _editing = false;
  String _clockTime = '';
  String _dateLine = '';
  String _weatherLine = '';
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _refreshClock();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) _refreshClock();
    });
    _load();
    _loadWeather();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _refreshClock() {
    final now = DateTime.now();
    setState(() {
      _clockTime = DateFormat('HH:mm').format(now);
      _dateLine = DateFormat(AppLocalizations.of(context)!.dateLinePattern).format(now);
    });
  }

  Future<void> _loadWeather() async {
    try {
      final line = await ApiClient().getPhoneWeather();
      if (mounted) setState(() => _weatherLine = line);
    } catch (_) {}
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().getPhoneLayouts(widget.character.id);
      if (!mounted) return;
      final loaded = (data['apps'] as List? ?? [])
          .cast<Map<String, dynamic>>();
      final catalog = (data['catalog'] as List? ?? [])
          .cast<Map<String, dynamic>>();
      final browserEnabled = data['browser_plugin_enabled'] == true;
      setState(() {
        _wallpaper = data['wallpaper'] as String?;
        _catalog = catalog;
        _apps.clear();
        final usedPos = <int>{};
        for (final item in catalog) {
          final key = item['key'] as String? ?? '';
          final meta = _appMeta[key];
          if (meta == null) continue;
          Map<String, dynamic>? rec;
          for (final r in loaded) {
            if (r['key'] == key) { rec = r; break; }
          }
          final plugin = meta.plugin;
          var hidden = rec?['is_hidden'] == true;
          var pos = (rec?['pos'] as num?)?.toInt() ?? 0;
          if (plugin != null) {
            // 扩展附属产品：关闭扩展不显示（视为删除）；开启后重新显示
            if (!browserEnabled) {
              hidden = true;
            } else {
              hidden = false;
            }
          }
          if (hidden) {
            _apps.add(_PhoneApp(key: key, label: meta.label, icon: meta.icon,
                c1: meta.c1, c2: meta.c2, deletable: meta.deletable,
                plugin: plugin, pos: pos, hidden: true));
            continue;
          }
          if (rec == null) {
            // 首次出现：找空位
            pos = 0;
            while (usedPos.contains(pos)) { pos++; }
          }
          usedPos.add(pos);
          _apps.add(_PhoneApp(key: key, label: meta.label, icon: meta.icon,
              c1: meta.c1, c2: meta.c2, deletable: meta.deletable,
              plugin: plugin, pos: pos, hidden: false));
        }
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _persist() async {
    try {
      await ApiClient().savePhoneLayouts(
        widget.character.id,
        _apps.map((a) => {
          'key': a.key,
          'pos': a.pos,
          'is_hidden': a.hidden,
        }).toList(),
        wallpaper: _wallpaper,
      );
    } catch (_) {}
  }

  int _nextFreePos() {
    final used = _apps.map((a) => a.pos).toSet();
    var p = 0;
    while (used.contains(p)) {
      p++;
    }
    return p;
  }

  void _swapApps(int fromPos, int toPos) {
    if (fromPos == toPos) return;
    int? ai;
    int? bi;
    for (var i = 0; i < _apps.length; i++) {
      if (_apps[i].pos == fromPos) ai = i;
      if (_apps[i].pos == toPos) bi = i;
    }
    if (ai == null || bi == null) return;
    final ia = ai;
    final ib = bi;
    setState(() {
      final a = _apps[ia];
      final b = _apps[ib];
      final tmp = a.pos;
      a.pos = b.pos;
      b.pos = tmp;
    });
    _persist();
  }

  Future<void> _restoreApp(String key) async {
    _PhoneApp? app;
    for (final a in _apps) {
      if (a.key == key) { app = a; break; }
    }
    if (app == null) return;
    final target = app;
    setState(() {
      target.hidden = false;
      target.pos = _nextFreePos();
    });
    await _persist();
  }

  Future<void> _setWallpaper(String? wallpaper) async {
    setState(() => _wallpaper = wallpaper);
    await _persist();
  }

  void _openApp(_PhoneApp app) {
    switch (app.key) {
      case 'chat':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => _WechatHomeScreen(
            character: widget.character,
            chats: widget.chats,
            charMap: widget.charMap,
          ),
        ));
        break;
      case 'album':
        Navigator.of(context).push(MaterialPageRoute(builder: (_) => const AlbumScreen()));
        break;
      case 'market':
        final installed = _apps.where((a) => !a.hidden).map((a) => a.key).toSet();
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => MarketScreen(
            catalog: _catalog,
            installedKeys: installed,
            onRestore: _restoreApp,
          ),
        )).then((_) {
          if (mounted) setState(() {});
        });
        break;
      case 'calendar':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => CalendarScreen(characterId: widget.character.id),
        ));
        break;
      case 'browser':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => BrowserScreen(characterId: widget.character.id, characterName: widget.character.name),
        ));
        break;
      case 'theme':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => ThemeScreen(
            current: _wallpaper,
            onChanged: _setWallpaper,
          ),
        ));
        break;
      case 'settings':
        Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SettingsScreen()));
        break;
      case 'memo':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => MemoScreen(characterId: widget.character.id),
        ));
        break;
      case 'pets':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => PhonePetScreen(
            characterId: widget.character.id,
            characterName: widget.character.name,
          ),
        ));
        break;
    }
  }

  Widget _background() {
    final wp = _wallpaper;
    if (wp != null && wp.startsWith('http')) {
      return Image.network(wp, fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => Container(
            decoration: const BoxDecoration(gradient: LinearGradient(
                begin: Alignment.topLeft, end: Alignment.bottomRight,
                colors: _defColors)),
          ));
    }
    final colors = _wallpapers[wp ?? ''] ?? _defColors;
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: colors,
        ),
      ),
      child: const SizedBox.expand(),
    );
  }

  Widget _clockWidget() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Colors.white.withValues(alpha: 0.18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(_clockTime,
              style: const TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w600)),
          const SizedBox(height: 2),
          Text(_dateLine, style: const TextStyle(color: Colors.white70, fontSize: 13)),
          if (_weatherLine.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(_weatherLine,
                maxLines: 1, overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: Colors.white60, fontSize: 12)),
          ],
        ],
      ),
    );
  }

  Widget _iconWidget(_PhoneApp app, {bool editing = false}) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Stack(
          clipBehavior: Clip.none,
          children: [
            Container(
              width: 60,
              height: 60,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Color(app.c1), Color(app.c2)],
                ),
                borderRadius: BorderRadius.circular(16),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.25),
                    blurRadius: 10,
                    offset: const Offset(0, 4),
                  ),
                ],
              ),
              child: Icon(app.icon, color: Colors.white, size: 30),
            ),
            if (editing && app.deletable)
              Positioned(
                top: -6,
                right: -6,
                child: GestureDetector(
                  onTap: () {
                    setState(() => app.hidden = true);
                    _persist();
                  },
                  child: Container(
                    width: 20,
                    height: 20,
                    decoration: const BoxDecoration(color: Colors.red, shape: BoxShape.circle),
                    child: const Icon(Icons.close, color: Colors.white, size: 14),
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 6),
        Text(_appLabel(app, AppLocalizations.of(context)!),
            maxLines: 1, overflow: TextOverflow.ellipsis,
            style: const TextStyle(color: Colors.white, fontSize: 11)),
      ],
    );
  }

  Widget _iconTile(_PhoneApp app) {
    return DragTarget<int>(
      onWillAcceptWithDetails: (_) => true,
      onAcceptWithDetails: (d) => _swapApps(d.data, app.pos),
      builder: (context, candidates, _) {
        final hovering = candidates.isNotEmpty;
        return LongPressDraggable<int>(
          data: app.pos,
          feedback: Material(
            color: Colors.transparent,
            child: _iconWidget(app),
          ),
          childWhenDragging: Opacity(opacity: 0.3, child: _iconWidget(app)),
          child: GestureDetector(
            onTap: _editing ? null : () => _openApp(app),
            child: AnimatedScale(
              scale: hovering ? 1.08 : 1.0,
              duration: const Duration(milliseconds: 120),
              child: _iconWidget(app, editing: _editing),
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final visible = _apps.where((a) => !a.hidden).toList()
      ..sort((x, y) => x.pos.compareTo(y.pos));
    return Scaffold(
      body: Stack(
        fit: StackFit.expand,
        children: [
          _background(),
          SafeArea(
            child: Column(
              children: [
                // 顶部状态栏 + 返回
                Padding(
                  padding: const EdgeInsets.fromLTRB(8, 8, 20, 0),
                  child: Row(
                    children: [
                      IconButton(
                        icon: const Icon(Icons.arrow_back_ios_new, size: 18, color: Colors.white70),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                      if (_editing)
                        Text(l10n.dragEditHint,
                            style: const TextStyle(color: Colors.white70, fontSize: 11)),
                      const Spacer(),
                      TextButton(
                        onPressed: () => setState(() => _editing = !_editing),
                        style: TextButton.styleFrom(
                          foregroundColor: Colors.white70,
                          visualDensity: VisualDensity.compact,
                        ),
                        child: Text(_editing ? l10n.done : l10n.edit,
                            style: const TextStyle(fontSize: 12)),
                      ),
                      const Text('9:41',
                          style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(width: 6),
                      const Text('●●● ▂▄▆ 100%', style: TextStyle(color: Colors.white70, fontSize: 12)),
                    ],
                  ),
                ),
                Expanded(
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () {
                      if (_editing) setState(() => _editing = false);
                    },
                    child: _loading
                        ? const Center(child: CircularProgressIndicator(color: Colors.white70))
                        : SingleChildScrollView(
                            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                            child: Column(
                              children: [
                                _clockWidget(),
                                const SizedBox(height: 24),
                                GridView.count(
                                  crossAxisCount: 4,
                                  shrinkWrap: true,
                                  physics: const NeverScrollableScrollPhysics(),
                                  mainAxisSpacing: 18,
                                  crossAxisSpacing: 10,
                                  childAspectRatio: 0.72,
                                  children: [for (final app in visible) _iconTile(app)],
                                ),
                              ],
                            ),
                          ),
                  ),
                ),
                // 底部指示条
                Container(
                  width: 120,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 10),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.35),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 角色"手机"方块
class _PhoneTile extends StatelessWidget {
  const _PhoneTile({
    required this.character,
    required this.chats,
    this.present,
    required this.onTap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  /// AI 此刻（Phase D，2026-08-14）：{phase, mood}
  final Map<String, dynamic>? present;
  final VoidCallback onTap;

  /// 角色卡片上的「此刻」精简状态行（present 为空时不显示）
  Widget _presentLine(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final p = present;
    if (p == null) return const SizedBox.shrink();
    final doing = switch (p['phase']) {
      'sleep' => l10n.phaseSleep,
      'morning' => l10n.phaseMorning,
      'afternoon' => l10n.phaseAfternoon,
      'evening' => l10n.phaseEvening,
      _ => l10n.phaseLiving,
    };
    final mood = (p['mood'] as int?) ?? 50;
    final moodText = mood >= 70
        ? l10n.moodGreat
        : mood >= 50
            ? l10n.moodGood
            : mood >= 30
                ? l10n.moodOk
                : l10n.moodLow;
    return Text(
      l10n.presentLine(doing, moodText),
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(fontSize: 10, color: Theme.of(context).colorScheme.primary),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context)!;
    final hasChats = chats.isNotEmpty;
    final last = hasChats ? chats.last : null;
    return Opacity(
      opacity: hasChats ? 1.0 : 0.55,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(22),
          child: Container(
            decoration: BoxDecoration(
              color: theme.colorScheme.surface,
              borderRadius: BorderRadius.circular(22),
              border: Border.all(color: Theme.of(context).dividerColor, width: 1.5),
            ),
            padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
            child: Column(
              children: [
                // 听筒
                Container(
                  width: 34,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.outlineVariant,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(height: 10),
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      AIAvatar(
                        name: character.name,
                        size: 52,
                        imageUrl: character.avatarUrl,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        character.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        last != null ? last.content : l10n.noChats,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: Theme.of(context).colorScheme.onSurfaceVariant),
                      ),
                      const SizedBox(height: 3),
                      _presentLine(context),
                    ],
                  ),
                ),

              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 该角色的"内置畅聊"首页：私信会话 + 家庭群聊会话（同一列表，右上角创建群聊）
class _WechatHomeScreen extends StatefulWidget {
  const _WechatHomeScreen({
    required this.character,
    required this.chats,
    required this.charMap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  final Map<int, AICharacter> charMap;

  @override
  State<_WechatHomeScreen> createState() => _WechatHomeScreenState();
}

class _WechatHomeScreenState extends State<_WechatHomeScreen> {
  List<Map<String, dynamic>> _groups = [];
  final Map<int, Map<String, dynamic>> _groupLast = {};
  final Map<int, int> _groupUnread = {};

  AICharacter get character => widget.character;
  Map<int, AICharacter> get charMap => widget.charMap;

  String _otherName(int otherId, AIChat first) =>
      charMap[otherId]?.name ??
      (first.characterAId == otherId ? first.characterAName : first.characterBName);

  /// 会话分组：对方角色 id -> 消息（时间正序），按最后消息时间倒序
  List<MapEntry<int, List<AIChat>>> _sessions() {
    final m = <int, List<AIChat>>{};
    for (final c in widget.chats) {
      final otherId =
          c.characterAId == character.id ? c.characterBId : c.characterAId;
      m.putIfAbsent(otherId, () => []).add(c);
    }
    final list = m.entries.toList();
    list.sort((x, y) => y.value.last.createdAt.compareTo(x.value.last.createdAt));
    return list;
  }

  /// 私聊未读数：本地已读标记之后的新消息数（进入会话后清除）
  Future<int> _unreadCount(List<AIChat> msgs, int otherId) async {
    final sp = await SharedPreferences.getInstance();
    final read = sp.getInt('wechat_read_${character.id}_$otherId') ?? 0;
    return msgs.where((c) => c.id > read).length;
  }

  Future<void> _loadGroups() async {
    try {
      final groups = await ApiClient().getChatGroups();
      final sp = await SharedPreferences.getInstance();
      final last = <int, Map<String, dynamic>>{};
      final unread = <int, int>{};
      for (final g in groups) {
        final gid = g['id'] as int;
        try {
          final msgs = await ApiClient().getChatGroupMessages(gid, limit: 100);
          last[gid] = msgs.isNotEmpty ? msgs.last : const {};
          final read = sp.getInt('group_read_$gid') ?? 0;
          unread[gid] =
              msgs.where((m) => ((m['id'] as num?)?.toInt() ?? 0) > read).length;
        } catch (_) {}
      }
      if (mounted) {
        setState(() {
          _groups = groups;
          _groupLast.addAll(last);
          _groupUnread.addAll(unread);
        });
      }
    } catch (_) {}
  }
  Future<void> _createGroup() async {
    final l10n = AppLocalizations.of(context)!;
    final chars = charMap.values.toList();
    if (chars.length < 2) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.needTwoChars)),
        );
      }
      return;
    }
    final selected = <int>{};
    final nameCtrl = TextEditingController();
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlgState) => AlertDialog(
          title: Text(l10n.createGroupDialog),
          content: SizedBox(
            width: 320,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                TextField(
                  controller: nameCtrl,
                  decoration: InputDecoration(labelText: l10n.groupNameLabel, hintText: l10n.groupTitle),
                ),
                const SizedBox(height: 8),
                Text(l10n.selectMinTwo, style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 4),
                SizedBox(
                  height: 220,
                  child: ListView(
                    children: [
                      for (final c in chars)
                        CheckboxListTile(
                          dense: true,
                          title: Text(c.name, style: const TextStyle(fontSize: 14)),
                          value: selected.contains(c.id),
                          onChanged: (v) => setDlgState(() {
                            if (v == true) {
                              selected.add(c.id);
                            } else {
                              selected.remove(c.id);
                            }
                          }),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
            FilledButton(
              onPressed: selected.length >= 2
                  ? () async {
                      final name = nameCtrl.text.trim().isEmpty ? l10n.groupTitle : nameCtrl.text.trim();
                      try {
                        await ApiClient().createChatGroup(name, selected.toList());
                      } catch (_) {}
                      if (ctx.mounted) Navigator.pop(ctx, true);
                    }
                  : null,
              child: Text(l10n.create),
            ),
          ],
        ),
      ),
    );
    if (created == true && mounted) {
      await _loadGroups();
    }
  }

  Future<void> _deleteGroup(int id) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(AppLocalizations.of(ctx)!.deleteGroup),
        content: Text(AppLocalizations.of(ctx)!.deleteGroupConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(AppLocalizations.of(ctx)!.cancel)),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: Text(AppLocalizations.of(ctx)!.delete)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await ApiClient().deleteChatGroup(id);
      await _loadGroups();
    } catch (_) {}
  }

  @override
  void initState() {
    super.initState();
    _loadGroups();
  }

  /// 统一会话项（私信 / 群聊），按最后消息时间倒序
  List<_ChatEntry> _entries() {
    final list = <_ChatEntry>[];
    for (final e in _sessions()) {
      list.add(_ChatEntry.dm(
        otherId: e.key,
        msgs: e.value,
        other: charMap[e.key],
        otherName: _otherName(e.key, e.value.first),
      ));
    }
    for (final g in _groups) {
      list.add(_ChatEntry.group(
        group: g,
        lastMsg: _groupLast[g['id'] as int],
      ));
    }
    list.sort((a, b) {
      final ta = a.lastTime;
      final tb = b.lastTime;
      if (ta == null && tb == null) return 0;
      if (ta == null) return 1;
      if (tb == null) return -1;
      return tb.compareTo(ta);
    });
    return list;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final sessions = _sessions();
    final entries = _entries();
    final hasAny = sessions.isNotEmpty || _groups.isNotEmpty;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLowest,
      appBar: AppBar(
        title: Row(
          children: [
            Icon(Icons.chat_bubble, size: 20),
            const SizedBox(width: 6),
            Text(l10n.chatOf(character.name)),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.group_add),
            tooltip: l10n.createGroup,
            onPressed: _createGroup,
          ),
        ],
      ),
      body: !hasAny
          ? Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  AIAvatar(name: character.name, size: 56, imageUrl: character.avatarUrl),
                  const SizedBox(height: 12),
                  Text(l10n.noChatRecords, style: const TextStyle(color: IosCardColors.subtitle)),
                ],
              ),
            )
          : ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: entries.length,
              itemBuilder: (context, i) => _buildEntry(entries[i]),
            ),
    );
  }

  Widget _buildEntry(_ChatEntry e) => e.isGroup ? _buildGroupEntry(e) : _buildDmEntry(e);

  Widget _buildDmEntry(_ChatEntry e) {
    final l10n = AppLocalizations.of(context)!;
    final msgs = e.msgs!;
    final last = msgs.last;
    final other = e.other;
    return FutureBuilder<int>(
      future: _unreadCount(msgs, e.otherId!),
      builder: (context, snap) {
        final unread = snap.data ?? 0;
        return ListTile(
          leading: AIAvatar(
            name: e.otherName,
            size: 44,
            imageUrl: other?.avatarUrl,
          ),
          title: Text(e.otherName, style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Text(last.content, maxLines: 1, overflow: TextOverflow.ellipsis),
          trailing: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                _wechatTime(last.createdAt, l10n),
                style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
              ),
              if (unread > 0) ...[
                const SizedBox(height: 4),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                  decoration: BoxDecoration(
                    color: Colors.red,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    '$unread',
                    style: const TextStyle(fontSize: 10, color: Colors.white),
                  ),
                ),
              ],
            ],
          ),
          onTap: () async {
            await Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => _WechatChatScreen(
                  self: character,
                  other: other ?? AICharacter(id: e.otherId!, name: e.otherName),
                  chats: msgs,
                ),
              ),
            );
            if (mounted) setState(() {});
          },
        );
      },
    );
  }

  Widget _buildGroupEntry(_ChatEntry e) {
    final l10n = AppLocalizations.of(context)!;
    final g = e.group!;
    final gid = g['id'] as int;
    final name = g['name'] as String? ?? l10n.groupTitle;
    final lastMsg = e.lastMsg;
    final lastText = (lastMsg != null && lastMsg.isNotEmpty)
        ? '${lastMsg['sender_name'] ?? ''}: ${lastMsg['content'] ?? ''}'
        : (g['members'] as List? ?? [])
            .map((m) => (m as Map)['name']?.toString() ?? '')
            .join('、');
    final unread = _groupUnread[gid] ?? 0;
    return ListTile(
      leading: Container(
        width: 44,
        height: 44,
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.primaryContainer,
          borderRadius: BorderRadius.circular(12),
        ),
        child: const Icon(Icons.groups, color: Colors.white, size: 24),
      ),
      title: Text(name, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(lastText, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (e.lastTime != null)
            Text(
              _wechatTime(e.lastTime!, l10n),
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
            ),
          if (unread > 0) ...[
            const SizedBox(height: 4),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: Colors.red,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                '$unread',
                style: const TextStyle(fontSize: 10, color: Colors.white),
              ),
            ),
          ],
        ],
      ),
      onTap: () async {
        await Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => ChatGroupChatScreen(
              groupId: gid,
              groupName: name,
              viewCharacter: character, // 谁的入口进入，右侧就是谁
            ),
          ),
        );
        if (mounted) {
          setState(() {});
          await _loadGroups();
        }
      },
      onLongPress: () => _deleteGroup(gid),
    );
  }
}

/// 畅聊主页统一会话项（私信 / 家庭群聊）
class _ChatEntry {
  _ChatEntry.dm({
    required this.otherId,
    required this.msgs,
    this.other,
    required this.otherName,
  })  : isGroup = false,
        group = null,
        lastMsg = null;

  _ChatEntry.group({
    required this.group,
    this.lastMsg,
  })  : isGroup = true,
        otherId = null,
        msgs = null,
        other = null,
        otherName = '';

  final bool isGroup;
  final int? otherId;
  final List<AIChat>? msgs;
  final AICharacter? other;
  final String otherName;
  final Map<String, dynamic>? group;
  final Map<String, dynamic>? lastMsg;

  DateTime? get lastTime {
    if (isGroup) {
      final m = lastMsg;
      if (m == null || m.isEmpty) return null;
      try {
        return DateTime.parse(formatBeijingTime(m['created_at'] as String? ?? ''));
      } catch (_) {
        return null;
      }
    }
    final list = msgs;
    if (list == null || list.isEmpty) return null;
    return list.last.createdAt;
  }
}

/// 内置畅聊聊天窗口（该角色视角，只读）
class _WechatChatScreen extends StatefulWidget {
  const _WechatChatScreen({
    required this.self,
    required this.other,
    required this.chats,
  });

  final AICharacter self;
  final AICharacter other;
  final List<AIChat> chats;

  @override
  State<_WechatChatScreen> createState() => _WechatChatScreenState();
}

/// 畅聊聊天窗口（该角色视角，只读）：只展示最近 50 条，进入自动滚动到最新消息；右上角聊天记录箱看完整记录
class _WechatChatScreenState extends State<_WechatChatScreen> {
  static const int _maxDisplay = 50;
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _jumpToLatest());
    _markRead();
  }

  /// 进入对话即视为已读（红点依据本地已读标记判断）
  Future<void> _markRead() async {
    try {
      if (widget.chats.isEmpty) return;
      final sp = await SharedPreferences.getInstance();
      await sp.setInt(
        'wechat_read_${widget.self.id}_${widget.other.id}',
        widget.chats.last.id,
      );
    } catch (_) {}
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _jumpToLatest() {
    if (!mounted || !_scroll.hasClients) return;
    _scroll.jumpTo(_scroll.position.maxScrollExtent);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final self = widget.self;
    final other = widget.other;
    final all = widget.chats;
    final chats =
        all.length > _maxDisplay ? all.sublist(all.length - _maxDisplay) : all;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLowest,
      appBar: AppBar(
        title: Row(
          children: [
            AIAvatar(name: other.name, size: 28, imageUrl: other.avatarUrl),
            const SizedBox(width: 8),
            Text(other.name),
          ],
        ),
        actions: [
          IconButton(
            tooltip: l10n.archiveBox,
            icon: const Icon(Icons.inventory_2),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => _WechatArchiveScreen(self: self, other: other),
                ),
              );
            },
          ),
        ],
      ),
      body: ListView.builder(
        controller: _scroll,
        padding: const EdgeInsets.all(12),
        itemCount: chats.length,
        itemBuilder: (context, i) {
          final chat = chats[i];
          final mine = chat.speakerId == self.id;
          return _WechatBubble(
            chat: chat,
            mine: mine,
            avatar: mine ? self.avatarUrl : other.avatarUrl,
          );
        },
      ),
      bottomNavigationBar: Container(
        color: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        child: Row(
          children: [
            Icon(Icons.add_circle_outline, size: 26, color: Colors.grey.shade600),
            const SizedBox(width: 8),
            Expanded(
              child: Container(
                height: 34,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                alignment: Alignment.centerLeft,
                decoration: BoxDecoration(
                  color: Colors.grey.shade200,
                  borderRadius: BorderRadius.circular(17),
                ),
                child: Text(
                  l10n.readOnly,
                  style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
                ),
              ),
            ),
            const SizedBox(width: 8),
            Icon(Icons.mood, size: 24, color: Colors.grey.shade600),
          ],
        ),
      ),
    );
  }
}

/// 畅聊聊天记录箱：按 年→月→日 折叠展示该角色对完整聊天记录（只读）
class _WechatArchiveScreen extends StatefulWidget {
  const _WechatArchiveScreen({required this.self, required this.other});

  final AICharacter self;
  final AICharacter other;

  @override
  State<_WechatArchiveScreen> createState() => _WechatArchiveScreenState();
}

class _WechatArchiveScreenState extends State<_WechatArchiveScreen> {
  List<AIChat> _chats = [];
  bool _loading = true;
  String? _error;
  final Set<String> _expandedYears = {};
  final Set<String> _expandedMonths = {};
  final Set<String> _expandedDays = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final chats = await ApiClient().getAiChats(
        limit: 500,
        charA: widget.self.id,
        charB: widget.other.id,
      );
      if (!mounted) return;
      setState(() {
        _chats = chats;
        _loading = false;
        final now = DateTime.now();
        _expandedYears.add(now.year.toString());
        _expandedMonths.add('${now.year}-${now.month.toString().padLeft(2, '0')}');
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.loadFailed;
        _loading = false;
      });
    }
  }

  /// 年 → 月(yyyy-MM) → 日(yyyy-MM-dd) → 消息（时间正序）
  Map<String, Map<String, Map<String, List<AIChat>>>> _grouped() {
    final result = <String, Map<String, Map<String, List<AIChat>>>>{};
    for (final c in _chats) {
      final d = c.createdAt;
      final year = d.year.toString();
      final month = '${d.year}-${d.month.toString().padLeft(2, '0')}';
      final day =
          '$year-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
      result.putIfAbsent(year, () => {});
      result[year]!.putIfAbsent(month, () => {});
      result[year]![month]!.putIfAbsent(day, () => []);
      result[year]![month]![day]!.add(c);
    }
    return result;
  }

  String _monthLabel(String m) {
    final l10n = AppLocalizations.of(context)!;
    final labels = <String>['', l10n.month1, l10n.month2, l10n.month3, l10n.month4, l10n.month5, l10n.month6, l10n.month7, l10n.month8, l10n.month9, l10n.month10, l10n.month11, l10n.month12];
    final idx = int.tryParse(m.split('-')[1]);
    return (idx != null && idx >= 1 && idx <= 12) ? labels[idx] : l10n.monthNumFallback(m.split('-')[1]);
  }

  String _dayLabel(String d) {
    final l10n = AppLocalizations.of(context)!;
    final p = d.split('-');
    return l10n.dayLabel(int.parse(p[1]), int.parse(p[2]));
  }

  String _timeLabel(DateTime t) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final day = DateTime(t.year, t.month, t.day);
    if (day == today) return DateFormat('HH:mm').format(t);
    return DateFormat('MM-dd HH:mm').format(t);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.archiveTitle(widget.self.name, widget.other.name))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _chats.isEmpty
                  ? Center(child: Text(l10n.noArchive))
                  : _buildTree(),
    );
  }

  Widget _buildTree() {
    final grouped = _grouped();
    final years = grouped.keys.toList()..sort((a, b) => b.compareTo(a));
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        for (final year in years) _buildYearSection(year, grouped[year]!),
      ],
    );
  }

  Widget _buildYearSection(
      String year, Map<String, Map<String, List<AIChat>>> months) {
    final l10n = AppLocalizations.of(context)!;
    final isExpanded = _expandedYears.contains(year);
    final totalDays =
        months.values.fold<int>(0, (sum, days) => sum + days.length);
    final monthKeys = months.keys.toList()..sort((a, b) => b.compareTo(a));
    return IosCardGroup(
      children: [
        InkWell(
          onTap: () {
            setState(() {
              if (isExpanded) {
                _expandedYears.remove(year);
                for (final k in monthKeys) {
                  _expandedMonths.remove(k);
                }
              } else {
                _expandedYears.add(year);
              }
            });
          },
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
            ),
            child: Row(
              children: [
                Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 20),
                const SizedBox(width: 8),
                Text(year, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const Spacer(),
                Text(l10n.daysCount(totalDays), style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
              ],
            ),
          ),
        ),
        if (isExpanded) ...[
          const IosCardDivider(indent: 16),
          ...monthKeys.map((m) => _buildMonthSection(m, months[m]!)),
        ],
      ],
    );
  }

  Widget _buildMonthSection(
      String month, Map<String, List<AIChat>> days) {
    final l10n = AppLocalizations.of(context)!;
    final isExpanded = _expandedMonths.contains(month);
    final totalMsg = days.values.fold<int>(0, (sum, list) => sum + list.length);
    final dayKeys = days.keys.toList()..sort((a, b) => b.compareTo(a));
    return Column(
      children: [
        InkWell(
          onTap: () {
            setState(() {
              if (isExpanded) {
                _expandedMonths.remove(month);
              } else {
                _expandedMonths.add(month);
              }
            });
          },
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.only(left: 24, right: 16, top: 10, bottom: 10),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
            ),
            child: Row(
              children: [
                Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: IosCardColors.subtitle),
                const SizedBox(width: 6),
                Text(_monthLabel(month), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                const Spacer(),
                Text(l10n.msgCount(totalMsg), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
              ],
            ),
          ),
        ),
        if (isExpanded)
          ...dayKeys.map((d) => _buildDaySection(d, days[d]!)),
      ],
    );
  }

  Widget _buildDaySection(String day, List<AIChat> messages) {
    final l10n = AppLocalizations.of(context)!;
    final isExpanded = _expandedDays.contains(day);
    return Container(
      margin: const EdgeInsets.only(left: 36, right: 8, top: 4, bottom: 4),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: () {
              setState(() {
                if (isExpanded) {
                  _expandedDays.remove(day);
                } else {
                  _expandedDays.add(day);
                }
              });
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
              ),
              child: Row(
                children: [
                  Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: IosCardColors.subtitle),
                  const SizedBox(width: 4),
                  Text(_dayLabel(day), style: const TextStyle(fontWeight: FontWeight.w500, fontSize: 13, color: IosCardColors.subtitle)),
                  const Spacer(),
                  Text(l10n.msgCountShort(messages.length), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                ],
              ),
            ),
          ),
          if (isExpanded)
            ...messages.map((c) {
              final mine = c.speakerId == widget.self.id;
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(_timeLabel(c.createdAt),
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle, fontFamily: 'monospace')),
                    const SizedBox(width: 6),
                    Text(
                      c.speakerName,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: mine ? Colors.blue : Colors.green,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(c.content, style: const TextStyle(fontSize: 13, height: 1.3)),
                    ),
                  ],
                ),
              );
            }),
        ],
      ),
    );
  }
}

/// 畅聊气泡消息
class _WechatBubble extends StatelessWidget {
  const _WechatBubble({
    required this.chat,
    required this.mine,
    required this.avatar,
  });

  final AIChat chat;
  final bool mine;
  final String? avatar;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final bubbleColor = mine ? scheme.primaryContainer : scheme.surfaceContainerHighest;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!mine) ...[
              AIAvatar(name: chat.speakerName, size: 36, imageUrl: avatar),
              const SizedBox(width: 8),
            ],
            Flexible(
              child: Column(
                crossAxisAlignment:
                    mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
                children: [
                  Text(
                    '${chat.speakerName} · ${DateFormat('MM-dd HH:mm').format(chat.createdAt)}',
                    style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle),
                  ),
                  const SizedBox(height: 3),
                  Container(
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.62,
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: bubbleColor,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      chat.content,
                      style: const TextStyle(fontSize: 14, height: 1.35),
                    ),
                  ),
                ],
              ),
            ),
            if (mine) ...[
              const SizedBox(width: 8),
              AIAvatar(name: chat.speakerName, size: 36, imageUrl: avatar),
            ],
          ],
        ),
      ),
    );
  }
}

/// 畅聊风格时间：今天显示 HH:mm，昨天显示"昨天"，更早显示 MM-dd
String _wechatTime(DateTime t, AppLocalizations l10n) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  if (day == today) return DateFormat('HH:mm').format(t);
  if (day == today.subtract(const Duration(days: 1))) return l10n.yesterday;
  return DateFormat('MM-dd').format(t);
}

class _EmptyView extends StatelessWidget {
  const _EmptyView({required this.theme});
  final ThemeData theme;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        const SizedBox(height: 120),
        Icon(Icons.phone_android, size: 64, color: theme.colorScheme.primary),
        const SizedBox(height: 16),
        Center(
          child: Text(
            l10n.noCharacters,
            style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold),
          ),
        ),
        const SizedBox(height: 8),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 40),
          child: Text(
            l10n.createRoleHint,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 13, color: Colors.grey),
          ),
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
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.cloud_off, size: 48, color: IosCardColors.subtitle),
          const SizedBox(height: 12),
          Text(message, style: const TextStyle(color: IosCardColors.subtitle)),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onRetry, child: Text(AppLocalizations.of(context)!.retry)),
        ],
      ),
    );
  }
}