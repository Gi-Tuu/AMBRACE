import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter/services.dart' show rootBundle;
import '../../services/api_client.dart';
import '../character/pet_screen.dart';

/// 生活可视·小家（v3.1.0+）：多房间像素家居 + 宠物素材 + 宠物互动
/// 触屏点地面移动、点家具/宠物弹居中弹窗交互；底部虚拟方向键 + 交互按钮（保留点屏移动）。
class HomeVisualScreen extends StatefulWidget {
  const HomeVisualScreen({super.key});
  @override
  State<HomeVisualScreen> createState() => _HomeVisualScreenState();
}

class _Furniture {
  final String key;
  final String name;
  final double gx, gy, gw, gh;
  final String? action;
  const _Furniture(this.key, this.name, this.gx, this.gy, this.gw, this.gh,
      [this.action]);
  factory _Furniture.fromMap(Map<String, dynamic> m) => _Furniture(
        m['key'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['gx'] as num?) ?? 0).toDouble(),
        ((m['gy'] as num?) ?? 0).toDouble(),
        ((m['gw'] as num?) ?? 1).toDouble(),
        ((m['gh'] as num?) ?? 1).toDouble(),
        m['action'] as String?,
      );
}

class _Room {
  final String id;
  final String name;
  final List<_Furniture> furniture;
  const _Room(this.id, this.name, this.furniture);
  factory _Room.fromMap(Map<String, dynamic> m) => _Room(
        m['id'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['furniture'] as List?) ?? const [])
            .map((e) => _Furniture.fromMap(Map<String, dynamic>.from(e as Map)))
            .toList(),
      );
}

Map<String, String> _kActionLabels(AppLocalizations l10n) => {
  'sleep': l10n.actionSleep,
  'work': l10n.actionWork,
  'cook': l10n.actionCook,
  'eat': l10n.actionEat,
  'tv': l10n.actionTv,
  'read': l10n.actionRead,
  'shower': l10n.actionShower,
  'exercise': l10n.actionExercise,
  'music': l10n.actionMusic,
  'game': l10n.actionGame,
};

