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

  group('pickWeaveLayoutMode 布局模式决策（织网 2.5D 加强）', () {
    test('≤80 走球面；>80 走聚类（根治 >80 拍平 2D）', () {
      expect(kWeaveDirectSphereMax, 80);
      expect(kWeaveClusterTargetMax, lessThanOrEqualTo(70));
      expect(pickWeaveLayoutMode(80), WeaveLayoutMode.sphere);
      expect(pickWeaveLayoutMode(81), WeaveLayoutMode.cluster);
      expect(pickWeaveLayoutMode(300), WeaveLayoutMode.cluster);
    });
  });

  group('normalizeDepth 深度归一化', () {
    test('近=1、远=min，单调', () {
      expect(normalizeDepth(1.0), 1.0);
      expect(normalizeDepth(-1.0), 0.12);
      expect(normalizeDepth(0.0), greaterThan(normalizeDepth(-1.0)));
      expect(normalizeDepth(0.5), lessThan(normalizeDepth(1.0)));
    });
  });

  group('clampScale 缩放钳制', () {
    test('限制在 [0.55,2.2] 内，避免极端', () {
      expect(clampScale(5.0), 2.2);
      expect(clampScale(0.1), 0.55);
      expect(clampScale(1.2), 1.2);
    });
  });

  group('nodeDepthOpacity 背面淡化', () {
    test('近=实、远=虚；z<0 显著低于正面', () {
      expect(nodeDepthOpacity(1.0), 1.0);
      expect(nodeDepthOpacity(-1.0), 0.30);
      expect(nodeDepthOpacity(-1.0), lessThan(nodeDepthOpacity(-0.3)));
      expect(nodeDepthOpacity(-0.3), lessThan(nodeDepthOpacity(0.0)));
      expect(nodeDepthOpacity(0.0), lessThan(nodeDepthOpacity(0.8)));
    });
  });

  group('edgeAlpha 连线淡化', () {
    test('两端更靠前 → 更明显；强度越高越明显', () {
      expect(edgeAlpha(1.0, 1.0, 0.5), greaterThan(edgeAlpha(0.2, 0.2, 0.5)));
      expect(edgeAlpha(0.5, 0.5, 0.0), lessThan(edgeAlpha(0.5, 0.5, 1.0)));
      expect(edgeAlpha(0.3, 0.3, 1.0), greaterThan(0));
    });
  });

  group('projectPoint 透视增强（真透视除法）', () {
    test('depth 与 depthNorm 一并返回', () {
      final p = projectPoint(0, 0, 0.5, 0, 0, 100, perspective: 0.55);
      expect(p.depth, 0.5);
      expect(p.depthNorm, greaterThan(0));
      expect(p.depthNorm, lessThan(1));
    });

    test('消失点汇聚：远端更靠近中心', () {
      final near = projectPoint(0.8, 0, 1.0, 200, 300, 100, perspective: 0.55);
      final far = projectPoint(0.8, 0, -1.0, 200, 300, 100, perspective: 0.55);
      expect((near.x - 200).abs(), greaterThan((far.x - 200).abs()));
    });

    test('近/远尺寸比明显大于旧 0.3 的 1.86 倍', () {
      final near = projectPoint(0, 0, 1.0, 0, 0, 100, perspective: 0.55);
      final far = projectPoint(0, 0, -1.0, 0, 0, 100, perspective: 0.55);
      expect(near.scale / far.scale, greaterThan(2.5));
    });
  });

  group('clusterSphereBubbles 球面聚类', () {
    test('聚类：≤target 个泡、成员不重不漏、结果确定', () {
      final pts = fibonacciSphere(100);
      final a = clusterSphereBubbles(points: pts, targetClusters: 70);
      final b = clusterSphereBubbles(points: pts, targetClusters: 70);
      expect(a.length, greaterThan(0));
      expect(a.length, lessThanOrEqualTo(70));
      final seen = <int>{};
      for (final c in a) {
        expect(c.count, greaterThan(0));
        for (final m in c.members) {
          expect(seen.add(m), isTrue);
        }
      }
      expect(seen.length, 100);
      // 确定性（同输入同输出），供画布缓存复用
      expect(a.length, b.length);
      for (var i = 0; i < a.length; i++) {
        expect(a[i].members, orderedEquals(b[i].members));
        expect(a[i].lat, closeTo(b[i].lat, 1e-9));
        expect(a[i].lon, closeTo(b[i].lon, 1e-9));
      }
    });

    test('高密度节点被压缩为聚类泡（不再拍平成 2D）', () {
      final pts = fibonacciSphere(200);
      final clusters = clusterSphereBubbles(points: pts, targetClusters: 70);
      expect(clusters.length, lessThan(200));
      expect(clusters.length, lessThanOrEqualTo(70));
    });

    test('桶数≥点数时每个点自成一组', () {
      final pts = fibonacciSphere(10);
      final clusters = clusterSphereBubbles(points: pts, targetClusters: 10);
      expect(clusters.length, 10);
      final total = clusters.fold<int>(0, (s, c) => s + c.count);
      expect(total, 10);
    });

    test('空输入返回空', () {
      expect(clusterSphereBubbles(points: const [], targetClusters: 5), isEmpty);
    });
  });
}
