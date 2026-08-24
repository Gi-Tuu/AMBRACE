// 织网 3D P3 · 可测纯逻辑单测（2026-08-24，真机灰块修复 + 性能优化）
// 覆盖：
// - 纹理尺寸常量为 2 的幂 + 单张/池显存估算（NPOT+mipmap 采样异常是灰块根因）；
// - 分批节流策略（planWeaveTextureBatches 批划分 / kWeaveWarmBatchSize）；
// - LineSegmentsGeometry 顶点写入/分档（tierEdgePositions / bucketWeaveEdges3D 默认档数）；
// - 降级原因文案逻辑（WeaveFallbackReason → weaveFallbackReasonKey）；
// - 帧率降级暖机宽限（WeaveDegradeMonitor.warmupFrames）。
//
// 说明：卡片纹理真正上传 GPU（Texture2D.fromImage）与 LineSegmentsGeometry 的 GPU 缓冲在
// flutter 测试环境不可用（需 GPU），故本测试只覆盖上述纯逻辑/常量/分档，GPU 由真机冒烟验证。
import 'dart:typed_data';
import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
import 'package:ai_companion/screens/weave/weave_card_texture.dart';
import 'package:ai_companion/screens/weave/weave_edge_render.dart';
import 'package:ai_companion/screens/weave/weave_perf_monitor.dart';
import 'package:ai_companion/screens/weave/weave_scene_view.dart'
    show WeaveFallbackReason, weaveFallbackReasonKey;

/// 构造 n 个节点（球面坐标用 fibonacciSphere 均匀分布，与画布一致）。
List<WeaveSceneNode> _nodes(int n) {
  return [for (var i = 0; i < n; i++) _node(i + 1)];
}

WeaveSceneNode _node(int id, {String title = '标题', String summary = '摘要'}) {
  return WeaveSceneNode(
    id: id,
    characterId: id % 8,
    characterIds: [id % 8],
    title: title,
    summary: summary,
    importance: 0,
    mood: '',
    lat: 0.0,
    lon: 0.0,
  );
}

