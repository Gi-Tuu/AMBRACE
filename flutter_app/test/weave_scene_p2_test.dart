// 织网 3D P2 · 可测纯逻辑单测（2026-08-24）
// 覆盖：theta 放开边界/惯性（3D 放开、2.5D 保留 ±1.35）；新 edgeAlpha 公式（提亮）；
// weaveEdgeAlpha3D（整体提亮 + 高密度弱边衰减 + 钳制）；低端机降级判定（WeaveDegradeMonitor）；
// 3D 连线组装/分档（buildWeaveEdges3D / bucketWeaveEdges3D / writeEdgeFloats）；
// weave_3d 默认开（FeatureFlagService 兜底）。
import 'dart:typed_data';
import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/weave/weave_scene_controller.dart';
import 'package:ai_companion/features/weave/weave_card_texture.dart'
    show weaveShouldDegradeToDots;
import 'package:ai_companion/features/weave/weave_edge_render.dart';
import 'package:ai_companion/features/weave/weave_perf_monitor.dart';
import 'package:ai_companion/services/feature_flag_service.dart';
import 'package:ai_companion/utils/sphere_projection.dart';

/// 构造 n 个节点：球面坐标用 fibonacciSphere 均匀分布（与画布一致）。
List<WeaveSceneNode> _nodes(int n) {
  final pts = fibonacciSphere(n);
  return [
    for (var i = 0; i < n; i++)
      WeaveSceneNode(
        id: i + 1,
        characterId: i % 8,
        characterIds: [i % 8],
        characterName: '角色${i % 8}',
        title: '节点${i + 1}',
        summary: '摘要${i + 1}',
        importance: (i % 100).toDouble(),
        mood: '',
        createdAt: null,
        lat: pts[i].lat,
        lon: pts[i].lon,
        lifeType: '',
        hotTags: const [],
      ),
  ];
}