class _HomeVisualScreenState extends State<HomeVisualScreen>
    with SingleTickerProviderStateMixin {
  static const double _cw = 640;
  static const double _ch = 480;

  Map<String, dynamic>? _state;
  bool _loading = true;
  String? _error;
  int _characterId = 0;
  List<_Room> _rooms = const [];
  String _currentRoom = 'living';
  List<Map<String, dynamic>> _pets = const [];

  Offset _userPos = const Offset(240, 340);
  late final AnimationController _moveCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 500),
  );
  Animation<Offset>? _moveAnim;
  Timer? _dpadTimer;
  String? _dpadDir;

  String? _selected;
  String? _actionBubble;
  Timer? _bubbleTimer;
  final Map<String, String> _petState = {};
  final Map<String, ui.Image> _images = {};
  bool _imagesReady = false;

  static Future<ui.Image> _loadImage(String path) async {
    final data = await rootBundle.load(path);
    final codec = await ui.instantiateImageCodec(data.buffer.asUint8List());
    final frame = await codec.getNextFrame();
    return frame.image;
  }

  Future<void> _loadVisualAssets() async {
    final keys = <String>[
      'floor_living', 'floor_bedroom', 'floor_kitchen', 'floor_bathroom',
      'wall_living', 'wall_bedroom', 'wall_kitchen', 'wall_bathroom',
      'char_user', 'char_ai',
      'furn_bed', 'furn_sofa', 'furn_tv', 'furn_stove', 'furn_fridge',
      'furn_table', 'furn_desk', 'furn_bookshelf', 'furn_shower', 'furn_bathtub',
      'furn_wardrobe', 'furn_nightstand', 'furn_coffee', 'furn_chair',
      'furn_speaker', 'furn_game', 'furn_plant', 'furn_petbed', 'furn_sink',
      'furn_rug', 'furn_painting', 'furn_lamp', 'furn_shelf', 'furn_bin',
      'furn_clock',
    ];
    for (final k in keys) {
      try {
        _images[k] = await _loadImage('assets/life_visual/$k.png');
      } catch (_) {}
    }
    if (mounted) setState(() => _imagesReady = true);
  }

  @override
  void initState() {
    super.initState();
    _moveCtrl.addListener(() {
      final anim = _moveAnim;
      if (anim != null && mounted) {
        setState(() => _userPos = anim.value);
      }
    });
    _load();
    _loadVisualAssets();
  }

  @override
  void dispose() {
    _moveCtrl.dispose();
    _bubbleTimer?.cancel();
    _dpadTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await ApiClient().getLifeHomeState(characterId: _characterId);
      if (!mounted) return;
      setState(() {
        _state = r;
        _characterId = (r['character_id'] as int?) ?? 0;
        _rooms = ((r['rooms'] as List?) ?? const [])
            .map((e) => _Room.fromMap(Map<String, dynamic>.from(e as Map)))
            .toList();
        _pets = ((r['pets'] as List?) ?? const []).cast<Map<String, dynamic>>();
        _currentRoom = r['current_room'] as String? ?? 'living';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      setState(() {
        _loading = false;
        _error = l10n.loadHomeFailed(e.toString());
      });
    }
  }

  _Room get _room {
    for (final r in _rooms) {
      if (r.id == _currentRoom) return r;
    }
    return _rooms.isNotEmpty ? _rooms.first : const _Room('living', 'living', []);
  }

  // ── 触屏移动 ──
  Offset _toLogic(Offset screen, Size size) {
    final scale = math.min(size.width / _cw, size.height / _ch);
    final ox = (size.width - _cw * scale) / 2;
    final oy = (size.height - _ch * scale) / 2;
    return Offset((screen.dx - ox) / scale, (screen.dy - oy) / scale);
  }

  void _onTap(Offset screen, Size size) {
    final p = _toLogic(screen, size);
    for (final f in _room.furniture) {
      final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
      if (rect.contains(p)) {
        setState(() => _selected = f.key);
        _showFurnitureDialog(f);
        return;
      }
    }
    _moveTo(Offset(
      p.dx.clamp(20.0, _cw - 20),
      p.dy.clamp(20.0, _ch - 20),
    ));
  }

  void _moveTo(Offset target) {
    setState(() => _selected = null);
    _moveCtrl.stop();
    _moveAnim = Tween<Offset>(
      begin: _userPos,
      end: target,
    ).animate(CurvedAnimation(parent: _moveCtrl, curve: Curves.easeInOut));
    _moveCtrl
      ..duration = Duration(
        milliseconds: (200 + (_userPos - target).distance * 2).round(),
      )
      ..forward(from: 0);
  }

  // ── 虚拟方向键 ──
  void _startDpad(String dir) {
    _dpadDir = dir;
    _dpadTimer?.cancel();
    _dpadTimer = Timer.periodic(const Duration(milliseconds: 40), (_) {
      if (!mounted || _dpadDir == null) return;
      setState(() {
        switch (_dpadDir) {
          case 'up':
            _userPos = Offset(_userPos.dx, math.max(20, _userPos.dy - 7));
            break;
          case 'down':
            _userPos = Offset(_userPos.dx, math.min(_ch - 20, _userPos.dy + 7));
            break;
          case 'left':
            _userPos = Offset(math.max(20, _userPos.dx - 7), _userPos.dy);
            break;
          case 'right':
            _userPos = Offset(math.min(_cw - 20, _userPos.dx + 7), _userPos.dy);
            break;
        }
      });
    });
  }

  void _stopDpad() {
    _dpadDir = null;
    _dpadTimer?.cancel();
  }

  // ── 交互按钮：找角色附近最近的可交互家具 ──
  void _interactNearby() {
    final l10n = AppLocalizations.of(context)!;
    _Furniture? nearest;
    var best = double.infinity;
    for (final f in _room.furniture) {
      if (f.action == null) continue;
      final cx = (f.gx + f.gw / 2) * 40;
      final cy = (f.gy + f.gh / 2) * 40;
      final d = (Offset(cx, cy) - _userPos).distance;
      if (d < 80 && d < best) {
        best = d;
        nearest = f;
      }
    }
    final n = nearest;
    if (n != null) {
      setState(() => _selected = n.key);
      _showFurnitureDialog(n);
    } else {
      setState(() => _actionBubble = l10n.noNearbyFurniture);
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(milliseconds: 1500), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    }
  }

  // ── 居中弹窗 ──
  Future<void> _showFurnitureDialog(_Furniture f) async {
    final l10n = AppLocalizations.of(context)!;
    await showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => AlertDialog(
        title: Text(f.name),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (f.key == 'petbed')
              FilledButton.tonal(
                onPressed: () {
                  Navigator.pop(ctx);
                  _openPets();
                },
                child: Text(l10n.petEntry),
              )
            else if (f.action != null)
              FilledButton(
                onPressed: () {
                  Navigator.pop(ctx);
                  _runEvent(f.action!);
                },
                child: Text(_kActionLabels(l10n)[f.action] ?? l10n.interact),
              )
            else
              Text(l10n.furnitureInactive,
                  style: const TextStyle(fontSize: 13, color: Colors.grey)),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () {
              Navigator.pop(ctx);
              if (mounted) setState(() => _selected = null);
            },
            child: Text(l10n.cancel),
          ),
        ],
      ),
    );
    if (mounted) setState(() => _selected = null);
  }

  Future<void> _openPets() async {
    await Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => const PetScreen()),
    );
    _load();
  }

  Future<void> _runEvent(String action, {int? petId}) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await ApiClient().postLifeHomeEvent(
        characterId: _characterId,
        action: action,
        petId: petId,
      );
      if (!mounted) return;
      setState(() {
        final p = r['player'] as Map<String, dynamic>?;
        final old = _state;
        if (p != null && old != null) {
          old['player'] = p;
        }
        final pet = r['pet'] as Map<String, dynamic>?;
        if (pet != null) {
          final pid = '${pet['id']}';
          for (var i = 0; i < _pets.length; i++) {
            if ('${_pets[i]['id']}' == pid) {
              _pets[i] = pet;
            }
          }
          _petState[pid] = action == 'pet_feed' ? 'eating' : 'playing';
          Timer(const Duration(seconds: 2), () {
            if (mounted) setState(() => _petState[pid] = 'idle');
          });
        }
        _actionBubble = petId != null
            ? '${_kActionLabels(l10n)[action] ?? l10n.interact}${l10n.actionDone}'
            : l10n.actionInProgress(_kActionLabels(l10n)[action] ?? l10n.interact);
      });
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(seconds: 2), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _actionBubble = l10n.opFailedErr(e.toString());
      });
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(seconds: 2), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(_state?['character_name'] as String? ?? l10n.homeTitle),
        actions: [
          if (_state != null)
            IconButton(
              tooltip: l10n.refresh,
              icon: const Icon(Icons.refresh),
              onPressed: _load,
            ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 12),
                      FilledButton.tonal(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : Column(
                  children: [
                    _statusBar(),
                    _roomTabs(),
                    Expanded(child: _roomView()),
                    _controlBar(),
                  ],
                ),
    );
  }

  Widget _statusBar() {
    final l10n = AppLocalizations.of(context)!;
    final p = _state?['player'] as Map<String, dynamic>?;
    final stamina = (p?['stamina'] as num?)?.toInt() ?? 70;
    final mood = (p?['mood'] as num?)?.toInt() ?? 50;
    final hunger = (p?['hunger'] as num?)?.toInt() ?? 70;
    final ai = _state?['ai'] as Map<String, dynamic>?;
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _bar(l10n.stamina, stamina, Colors.green),
              const SizedBox(width: 10),
              _bar(l10n.mood, mood, Colors.orange),
              const SizedBox(width: 10),
              _bar(l10n.petHunger, hunger, Colors.redAccent),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${ai?['name'] ?? ''}：${ai?['current_status'] ?? ''}',
            style: const TextStyle(fontSize: 12, color: Colors.grey),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  Widget _bar(String label, int value, Color color) {
    return Expanded(
      child: Row(
        children: [
          Text(label, style: const TextStyle(fontSize: 11)),
          const SizedBox(width: 4),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: (value / 100).clamp(0.0, 1.0),
                minHeight: 8,
                color: color,
                backgroundColor: color.withValues(alpha: 0.15),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _roomTabs() {
    if (_rooms.isEmpty) return const SizedBox.shrink();
    return SizedBox(
      height: 40,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        itemCount: _rooms.length,
        separatorBuilder: (_, __) => const SizedBox(width: 8),
        itemBuilder: (_, i) {
          final r = _rooms[i];
          final active = r.id == _currentRoom;
          return ChoiceChip(
            label: Text(r.name),
            selected: active,
            onSelected: (_) => _switchRoom(r.id),
          );
        },
      ),
    );
  }

  void _switchRoom(String id) {
    setState(() {
      _currentRoom = id;
      _selected = null;
    });
  }

  Widget _roomView() {
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(constraints.maxWidth, constraints.maxHeight);
        return Stack(
          children: [
            GestureDetector(
              onTapUp: (d) => _onTap(d.localPosition, size),
              child: CustomPaint(
                size: Size.infinite,
                painter: _RoomPainter(
                  roomId: _currentRoom,
                  furniture: _room.furniture,
                  userPos: _userPos,
                  aiStatus:
                      (_state?['ai'] as Map<String, dynamic>?)?['current_status']
                              as String? ??
                          '',
                  selected: _selected,
                  bubble: _actionBubble,
                  images: _images,
                  imagesReady: _imagesReady,
                ),
              ),
            ),
            // 2026-08-15 暂时隐藏宠物实体（挡住「宠物窝」入口；恢复时取消注释）
            // if (_currentRoom == 'living')
            //   for (var i = 0; i < _pets.length && i < 4; i++)
          ],
        );
      },
    );
  }

  Widget _controlBar() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      child: Row(
        children: [
          // 方向键（左/上/下/右）
          _dpad(),
          const Spacer(),
          // 交互按钮（模拟 E）
          Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              GestureDetector(
                onTapDown: (_) => _interactNearby(),
                child: Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.primary,
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.touch_app, color: Colors.white, size: 26),
                ),
              ),
              const SizedBox(height: 2),
              Text(l10n.interact, style: const TextStyle(fontSize: 10)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _dpad() {
    Widget keyBtn(IconData icon, String dir) {
      return GestureDetector(
        onTapDown: (_) => _startDpad(dir),
        onTapUp: (_) => _stopDpad(),
        onTapCancel: _stopDpad,
        child: Container(
          width: 44,
          height: 44,
          margin: const EdgeInsets.all(2),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.9),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon, size: 22, color: Colors.grey.shade700),
        ),
      );
    }

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        keyBtn(Icons.chevron_left, 'left'),
        Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            keyBtn(Icons.keyboard_arrow_up, 'up'),
            keyBtn(Icons.keyboard_arrow_down, 'down'),
          ],
        ),
        keyBtn(Icons.chevron_right, 'right'),
      ],
    );
  }
}

