// 织网 3D · 连线数据组装（纯逻辑，可单测）
//
// 2026-08-24（织网 3D P2）：3D 视图此前没有渲染连线（edges 只在 2.5D WeaveCanvasPainter 里画）。
// 本文件把「节点投影（深度/单位球坐标）+ 边（strength）」算成每条 3D 连线的世界坐标端点 + 透明度。
//
// 2026-08-24（织网 3D P3，性能优化）：3D 视图改用 flutter_scene 的 LineSegmentsGeometry 批量渲染连线
// （取代逐边细长圆柱 SceneMesh——N 条边 = N 个 draw call，对低端机太重）。顶点/透明度的纯计算仍在本文件：
// - buildWeaveEdges3D：每条边两端世界坐标端点 + 透明度（depth×strength 衰减、高密度弱边衰减）；
// - bucketWeaveEdges3D：按透明度量化分档（每档一份 LineSegmentsGeometry + 一份 UnlitMaterial，
//   透明度=档位代表值），draw calls 从 N 降到 [kWeaveEdgeRenderTiers]（个位数）；
// - tierEdgePositions：把某档所有边的端点写入一个紧凑 Float32List，直接作为
//   `LineSegmentData(positions: ...)` 供 LineSegmentsGeometry 构造。
//
// 纯函数、不依赖 flutter_scene / GPU，可在 flutter 测试环境直接单测。
import 'dart:typed_data';

import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
import 'package:ai_companion/utils/sphere_projection.dart';

/// 一条 3D 连线（纯数据）：两端节点 id、两端世界坐标（单位矢量已乘球半径）+ 透明度。
class WeaveEdgeRender {
  const WeaveEdgeRender({
    required this.source,
    required this.target,
    required this.ax,
    required this.ay,
    required this.az,
    required this.bx,
    required this.by,
    required this.bz,
    required this.alpha,
  });

  final int source;
  final int target;
  final double ax, ay, az, bx, by, bz;
  final double alpha;

  /// 线段端点数量（每条边 2 个端点 × 3 个 float）。
  static const int kFloatsPerEdge = 6;
}

/// 3D 连线批量渲染的透明度分档数（每档一份 LineSegmentsGeometry + 一份 UnlitMaterial）。
/// 2026-08-24（织网 3D P3）：取 4 档（3-5 档区间内偏低，更省 draw call；对 0.14~0.5 的小范围
/// 透明度量化为 4 档，视觉误差 ≤1/4，足够接近连续衰减）。
const int kWeaveEdgeRenderTiers = 4;

/// 计算 3D 连线（纯函数）。
///
/// 只保留两端均可见（在 [byId] 中）的边；透明度参照 2.5D [edgeAlpha] 语义但整体提亮
/// [brighten]（见 [weaveEdgeAlpha3D]）；高密度（edges.length > kWeaveEdgeHighDensityMax）
/// 时弱边（strength < kWeaveEdgeWeakStrength）额外衰减 0.35 以控噪。
///
/// [byId] 应来自同一帧的 `project(size)`（depthNorm 用于深度衰减、ux/uy/uz 是旋转后单位球坐标）。
List<WeaveEdgeRender> buildWeaveEdges3D({
  required List<WeaveSceneEdge> edges,
  required Map<int, WeaveNodeProjection> byId,
  required double sphereRadius,
  double brighten = kWeaveEdge3DBrighten,
}) {
  final out = <WeaveEdgeRender>[];
  if (edges.isEmpty || byId.isEmpty) return out;
  final highDensity = edges.length > kWeaveEdgeHighDensityMax;
  for (final e in edges) {
    final a = byId[e.source];
    final b = byId[e.target];
    if (a == null || b == null) continue;
    final alpha = weaveEdgeAlpha3D(
      a.depthNorm,
      b.depthNorm,
      e.strength,
      highDensity: highDensity,
      brighten: brighten,
    );
    if (alpha <= 0.01) continue;
    out.add(WeaveEdgeRender(
      source: e.source,
      target: e.target,
      ax: a.ux * sphereRadius,
      ay: a.uy * sphereRadius,
      az: a.uz * sphereRadius,
      bx: b.ux * sphereRadius,
      by: b.uy * sphereRadius,
      bz: b.uz * sphereRadius,
      alpha: alpha,
    ));
  }
  return out;
}

/// 把 3D 连线按透明度量化成 [tiers] 个档位的线段（纯函数）。
///
/// 返回 `List<({double alpha, List<WeaveEdgeRender> edges})>`，每档用一份
/// `LineSegmentsGeometry` 批量渲染（档内共用同一材质透明度，避免逐边材质/几何体的内存与绘制开销）。
/// 用「档位中心透明度」作为该档材质透明度，量化误差 ≤ 1/tiers，视觉上接近连续衰减。
List<({double alpha, List<WeaveEdgeRender> edges})> bucketWeaveEdges3D(
  List<WeaveEdgeRender> edges, {
  int tiers = kWeaveEdgeRenderTiers,
}) {
  final buckets = <int, List<WeaveEdgeRender>>{};
  for (final e in edges) {
    final idx = (e.alpha * tiers).floor().clamp(0, tiers - 1);
    (buckets[idx] ??= []).add(e);
  }
  final result = <({double alpha, List<WeaveEdgeRender> edges})>[];
  for (var i = 0; i < tiers; i++) {
    final list = buckets[i];
    if (list == null || list.isEmpty) continue;
    // 档位中心透明度；仅 0 档用其下界，避免全透明。
    final center = (i + 0.5) / tiers;
    result.add((alpha: center, edges: list));
  }
  return result;
}

/// 把一档 [edges] 的所有端点写入一个紧凑 `Float32List`（长度 = edges.length×6），
/// 直接作为 `LineSegmentData(positions: ...)` 交给 [LineSegmentsGeometry]。
/// 纯函数，可单测。
Float32List tierEdgePositions(List<WeaveEdgeRender> edges) {
  final out = Float32List(edges.length * WeaveEdgeRender.kFloatsPerEdge);
  var offset = 0;
  for (final e in edges) {
    offset = writeEdgeFloats(out, offset, e);
  }
  return out;
}

/// 便捷：把一条 [WeaveEdgeRender] 的端点填入 Float32List（6 个 float/条），返回写入的下标。
/// 供 LineSegmentsGeometry 的 `LineSegmentData(positions: ...)` 使用。
int writeEdgeFloats(Float32List out, int offset, WeaveEdgeRender e) {
  out[offset] = e.ax;
  out[offset + 1] = e.ay;
  out[offset + 2] = e.az;
  out[offset + 3] = e.bx;
  out[offset + 4] = e.by;
  out[offset + 5] = e.bz;
  return offset + 6;
}