void main() {
  const size = Size(400, 400);

  group('theta 限幅与放开（2.5D 与 3D 视图均放开）', () {
    test('控制器默认限幅兜底为 ±1.35（视图未显式放开前）', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.rotate(0, 1e6);
      expect(c.theta, 1.35);
      c.rotate(0, -1e6);
      expect(c.theta, closeTo(-1.35, 1e-9));
    });

    test('放开（2.5D/3D 视图均调用 setThetaLimit(null)）theta 可越过 ±77°（1.35 rad）自由旋转', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.setThetaLimit(null);
      c.rotate(0, 220.0); // 每次 +1 rad
      c.rotate(0, 220.0);
      c.rotate(0, 220.0); // theta ≈ 0.35 + 3 = 3.35
      expect(c.theta, greaterThan(1.35), reason: '放开后应能翻到球背面（越过 ±77°）');
    });

    test('放开后惯性按放开范围衰减（不被钳制回 1.35）', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.setThetaLimit(null);
      c.onScaleStart();
      c.trackDrag(0, 220.0 * 0.3); // lastDy=66 → vTheta = 66/220*8 = 2.4
      final shouldInertia = c.onScaleEnd();
      expect(shouldInertia, isTrue);
      var still = true;
      var guard = 0;
      var peak = c.theta;
      while (still && guard < 200) {
        still = c.tickInertia();
        if (c.theta > peak) peak = c.theta;
        guard++;
      }
      expect(still, isFalse, reason: '惯性最终应衰减停止');
      expect(peak, greaterThan(1.35),
          reason: '放开后惯性应把 theta 推到 ±1.35 之外');
    });

    test('需要时 setThetaLimit 可重新限幅（兼容受限视图/关闭放开）', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.setThetaLimit(null);
      c.rotate(0, 220.0 * 3.0); // theta ≈ 3.35
      expect(c.theta, greaterThan(1.35));
      c.setThetaLimit(1.35);
      expect(c.theta, 1.35);
    });
  });

  group('edgeAlpha 提亮公式', () {
    test('下限 0.14、strength 权重 0.36', () {
      // 两端都近、strength 0 → 贴下限
      expect(edgeAlpha(1.0, 1.0, 0.0), closeTo(0.14, 1e-9));
      // strength 1 → 0.14 + 0.36 = 0.50
      expect(edgeAlpha(1.0, 1.0, 1.0), closeTo(0.50, 1e-9));
      // closeness 取两端归一化深度的较小值
      expect(edgeAlpha(0.4, 0.2, 0.5),
          closeTo(edgeAlpha(0.2, 0.2, 0.5), 1e-9));
      // 单调：strength 越大 alpha 越大
      expect(edgeAlpha(0.5, 0.5, 0.0), lessThan(edgeAlpha(0.5, 0.5, 1.0)));
    });
  });

  group('weaveEdgeAlpha3D（整体提亮 + 高密度弱边衰减）', () {
    test('非高密度时比 edgeAlpha 亮一档（brighten=1.15）', () {
      final base = edgeAlpha(1.0, 1.0, 0.5);
      final alpha =
          weaveEdgeAlpha3D(1.0, 1.0, 0.5, highDensity: false);
      expect(alpha, closeTo((base * kWeaveEdge3DBrighten), 1e-9));
      expect(alpha, greaterThan(base), reason: '3D 应整体提亮');
    });

    test('高密度下弱边（strength<0.5）额外衰减 0.35', () {
      final weak =
          weaveEdgeAlpha3D(1.0, 1.0, 0.4, highDensity: true);
      expect(
        weak,
        closeTo(
          (edgeAlpha(1.0, 1.0, 0.4) * kWeaveEdge3DBrighten *
                  kWeaveEdgeWeakFactor)
              .clamp(0.0, 1.0),
          1e-9,
        ),
      );
      // 强边（strength>=0.5）不衰减
      final strong =
          weaveEdgeAlpha3D(1.0, 1.0, 0.5, highDensity: true);
      expect(strong,
          closeTo((edgeAlpha(1.0, 1.0, 0.5) * kWeaveEdge3DBrighten), 1e-9));
    });

    test('alpha 钳制在 [0,1]', () {
      expect(
        weaveEdgeAlpha3D(1.0, 1.0, 1.0, highDensity: false),
        lessThanOrEqualTo(1.0),
      );
      expect(
        weaveEdgeAlpha3D(1.0, 1.0, 1.0, highDensity: false),
        greaterThan(0.0),
      );
    });
  });

  group('WeaveDegradeMonitor 低端机降级判定', () {
    test('窗口内平均帧率低于阈值 → 判定降级', () {
      final m = WeaveDegradeMonitor(windowMs: 2000, minFps: 30);
      var degraded = false;
      // 21 帧 / 2000ms ≈ 10.5fps < 30
      for (var t = 0; t <= 2000; t += 100) {
        degraded = m.recordFrame(t) || degraded;
      }
      expect(degraded, isTrue);
      expect(m.shouldDegrade, isTrue);
    });

    test('窗口内帧率足够则不降级，并重置窗口继续监测', () {
      final m = WeaveDegradeMonitor(windowMs: 2000, minFps: 30);
      // 201 帧 / 2000ms ≈ 100.5fps
      for (var t = 0; t <= 2000; t += 10) {
        expect(m.recordFrame(t), isFalse);
      }
      expect(m.shouldDegrade, isFalse);
    });

    test('一次性降级后保持（不抖动），reset 可复位', () {
      final m = WeaveDegradeMonitor(windowMs: 500, minFps: 30);
      for (var t = 0; t <= 500; t += 100) {
        m.recordFrame(t); // 6 帧 / 0.5s = 12fps
      }
      expect(m.shouldDegrade, isTrue);
      expect(m.recordFrame(600), isTrue, reason: '降级后应一直返回 true');
      m.reset();
      expect(m.shouldDegrade, isFalse);
    });

    test('isLowFps 边界：等于阈值不算低', () {
      expect(WeaveDegradeMonitor.isLowFps(30.0), isFalse);
      expect(WeaveDegradeMonitor.isLowFps(29.99), isTrue);
    });
  });

  group('3D 连线组装（buildWeaveEdges3D / bucketWeaveEdges3D / writeEdgeFloats）', () {
    test('只保留两端均可见的边，并按深度/strength 计算透明度', () {
      final c = WeaveSceneController()
        ..setGraph(
          nodes: _nodes(3),
          edges: const [
            WeaveSceneEdge(source: 1, target: 2, strength: 0.8),
            WeaveSceneEdge(source: 1, target: 99, strength: 0.5), // 99 不可见
          ],
        );
      final layout = c.project(size);
      final byId = {for (final l in layout.nodes) l.node.id: l};
      final edges = buildWeaveEdges3D(
        edges: c.edges,
        byId: byId,
        sphereRadius: 2.0,
      );
      expect(edges.length, 1, reason: '不可见节点（99）的边应丢弃');
      expect(edges.first.source, 1);
      expect(edges.first.target, 2);
      expect(edges.first.alpha, greaterThan(0.0));
    });

    test('bucketWeaveEdges3D 把连线按透明度分档（档数受限、总数不变）', () {
      final edges = [
        const WeaveEdgeRender(
            source: 1, target: 2, ax: 0, ay: 0, az: 1, bx: 0, by: 0, bz: -1,
            alpha: 0.05),
        const WeaveEdgeRender(
            source: 2, target: 3, ax: 1, ay: 0, az: 0, bx: 0, by: 1, bz: 0,
            alpha: 0.5),
        const WeaveEdgeRender(
            source: 3, target: 4, ax: 0, ay: 1, az: 0, bx: 0, by: 0, bz: 1,
            alpha: 0.95),
      ];
      final buckets = bucketWeaveEdges3D(edges, tiers: 10);
      expect(buckets, isNotEmpty);
      expect(buckets.length, lessThanOrEqualTo(10));
      final total = buckets.fold<int>(0, (s, b) => s + b.edges.length);
      expect(total, 3, reason: '每条边都应落入某档');
      for (final b in buckets) {
        expect(b.alpha, greaterThan(0.0));
        expect(b.alpha, lessThanOrEqualTo(1.0));
      }
    });

    test('writeEdgeFloats 写入两端 6 个顶点分量', () {
      final out = Float32List(6);
      const e = WeaveEdgeRender(
          source: 1, target: 2, ax: 1, ay: 2, az: 3, bx: 4, by: 5, bz: 6,
          alpha: 0.5);
      final next = writeEdgeFloats(out, 0, e);
      expect(next, 6);
      expect(out[0], 1);
      expect(out[1], 2);
      expect(out[2], 3);
      expect(out[3], 4);
      expect(out[4], 5);
      expect(out[5], 6);
    });
  });

  group('织网 3D 默认开（客户端兜底）', () {
    test('FeatureFlagService 未加载时 weave_3d 默认 true', () {
      // 默认由 `_knownDefaults['weave_3d']=true` 兜底（与后端 AGENT_FLAGS 一致）。
      expect(FeatureFlagService.instance.isEnabled('weave_3d'), isTrue);
    });
  });

  group('性能分级阈值为纯逻辑', () {
    test('weaveShouldDegradeToDots 阈值 150（与 2.5D 一致）', () {
      expect(weaveShouldDegradeToDots(150), isFalse);
      expect(weaveShouldDegradeToDots(151), isTrue);
      expect(kWeaveDirectSphereMax, 80);
    });
  });
}
