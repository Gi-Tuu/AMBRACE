// 织库画布 2.5D 球面投影纯函数（Phase B，2026-08-12）
// 布局：斐波那契球面均匀布点 → 绕 Y/X 轴旋转 → 透视投影（近大远小）
//
// 织网 2.5D 立体感加强（2026-08-23）：本次在保留原 API 的前提下强化三维提示
//   - 真透视除法（消失点汇聚），让近/远尺寸比更明显
//   - 深度归一化（z∈[-1,1] → [0,1]），供卡片缩放/字号/透明度单调变化
//   - 背面淡化透明度曲线（z<0 大幅降透明）
//   - 球面聚类（Lloyd k-means on sphere），高密度下仍呈「一颗立体的球」
// 全部为纯函数，不依赖 Flutter，画布与测试共用。
import 'dart:math' as math;

class SpherePoint {
  final double lat; // 纬度（弧度）
  final double lon; // 经度（弧度）
  const SpherePoint(this.lat, this.lon);
}

/// 布局模式（织网 2.5D 加强）：
enum WeaveLayoutMode {
  /// 节点数 ≤ 直接球面上限：逐个球面投影
  sphere,

  /// 节点数多：按球面距离聚成「聚类泡」
  cluster,

  /// 聚类失败的兜底：原有的 2D 螺旋
  spiral,
}

/// 直接球面投影的节点数上限；超过才进入球面聚类（此前 >80 直接降级 2D 螺旋）。
const int kWeaveDirectSphereMax = 80;

/// 聚类泡目标数量上限（高密度下仍呈「一颗立体的球」，而非拍平成 2D）。
const int kWeaveClusterTargetMax = 70;

/// 判断当前节点数应使用哪种布局模式（供画布与测试共用）。
WeaveLayoutMode pickWeaveLayoutMode(int nodeCount) {
  if (nodeCount <= kWeaveDirectSphereMax) return WeaveLayoutMode.sphere;
  // 高密度仍走球面（聚类）；仅在聚类结果为空/失败时由画布兜底为 spiral。
  return WeaveLayoutMode.cluster;
}

/// 斐波那契球面均匀分布 n 个点（黄金角螺旋）
List<SpherePoint> fibonacciSphere(int n) {
  if (n <= 0) return const [];
  final ga = math.pi * (3.0 - math.sqrt(5.0)); // 黄金角 ≈ 2.39996
  return [
    for (var i = 0; i < n; i++)
      () {
        final y = 1.0 - 2.0 * (i + 0.5) / n;
        final theta = ga * i;
        return SpherePoint(math.asin(y), theta);
      }(),
  ];
}

/// 球面坐标 → 单位球 3D 坐标
(double x, double y, double z) sphereToCartesian(double lat, double lon) {
  final cosLat = math.cos(lat);
  return (cosLat * math.cos(lon), math.sin(lat), cosLat * math.sin(lon));
}

/// 绕 Y 轴旋转 phi、绕 X 轴旋转 theta（弧度）
(double x, double y, double z) rotatePoint(
  double x,
  double y,
  double z,
  double phi,
  double theta,
) {
  final cosPhi = math.cos(phi);
  final sinPhi = math.sin(phi);
  final x1 = x * cosPhi + z * sinPhi;
  final z1 = -x * sinPhi + z * cosPhi;
  final cosTheta = math.cos(theta);
  final sinTheta = math.sin(theta);
  final y2 = y * cosTheta - z1 * sinTheta;
  final z2 = y * sinTheta + z1 * cosTheta;
  return (x1, y2, z2);
}

/// 深度归一化：z∈[-1,1] → [0,1]（近=1、远=0），带上下限避免极端贴边。
/// 供卡片缩放/字号/透明度随深度单调变化（近大实亮、远小虚淡）。
double normalizeDepth(double z, {double min = 0.12, double max = 1.0}) {
  final t = ((z + 1.0) / 2.0).clamp(0.0, 1.0);
  return min + (max - min) * t;
}

/// 缩放钳制：避免近端卡片过大 / 远端过小（配合真透视除法，防止极端）。
double clampScale(double scale, {double min = 0.55, double max = 2.2}) {
  return scale.clamp(min, max);
}

/// 节点透明度随深度变化的曲线（近=实、远=虚；z<0 后半球显著淡化为剪影）。
/// 返回 [0,1] 的乘数，用于与基础 alpha 相乘。
/// 前半球 z∈[0,1]：frontFar→frontNear 微增，保持「实、亮」；
/// 后半球 z∈[-1,0]：快速衰减到 backFar，避免正/背同清晰度叠加。
double nodeDepthOpacity(
  double z, {
  double frontNear = 1.0,
  double frontFar = 0.72,
  double backFar = 0.30,
}) {
  if (z >= 0) {
    return frontFar + (frontNear - frontFar) * z;
  }
  final t = (z + 1.0); // z∈[-1,0] → t∈[0,1]
  return backFar + (frontFar - backFar) * t;
}

/// 连线透明度：取两端归一化深度的较小值作为衰减因子，减少高 N 时杂乱。
/// closeness ∈ [0,1]（两端都靠前则线更明显）。
double edgeAlpha(double aDepthNorm, double bDepthNorm, double strength) {
  final closeness = math.min(aDepthNorm, bDepthNorm);
  final base = 0.06 + 0.32 * strength;
  return base * closeness;
}

