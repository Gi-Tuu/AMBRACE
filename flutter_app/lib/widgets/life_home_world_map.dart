import 'dart:ui' as ui;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:vector_math/vector_math_64.dart' show Vector3;
import 'package:ai_companion/l10n/app_localizations.dart';

/// 小家大地图 v1.2（2026-08-27）：可交互世界画布组件。
///
/// 输入后端 home_state.world 载荷 →
/// - 按 room_origins 把 4 房间画在同一张大画布上（每房间 ROOM_W×ROOM_H 格）；
/// - 门（adjacency）与出口（exit）标记；
/// - 角色坐标 character.wx/wy（格子坐标），location != "home" 时角色移到出口并隐藏室内标记。
///
/// 与 v1.1（只读 InteractiveViewer）不同，v1.2：
/// - 完全去掉 InteractiveViewer（无双指捏合 / 无拖动平移），改为 Transform + 手势自管 [viewScale]/[viewOffset]；
/// - 镜头始终跟随角色（进入时/角色移动时以角色为画面中心），用户不能手动拖动地图；
/// - 缩放按钮已移到宿主工具栏（issue #3）：本组件只暴露 [zoomIn]/[zoomOut]/[resetView]，宿主通过 GlobalKey 调用；
/// - 点家具上抛 [onFurnitureTap]，长按 300ms 拖动家具上抛 [onFurnitureDragStart]/[onFurnitureDragUpdate]/[onFurnitureDragEnd]；
/// - 点地面触发角色移动动画（镜头保持跟随）。
class LifeHomeWorldMap extends StatefulWidget {
  final Map<String, dynamic> world;
  final AppLocalizations l10n;

  /// 房间 id → 显示名（后端 rooms[].name，缺省用 id）。
  final Map<String, String> roomNames;

  /// 房间家具数据（后端 home_state.rooms，与旧房间视图同一来源；拖动时由宿主实时更新）。
  final List<Map<String, dynamic>> rooms;

  /// 小家像素素材（floor_*/wall_*/furn_*/char_*，由 HomeVisualScreen 预加载）。
  final Map<String, ui.Image> images;

  /// 编辑态：被编辑家具所在房间与 key（用于高亮 + 宿主回退/旋转）。
  final String? editingRoom;
  final String? editingKey;

  /// 选中（弹窗交互）家具的 key；配合地图内部记录的命中房间高亮。
  final String? selected;

  /// 点家具（非编辑态 → 弹交互弹窗；编辑态 → 进入被编辑）。
  final void Function(String roomId, String key) onFurnitureTap;

  /// 长按拖动家具。
  final void Function(String roomId, String key) onFurnitureDragStart;
  final void Function(String roomId, String key, double gx, double gy)
      onFurnitureDragUpdate;
  final void Function(String roomId, String key, double gx, double gy)
      onFurnitureDragEnd;

  const LifeHomeWorldMap({
    super.key,
    required this.world,
    required this.l10n,
    this.roomNames = const {},
    this.rooms = const [],
    this.images = const {},
    this.editingRoom,
    this.editingKey,
    this.selected,
    this.onFurnitureTap = _noopFurnitureTap,
    this.onFurnitureDragStart = _noopFurnitureTap,
    this.onFurnitureDragUpdate = _noopFurnitureDragUpdate,
    this.onFurnitureDragEnd = _noopFurnitureDragUpdate,
  });

  static void _noopFurnitureTap(String roomId, String key) {}
  static void _noopFurnitureDragUpdate(
      String roomId, String key, double gx, double gy) {}

  @override
  State<LifeHomeWorldMap> createState() => LifeHomeWorldMapState();
}

const double _kCell = 40.0;

/// 视图缩放范围（沿用 HomeVisualScreen 常量，避免双指捏合/底部 tab 滑动冲突）。
const double _kMinViewScale = 0.6;
const double _kMaxViewScale = 2.5;
const double _kStepScale = 1.25;

