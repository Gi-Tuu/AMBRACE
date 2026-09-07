// aegean_geometry.dart
// 爱琴海典藏 · 公式化几何层（三端同源：App Dart / 官网 / 编辑器 JS 使用同一套数学）。
// 纯函数、无 Widget、无颜色策略：只产出 Path / 点列，便于单测与复用。
import 'dart:math' as math;
import 'package:flutter/widgets.dart';

/// 希腊三柱式
enum AegeanOrder { doric, ionic, corinthian }

class AegeanGeometry {
  AegeanGeometry._();

  // ── 回纹（Greek key）：等距网格，单位宽 4g；整体收口于 [band] 内、不贴边 ──
  static Path meander(Rect band, double g) {
    final path = Path();
    final u = 4 * g;
    final left = band.left, right = band.right;
    final yb = band.top + 0.5 + 3 * g;
    path.moveTo(left, yb);
    path.lineTo(right, yb); // 基线
    for (double x = left; x < right - 1; x += u) {
      final x0 = x + 0.5;
      if (x0 + 3 * g + 0.5 > right) break; // 末端不画出半个单元
      path
        ..moveTo(x0, yb)
        ..lineTo(x0, band.top + 0.5)
        ..lineTo(x0 + 3 * g, band.top + 0.5)
        ..lineTo(x0 + 3 * g, band.top + 0.5 + 2 * g)
        ..lineTo(x0 + g, band.top + 0.5 + 2 * g)
        ..lineTo(x0 + g, band.top + 0.5 + g)
        ..lineTo(x0 + 2 * g, band.top + 0.5 + g);
    }
    return path;
  }

  /// 柱身收分曲线：hw(t) = hwBot − Δ·t^k。返回 (半宽, y) 点列。
  static List<Offset> shaftProfile(
      double y0, double y1, double hwTop, double hwBot, double k, int n) {
    return List.generate(n, (i) {
      final t = i / (n - 1);
      return Offset(
        hwBot - (hwBot - hwTop) * math.pow(t, k).toDouble(),
        y0 + (y1 - y0) * t,
      );
    });
  }

  /// 柱头/柱基凸箍（鼓形）侧轮廓。
  static List<Offset> bandProfile(double y0, double y1, double hwMid,
      double amp, bool convex, int n) {
    return List.generate(n, (i) {
      final t = i / (n - 1);
      final kk = math.pow(2 * t - 1, 2).toDouble();
      return Offset(
        convex ? hwMid - amp * kk : hwMid - amp * (1 - kk),
        y0 + (y1 - y0) * t,
      );
    });
  }

  /// 凹槽（flute）：圆投影 u = sin θ。返回每条凹槽的路径（相对柱中心 cx）。
  static List<Path> flutes(List<Offset> profile, double cx, int count) {
    final out = <Path>[];
    for (var j = 1; j <= count; j++) {
      final u = math.sin((j / (count + 1) - 0.5) * 1.85);
      final p = Path();
      for (var i = 0; i < profile.length; i++) {
        final x = cx + u * profile[i].dx;
        final y = profile[i].dy;
        if (i == 0) {
          p.moveTo(x, y);
        } else {
          p.lineTo(x, y);
        }
      }
      out.add(p);
    }
    return out;
  }

  /// 柱身单侧轮廓（left=true 左侧）。
  static Path side(List<Offset> profile, double cx, {required bool left}) {
    final p = Path();
    for (var i = 0; i < profile.length; i++) {
      final x = cx + (left ? -profile[i].dx : profile[i].dx);
      final y = profile[i].dy;
      if (i == 0) {
        p.moveTo(x, y);
      } else {
        p.lineTo(x, y);
      }
    }
    return p;
  }

  /// 爱奥尼涡卷：对数螺线 r = r0·e^(−bθ)。
  static Path spiral(
      Offset c, double r0, double r1, double turns, double phase) {
    final b = math.log(r0 / r1) / (turns * 2 * math.pi);
    final p = Path();
    for (var i = 0; i <= 72; i++) {
      final th = (i / 72) * turns * 2 * math.pi;
      final r = r0 * math.exp(-b * th);
      final o = c +
          Offset(r * math.cos(th + phase), r * math.sin(th + phase));
      if (i == 0) {
        p.moveTo(o.dx, o.dy);
      } else {
        p.lineTo(o.dx, o.dy);
      }
    }
    return p;
  }

  /// 莨苕 / 月桂叶片（局部 y 由 0 向 −length 生长）。
  static Path leaf(Offset c, double length, double width, double angle,
      double lobes, double serr) {
    final ca = math.cos(angle), sa = math.sin(angle);
    Offset tf(double x, double y) =>
        c + Offset(x * ca - y * sa, x * sa + y * ca);
    double half(double s) {
      if (s <= 0 || s >= 1) return 0;
      return width *
          math.pow(math.sin(math.pi * math.pow(s, 0.78)), 0.85).toDouble() *
          (1 + serr * math.sin(lobes * math.pi * s));
    }

    final pts = <Offset>[tf(0, 0)];
    for (var i = 0; i <= 26; i++) {
      final s = i / 26;
      pts.add(tf(half(s), -length * s));
    }
    pts.add(tf(0, -length));
    for (var i = 26; i >= 0; i--) {
      final s = i / 26;
      pts.add(tf(-half(s), -length * s));
    }
    final p = Path()..moveTo(pts.first.dx, pts.first.dy);
    for (final o in pts.skip(1)) {
      p.lineTo(o.dx, o.dy);
    }
    return p..close();
  }

  /// 桂冠环路径：拱顶在正上方（270°），角度 200°→340°，两端对称、开口在底。
  static Path wreathArc(Offset c, double r) {
    final p = Path();
    for (var i = 0; i <= 60; i++) {
      final a = (200 + 140 * i / 60) * math.pi / 180;
      final o = c + Offset(r * math.cos(a), r * math.sin(a));
      if (i == 0) {
        p.moveTo(o.dx, o.dy);
      } else {
        p.lineTo(o.dx, o.dy);
      }
    }
    return p;
  }
}
