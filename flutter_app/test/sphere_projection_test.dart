import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/utils/sphere_projection.dart';

void main() {
  group('fibonacciSphere', () {
    test('点数正确', () {
      expect(fibonacciSphere(0), isEmpty);
      expect(fibonacciSphere(10).length, 10);
      expect(fibonacciSphere(80).length, 80);
    });

    test('点互不相同（均匀分布）', () {
      final pts = fibonacciSphere(20);
      final seen = <String>{};
      for (final p in pts) {
        final key = '${p.lat.toStringAsFixed(6)},${p.lon.toStringAsFixed(6)}';
        expect(seen.contains(key), isFalse);
        seen.add(key);
      }
    });
  });

  group('rotatePoint 旋转闭环', () {
    test('绕 Y 轴旋转 2π 回到原点', () {
      final c = sphereToCartesian(0.3, 1.1);
      final r = rotatePoint(c.$1, c.$2, c.$3, 2 * math.pi, 0);
      expect(r.$1, closeTo(c.$1, 1e-9));
      expect(r.$2, closeTo(c.$2, 1e-9));
      expect(r.$3, closeTo(c.$3, 1e-9));
    });

    test('先转 phi 再转 -phi 回到原点', () {
      final c = sphereToCartesian(-0.2, 0.7);
      final r1 = rotatePoint(c.$1, c.$2, c.$3, 0.8, 0.4);
      // 逆序：先绕 X(-0.4) 再绕 Y(-0.8)
      final t1 = rotatePoint(r1.$1, r1.$2, r1.$3, 0, -0.4);
      final r2 = rotatePoint(t1.$1, t1.$2, t1.$3, -0.8, 0);
      expect(r2.$1, closeTo(c.$1, 1e-9));
      expect(r2.$2, closeTo(c.$2, 1e-9));
      expect(r2.$3, closeTo(c.$3, 1e-9));
    });

    test('旋转保持单位长度', () {
      final c = sphereToCartesian(0.5, 2.0);
      final r = rotatePoint(c.$1, c.$2, c.$3, 1.2, 0.9);
      final len = math.sqrt(r.$1 * r.$1 + r.$2 * r.$2 + r.$3 * r.$3);
      expect(len, closeTo(1.0, 1e-9));
    });
  });

  group('projectPoint 透视投影', () {
    test('正面点在球面半径内', () {
      final p = projectPoint(1.0, 0.0, 0.0, 200, 300, 100);
      expect(p.x, greaterThan(100));
      expect(p.y, closeTo(300, 1e-6));
    });

    test('近大远小：z=1 缩放大于 z=-1', () {
      final near = projectPoint(0, 0, 1.0, 0, 0, 100);
      final far = projectPoint(0, 0, -1.0, 0, 0, 100);
      expect(near.scale, greaterThan(far.scale));
    });

    test('屏幕坐标不越出半径范围（scale 有界）', () {
      final p = projectPoint(0.5, -0.5, 0.8, 200, 300, 100);
      final dx = (p.x - 200).abs();
      final dy = (p.y - 300).abs();
      expect(dx, lessThanOrEqualTo(100 * p.scale + 1e-6));
      expect(dy, lessThanOrEqualTo(100 * p.scale + 1e-6));
    });
  });

  group('jitterOffset 抖动', () {
    test('幅度有界', () {
      for (var t = 0.0; t < 10.0; t += 0.37) {
        final v = jitterOffset(t, 1.0, amplitude: 2.2);
        expect(v.abs(), lessThanOrEqualTo(2.2 + 1e-9));
      }
    });

    test('同相位随时间连续变化', () {
      final a = jitterOffset(1.0, 0.5);
      final b = jitterOffset(1.01, 0.5);
      expect((b - a).abs(), lessThan(0.5));
    });
  });
}