/// 世界画布总尺寸（px）：按 room_origins + room_size 计算。
Size _worldSize(Map<String, dynamic> world) {
  final origins = (world['room_origins'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
  final rs = (world['room_size'] as Map<String, dynamic>?) ?? const <String, dynamic>{'w': 16, 'h': 12};
  final rw = ((rs['w'] as num?) ?? 16).toDouble();
  final rh = ((rs['h'] as num?) ?? 12).toDouble();
  double maxX = 0, maxY = 0;
  origins.forEach((_, v) {
    final o = (v as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final x = ((o['wx'] as num?) ?? 0).toDouble();
    final y = ((o['wy'] as num?) ?? 0).toDouble();
    if (x + rw > maxX) maxX = x + rw;
    if (y + rh > maxY) maxY = y + rh;
  });
  return Size(maxX * _kCell, maxY * _kCell);
}

class LifeHomeWorldMapState extends State<LifeHomeWorldMap>
    with SingleTickerProviderStateMixin {
  // ── 视图变换（镜头） ──
  double _viewScale = 1.0;
  Offset _viewOffset = Offset.zero;
  Size _viewSize = Size.zero;

  // ── 角色（世界格 foot 位置，px） ──
  Offset _charWorld = Offset.zero;
  bool _outside = false;
  late final AnimationController _moveCtrl;
  Animation<Offset>? _moveAnim;

  // ── 家具拖动状态 ──
  String? _dragRoomId;
  String? _dragKey;
  Offset? _dragGrab;
  (double, double)? _lastDragGrid;

  // 上次点选/交互的房间（用于高亮 selected 家具）
  String? _selectedRoomForHighlight;

  double get viewScale => _viewScale;
  Offset get viewOffset => _viewOffset;
  Size get viewSize => _viewSize;
  Offset get characterWorld => _charWorld;

  @override
  void initState() {
    super.initState();
    _initFromWorld();
    _moveCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    )..addListener(() {
        final anim = _moveAnim;
        if (anim != null && mounted) {
          setState(() {
            _charWorld = anim.value;
            _outside = false;
            _followUser();
          });
        }
      });
    // 进入界面：帧后把镜头对准角色（角色始终可见）。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) centerOnCharacter();
    });
  }

  @override
  void didUpdateWidget(covariant LifeHomeWorldMap oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.world != widget.world) {
      _initFromWorld();
      _viewScale = 1.0;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) centerOnCharacter();
      });
    }
  }

  @override
  void dispose() {
    _moveCtrl.dispose();
    super.dispose();
  }

  void _initFromWorld() {
    final world = widget.world;
    final ch = (world['character'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    _outside = (ch['location'] as String?) != 'home';
    if (_outside) {
      final exit = (world['exit'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final eo = (world['room_origins']?[exit['room']] as Map<String, dynamic>?) ??
          const <String, dynamic>{};
      final ex = (((eo['wx'] as num?) ?? 0) + ((exit['x'] as num?) ?? 0)).toDouble() * _kCell;
      final ey = (((eo['wy'] as num?) ?? 0) + ((exit['y'] as num?) ?? 0)).toDouble() * _kCell;
      _charWorld = Offset(ex, ey);
    } else {
      final wx = ((ch['wx'] as num?) ?? 0).toDouble();
      final wy = ((ch['wy'] as num?) ?? 0).toDouble();
      _charWorld = Offset(wx * _kCell, wy * _kCell);
    }
  }

  // ── 镜头：进入时/移动时以角色为中心 ──
  void centerOnCharacter() {
    if (mounted && _viewSize != Size.zero) {
      setState(() {
        _viewOffset = Offset(
          _viewSize.width / 2 - _charWorld.dx * _viewScale,
          _viewSize.height / 2 - _charWorld.dy * _viewScale,
        );
      });
    }
  }

  /// 镜头跟随：角色始终保持在视口中心（不再有手动平移手势，故无需暂停逻辑）。
  void _followUser() {
    if (_viewSize == Size.zero) return;
    final target = Offset(
      _viewSize.width / 2 - _charWorld.dx * _viewScale,
      _viewSize.height / 2 - _charWorld.dy * _viewScale,
    );
    if ((target - _viewOffset).distance > 0.5) {
      _viewOffset = target;
    }
  }

  // ── 角色移动（点地面 / dvpad） ──
  void _moveTo(Offset targetPx) {
    final size = _worldSize(widget.world);
    final target = Offset(
      targetPx.dx.clamp(20.0, size.width - 20.0),
      targetPx.dy.clamp(20.0, size.height - 20.0),
    );
    _moveCtrl.stop();
    _moveAnim = Tween<Offset>(
      begin: _charWorld,
      end: target,
    ).animate(CurvedAnimation(parent: _moveCtrl, curve: Curves.easeInOut));
    _moveCtrl
      ..duration = Duration(
        milliseconds: (200 + (_charWorld - target).distance * 2).round(),
      )
      ..forward(from: 0);
  }

  /// 宿主方向键每拍调用：按世界 px 移动角色（镜头跟随）。
  void moveBy(double dx, double dy) {
    final size = _worldSize(widget.world);
    setState(() {
      _charWorld = Offset(
        (_charWorld.dx + dx).clamp(20.0, size.width - 20.0),
        (_charWorld.dy + dy).clamp(20.0, size.height - 20.0),
      );
      _outside = false;
    });
    _followUser();
  }

  /// 交互按钮：找角色附近最近的可交互家具；命中则上抛 onFurnitureTap，否则返回 false（宿主弹提示）。
  bool interactNearby() {
    final byId = {for (final r in widget.rooms) (r['id'] as String? ?? ''): r};
    final origins = (widget.world['room_origins'] as Map<String, dynamic>?) ??
        const <String, dynamic>{};
    Map<String, dynamic>? best;
    String? bestRoom;
    var bestDist = double.infinity;
    origins.forEach((rid, v) {
      final room = byId[rid];
      if (room == null) return;
      final o = (v as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final ox = ((o['wx'] as num?) ?? 0).toDouble() * _kCell;
      final oy = ((o['wy'] as num?) ?? 0).toDouble() * _kCell;
      for (final f in ((room['furniture'] as List?) ?? const [])) {
        final m = (f as Map<String, dynamic>?) ?? const <String, dynamic>{};
        final key = m['key'] as String? ?? '';
        final action = m['action'] as String?;
        final interactive = action != null || key == 'game' || key == 'petbed';
        if (!interactive) continue;
        final cx = ox + ((m['gx'] as num?) ?? 0).toDouble() * _kCell +
            ((m['gw'] as num?) ?? 1).toDouble() * _kCell / 2;
        final cy = oy + ((m['gy'] as num?) ?? 0).toDouble() * _kCell +
            ((m['gh'] as num?) ?? 1).toDouble() * _kCell / 2;
        final d = (Offset(cx, cy) - _charWorld).distance;
        if (d < 80 && d < bestDist) {
          bestDist = d;
          best = m;
          bestRoom = rid;
        }
      }
    });
    final b = best;
    final br = bestRoom;
    if (b == null || br == null) return false;
    setState(() => _selectedRoomForHighlight = br);
    widget.onFurnitureTap(br, b['key'] as String? ?? '');
    return true;
  }

  // ── 坐标换算 ──
  Offset _toWorld(Offset screen) => (screen - _viewOffset) / _viewScale;

  Map<String, dynamic>? _roomById(String id) {
    for (final r in widget.rooms) {
      if (r['id'] == id) return r;
    }
    return null;
  }

  /// 命中检测：所有房间家具，按脚底 Y（画面更靠前）优先；返回 (房间id, key)。
  (String, String)? _hitFurniture(Offset worldPx) {
    final origins = (widget.world['room_origins'] as Map<String, dynamic>?) ??
        const <String, dynamic>{};
    String? bestRoom;
    String? bestKey;
    var bestBottom = double.negativeInfinity;
    origins.forEach((rid, v) {
      final room = _roomById(rid);
      if (room == null) return;
      final o = (v as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final ox = ((o['wx'] as num?) ?? 0).toDouble() * _kCell;
      final oy = ((o['wy'] as num?) ?? 0).toDouble() * _kCell;
      for (final f in ((room['furniture'] as List?) ?? const [])) {
        final m = (f as Map<String, dynamic>?) ?? const <String, dynamic>{};
        final gx = ((m['gx'] as num?) ?? 0).toDouble() * _kCell;
        final gy = ((m['gy'] as num?) ?? 0).toDouble() * _kCell;
        final gw = ((m['gw'] as num?) ?? 1).toDouble() * _kCell;
        final gh = ((m['gh'] as num?) ?? 1).toDouble() * _kCell;
        final rect = Rect.fromLTWH(ox + gx, oy + gy, gw, gh);
        if (!rect.contains(worldPx)) continue;
        final bottom = oy + gy + gh;
        if (bottom > bestBottom) {
          bestBottom = bottom;
          bestRoom = rid;
          bestKey = m['key'] as String? ?? '';
        }
      }
    });
    if (bestRoom == null || bestKey == null) return null;
    return (bestRoom!, bestKey!);
  }

  /// 家具世界坐标左上角（px）。
  Offset _furnitureTopLeft(String roomId, Map<String, dynamic> f) {
    final o = (widget.world['room_origins']?[roomId] as Map<String, dynamic>?) ??
        const <String, dynamic>{};
    final ox = ((o['wx'] as num?) ?? 0).toDouble();
    final oy = ((o['wy'] as num?) ?? 0).toDouble();
    final gx = ((f['gx'] as num?) ?? 0).toDouble();
    final gy = ((f['gy'] as num?) ?? 0).toDouble();
    return Offset((ox + gx) * _kCell, (oy + gy) * _kCell);
  }

  /// 世界左上角 px → 房间内格坐标 gx/gy（钳制在家具矩形[0, roomSize-furnitureSize]内）。
  (double, double) _toRoomGrid(String roomId, Offset worldTopLeft) {
    final rs = (widget.world['room_size'] as Map<String, dynamic>?) ??
        const <String, dynamic>{'w': 16, 'h': 12};
    final rw = ((rs['w'] as num?) ?? 16).toDouble();
    final rh = ((rs['h'] as num?) ?? 12).toDouble();
    final o = (widget.world['room_origins']?[roomId] as Map<String, dynamic>?) ??
        const <String, dynamic>{};
    final ox = ((o['wx'] as num?) ?? 0).toDouble();
    final oy = ((o['wy'] as num?) ?? 0).toDouble();
    final f = _findFurniture(roomId, _dragKey ?? '');
    final gw = (f?['gw'] as num?) ?? 1.0;
    final gh = (f?['gh'] as num?) ?? 1.0;
    final rawGx = (worldTopLeft.dx / _kCell) - ox;
    final rawGy = (worldTopLeft.dy / _kCell) - oy;
    final gx = rawGx.clamp(0.0, rw - gw);
    final gy = rawGy.clamp(0.0, rh - gh);
    return (gx, gy);
  }

  // ── 点家具 / 长按拖动 ──
  void _onTap(Offset local) {
    final world = _toWorld(local);
    final hit = _hitFurniture(world);
    if (hit != null) {
      setState(() => _selectedRoomForHighlight = hit.$1);
      widget.onFurnitureTap(hit.$1, hit.$2);
    } else {
      _moveTo(world);
    }
  }

  void _onDragStart(Offset local) {
    final world = _toWorld(local);
    final hit = _hitFurniture(world);
    if (hit == null) return;
    final f = _findFurniture(hit.$1, hit.$2);
    if (f == null) return;
    final tl = _furnitureTopLeft(hit.$1, f);
    setState(() {
      _dragRoomId = hit.$1;
      _dragKey = hit.$2;
      _dragGrab = world - tl;
    });
    widget.onFurnitureDragStart(hit.$1, hit.$2);
  }

  void _onDragUpdate(Offset local) {
    final roomId = _dragRoomId;
    final key = _dragKey;
    final grab = _dragGrab;
    if (roomId == null || key == null || grab == null) return;
    final world = _toWorld(local);
    final grid = _toRoomGrid(roomId, world - grab);
    _lastDragGrid = grid;
    widget.onFurnitureDragUpdate(roomId, key, grid.$1, grid.$2);
  }

  void _onDragEnd() {
    final roomId = _dragRoomId;
    final key = _dragKey;
    if (roomId == null || key == null) return;
    final grid = _lastDragGrid ?? (0.0, 0.0);
    setState(() {
      _dragRoomId = null;
      _dragKey = null;
      _dragGrab = null;
      _lastDragGrid = null;
    });
    widget.onFurnitureDragEnd(roomId, key, grid.$1, grid.$2);
  }

  void _cancelDrag() {
    setState(() {
      _dragRoomId = null;
      _dragKey = null;
      _dragGrab = null;
      _lastDragGrid = null;
    });
  }

  Map<String, dynamic>? _findFurniture(String roomId, String key) {
    final room = _roomById(roomId);
    if (room == null) return null;
    for (final f in ((room['furniture'] as List?) ?? const [])) {
      final m = (f as Map<String, dynamic>?) ?? const <String, dynamic>{};
      if (m['key'] == key) return m;
    }
    return null;
  }

  // ── 缩放（右上角悬浮按钮已移除，改由宿主工具栏调用公开方法；issue #3）──
  void _zoomBy(double factor) {
    final newScale = (_viewScale * factor).clamp(_kMinViewScale, _kMaxViewScale);
    if (newScale == _viewScale) return;
    final anchor = Offset(
      _charWorld.dx * _viewScale + _viewOffset.dx,
      _charWorld.dy * _viewScale + _viewOffset.dy,
    );
    setState(() {
      _viewScale = newScale;
      _viewOffset = Offset(
        anchor.dx - _charWorld.dx * newScale,
        anchor.dy - _charWorld.dy * newScale,
      );
    });
  }

  /// 放大（以角色为锚点）；宿主通过 [_worldKey] 调用。
  void zoomIn() => _zoomBy(_kStepScale);

  /// 缩小（以角色为锚点）。
  void zoomOut() => _zoomBy(1 / _kStepScale);

  /// 复位视图（回到 1.0 并将角色居中）。
  void resetView() {
    setState(() {
      _viewScale = 1.0;
      _viewOffset = Offset(
        _viewSize.width / 2 - _charWorld.dx,
        _viewSize.height / 2 - _charWorld.dy,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = widget.l10n;
    // ClipRect + Stack(hardEdge)：缩放/平移画布最外层就被裁到地图自身区域，
    // 绝不溢出到宿主 Column 的 _statusBar/_worldToolbar/_controlBar 区域（issue #4）。
    return ClipRect(
      child: Column(
        children: [
          Expanded(
            child: LayoutBuilder(
              builder: (context, constraints) {
                final size = Size(constraints.maxWidth, constraints.maxHeight);
                _viewSize = size;
                return Stack(
                  clipBehavior: Clip.hardEdge,
                  children: [
                    // 世界背景：铺满视口（低倍率下避免地图四周露出透明缝隙）
                    Positioned.fill(
                      child: const ColoredBox(color: Color(0xFF2C3A3E)),
                    ),
                    _gestureCanvas(),
                  ],
                );
              },
            ),
          ),
          _legendPanel(l10n, _outside),
        ],
      ),
    );
  }

  Widget _gestureCanvas() {
    return RawGestureDetector(
      gestures: {
        LongPressGestureRecognizer:
            GestureRecognizerFactoryWithHandlers<LongPressGestureRecognizer>(
          () => LongPressGestureRecognizer(
              duration: const Duration(milliseconds: 300)),
          (instance) => instance
            ..onLongPressStart = (d) { _onDragStart(d.localPosition); }
            ..onLongPressMoveUpdate = (d) { _onDragUpdate(d.localPosition); }
            ..onLongPressEnd = (_) { _onDragEnd(); }
            ..onLongPressCancel = _cancelDrag,
        ),
      },
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapUp: (d) => _onTap(d.localPosition),
        child: Transform(
          transform: Matrix4.identity()
            ..translateByVector3(Vector3(_viewOffset.dx, _viewOffset.dy, 0.0))
            ..scaleByVector3(Vector3.all(_viewScale)),
          child: CustomPaint(
            size: _worldSize(widget.world),
            painter: _WorldMapPainter(
              world: widget.world,
              l10n: widget.l10n,
              roomNames: widget.roomNames,
              rooms: widget.rooms,
              images: widget.images,
              characterWorld: _charWorld,
              outside: _outside,
              editingRoom: widget.editingRoom,
              editingKey: widget.editingKey,
              selected: widget.selected,
              selectedRoom: _selectedRoomForHighlight,
              draggingRoom: _dragRoomId,
              draggingKey: _dragKey,
            ),
          ),
        ),
      ),
    );
  }

  Widget _legendPanel(AppLocalizations l10n, bool outside) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      color: scheme.surfaceContainerHighest,
      child: Row(
        children: [
          Icon(Icons.map, size: 14, color: scheme.primary),
          const SizedBox(width: 6),
          Text(l10n.homeWorldMap, style: const TextStyle(fontSize: 12)),
          const Spacer(),
          if (outside) ...[
            const Icon(Icons.door_front_door, size: 14, color: Colors.redAccent),
            const SizedBox(width: 4),
            Text(l10n.homeGoOut, style: const TextStyle(fontSize: 12)),
          ] else ...[
            const Icon(Icons.door_front_door, size: 14),
            const SizedBox(width: 4),
            Text(l10n.homeExit, style: const TextStyle(fontSize: 12)),
          ],
        ],
      ),
    );
  }
}

class _WorldMapPainter extends CustomPainter {
  final Map<String, dynamic> world;
  final AppLocalizations l10n;
  final Map<String, String> roomNames;
  final List<Map<String, dynamic>> rooms;
  final Map<String, ui.Image> images;
  final Offset characterWorld;
  final bool outside;
  final String? editingRoom;
  final String? editingKey;
  final String? selected;
  final String? selectedRoom;
  final String? draggingRoom;
  final String? draggingKey;

  _WorldMapPainter({
    required this.world,
    required this.l10n,
    required this.roomNames,
    required this.rooms,
    required this.images,
    required this.characterWorld,
    required this.outside,
    this.editingRoom,
    this.editingKey,
    this.selected,
    this.selectedRoom,
    this.draggingRoom,
    this.draggingKey,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final origins = (world['room_origins'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final rs = (world['room_size'] as Map<String, dynamic>?) ?? const <String, dynamic>{'w': 16, 'h': 12};
    final rw = ((rs['w'] as num?) ?? 16).toDouble();
    final rh = ((rs['h'] as num?) ?? 12).toDouble();

    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width, size.height),
      Paint()..color = const Color(0xFF2C3A3E),
    );

    final roomById = {for (final r in rooms) (r['id'] as String? ?? ''): r};
    origins.forEach((id, v) {
      final o = (v as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final x = ((o['wx'] as num?) ?? 0).toDouble();
      final y = ((o['wy'] as num?) ?? 0).toDouble();
      final originPx = Offset(x * _kCell, y * _kCell);
      final rect = Rect.fromLTWH(originPx.dx, originPx.dy, rw * _kCell, rh * _kCell);
      final room = roomById[id] ?? const <String, dynamic>{};
      _paintRoomVisuals(canvas, id, room, originPx);
      canvas.drawRect(
        rect,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFF5A4632),
      );
      _paintLabel(canvas, roomNames[id] ?? id, rect.topLeft + const Offset(10, 10));
    });

    final adjacency = (world['adjacency'] as List?) ?? const [];
    for (final a in adjacency) {
      final m = (a as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final from = (m['from'] as String?) ?? '';
      final side = (m['side'] as String?) ?? 'east';
      final o = origins[from] as Map<String, dynamic>?;
      if (o == null) continue;
      final x = ((o['wx'] as num?) ?? 0).toDouble();
      final y = ((o['wy'] as num?) ?? 0).toDouble();
      _paintDoor(canvas, x, y, rw, rh, side);
    }

    // 出口
    final exit = (world['exit'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final exitRoom = (exit['room'] as String?) ?? 'living';
    final exitX = ((exit['x'] as num?) ?? 0).toDouble();
    final exitY = ((exit['y'] as num?) ?? 0).toDouble();
    final eo = origins[exitRoom] as Map<String, dynamic>?;
    if (eo != null) {
      final ex = ((eo['wx'] as num?) ?? 0).toDouble();
      final ey = ((eo['wy'] as num?) ?? 0).toDouble();
      final ep = Offset((ex + exitX) * _kCell, (ey + exitY) * _kCell);
      canvas.drawRect(
        Rect.fromCenter(center: ep, width: _kCell * 1.2, height: _kCell * 1.2),
        Paint()..color = const Color(0xFFFFC107),
      );
      _paintLabel(canvas, l10n.homeExit, ep + const Offset(8, -8), color: const Color(0xFF3A2B00));
    }

    // 角色（当前世界位置，可能随移动动画而变化）
    _paintCharacter(canvas, characterWorld);
  }

  (Color, Color, Color) _roomTheme(String id) {
    switch (id) {
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

  void _paintRoomVisuals(Canvas canvas, String id, Map<String, dynamic> room, Offset origin) {
    final floorImg = images['floor_$id'];
    final wallImg = images['wall_$id'];
    final (wall, floor, wallDark) = _roomTheme(id);
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 2; j++) {
        if (wallImg != null) {
          canvas.drawImage(wallImg, origin + Offset(i * 40.0, j * 40.0), Paint());
        }
      }
      for (var j = 2; j < 12; j++) {
        if (floorImg != null) {
          canvas.drawImage(floorImg, origin + Offset(i * 40.0, j * 40.0), Paint());
        }
      }
    }
    if (wallImg == null) {
      canvas.drawRect(Rect.fromLTWH(origin.dx, origin.dy, 640, 80), Paint()..color = wall);
    }
    if (floorImg == null) {
      canvas.drawRect(Rect.fromLTWH(origin.dx, origin.dy + 80, 640, 400), Paint()..color = floor);
      final grid = Paint()
        ..color = wallDark.withValues(alpha: 0.35)
        ..strokeWidth = 1;
      for (var i = 1; i < 16; i++) {
        canvas.drawLine(origin + Offset(i * 40.0, 80), origin + Offset(i * 40.0, 480), grid);
      }
      for (var j = 2; j < 12; j++) {
        canvas.drawLine(origin + Offset(0, j * 40.0), origin + Offset(640, j * 40.0), grid);
      }
    }
    // 窗户（叠在墙上，与旧视图同位置）
    canvas.drawRect(
      Rect.fromLTWH(origin.dx + 430, origin.dy + 10, 120, 45),
      Paint()..color = const Color(0xFFBFE3FF),
    );
    canvas.drawLine(origin + const Offset(490, 10), origin + const Offset(490, 55),
        Paint()..color = Colors.white..strokeWidth = 3);
    canvas.drawLine(origin + const Offset(430, 32), origin + const Offset(550, 32),
        Paint()..color = Colors.white..strokeWidth = 3);

    final furniture = (room['furniture'] as List?) ?? const [];
    for (final f in furniture) {
      final m = (f as Map<String, dynamic>?) ?? const <String, dynamic>{};
      _paintFurniture(canvas, id, origin, m);
    }
  }

  void _paintFurniture(Canvas canvas, String roomId, Offset origin, Map<String, dynamic> f) {
    final gx = ((f['gx'] as num?) ?? 0).toDouble();
    final gy = ((f['gy'] as num?) ?? 0).toDouble();
    final gw = ((f['gw'] as num?) ?? 1).toDouble();
    final gh = ((f['gh'] as num?) ?? 1).toDouble();
    final key = f['key'] as String? ?? '';
    final name = f['name'] as String? ?? '';
    final rect = Rect.fromLTWH(origin.dx + gx * 40, origin.dy + gy * 40, gw * 40, gh * 40);
    // 高亮：被编辑（橙）/拖动（青）/选中（琥珀）
    if (editingKey == key && editingRoom == roomId) {
      canvas.drawRect(
        rect.inflate(4),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 4
          ..color = const Color(0xFFFF6D00),
      );
    }
    if (draggingKey == key && draggingRoom == roomId) {
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5
          ..color = const Color(0xFF00BCD4),
      );
    }
    if (selected == key && selectedRoom == roomId) {
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFFFFC107),
      );
    }
    final wood = Paint()..color = const Color(0xFF9B7E5E);
    final img = images['furn_$key'];
    if (img != null) {
      canvas.drawImage(
        img,
        Offset(origin.dx + gx * 40, origin.dy + (gy + gh) * 40 - img.height.toDouble()),
        Paint(),
      );
    } else {
      canvas.drawRect(rect, wood);
      canvas.drawRect(
        rect.deflate(4),
        Paint()..color = const Color(0xFFC9B08C),
      );
    }
    final tp = TextPainter(
      text: TextSpan(
        text: name,
        style: const TextStyle(fontSize: 11, color: Color(0xFF5A4632)),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(origin.dx + gx * 40, origin.dy + gy * 40 - 14));
  }

  void _paintDoor(Canvas canvas, double x, double y, double rw, double rh, String side) {
    final doorColor = Paint()..color = const Color(0xFFB8A080);
    if (side == 'east' || side == 'west') {
      final dx = side == 'east' ? (x + rw) * _kCell : x * _kCell;
      canvas.drawRect(
        Rect.fromLTWH(dx - 6, (y + rh / 2) * _kCell - 6, 12, 12),
        doorColor,
      );
    } else {
      final dy = side == 'south' ? (y + rh) * _kCell : y * _kCell;
      canvas.drawRect(
        Rect.fromLTWH((x + rw / 2) * _kCell - 6, dy - 6, 12, 12),
        doorColor,
      );
    }
  }

  void _paintLabel(Canvas canvas, String text, Offset topLeft,
      {Color color = const Color(0xFF3A2B00)}) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: 14, color: color, fontWeight: FontWeight.bold),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, topLeft);
  }

  /// 角色：优先用 char_user 像素精灵（与旧 _RoomPainter 一致：底部对齐到脚点）；
  /// 素材缺失时回退红色圆点色块兜底。
  void _paintCharacter(Canvas canvas, Offset p) {
    final img = images['char_user'];
    if (img != null) {
      canvas.drawImage(
        img,
        Offset(p.dx - img.width / 2, p.dy - img.height.toDouble()),
        Paint(),
      );
      return;
    }
    canvas.drawCircle(p, 14, Paint()..color = const Color(0x22000000));
    canvas.drawCircle(p, 8, Paint()..color = const Color(0xFFE05A5A));
    canvas.drawCircle(
      p,
      8,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = Colors.white,
    );
  }

  @override
  bool shouldRepaint(covariant _WorldMapPainter old) =>
      old.world != world ||
      old.characterWorld != characterWorld ||
      old.outside != outside ||
      old.roomNames != roomNames ||
      old.rooms != rooms ||
      old.images != images ||
      old.editingRoom != editingRoom ||
      old.editingKey != editingKey ||
      old.selected != selected ||
      old.selectedRoom != selectedRoom ||
      old.draggingRoom != draggingRoom ||
      old.draggingKey != draggingKey;
}
