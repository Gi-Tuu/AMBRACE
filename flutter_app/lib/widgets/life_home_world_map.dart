import 'package:flutter/material.dart';
import 'package:vector_math/vector_math_64.dart' show Vector3;
import 'package:ai_companion/l10n/app_localizations.dart';

/// 小家大地图 v1.1（2026-08-26）：世界画布组件。
///
/// 输入后端 home_state.world 载荷 →
/// - 按 room_origins 把 4 房间画在同一张大画布上（每房间 ROOM_W×ROOM_H 格）；
/// - 门（adjacency）与出口（exit）标记；
/// - 角色坐标 character.wx/wy（格子坐标），location != "home" 时角色移到出口并隐藏室内标记；
/// - 支持缩放/平移（InteractiveViewer），摄像机跟随 character.wx/wy。
class LifeHomeWorldMap extends StatefulWidget {
  final Map<String, dynamic> world;
  final AppLocalizations l10n;

  /// 房间 id → 显示名（后端 rooms[].name，缺省用 id）。
  final Map<String, String> roomNames;

  const LifeHomeWorldMap({
    super.key,
    required this.world,
    required this.l10n,
    this.roomNames = const {},
  });

  @override
  State<LifeHomeWorldMap> createState() => _LifeHomeWorldMapState();
}

const double _kCell = 40.0;

class _LifeHomeWorldMapState extends State<LifeHomeWorldMap> {
  final TransformationController _controller = TransformationController();

  @override
  void initState() {
    super.initState();
    _centerOnCharacter();
  }

  @override
  void didUpdateWidget(covariant LifeHomeWorldMap old) {
    super.didUpdateWidget(old);
    if (old.world != widget.world) {
      _centerOnCharacter();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _centerOnCharacter() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final size = MediaQuery.of(context).size;
      final ch = (widget.world['character'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final wx = ((ch['wx'] as num?) ?? 0).toDouble();
      final wy = ((ch['wy'] as num?) ?? 0).toDouble();
      final cx = wx * _kCell;
      final cy = wy * _kCell;
      const scale = 1.0;
      _controller.value = Matrix4.identity()
        ..translateByVector3(
          Vector3(size.width / 2 - cx * scale, size.height / 2 - cy * scale, 0),
        )
        ..scaleByVector3(Vector3.all(scale));
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = widget.l10n;
    final ch = (widget.world['character'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final outside = (ch['location'] as String?) != 'home';
    return Column(
      children: [
        Expanded(
          child: InteractiveViewer(
            transformationController: _controller,
            minScale: 0.3,
            maxScale: 4.0,
            constrained: false,
            boundaryMargin: const EdgeInsets.all(600),
            child: CustomPaint(
              size: _worldSize(widget.world),
              painter: _WorldMapPainter(
                world: widget.world,
                l10n: l10n,
                roomNames: widget.roomNames,
                outside: outside,
              ),
            ),
          ),
        ),
        _legendPanel(l10n, outside),
      ],
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

class _WorldMapPainter extends CustomPainter {
  final Map<String, dynamic> world;
  final AppLocalizations l10n;
  final Map<String, String> roomNames;
  final bool outside;

  _WorldMapPainter({
    required this.world,
    required this.l10n,
    required this.roomNames,
    required this.outside,
  });

  static const Map<String, Color> _roomColors = {
    'living': Color(0xFFE8DCC8),
    'bedroom': Color(0xFFE3D6C4),
    'kitchen': Color(0xFFE8E4DA),
    'bathroom': Color(0xFFD6E8EC),
  };

  @override
  void paint(Canvas canvas, Size size) {
    final origins = (world['room_origins'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    final rs = (world['room_size'] as Map<String, dynamic>?) ?? const <String, dynamic>{'w': 16, 'h': 12};
    final rw = ((rs['w'] as num?) ?? 16).toDouble();
    final rh = ((rs['h'] as num?) ?? 12).toDouble();

    // 背景
    canvas.drawRect(
      Rect.fromLTWH(0, 0, size.width, size.height),
      Paint()..color = const Color(0xFF2C3A3E),
    );

    // 房间
    origins.forEach((id, v) {
      final o = (v as Map<String, dynamic>?) ?? const <String, dynamic>{};
      final x = ((o['wx'] as num?) ?? 0).toDouble();
      final y = ((o['wy'] as num?) ?? 0).toDouble();
      final rect = Rect.fromLTWH(x * _kCell, y * _kCell, rw * _kCell, rh * _kCell);
      final fill = _roomColors[id] ?? const Color(0xFFD8D0C4);
      canvas.drawRect(rect, Paint()..color = fill);
      canvas.drawRect(
        rect,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFF5A4632),
      );
      _paintLabel(canvas, roomNames[id] ?? id, rect.topLeft + const Offset(10, 10));
    });

    // 门（adjacency）：在 from 房间边界画一个浅色缺口标记
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

    // 角色
    final ch = (world['character'] as Map<String, dynamic>?) ?? const <String, dynamic>{};
    Offset cpos;
    if (outside && eo != null) {
      final ex = ((eo['wx'] as num?) ?? 0).toDouble();
      final ey = ((eo['wy'] as num?) ?? 0).toDouble();
      cpos = Offset((ex + exitX) * _kCell, (ey + exitY) * _kCell);
    } else {
      final wx = ((ch['wx'] as num?) ?? 0).toDouble();
      final wy = ((ch['wy'] as num?) ?? 0).toDouble();
      cpos = Offset(wx * _kCell, wy * _kCell);
    }
    _paintCharacter(canvas, cpos);
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

  void _paintCharacter(Canvas canvas, Offset p) {
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
      old.world != world || old.outside != outside || old.roomNames != roomNames;
}
