// 织库画布 2.5D 球面投影纯函数（Phase B，2026-08-12）
// 布局：斐波那契球面均匀布点 → 绕 Y/X 轴旋转 → 透视投影（近大远小）
import 'dart:math' as math;

class SpherePoint {
  final double lat; // 纬度（弧度）
  final double lon; // 经度（弧度）
  const SpherePoint(this.lat, this.lon);
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

/// 透视投影：z∈[-1,1]（正 = 靠近相机），返回屏幕坐标、缩放与深度
({double x, double y, double scale, double depth}) projectPoint(
  double x,
  double y,
  double z,
  double cx,
  double cy,
  double radius, {
  double perspective = 0.3,
}) {
  final scale = 1.0 + perspective * z; // 近大远小
  return (
    x: cx + x * radius * scale,
    y: cy + y * radius * scale,
    scale: scale,
    depth: z,
  );
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
