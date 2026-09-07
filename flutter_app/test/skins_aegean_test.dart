import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/theme/skins/aegean/aegean_geometry.dart';
import 'package:ai_companion/theme/skins/aegean/aegean_architects.dart';

const gold = Color(0xFFA9823F);
const deep = Color(0xFF836428);
const terra = Color(0xFFB0543A);
const red = Color(0xFFFF0000);

Widget _frame(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(width: 120, height: 120, child: child),
    );

/// 取出组件内部 CustomPaint 的 painter（不暴露私有 Painter 类也能断言 shouldRepaint）。
Future<CustomPainter> _painterOf(WidgetTester t, Widget w) async {
  await t.pumpWidget(_frame(w));
  final cp = t.widget<CustomPaint>(find.byType(CustomPaint).first);
  return (cp.painter ?? cp.foregroundPainter)!;
}

void main() {
  group('AegeanGeometry 纯公式', () {
    test('meander 收口于 band 内、不画出半个单元', () {
      for (final w in [320.0, 390.0, 412.0]) {
        final p = AegeanGeometry.meander(Rect.fromLTWH(18, 0, w - 36, 14), 4.5);
        double maxX = 0, minX = double.infinity;
        for (final m in p.computeMetrics()) {
          for (double tt = 0; tt <= 1; tt += 0.02) {
            final o = m.getTangentForOffset(m.length * tt)?.position;
            if (o != null) {
              maxX = math.max(maxX, o.dx);
              minX = math.min(minX, o.dx);
            }
          }
        }
        expect(maxX <= w - 18 + 0.5, true, reason: 'w=$w maxX=$maxX');
        expect(minX >= 18 - 0.5, true, reason: 'w=$w minX=$minX');
      }
    });

    test('柱身左右轮廓关于 cx 严格镜像', () {
      final prof = AegeanGeometry.shaftProfile(0, 100, 16, 20, 1.75, 24);
      final lm = AegeanGeometry.side(prof, 50, left: true).computeMetrics().first;
      final rm = AegeanGeometry.side(prof, 50, left: false).computeMetrics().first;
      for (double tt = 0; tt <= 1; tt += 0.05) {
        final a = lm.getTangentForOffset(lm.length * tt)!.position;
        final b = rm.getTangentForOffset(rm.length * tt)!.position;
        // 1e-4 = 亚像素（computeMetrics 弧长量化约 5e-6，视觉严格对称）
        expect((a.dx + b.dx - 100).abs(), lessThan(1e-4));
        expect((a.dy - b.dy).abs(), lessThan(1e-4));
      }
    });

    test('wreathArc 拱顶居中在上、两端对称、开口在底', () {
      const c = Offset(100, 100);
      final arc = AegeanGeometry.wreathArc(c, 80);
      final m = arc.computeMetrics().first;
      final s = m.getTangentForOffset(0)!.position;
      final e = m.getTangentForOffset(m.length)!.position;
      double minY = double.infinity, minX = 0;
      for (double tt = 0; tt <= 1; tt += 0.01) {
        final o = m.getTangentForOffset(m.length * tt)!.position;
        if (o.dy < minY) { minY = o.dy; minX = o.dx; }
      }
      expect((minX - 100).abs(), lessThan(1e-4)); // 拱顶在中轴正上方
      expect(s.dy > minY && e.dy > minY, true);  // 两端在拱顶下方 → 开口在底
      expect((s.dx + e.dx - 200).abs(), lessThan(1e-4));
    });
  });

  group('shouldRepaint 颜色/参数变化必须重绘（Codex 1.4）', () {
    testWidgets('Wreath：同 leafPairs 不同叶色 -> 重绘', (t) async {
      final a = await _painterOf(
          t, const AegeanWreath(leafPairs: 4, leafColor: gold, berryColor: terra));
      final b = await _painterOf(
          t, const AegeanWreath(leafPairs: 4, leafColor: red, berryColor: terra));
      expect(a.shouldRepaint(b), isTrue);
      final same = await _painterOf(
          t, const AegeanWreath(leafPairs: 4, leafColor: gold, berryColor: terra));
      expect(a.shouldRepaint(same), isFalse);
    });

    testWidgets('Column：同 order 不同颜色 -> 重绘', (t) async {
      final a = await _painterOf(
          t, const AegeanColumn(order: AegeanOrder.ionic, color: gold));
      final b = await _painterOf(
          t, const AegeanColumn(order: AegeanOrder.ionic, color: red));
      expect(a.shouldRepaint(b), isTrue);
    });

    testWidgets('CardFrame：不同 radius/颜色 -> 重绘', (t) async {
      final a = await _painterOf(
          t, const AegeanCardFrame(hairColor: deep, arcColor: gold, radius: 20));
      final b = await _painterOf(
          t, const AegeanCardFrame(hairColor: deep, arcColor: gold, radius: 14));
      expect(a.shouldRepaint(b), isTrue);
      final c2 = await _painterOf(
          t, const AegeanCardFrame(hairColor: red, arcColor: gold, radius: 20));
      expect(a.shouldRepaint(c2), isTrue);
    });

    testWidgets('MeanderBand：不同透明度/颜色 -> 重绘', (t) async {
      final a = await _painterOf(t, AegeanMeanderBand(color: gold, opacity: 0.34));
      final b = await _painterOf(t, AegeanMeanderBand(color: gold, opacity: 0.6));
      expect(a.shouldRepaint(b), isTrue);
    });
  });
}