/// 透视投影（真透视除法 + 消失点汇聚）：
/// 以 w = 1 - perspective·z 为景深因子，near(z>0) 放大、far(z<0) 收缩并向中心汇聚。
/// 返回屏幕坐标、缩放、深度 z 与归一化深度。
({double x, double y, double scale, double depth, double depthNorm})
    projectPoint(
  double x,
  double y,
  double z,
  double cx,
  double cy,
  double radius, {
  double perspective = 0.5,
}) {
  final w = 1.0 - perspective * z;
  final safe = math.max(w, 0.15); // 防止 far 端 w 过小导致放大越界
  final scale = 1.0 / safe;
  return (
    x: cx + x * radius * scale,
    y: cy + y * radius * scale,
    scale: scale,
    depth: z,
    depthNorm: normalizeDepth(z),
  );
}

/// 便捷函数：直接把单位球坐标（lat/lon）做「旋转 + 透视」投影到屏幕。
({double x, double y, double scale, double depth, double depthNorm})
    projectSphere(
  double lat,
  double lon, {
  required double phi,
  required double theta,
  required double cx,
  required double cy,
  required double radius,
  double perspective = 0.5,
}) {
  final c = sphereToCartesian(lat, lon);
  final r = rotatePoint(c.$1, c.$2, c.$3, phi, theta);
  return projectPoint(r.$1, r.$2, r.$3, cx, cy, radius,
      perspective: perspective);
}

/// 一个「聚类泡」（含质心球面坐标、成员索引、成员数）。
class SphereCluster {
  final double lat;
  final double lon;
  final List<int> members;
  const SphereCluster({
    required this.lat,
    required this.lon,
    required this.members,
  });
  int get count => members.length;
}

/// 球面 k-means（Lloyd）聚类：把 points 聚成 ≤targetClusters 个聚类泡。
/// - 种子：斐波那契球面均匀取 targetClusters 个点（确定性，可复现）；
/// - 距离：球面角距（单位向量点积的余弦距离）；
/// - 空簇用「离现有质心最远的采样点」重置（确定性）；
/// - 返回按成员数降序、再按质心经度升序排序的稳定结果。
List<SphereCluster> clusterSphereBubbles({
  required List<SpherePoint> points,
  required int targetClusters,
  int maxIterations = 12,
}) {
  final n = points.length;
  if (n == 0) return const [];
  final k = targetClusters < 1 ? 1 : (targetClusters > n ? n : targetClusters);
  if (k >= n) {
    // 桶数≥点数：每个点自成一组（无需合并）
    return [
      for (var i = 0; i < n; i++)
        SphereCluster(lat: points[i].lat, lon: points[i].lon, members: [i]),
    ];
  }

  final carts = [for (final p in points) sphereToCartesian(p.lat, p.lon)];
  final seeds = fibonacciSphere(k);
  var centroids = [for (final s in seeds) sphereToCartesian(s.lat, s.lon)];

  List<List<int>> assignment = List.generate(k, (_) => <int>[]);
  for (var it = 0; it < maxIterations; it++) {
    assignment = List.generate(k, (_) => <int>[]);
    for (var i = 0; i < n; i++) {
      var best = 0;
      var bestDot = -2.0;
      for (var c = 0; c < k; c++) {
        final d = _dot(carts[i], centroids[c]);
        if (d > bestDot) {
          bestDot = d;
          best = c;
        }
      }
      assignment[best].add(i);
    }

    var moved = false;
    for (var c = 0; c < k; c++) {
      final mem = assignment[c];
      if (mem.isEmpty) {
        centroids[c] = _farthestPoint(carts, centroids);
        moved = true;
        continue;
      }
      var sx = 0.0, sy = 0.0, sz = 0.0;
      for (final i in mem) {
        final p = carts[i];
        sx += p.$1;
        sy += p.$2;
        sz += p.$3;
      }
      final len = math.sqrt(sx * sx + sy * sy + sz * sz);
      if (len < 1e-12) continue;
      final nx = sx / len, ny = sy / len, nz = sz / len;
      if (_dist2((nx, ny, nz), centroids[c]) > 1e-12) moved = true;
      centroids[c] = (nx, ny, nz);
    }
    if (!moved) break;
  }

  final result = <SphereCluster>[];
  for (var c = 0; c < k; c++) {
    final mem = assignment[c];
    if (mem.isEmpty) continue;
    final cen = centroids[c];
    final lat = math.asin(cen.$2);
    final lon = math.atan2(cen.$3, cen.$1);
    result.add(SphereCluster(lat: lat, lon: lon, members: mem));
  }

  result.sort((a, b) {
    final byCount = b.count.compareTo(a.count);
    if (byCount != 0) return byCount;
    return a.lon.compareTo(b.lon);
  });
  return result;
}

double _dot((double, double, double) a, (double, double, double) b) =>
    a.$1 * b.$1 + a.$2 * b.$2 + a.$3 * b.$3;

double _dist2((double, double, double) a, (double, double, double) b) {
  final dx = a.$1 - b.$1, dy = a.$2 - b.$2, dz = a.$3 - b.$3;
  return dx * dx + dy * dy + dz * dz;
}

/// 选择离所有质心最远的一个采样点（用于确定性重置空簇）。
(double, double, double) _farthestPoint(
  List<(double, double, double)> pts,
  List<(double, double, double)> centroids,
) {
  var best = pts.first;
  var bestMin = -1.0;
  for (final p in pts) {
    var minD = double.infinity;
    for (final c in centroids) {
      final d = _dist2(p, c);
      if (d < minD) minD = d;
    }
    if (minD > bestMin) {
      bestMin = minD;
      best = p;
    }
  }
  return best;
}

/// 抖动位移：按时间与相位返回当前帧偏移（px），幅度有界
double jitterOffset(
  double timeSeconds,
  double phase, {
  double amplitude = 2.2,
  double freq = 2.4,
}) {
  return math.sin(timeSeconds * 2 * math.pi * freq + phase) * amplitude;
}