void main() {
  const size = Size(400, 400);

  group('A.1 纹理尺寸为 2 的幂 + 显存估算（灰块根因修复）', () {
    test('宽度/高度均为 2 的幂', () {
      expect(isPowerOfTwo(kWeaveCardTextureWidth), isTrue,
          reason: '宽度必须为 2 的幂（NPOT+mipmap 是灰块高概率根因）');
      expect(isPowerOfTwo(kWeaveCardTextureHeight), isTrue,
          reason: '高度必须为 2 的幂（NPOT+mipmap 是灰块高概率根因）');
    });

    test('单张/80 张显存估算符合注释（256×256 RGBA8888 ≈ 20MiB）', () {
      expect(kWeaveCardTextureBytesPerTexture(), 256 * 256 * 4);
      final perTex = kWeaveCardTextureBytesPerTexture();
      final poolBytes = perTex * kWeaveTextureMaxCache;
      // 256×256×4×80 = 20971520 B = 20 MiB
      expect(poolBytes, 20 * 1024 * 1024,
          reason: '20MiB 是紧凑排版下的显存估算（注释依据）');
    });

    test('节点数超阈值（>150）降级纯色圆点', () {
      expect(weaveShouldDegradeToDots(kWeaveTextureDegradeAbove), isFalse);
      expect(weaveShouldDegradeToDots(kWeaveTextureDegradeAbove + 1), isTrue);
    });
  });

  group('A.4 分批节流策略', () {
    test('planWeaveTextureBatches 按 batchSize 切分，末批不足时保留', () {
      expect(planWeaveTextureBatches([1, 2, 3, 4, 5], 2), [
        [1, 2],
        [3, 4],
        [5],
      ]);
      expect(planWeaveTextureBatches([1, 2, 3], kWeaveWarmBatchSize), [
        [1, 2, 3],
      ]);
      expect(planWeaveTextureBatches([], 3), isEmpty);
    });

    test('batchSize<1 时退化为每批 1 个（最保守）', () {
      expect(planWeaveTextureBatches([1, 2, 3], 0), [
        [1],
        [2],
        [3],
      ]);
    });

    test('分批总节点数守恒（不丢不漏）', () {
      final ids = [for (var i = 0; i < 11; i++) i];
      final batches = planWeaveTextureBatches(ids, kWeaveWarmBatchSize);
      final all = batches.expand((b) => b).toList();
      expect(all.length, ids.length);
      expect(all.toSet(), ids.toSet());
    });

    test('kWeaveWarmBatchSize 落在 2-3（每帧只生成 2-3 张，避免峰值）', () {
      expect(kWeaveWarmBatchSize, inInclusiveRange(2, 3));
    });
  });

  group('B.1 LineSegmentsGeometry 顶点写入/分档', () {
    const e0 = WeaveEdgeRender(
        source: 1, target: 2, ax: 1, ay: 2, az: 3, bx: 4, by: 5, bz: 6,
        alpha: 0.1);
    const e1 = WeaveEdgeRender(
        source: 2, target: 3, ax: -1, ay: -2, az: -3, bx: -4, by: -5, bz: -6,
        alpha: 0.5);
    const e2 = WeaveEdgeRender(
        source: 3, target: 4, ax: 0, ay: 1, az: 0, bx: 1, by: 0, bz: 1,
        alpha: 0.95);

    test('tierEdgePositions 把整档端点顺序写入 compact Float32List', () {
      final positions = tierEdgePositions([e0, e1]);
      // 2 条边 × 6 float = 12
      expect(positions.length, 12);
      expect(positions[0], 1);
      expect(positions[1], 2);
      expect(positions[2], 3);
      expect(positions[3], 4); // e0.bx
      expect(positions[4], 5);
      expect(positions[5], 6);
      expect(positions[6], -1); // e1.ax
      expect(positions[7], -2);
      expect(positions[8], -3);
      expect(positions[9], -4); // e1.bx
      expect(positions[10], -5);
      expect(positions[11], -6);
    });

    test('tierEdgePositions 空档返回空数组', () {
      expect(tierEdgePositions(const []), isEmpty);
    });

    test('bucketWeaveEdges3D 默认档数 = kWeaveEdgeRenderTiers（3-5 内、总量守恒）', () {
      final buckets = bucketWeaveEdges3D([e0, e1, e2]);
      expect(buckets.length, lessThanOrEqualTo(kWeaveEdgeRenderTiers));
      expect(buckets.length, greaterThanOrEqualTo(1));
      final total = buckets.fold<int>(0, (s, b) => s + b.edges.length);
      expect(total, 3, reason: '每条边都应落入某档');
      for (final b in buckets) {
        expect(b.alpha, greaterThan(0.0));
        expect(b.alpha, lessThanOrEqualTo(1.0));
      }
    });

    test('kWeaveEdgeRenderTiers 落在 3-5（个位数 draw call）', () {
      expect(kWeaveEdgeRenderTiers, inInclusiveRange(3, 5));
    });

    test('writeEdgeFloats 返回下一个写入下标（供 tierEdgePositions 串联）', () {
      final out = Float32List(12);
      var off = writeEdgeFloats(out, 0, e0);
      off = writeEdgeFloats(out, off, e1);
      expect(off, 12);
      expect(out[11], -6);
    });

    test('3D 连线组装：只保留两端均可见的边（复用 buildWeaveEdges3D）', () {
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
      final edges =
          buildWeaveEdges3D(edges: c.edges, byId: byId, sphereRadius: 2.0);
      expect(edges.length, 1);
      expect(edges.first.source, 1);
      expect(edges.first.target, 2);
    });
  });

  group('C 降级原因文案逻辑', () {
    test('WeaveFallbackReason 恰有三个取值（渲染异常/持续低帧率/节点数超限）', () {
      expect(WeaveFallbackReason.values.length, 3);
      expect(
        WeaveFallbackReason.values.map((r) => r.name),
        containsAll(['renderError', 'lowFps', 'nodesExceed']),
      );
    });

    test('weaveFallbackReasonKey 映射稳定且唯一', () {
      final keys = WeaveFallbackReason.values.map(weaveFallbackReasonKey).toSet();
      expect(keys.length, 3, reason: '每个 reason 应有唯一 key');
      expect(keys, containsAll(['renderError', 'lowFps', 'nodesExceed']));
      expect(weaveFallbackReasonKey(WeaveFallbackReason.renderError),
          'renderError');
      expect(weaveFallbackReasonKey(WeaveFallbackReason.lowFps), 'lowFps');
      expect(weaveFallbackReasonKey(WeaveFallbackReason.nodesExceed),
          'nodesExceed');
    });
  });

  group('A.5 / 帧率降级暖机宽限（WeaveDegradeMonitor.warmupFrames）', () {
    test('默认 warmupFrames=0 保持旧判定行为（前几帧不宽限）', () {
      final m = WeaveDegradeMonitor(windowMs: 500, minFps: 30);
      for (var t = 0; t <= 500; t += 100) {
        m.recordFrame(t); // 6 帧 / 0.5s = 12fps
      }
      expect(m.shouldDegrade, isTrue);
    });

    test('warmupFrames>0 时暖机帧不计入统计（瞬态低谷不误降级）', () {
      final m = WeaveDegradeMonitor(
          windowMs: 2000, minFps: 30, warmupFrames: 30);
      // 前 30 帧宽限 + 之后 21 帧/2000ms ≈ 10.5fps
      var degraded = false;
      var t = 0;
      for (var i = 0; i < 30; i++) {
        degraded = m.recordFrame(t += 100) || degraded;
      }
      expect(degraded, isFalse, reason: '暖机帧不应触发降级');
      for (var i = 0; i <= 20; i++) {
        degraded = m.recordFrame(t += 100) || degraded;
      }
      expect(degraded, isTrue, reason: '暖机结束后按真实帧率判定');
    });

    test('reset 恢复暖机宽限', () {
      final m = WeaveDegradeMonitor(
          windowMs: 500, minFps: 30, warmupFrames: 5);
      for (var i = 0; i < 5; i++) {
        expect(m.recordFrame(i * 100), isFalse); // 宽限
      }
      m.reset();
      // reset 后应重新宽限 5 帧
      for (var i = 0; i < 5; i++) {
        expect(m.recordFrame(i * 100), isFalse);
      }
    });
  });
}
