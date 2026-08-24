import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../utils/memory_decay.dart';

/// 记忆衰减曲线可视化（2026-08-24）：CustomPaint 小曲线图，不引重型图表库。
///
/// 横轴未来 [horizonDays] 天，纵轴保留率 0-100%；艾宾浩斯 R=exp(-Δt/S)，
/// 起点为当前保留率。[isLocked] 为真时曲线冻结为水平线（锁住后不再衰减）；
/// [nextReviewDay] 落在范围内时画橙色下次复习虚线标记。
class MemoryDecayChart extends StatelessWidget {
  final double strengthDays;
  final double elapsedDays;
  final bool isLocked;
  final int horizonDays;
  final double? nextReviewDay;
  final Color accentColor;
  final Color lockedColor;

  const MemoryDecayChart({
    super.key,
    required this.strengthDays,
    required this.elapsedDays,
    required this.isLocked,
    this.horizonDays = 30,
    this.nextReviewDay,
    this.accentColor = const Color(0xFF1E88E5),
    this.lockedColor = const Color(0xFF78909C),
  });

  @override
  Widget build(BuildContext context) {
    final points = memoryDecayCurve(
      strengthDays: strengthDays,
      elapsedDays: elapsedDays,
      horizonDays: horizonDays,
      isLocked: isLocked,
    );
    return CustomPaint(
      painter: _MemoryDecayPainter(
        points: points,
        horizonDays: horizonDays,
        isLocked: isLocked,
        nextReviewDay: nextReviewDay,
        accentColor: accentColor,
        lockedColor: lockedColor,
      ),
      child: const SizedBox.expand(),
    );
  }
}

class _MemoryDecayPainter extends CustomPainter {
  final List<MemoryDecayPoint> points;
  final int horizonDays;
  final bool isLocked;
  final double? nextReviewDay;
  final Color accentColor;
  final Color lockedColor;

  _MemoryDecayPainter({
    required this.points,
    required this.horizonDays,
    required this.isLocked,
    required this.nextReviewDay,
    required this.accentColor,
    required this.lockedColor,
  });

  static const _padLeft = 34.0;
  static const _padRight = 12.0;
  static const _padTop = 12.0;
  static const _padBottom = 22.0;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final gw = w - _padLeft - _padRight;
    final gh = h - _padTop - _padBottom;
    if (gw <= 0 || gh <= 0) return;
    final color = isLocked ? lockedColor : accentColor;

    // 水平网格线 25/50/75/100 + y 轴刻度
    final gridPaint = Paint()
      ..color = Colors.grey.withValues(alpha: 0.25)
      ..strokeWidth = 1;
    for (final p in [25.0, 50.0, 75.0, 100.0]) {
      final y = _padTop + gh * (1 - p / 100);
      canvas.drawLine(Offset(_padLeft, y), Offset(_padLeft + gw, y), gridPaint);
      final tp = _text('${p.toInt()}', 9, Colors.grey.shade600, FontWeight.w500);
      tp.paint(canvas, Offset(_padLeft - tp.width - 6, y - tp.height / 2));
    }

    // 坐标轴
    final axisPaint = Paint()
      ..color = Colors.grey.withValues(alpha: 0.6)
      ..strokeWidth = 1;
    canvas.drawLine(Offset(_padLeft, _padTop), Offset(_padLeft, _padTop + gh), axisPaint);
    canvas.drawLine(Offset(_padLeft, _padTop + gh), Offset(_padLeft + gw, _padTop + gh), axisPaint);

    Offset toOffset(MemoryDecayPoint p) =>
        Offset(_padLeft + gw * (p.day / horizonDays), _padTop + gh * (1 - p.pct / 100));

    // 曲线
    final path = Path();
    for (var i = 0; i < points.length; i++) {
      final o = toOffset(points[i]);
      if (i == 0) {
        path.moveTo(o.dx, o.dy);
      } else {
        path.lineTo(o.dx, o.dy);
      }
    }
    final fill = Path.from(path)
      ..lineTo(_padLeft + gw, _padTop + gh)
      ..lineTo(_padLeft, _padTop + gh)
      ..close();
    canvas.drawPath(fill, Paint()..color = color.withValues(alpha: isLocked ? 0.10 : 0.18)..style = PaintingStyle.fill);
    canvas.drawPath(path, Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round);

    // 起点（当前保留率）
    final start = toOffset(points.first);
    canvas.drawCircle(start, 3.5, Paint()..color = color);
    canvas.drawCircle(start, 1.6, Paint()..color = Colors.white);
    final curTp = _text('${points.first.pct.round()}%', 9, color, FontWeight.w600);
    curTp.paint(canvas, Offset(start.dx + 6, start.dy - curTp.height));

    // 下次复习虚线标记
    if (nextReviewDay != null) {
      final nx = _padLeft + gw * (nextReviewDay! / horizonDays);
      final dashPaint = Paint()
        ..color = Colors.orange.withValues(alpha: 0.8)
        ..strokeWidth = 1.4;
      const dash = 4.0;
      double yy = _padTop;
      while (yy < _padTop + gh) {
        canvas.drawLine(Offset(nx, yy), Offset(nx, math.min(yy + dash, _padTop + gh)), dashPaint);
        yy += dash * 2;
      }
      final tri = Path()
        ..moveTo(nx, _padTop + gh)
        ..lineTo(nx - 4, _padTop + gh - 5)
        ..lineTo(nx + 4, _padTop + gh - 5)
        ..close();
      canvas.drawPath(tri, Paint()..color = Colors.orange);
    }

    // x 轴刻度 0 / mid / horizon
    for (final d in [0, horizonDays ~/ 2, horizonDays]) {
      final x = _padLeft + gw * (d / horizonDays);
      final tp = _text('${d}d', 9, Colors.grey.shade600, FontWeight.w400);
      double tx = x - tp.width / 2;
      if (x < _padLeft + 8) {
        tx = _padLeft;
      } else if (x > _padLeft + gw - 8) {
        tx = _padLeft + gw - tp.width;
      }
      tp.paint(canvas, Offset(tx, _padTop + gh + 6));
    }
  }

  TextPainter _text(String text, double fontSize, Color color, FontWeight weight) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: TextStyle(fontSize: fontSize, color: color, fontWeight: weight)),
      textDirection: TextDirection.ltr,
    )..layout();
    return tp;
  }

  @override
  bool shouldRepaint(covariant _MemoryDecayPainter old) {
    return old.points != points || old.isLocked != isLocked || old.nextReviewDay != nextReviewDay || old.horizonDays != horizonDays;
  }
}
