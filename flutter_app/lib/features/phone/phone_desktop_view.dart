// F7-c-2（2026-08-31）自 screens/phone/ai_interaction_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../screens/phone/phone_app_screens.dart';
import '../../screens/phone/phone_pet_screen.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../services/api/phone_desktop_api.dart';
import '../../theme/aurora_tokens.dart';
import 'phone_tiles.dart' show maybeReduceBlur, maybeReduceMotion, IconPressScale;
import 'phone_wechat_home.dart';

class PhoneApp {
  PhoneApp({
    required this.key,
    required this.icon,
    required this.c1,
    required this.c2,
    required this.deletable,
    this.plugin,
    required this.pos,
    this.hidden = false,
  });

  final String key;
  final IconData icon;
  final int c1;
  final int c2;
  final bool deletable;
  final String? plugin; // 扩展附属产品（如 browser_mcp）：关闭扩展不显示
  int pos;
  bool hidden;
}

/// 角色小手机桌面：模拟手机桌面（拖拽换位 / 长按编辑删除 / 小组件 / 应用集）
class PhoneDesktopScreen extends StatefulWidget {
  const PhoneDesktopScreen({super.key, 
    required this.character,
    required this.chats,
    required this.charMap,
  });

  final AICharacter character;
  final List<AIChat> chats;
  final Map<int, AICharacter> charMap;

  @override
  State<PhoneDesktopScreen> createState() => PhoneDesktopScreenState();
}

class PhoneDesktopScreenState extends State<PhoneDesktopScreen> {
  static const _defColors = [Color(0xFF1C1C3A), Color(0xFF3A2E5C), Color(0xFF6C4E7E)];
  static const _wallpapers = <String, List<Color>>{
    '': [Color(0xFF1C1C3A), Color(0xFF3A2E5C), Color(0xFF6C4E7E)],
    'aurora': [Color(0xFF0F3443), Color(0xFF34E89E)],
    'sunset': [Color(0xFFC33764), Color(0xFF1D2671)],
    'ocean': [Color(0xFF2193B0), Color(0xFF6DD5ED)],
    'cherry': [Color(0xFFF7B3C6), Color(0xFF6A5ACD)],
    'coffee': [Color(0xFF3E2723), Color(0xFFB8860B)],
  };
  static const _appMeta = <String, ({IconData icon, int c1, int c2, bool deletable, String? plugin})>{
    'chat': (icon: Icons.chat_bubble, c1: 0xFF5B7CFA, c2: 0xFF00CEC9, deletable: false, plugin: null),
    'album': (icon: Icons.photo_library, c1: 0xFF00B4DB, c2: 0xFF11998E, deletable: false, plugin: null),
    'market': (icon: Icons.storefront, c1: 0xFF7F00FF, c2: 0xFFE100FF, deletable: false, plugin: null),
    'calendar': (icon: Icons.calendar_month, c1: 0xFFFF512F, c2: 0xFFF09819, deletable: false, plugin: null),
    'browser': (icon: Icons.public, c1: 0xFF2193B0, c2: 0xFF6DD5ED, deletable: true, plugin: 'browser_mcp'),
    'theme': (icon: Icons.palette, c1: 0xFFF953C6, c2: 0xFFB91D73, deletable: false, plugin: null),
    'settings': (icon: Icons.settings, c1: 0xFF616161, c2: 0xFF9E9E9E, deletable: false, plugin: null),
    'memo': (icon: Icons.sticky_note_2, c1: 0xFFFFB75E, c2: 0xFFED8F03, deletable: false, plugin: null),
    'pets': (icon: Icons.pets, c1: 0xFF43A047, c2: 0xFF8BC34A, deletable: false, plugin: null),
  };

  /// 应用名国际化（_appMeta 保持 const，label 运行时映射）
  String _appLabel(PhoneApp app, AppLocalizations l10n) => switch (app.key) {
        'chat' => l10n.appChat,
        'album' => l10n.appAlbum,
        'market' => l10n.appMarket,
        'calendar' => l10n.appCalendar,
        'browser' => l10n.appBrowser,
        'theme' => l10n.appTheme,
        'settings' => l10n.appSettings,
        'memo' => l10n.appMemo,
        'pets' => l10n.appPets,
        _ => app.key,
      };

  final List<PhoneApp> _apps = [];
  List<Map<String, dynamic>> _catalog = [];
  String? _wallpaper;
  bool _loading = true;
  bool _editing = false;
  String _clockTime = '';
  String _dateLine = '';
  String _weatherLine = '';
  Timer? _timer;

  bool _clockInitialized = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) _refreshClock();
    });
    _load();
    _loadWeather();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // 时钟首刷依赖 l10n（inherited），必须在 didChangeDependencies（initState 期间
    // 访问 inherited 会触发 debug 断言，P2 测试暴露的既有问题）
    if (!_clockInitialized) {
      _clockInitialized = true;
      _refreshClock();
    }
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
            _apps.add(PhoneApp(key: key, icon: meta.icon,
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
          _apps.add(PhoneApp(key: key, icon: meta.icon,
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
    PhoneApp? app;
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

  void _openApp(PhoneApp app) {
    switch (app.key) {
      case 'chat':
        Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => WechatHomeScreen(
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
    // Aurora P2：时钟玻璃卡——整桌面唯一 BackdropFilter，
    // sigma 经 AppGlass.effectiveBlur（读全局 reduceBlur，未包裹 Provider 兜底 false）
    final reduceBlur = maybeReduceBlur(context);
    final sigma = AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: reduceBlur);
    // 浅色壁纸下白字对比度不足：整卡叠一层黑色 scrim 兜底（不改字号/布局/渐变预设）
    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
        child: Stack(
          children: [
            Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(color: Colors.black.withValues(alpha: 0.20)),
              ),
            ),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.14),
                borderRadius: BorderRadius.circular(18),
                border: Border.all(color: Colors.white.withValues(alpha: AppGlass.borderAlpha)),
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
            ),
          ],
        ),
      ),
    );
  }

  Widget _iconWidget(PhoneApp app, {bool editing = false}) {
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

  Widget _iconTile(PhoneApp app) {
    return DragTarget<int>(
      onWillAcceptWithDetails: (_) => true,
      onAcceptWithDetails: (d) => _swapApps(d.data, app.pos),
      builder: (context, candidates, _) {
        final hovering = candidates.isNotEmpty;
        final pressEnabled = !_editing &&
            !MediaQuery.disableAnimationsOf(context) &&
            !maybeReduceMotion(context);
        return LongPressDraggable<int>(
          data: app.pos,
          feedback: Material(
            color: Colors.transparent,
            child: _iconWidget(app),
          ),
          childWhenDragging: Opacity(opacity: 0.3, child: _iconWidget(app)),
          child: GestureDetector(
            onTap: _editing ? null : () => _openApp(app),
            // Aurora P2：拖拽悬停放大 1.08→1.05（AppMotion.fast）；
            // 点击按压 0.9 由内层 IconPressScale 提供（Listener 实现，不与手势 Arena 冲突；
            // 编辑模式与 reduceMotion/disableAnimations 时不缩放）
            child: AnimatedScale(
              scale: hovering ? 1.05 : 1.0,
              duration: AppMotion.fast,
              curve: AppMotion.emphasized,
              child: IconPressScale(
                key: pressEnabled ? const Key('iconPressScale') : null,
                enabled: pressEnabled,
                child: _iconWidget(app, editing: _editing),
              ),
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
