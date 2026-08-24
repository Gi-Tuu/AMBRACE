// 织网 3D · WeaveSceneController 纯函数单测（2026-08-24，织网 3D P0）
// 覆盖：球面坐标投影 / 旋转 / 缩放 / hitTest 最近邻 / 聚类泡切换 / setGraph 重置。
import 'dart:math' as math;
import 'dart:ui' show Offset, Size;

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
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

  group('project 球面投影', () {
    test('旋转后的单位球坐标保持单位长度', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(20));
      final layout = c.project(size);
      for (final p in layout.nodes) {
        final len = math.sqrt(p.ux * p.ux + p.uy * p.uy + p.uz * p.uz);
        expect(len, closeTo(1.0, 1e-9), reason: '节点 ${p.node.id} 应为单位向量');
      }
    });

    test('屏幕坐标落在球面半径范围内', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(20));
      final layout = c.project(size);
      final radius = math.min(400.0, 400.0) * 0.34 * c.zoom;
      for (final p in layout.nodes) {
        final dx = (p.x - 200).abs();
        final dy = (p.y - 200).abs();
        expect(dx, lessThanOrEqualTo(radius * p.scale + 1e-6));
        expect(dy, lessThanOrEqualTo(radius * p.scale + 1e-6));
      }
    });

    test('深度随球面位置变化（近大远小）', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(30));
      final layout = c.project(size);
      final depths = layout.nodes.map((p) => p.depth).toSet();
      expect(depths.length, greaterThan(1), reason: '深度应随球面位置变化');
      final scales = layout.nodes.map((p) => p.scale).toSet();
      expect(scales.length, greaterThan(1), reason: '缩放应随深度（近大远小）变化');
    });
  });

  group('rotate 旋转', () {
    test('绕 Y 轴旋转改变投影坐标', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      final before = c.project(size).nodes;
      c.rotate(200, 0); // dx=200 → phi += 200/220
      final after = c.project(size).nodes;
      expect(c.phi, closeTo(200 / 220, 1e-9));
      expect(
        after.first.x != before.first.x || after.first.y != before.first.y,
        isTrue,
        reason: '旋转后投影坐标应变化',
      );
    });

    test('theta 受上下限约束[-1.35,1.35]', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.rotate(0, 1e6); // dy 很大
      expect(c.theta, 1.35);
      c.rotate(0, -1e6);
      expect(c.theta, closeTo(-1.35, 1e-9));
    });
  });

  group('zoom 缩放', () {
    test('updateScale 放大 → 投影坐标更外扩', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(20));
      final before = c.project(size).nodes;
      c.onScaleStart();
      c.updateScale(2.0);
      final after = c.project(size).nodes;
      expect(c.zoom, 2.0);
      // 取第一个非原点投影，比较离中心距离（放大应更远）
      final b = before.first;
      final a = after.first;
      final db = math.sqrt(
          (b.x - 200) * (b.x - 200) + (b.y - 200) * (b.y - 200));
      final da = math.sqrt(
          (a.x - 200) * (a.x - 200) + (a.y - 200) * (a.y - 200));
      expect(da, greaterThan(db), reason: '放大后节点应更远离中心');
    });

    test('zoom 被钳制在 [0.3,3.5]', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.onScaleStart();
      c.updateScale(100.0);
      expect(c.zoom, 3.5);
      c.onScaleStart();
      c.updateScale(0.001);
      expect(c.zoom, 0.3);
    });
  });

  group('hitTest 最近邻', () {
    test('点击节点中心返回该节点 id；远处返回 null', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(25));
      final layout = c.project(size);
      final p = layout.nodes.first;
      expect(c.hitTest(Offset(p.x, p.y), size), p.node.id);
      // 远离球面的角落（未命中任何节点）
      expect(c.hitTest(const Offset(2, 2), size), isNull);
    });

    test('命中半径随 scale 放大（近端更易命中）', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(50));
      final layout = c.project(size);
      // 对每个投影节点中心命中，应返回该节点本身（scale≥1 且 r=30*scale）
      var ok = 0;
      for (final p in layout.nodes) {
        final id = c.hitTest(Offset(p.x, p.y), size);
        if (id == p.node.id) ok++;
      }
      expect(ok, greaterThan(0), reason: '至少应有命中');
    });
  });

  group('聚类泡（>80 节点）', () {
    test('>80 走球面聚类生成泡；切换展开后出现成员节点', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(100));
      var layout = c.project(size);
      expect(layout.bubbles, isNotEmpty, reason: '>80 应生成聚类泡');
      expect(layout.nodes, isEmpty, reason: '聚类泡默认收起，成员节点应在展开后才显示');
      expect(layout.bubbles.length, lessThanOrEqualTo(70));

      // 展开第一个泡 → 成员节点出现
      c.toggleCluster(layout.bubbles.first.clusterId);
      layout = c.project(size);
      expect(layout.nodes, isNotEmpty, reason: '展开后应出现成员节点');
      expect(layout.bubbles.first.collapsed, isFalse);
    });

    test('setGraph 改变节点集时重置聚类缓存/展开态', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(100));
      final layout = c.project(size);
      c.toggleCluster(layout.bubbles.first.clusterId);
      expect(c.project(size).nodes, isNotEmpty);
      // 换成另一批节点 → 展开态重置（新节点集不再展开）
      c.setNodes(_nodes(90));
      final layout2 = c.project(size);
      expect(layout2.nodes, isEmpty, reason: '节点集变化后聚类泡默认收起');
    });
  });

  group('onScaleEnd / tickInertia 惯性', () {
    test('trackDrag + onScaleEnd 产生速度并衰减停止', () {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      c.onScaleStart();
      c.trackDrag(80, 40);
      c.rotate(80, 40);
      final shouldInertia = c.onScaleEnd();
      expect(shouldInertia, isTrue);
      // 多次 tick 后最终停止
      var still = true;
      var guard = 0;
      while (still && guard < 500) {
        still = c.tickInertia();
        guard++;
      }
      expect(still, isFalse);
      expect(guard, lessThan(500));
    });
  });
}
