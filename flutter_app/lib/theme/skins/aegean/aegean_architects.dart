// aegean_architects.dart
// 爱琴海典藏 · 建筑框架组件（书眉/页脚回纹带、三柱式、对称门廊、桂冠、方案A卡框）。
// 只依赖 aegean_geometry（纯公式）与 material，不依赖 aegean_motifs（具象层），避免循环。
// 全部 CustomPainter 静态矢量；中轴一律 size.width/2；shouldRepaint 比较全部字段。
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'aegean_geometry.dart';

Paint _line(Color c, double w) => Paint()
  ..color = c
  ..style = PaintingStyle.stroke
  ..strokeWidth = w
  ..strokeCap = StrokeCap.round
  ..strokeJoin = StrokeJoin.round;

// ───────────────────────── 书眉 / 页脚回纹带 ─────────────────────────
class AegeanMeanderBand extends StatelessWidget {
  final double g;
  final Color color;
  final double opacity;
  final double strokeWidth;
  const AegeanMeanderBand({
    super.key,
    this.g = 4.5,
    required this.color,
    this.opacity = 0.34,
    this.strokeWidth = 1,
  });
  @override
  Widget build(BuildContext context) => CustomPaint(
        size: Size.infinite,
        painter: _MeanderPainter(g, color, opacity, strokeWidth),
      );
}

class _MeanderPainter extends CustomPainter {
  final double g, opacity, sw;
  final Color color;
  _MeanderPainter(this.g, this.color, this.opacity, this.sw);
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawPath(
      AegeanGeometry.meander(Offset.zero & size, g),
      _line(color.withValues(alpha: opacity), sw),
    );
  }

  @override
  bool shouldRepaint(covariant _MeanderPainter old) =>
      old.g != g || old.color != color || old.opacity != opacity || old.sw != sw;
}

// ───────────────────────── 希腊柱（三柱式，全工程唯一柱，自身中轴对称） ─────────────────────────
class AegeanColumn extends StatelessWidget {
  final AegeanOrder order;
  final Color color;
  final double opacity;
  const AegeanColumn({
    super.key,
    this.order = AegeanOrder.ionic,
    required this.color,
    this.opacity = 0.62,
  });
  @override
  Widget build(BuildContext context) => CustomPaint(
        size: Size.infinite,
        painter: _ColumnPainter(order, color, opacity),
      );
}

class _ColumnPainter extends CustomPainter {
  final AegeanOrder order;
  final Color color;
  final double opacity;
  _ColumnPainter(this.order, this.color, this.opacity);

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height, cx = w / 2;
    final c = color.withValues(alpha: opacity);
    void stroke(Path p) => canvas.drawPath(p, _line(c, 1.1));
    void sides(List<Offset> prof) {
      stroke(AegeanGeometry.side(prof, cx, left: true));
      stroke(AegeanGeometry.side(prof, cx, left: false));
    }

    void band(double yy, double rw, double rh) =>
        canvas.drawRect(Rect.fromLTWH(cx - rw / 2, yy, rw, rh), _line(c, 1.1));