// ── 像素房间绘制 ──
class _RoomPainter extends CustomPainter {
  final String roomId;
  final List<_Furniture> furniture;
  final Offset userPos;
  final String aiStatus;
  final String? selected;
  final String? bubble;
  final Map<String, ui.Image> images;
  final bool imagesReady;

  _RoomPainter({
    required this.roomId,
    required this.furniture,
    required this.userPos,
    required this.aiStatus,
    required this.selected,
    required this.bubble,
    required this.images,
    required this.imagesReady,
  });

  (Color, Color, Color) _theme() {
    switch (roomId) {
      case 'bedroom':
        return (const Color(0xFFE3D6C4), const Color(0xFFB8A080), const Color(0xFF7B6B55));
      case 'kitchen':
        return (const Color(0xFFE8E4DA), const Color(0xFFD0D0C0), const Color(0xFF888878));
      case 'bathroom':
        return (const Color(0xFFD6E8EC), const Color(0xFFA8C8D8), const Color(0xFF7898A8));
      default:
        return (const Color(0xFFE8DCC8), const Color(0xFFD9B98A), const Color(0xFF8B7355));
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final scale = math.min(size.width / 640.0, size.height / 480.0);
    canvas.save();
    canvas.translate(
      (size.width - 640 * scale) / 2,
      (size.height - 480 * scale) / 2,
    );
    canvas.scale(scale);

    final (wall, floor, wallDark) = _theme();
    // 地板平铺（40px 网格，480-60=420 高 -> 10.5 格，取 11 行覆盖）
    final floorImg = images['floor_$roomId'];
    final wallImg = images['wall_$roomId'];
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 11; j++) {
        if (floorImg != null) {
          canvas.drawImage(floorImg, Offset(i * 40.0, 60 + j * 40.0), Paint());
        }
      }
    }
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 2; j++) {
        if (wallImg != null) {
          canvas.drawImage(wallImg, Offset(i * 40.0, j * 40.0), Paint());
        }
      }
    }
    // 兜底：素材未加载时用纯色 + 网格
    if (wallImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 0, 640, 60), Paint()..color = wall);
    }
    if (floorImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 60, 640, 420), Paint()..color = floor);
      final grid = Paint()
        ..color = wallDark.withValues(alpha: 0.35)
        ..strokeWidth = 1;
      for (var i = 1; i < 16; i++) {
        canvas.drawLine(Offset(i * 40.0, 60), Offset(i * 40.0, 480), grid);
      }
      for (var j = 1; j < 11; j++) {
        canvas.drawLine(Offset(0, 60 + j * 40.0), Offset(640, 60 + j * 40.0), grid);
      }
    }
    // 窗户（叠在墙上）
    canvas.drawRect(
      const Rect.fromLTWH(430, 10, 120, 45),
      Paint()..color = const Color(0xFFBFE3FF),
    );
    canvas.drawLine(const Offset(490, 10), const Offset(490, 55),
        Paint()..color = const Color(0xFFFFFFFF)..strokeWidth = 3);
    canvas.drawLine(const Offset(430, 32), const Offset(550, 32),
        Paint()..color = const Color(0xFFFFFFFF)..strokeWidth = 3);

    // 按前后（脚底 Y）排序绘制：靠后的先画，实现前后遮挡
    final draws = <(double, void Function())>[];
    for (final f in furniture) {
      final bottomY = (f.gy + f.gh) * 40;
      draws.add((bottomY, () => _paintFurniture(canvas, f)));
    }
    draws.add((320, () => _paintCharacter(canvas, const Offset(220, 320), 'char_ai')));
    draws.add((userPos.dy, () => _paintCharacter(canvas, userPos, 'char_user')));
    draws.sort((a, b) => a.$1.compareTo(b.$1));
    for (final d in draws) {
      d.$2();
    }

    if (bubble != null && bubble!.isNotEmpty) {
      _paintBubble(canvas, userPos + const Offset(0, -46), bubble!);
    }

    canvas.restore();
  }

  void _paintFurniture(Canvas canvas, _Furniture f) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    if (selected == f.key) {
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFFFFC107),
      );
    }
    final wood = Paint()..color = const Color(0xFF9B7E5E);
    final img = images['furn_${f.key}'];
    if (img != null) {
      // 素材脚底对齐到格子底部（高度包含向上突出的部分）
      canvas.drawImage(
        img,
        Offset(f.gx * 40, (f.gy + f.gh) * 40 - img.height.toDouble()),
        Paint(),
      );
    } else {
      _paintFurnitureFallback(canvas, f, wood);
    }
    final tp = TextPainter(
      text: TextSpan(
        text: f.name,
        style: const TextStyle(fontSize: 11, color: Color(0xFF5A4632)),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(f.gx * 40, f.gy * 40 - 14));
  }

  void _paintFurnitureFallback(Canvas canvas, _Furniture f, Paint wood) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    switch (f.key) {
      case 'bed':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(6)),
          Paint()..color = const Color(0xFF7FA8C9),
        );
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 6, 30, 22),
          Paint()..color = const Color(0xFFFFFFFF),
        );
        break;
      case 'sofa':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(8)),
          Paint()..color = const Color(0xFF8B6FA8),
        );
        break;
      case 'tv':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF222831));
        canvas.drawRect(rect.deflate(8), Paint()..color = const Color(0xFF9BE8FF));
        break;
      case 'stove':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 2; i++) {
          canvas.drawCircle(
            Offset(f.gx * 40 + 26 + i * 30, f.gy * 40 + 20),
            11,
            Paint()..color = const Color(0xFF333333),
          );
        }
        break;
      case 'fridge':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF9AA5B1));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 36),
          Paint()..color = const Color(0xFF6B7684)..strokeWidth = 2,
        );
        break;
      case 'table':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'desk':
        canvas.drawRect(rect, wood);
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 10, f.gy * 40 + 8, 20, 14),
          Paint()..color = const Color(0xFFE8E2D8),
        );
        break;
      case 'bookshelf':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 3; i++) {
          canvas.drawLine(
            Offset(f.gx * 40 + 2, f.gy * 40 + 14 + i * 14),
            Offset(f.gx * 40 + 38, f.gy * 40 + 14 + i * 14),
            Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
          );
        }
        break;
      case 'shower':
        canvas.drawRect(rect, Paint()..color = const Color(0xFF9DD8F0));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 12),
          8,
          Paint()..color = const Color(0xFF4A90D9),
        );
        break;
      case 'petbed':
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 14, 28, 18),
          Paint()..color = const Color(0xFFE8A0A0),
        );
        break;
      case 'wardrobe':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFFA8825F));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 72),
          Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
        );
        break;
      case 'nightstand':
      case 'coffee':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'chair':
        canvas.drawRect(rect.deflate(6), Paint()..color = const Color(0xFFA8825F));
        break;
      case 'speaker':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF4A4E69));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 18),
          7,
          Paint()..color = const Color(0xFF22223B),
        );
        break;
      case 'game':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF2A2A3A));
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 8, f.gy * 40 + 10, 24, 16),
          Paint()..color = const Color(0xFF7BE0FF),
        );
        break;
      case 'bathtub':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(3), const Radius.circular(10)),
          Paint()..color = const Color(0xFFFFFFFF),
        );
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(10), const Radius.circular(6)),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'sink':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFE8E2D8));
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 12, f.gy * 40 + 10, 16, 10),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'plant':
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 14, f.gy * 40 + 24, 12, 10),
          Paint()..color = const Color(0xFFB07A3E),
        );
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 14),
          8,
          Paint()..color = const Color(0xFF3E9B4F),
        );
        break;
      default:
        canvas.drawRect(rect, wood);
    }
  }

  void _paintCharacter(Canvas canvas, Offset p, String imgKey) {
    final img = images[imgKey];
    if (img != null) {
      // 精灵底部对齐到脚点 p
      canvas.drawImage(
        img,
        Offset(p.dx - img.width / 2, p.dy - img.height.toDouble()),
        Paint(),
      );
      return;
    }
    final color = imgKey == 'char_ai'
        ? const Color(0xFFE05A5A)
        : const Color(0xFF4A90D9);
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy - 26, 16, 14),
        Paint()..color = const Color(0xFFF2C48D));
    canvas.drawRect(Rect.fromLTWH(p.dx - 10, p.dy - 12, 20, 16), Paint()..color = color);
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
    canvas.drawRect(Rect.fromLTWH(p.dx + 1, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
  }

  void _paintBubble(Canvas canvas, Offset p, String text) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 12, color: Color(0xFF333333)),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final rect = Rect.fromLTWH(
      p.dx - tp.width / 2 - 8,
      p.dy - 24,
      tp.width + 16,
      tp.height + 10,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(8)),
      Paint()..color = const Color(0xFFFFFFFF),
    );
    tp.paint(canvas, Offset(p.dx - tp.width / 2, p.dy - 20));
  }

  @override
  bool shouldRepaint(covariant _RoomPainter old) =>
      old.roomId != roomId ||
      old.userPos != userPos ||
      old.selected != selected ||
      old.bubble != bubble ||
      old.furniture != furniture ||
      old.imagesReady != imagesReady ||
      old.images != images;
}