    switch (order) {
      case AegeanOrder.doric:
        band(0.060 * h, 0.55 * w, 0.042 * h);
        sides(AegeanGeometry.shaftProfile(0.102 * h, 0.170 * h, 0.200 * w, 0.275 * w, 2.2, 10));
        stroke(Path()
          ..moveTo(cx - 0.200 * w, 0.180 * h)
          ..lineTo(cx + 0.200 * w, 0.180 * h));
        final sh = AegeanGeometry.shaftProfile(0.190 * h, 0.860 * h, 0.183 * w, 0.225 * w, 1.75, 24);
        sides(sh);
        for (final f in AegeanGeometry.flutes(sh, cx, 4)) {
          stroke(f);
        }
        band(0.860 * h, 0.667 * w, 0.040 * h);
        band(0.900 * h, 0.800 * w, 0.045 * h);
      case AegeanOrder.ionic:
        band(0.050 * h, 0.470 * w, 0.032 * h);
        sides(AegeanGeometry.shaftProfile(0.082 * h, 0.170 * h, 0.185 * w, 0.235 * w, 1.6, 10));
        for (final sgn in [-1, 1]) {
          final cc = Offset(cx + sgn * 0.240 * w, 0.145 * h);
          stroke(AegeanGeometry.spiral(cc, 0.115 * w, 0.016 * w, 2.25, sgn < 0 ? math.pi : 0));
          canvas.drawCircle(cc, 0.016 * w, _line(c, 1.1));
        }
        final sh = AegeanGeometry.shaftProfile(0.215 * h, 0.800 * h, 0.158 * w, 0.200 * w, 1.75, 24);
        sides(sh);
        for (final f in AegeanGeometry.flutes(sh, cx, 4)) {
          stroke(f);
        }
        sides(AegeanGeometry.bandProfile(0.800 * h, 0.840 * h, 0.258 * w, 0.033 * w, true, 9));
        sides(AegeanGeometry.bandProfile(0.840 * h, 0.872 * h, 0.233 * w, 0.025 * w, false, 9));
        sides(AegeanGeometry.bandProfile(0.872 * h, 0.912 * h, 0.267 * w, 0.033 * w, true, 9));
        band(0.912 * h, 0.650 * w, 0.038 * h);
      case AegeanOrder.corinthian:
        band(0.048 * h, 0.583 * w, 0.036 * h);
        sides(AegeanGeometry.shaftProfile(0.084 * h, 0.350 * h, 0.192 * w, 0.258 * w, 0.62, 12));
        void crown(List<double> angs, double by, double sw, double shh, double lb, double lc, double wd) {
          for (final a in angs) {
            final base = Offset(cx + sw * math.sin(a), by + shh * (1 - math.cos(a)));
            final len = h * (lb + lc * math.cos(a));
            stroke(AegeanGeometry.leaf(base, len, wd, a * 0.5, 3, 0.14));
          }
        }

        crown([-1.15, -0.6, 0, 0.6, 1.15], 0.325 * h, 0.175 * w, 0.025 * h, 0.110, 0.035, 0.122 * w);
        crown([-0.7, 0, 0.7], 0.215 * h, 0.133 * w, 0.020 * h, 0.085, 0.030, 0.100 * w);
        for (final sgn in [-1, 1]) {
          stroke(AegeanGeometry.spiral(
              Offset(cx + sgn * 0.225 * w, 0.120 * h), 0.058 * w, 0.010 * w, 2.2, sgn < 0 ? math.pi : 0));
        }
        final sh = AegeanGeometry.shaftProfile(0.350 * h, 0.800 * h, 0.158 * w, 0.192 * w, 1.75, 24);
        sides(sh);
        for (final f in AegeanGeometry.flutes(sh, cx, 4)) {
          stroke(f);
        }
        sides(AegeanGeometry.bandProfile(0.800 * h, 0.840 * h, 0.250 * w, 0.033 * w, true, 9));
        sides(AegeanGeometry.bandProfile(0.840 * h, 0.872 * h, 0.225 * w, 0.025 * w, false, 9));
        sides(AegeanGeometry.bandProfile(0.872 * h, 0.912 * h, 0.258 * w, 0.033 * w, true, 9));
        band(0.912 * h, 0.633 * w, 0.038 * h);
    }
  }

  @override
  bool shouldRepaint(covariant _ColumnPainter old) =>
      old.order != order || old.color != color || old.opacity != opacity;
}

// ───────────────────────── 对称双柱门廊（仅登录/引导/大留白页） ─────────────────────────
class AegeanPortico extends StatelessWidget {
  final AegeanOrder order;
  final double columnWidth;
  final double sideInset;
  final Color color;
  final Color deepColor;
  final Widget? child;
  const AegeanPortico({
    super.key,
    this.order = AegeanOrder.ionic,
    this.columnWidth = 54,
    this.sideInset = 40,
    required this.color,
    required this.deepColor,
    this.child,
  });
  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final w = c.maxWidth;
      final leftX = sideInset;
      final rightX = w - sideInset - columnWidth;
      return Stack(children: [
        Positioned(
            left: sideInset, right: sideInset, top: 0, height: 16,
            child: _Entablature(color: deepColor, g: 3.6)),
        Positioned(
            left: leftX, top: 12, bottom: 28, width: columnWidth,
            child: AegeanColumn(order: order, color: color)),
        Positioned(
            left: rightX, top: 12, bottom: 28, width: columnWidth,
            child: AegeanColumn(order: order, color: color)),
        Positioned(left: sideInset, right: sideInset, bottom: 12, height: 2,
            child: CustomPaint(painter: _HLine(deepColor))),
        Positioned(left: sideInset, right: sideInset, bottom: 0, height: 2,
            child: CustomPaint(painter: _HLine(deepColor))),
        if (child != null) Positioned.fill(child: child!),
      ]);
    });
  }
}

class _Entablature extends StatelessWidget {
  final Color color;
  final double g;
  const _Entablature({required this.color, required this.g});
  @override
  Widget build(BuildContext context) => CustomPaint(painter: _EntP(color, g));
}

class _EntP extends CustomPainter {
  final Color c;
  final double g;
  _EntP(this.c, this.g);
  @override
  void paint(Canvas canvas, Size size) {
    final p = _line(c, 1.2);
    canvas.drawLine(Offset.zero, Offset(size.width, 0), p);
    canvas.drawLine(Offset(0, size.height), Offset(size.width, size.height), p);
    final band = Rect.fromLTWH(4, 1.5, size.width - 8, size.height - 3);
    canvas.drawPath(AegeanGeometry.meander(band, g), _line(c.withValues(alpha: 0.4), 1));
  }

  @override
  bool shouldRepaint(covariant _EntP old) => old.c != c || old.g != g;
}

class _HLine extends CustomPainter {
  final Color c;
  _HLine(this.c);
  @override
  void paint(Canvas canvas, Size size) =>
      canvas.drawLine(Offset.zero, Offset(size.width, 0), _line(c, 1.2));
  @override
  bool shouldRepaint(covariant _HLine old) => old.c != c;
}

// ───────────────────────── 桂冠环（拱顶在上、开口在底，环抱标题） ─────────────────────────
class AegeanWreath extends StatelessWidget {
  final int leafPairs;
  final Color leafColor;
  final Color berryColor;
  const AegeanWreath({
    super.key,
    this.leafPairs = 4,
    required this.leafColor,
    required this.berryColor,
  });
  @override
  Widget build(BuildContext context) => CustomPaint(
      size: Size.infinite, painter: _WreathPainter(leafPairs, leafColor, berryColor));
}

class _WreathPainter extends CustomPainter {
  final int leaves;
  final Color leafColor, berryColor;
  _WreathPainter(this.leaves, this.leafColor, this.berryColor);
  @override
  void paint(Canvas canvas, Size size) {
    final c = Offset(size.width / 2, size.height / 2);
    final r = size.shortestSide / 2 * 0.92;
    canvas.drawPath(AegeanGeometry.wreathArc(c, r), _line(leafColor, 1.2));
    for (var i = 0; i < leaves; i++) {
      final t = (i + 0.5) / leaves;
      final a = (200 + 140 * t) * math.pi / 180;
      final b = c + Offset(r * math.cos(a), r * math.sin(a));
      final base = math.atan2(-math.sin(a), -math.cos(a));
      final len = r * 0.34 * (1 - 0.12 * t);
      for (final tilt in [22 * math.pi / 180, -26 * math.pi / 180]) {
        canvas.drawPath(
            AegeanGeometry.leaf(b, len, len * 0.28, base + tilt, 2, 0.08), _line(leafColor, 1.2));
      }
    }
    final berry = Paint()..color = berryColor;
    for (final deg in [200.0, 340.0]) {
      final a = deg * math.pi / 180;
      canvas.drawCircle(c + Offset(r * math.cos(a), r * math.sin(a)), 3.2, berry);
    }
  }

  @override
  bool shouldRepaint(covariant _WreathPainter old) =>
      old.leaves != leaves || old.leafColor != leafColor || old.berryColor != berryColor;
}

// ───────────────────────── 方案 A 卡框（整卡发丝 + 四角同心弧） ─────────────────────────
// radius 由宿主卡传入（好友卡 20 / 设置分组 14 / 对话框 16）；默认 20 仅兜底好友卡。
class AegeanCardFrame extends StatelessWidget {
  final Widget? child;
  final double radius;
  final double hair;
  final double arcW;
  final Color hairColor; // 发丝：goldDeep(b)，淡底可见
  final Color arcColor; // 角弧：gold(b)
  final Color? fillColor;
  const AegeanCardFrame({
    super.key,
    this.child,
    this.radius = 20,
    this.hair = 1.1,
    this.arcW = 1.7,
    required this.hairColor,
    required this.arcColor,
    this.fillColor,
  });
  @override
  Widget build(BuildContext context) => CustomPaint(
        foregroundPainter: _CardFramePainter(radius, hair, arcW, hairColor, arcColor, fillColor),
        child: child,
      );
}

class _CardFramePainter extends CustomPainter {
  final double radius, hair, arcW;
  final Color hairColor, arcColor;
  final Color? fillColor;
  _CardFramePainter(this.radius, this.hair, this.arcW, this.hairColor, this.arcColor, this.fillColor);

  // 一个角的「同心圆角弧 + 两端短挑尾」。用 addArc 沿圆角圆心走 1/4 弧（arcToPoint 在
  // 半径/方向不匹配时会退化成折线）；挑尾方向用角度判断，规避 sin(π)≈1e-16 浮点误差。
  void _angleCorner(Canvas canvas, Paint p, Offset o, double start) {
    // 仅画 1/4 同心圆角弧（收在卡内，无外伸挑尾——真机反馈挑尾突出卡片外沿）。
    const inset = 4.0;
    final ra = radius - inset;
    final rect = Rect.fromCircle(center: o, radius: ra);
    canvas.drawArc(rect, start, math.pi / 2, false, p);
  }

  @override
  void paint(Canvas canvas, Size size) {
    final rr = RRect.fromRectAndRadius(Offset.zero & size, Radius.circular(radius));
    if (fillColor != null) canvas.drawRRect(rr, Paint()..color = fillColor!);
    canvas.drawRRect(rr, _line(hairColor, hair));
    final p = _line(arcColor, arcW);
    final r = radius;
    _angleCorner(canvas, p, Offset(r, r), math.pi);
    _angleCorner(canvas, p, Offset(size.width - r, r), 3 * math.pi / 2);
    _angleCorner(canvas, p, Offset(size.width - r, size.height - r), 0);
    _angleCorner(canvas, p, Offset(r, size.height - r), math.pi / 2);
  }

  @override
  bool shouldRepaint(covariant _CardFramePainter old) =>
      old.radius != radius ||
      old.hair != hair ||
      old.arcW != arcW ||
      old.hairColor != hairColor ||
      old.arcColor != arcColor ||
      old.fillColor != fillColor;
}
