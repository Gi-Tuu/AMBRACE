// F7-c-5（2026-09-01）自 features/weave/weave_scene_view.dart 拆分迁入；逻辑逐字节保持。
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:vector_math/vector_math.dart' show Vector3;

import 'package:flutter_scene/scene.dart' show Camera;
import 'package:ai_companion/theme/tokens.dart';
import 'package:ai_companion/utils/sphere_projection.dart';
import 'package:ai_companion/features/weave/weave_scene_controller.dart';
/// 同时显示的前 N 个标签数（避免满屏标签压盖）。
const int kWeaveLabelMax = 10;
/// 标签锚点超出节点外壳的距离（世界单位）。
const double kWeaveLabelAnchorGap = 0.12;
/// 节点法线与「节点→相机」视线方向点积低于此值 → 标签隐藏（约偏离 78°）。
const double kWeaveLabelCosHidden = 0.20;
/// 点积高于此值 → 标签不透明度拉满（约偏离 50° 以内）。
const double kWeaveLabelCosFull = 0.64;
/// 标签标题最大字符数（长标题截断）。
const int kWeaveLabelTitleMaxChars = 10;

class WeaveLabelData {
  const WeaveLabelData({
    required this.nodeId,
    required this.title,
    required this.x,
    required this.y,
    required this.scale,
    required this.alpha,
  });
  final int nodeId;
  final String title;
  final double x;
  final double y;
  /// 近大远小：投影后的屏幕缩放（透视缩放）。
  final double scale;
  /// 可见度 [0,1]（正面全亮、朝向边缘淡入、背面隐藏）。
  final double alpha;
}

/// 标签候选（含重叠排斥用的估算矩形）。
class _WeaveLabelCandidate {
  _WeaveLabelCandidate({
    required this.nodeId,
    required this.title,
    required this.center,
    required this.scale,
    required this.alpha,
    required this.priority,
    required this.rect,
  });
  final int nodeId;
  final String title;
  final Offset center;
  final double scale;
  final double alpha;
  /// 排序权重（可见度 × 近端尺寸），大者先放。
  final double priority;
  final Rect rect;
}

/// A' 法线方向标签可见度（点积 → [0,1] 淡入）。
double _labelVisibility(double facing) {
  if (facing <= kWeaveLabelCosHidden) return 0.0;
  if (facing >= kWeaveLabelCosFull) return 1.0;
  return (facing - kWeaveLabelCosHidden) /
      (kWeaveLabelCosFull - kWeaveLabelCosHidden);
}

/// 标题截断（过长省略号）。
String _truncateLabel(String title, int maxChars, String unnamed) {
  final t = title.trim();
  if (t.isEmpty) return unnamed;
  return t.length > maxChars ? '${t.substring(0, maxChars)}…' : t;
}

/// 把标签中心 [v] 钳制在 [0, extent] 内、离边缘至少 [half]（半宽+边距）；视图过窄时取中心。
double _clampLabel(double v, double half, double extent) {
  if (half * 2.0 >= extent) return extent * 0.5;
  return v.clamp(half, extent - half);
}

/// 计算 A' 法线方向标签（纯函数，供 3D 视图屏幕层投影绘制）。
///
/// 逐节点：
/// - 锚点 = 节点中心 + 节点径向(法线)方向 × ([sphereRadius]+[nodeRadius]+[anchorGap])，
///   即把标签立到节点外壳外侧一点；经 [camera.worldToScreen] 投影到屏幕坐标。
/// - 可见性 = 法线与「节点→相机」视线方向点积：[facing] 低于 [kWeaveLabelCosHidden] 隐藏、
///   高于 [kWeaveLabelCosFull] 全亮、之间线性淡入；再乘节点深度淡化（近实远虚、背面更淡）。
/// - 近大远小：字号/间距用 [WeaveNodeProjection.scale]（透视缩放）。
/// - 前 [max] 个：先筛正面节点，按「可见度×近端尺寸」排序取前 [max]，再用简单矩形排斥
///   （已放置标签重叠则让位/舍弃），避免满屏标签压盖。
List<WeaveLabelData> buildWeaveLabels({
  required Camera camera,
  required List<WeaveNodeProjection> nodes,
  required Size size,
  required double sphereRadius,
  required double nodeRadius,
  required String unnamed,
  int max = kWeaveLabelMax,
}) {
  final out = <_WeaveLabelCandidate>[];
  final anchorDist = sphereRadius + nodeRadius + kWeaveLabelAnchorGap;
  final camPos = camera.position;
  for (final l in nodes) {
    final normal = Vector3(l.ux, l.uy, l.uz);
    final nLen = normal.length;
    if (nLen < 1e-6) continue;
    final n = normal / nLen; // 归一化径向（法线）
    final nodePos = n * sphereRadius;
    final toCam = camPos - nodePos;
    final len = toCam.length;
    if (len < 1e-6) continue;
    final facing = n.dot(toCam / len);
    final vis = _labelVisibility(facing);
    if (vis <= 0.001) continue;
    final depthA = nodeDepthOpacity(-l.uz);
    final alpha = vis * depthA;
    if (alpha <= 0.01) continue;
    final anchor = n * anchorDist;
    final screen = camera.worldToScreen(anchor, size);
    if (screen == null) continue;
    final s = clampScale(l.scale);
    final bw = (l.node.title.length * 11.0 + 16.0) * s;
    final bh = (20.0 * s).clamp(14.0, 30.0);
    // 标签立在节点上方一点（屏幕 Y 偏移），避免盖住节点球；并把标签中心钳制在视图内，
    // 避免屏幕边缘/顶部标签被裁掉。
    final cx = _clampLabel(screen.dx, bw * 0.5 + 6.0, size.width);
    final cy = _clampLabel(screen.dy - bh * 0.75, bh * 0.5 + 6.0, size.height);
    final rect = Rect.fromCenter(center: Offset(cx, cy), width: bw, height: bh);
    out.add(_WeaveLabelCandidate(
      nodeId: l.node.id,
      title: l.node.title,
      center: Offset(cx, cy),
      scale: s,
      alpha: alpha,
      priority: alpha * (0.5 + s * 0.5),
      rect: rect,
    ));
  }
  out.sort((a, b) => b.priority.compareTo(a.priority));
  final placed = <Rect>[];
  final result = <WeaveLabelData>[];
  for (final c in out) {
    if (result.length >= max) break;
    var overlap = false;
    for (final r in placed) {
      if (r.overlaps(c.rect)) {
        overlap = true;
        break;
      }
    }
    if (overlap) continue;
    placed.add(c.rect);
    result.add(WeaveLabelData(
      nodeId: c.nodeId,
      title: _truncateLabel(c.title, kWeaveLabelTitleMaxChars, unnamed),
      x: c.center.dx,
      y: c.center.dy,
      scale: c.scale,
      alpha: c.alpha,
    ));
  }
  return result;
}

/// 屏幕层标签 painter：绘制前 [kWeaveLabelMax] 个节点标签（圆角 pill + 标题文字）。
/// 字色用浅色 + 深底 + 浅描边，保证在深空背景上可读；字号/间距随 [WeaveLabelData.scale] 近大远小。
class WeaveLabelPainter extends CustomPainter {
  WeaveLabelPainter({required this.labels});
  final List<WeaveLabelData> labels;

  @override
  void paint(Canvas canvas, Size size) {
    for (final l in labels) {
      final tp = TextPainter(
        text: TextSpan(
          text: l.title,
          style: TextStyle(
            fontSize: 10.5 * l.scale,
            fontWeight: FontWeight.w600,
            color: AppColors.white.withValues(alpha: l.alpha),
          ),
        ),
        maxLines: 1,
        ellipsis: '…',
        textDirection: TextDirection.ltr,
      )..layout();
      final bw = math.max(tp.width + 14.0 * l.scale, 34.0 * l.scale);
      final bh = tp.height + 9.0 * l.scale;
      final rrect = RRect.fromRectAndRadius(
        Rect.fromCenter(center: Offset(l.x, l.y), width: bw, height: bh),
        Radius.circular(bh * 0.5),
      );
      canvas.drawRRect(
        rrect,
        Paint()
          ..color = const Color(0xFF0B1020).withValues(alpha: 0.72 * l.alpha),
      );
      canvas.drawRRect(
        rrect,
        Paint()
          ..color = AppColors.white.withValues(alpha: 0.55 * l.alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.0,
      );
      tp.paint(canvas, Offset(l.x - tp.width / 2, l.y - tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant WeaveLabelPainter oldDelegate) => true;
}

/// 星点（确定性伪随机）：归一化坐标 + 半径 + 透明度 + 色相倾向。
class _Star {
  const _Star(this.rx, this.ry, this.radius, this.alpha, this.color);
  final double rx;
  final double ry;
  final double radius;
  final double alpha;
  final Color color;
}

/// 生成确定性的星点列表（远小星、近大星；固定种子，避免逐帧抖动 / 无 GPU 依赖）。
List<_Star> _generateStars() {
  var seed = 987654321;
  double next() {
    seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF;
    return seed / 0x7FFFFFFF;
  }

  final stars = <_Star>[];
  const count = 140;
  for (var i = 0; i < count; i++) {
    final rx = next();
    final ry = next();
    final r = next();
    // 远小星（多数）与近大星（少数）混合。
    final radius = r < 0.85 ? 0.5 + next() * 0.7 : 1.4 + next() * 1.4;
    final alpha = 0.18 + next() * 0.72;
    final tint = next();
    final color = tint < 0.72
        ? const Color(0xFFFFFFFF)
        : (tint < 0.9 ? const Color(0xFFBFD6FF) : const Color(0xFFFFE0C2));
    final dx = rx * 2 - 1;
    final dy = (ry * 2 - 1) * 0.9;
    if (dx * dx + dy * dy < 0.18) {
      // 中央球体区域屏蔽：把星点拉到外围一圈，避免压盖球面。
      final ang = math.atan2(dy, dx);
      final rd = 0.45 + next() * 0.4;
      final nx = ((math.cos(ang) * rd + 1) / 2).clamp(0.0, 1.0);
      final ny = ((math.sin(ang) * rd * 0.9 + 1) / 2).clamp(0.0, 1.0);
      stars.add(_Star(nx, ny, radius, alpha, color));
    } else {
      stars.add(_Star(rx, ry, radius, alpha, color));
    }
  }
  return stars;
}

/// 深空星空背景 painter（3D 与 2.5D 共用）：深空基底渐变 + 稀疏静态星点 + 球下柔和阴影/光晕。
/// 固定深空色（不随深浅色主题切换）。星点静态（省性能）；阴影/光晕用简单形状（椭圆柔影 + 径向亮晕）。
class WeaveSpaceBackgroundPainter extends CustomPainter {
  WeaveSpaceBackgroundPainter({this.zoom = 1.0});
  final double zoom;

  static final List<_Star> _stars = _generateStars();

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    // 深空基底渐变（固定深空色：0B1020 → 141A30）
    canvas.drawRect(
      Offset.zero & size,
      Paint()
        ..shader = ui.Gradient.linear(
          Offset(0, 0),
          Offset(0, h),
          [const Color(0xFF0B1020), const Color(0xFF141A30)],
        ),
    );
    // 静态星点
    for (final s in _stars) {
      canvas.drawCircle(
        Offset(s.rx * w, s.ry * h),
        s.radius,
        Paint()..color = s.color.withValues(alpha: s.alpha),
      );
    }
    // 球下柔和阴影（假想地板）+ 光晕：让球像浮空。随 zoom 放大一点点。
    final cx = w / 2;
    final shScale = (0.9 + (zoom - 1.0) * 0.25).clamp(0.7, 1.6);
    final shadowRect = Rect.fromCenter(
      center: Offset(cx, h * 0.90),
      width: w * 0.30 * shScale,
      height: h * 0.075 * shScale,
    );
    canvas.drawOval(
      shadowRect,
      Paint()
        ..color = const Color(0xFF000000).withValues(alpha: 0.42)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18),
    );
    // 光晕：球周围一圈柔和亮光，营造浮空发光。
    final glowC = Offset(cx, h * 0.44);
    final glowR = w * 0.36 * shScale;
    canvas.drawCircle(
      glowC,
      glowR,
      Paint()
        ..shader = ui.Gradient.radial(
          glowC,
          glowR,
          [
            AppColors.accent.withValues(alpha: 0.10),
            AppColors.accent.withValues(alpha: 0.0),
          ],
        ),
    );
  }

  @override
  bool shouldRepaint(covariant WeaveSpaceBackgroundPainter oldDelegate) =>
      oldDelegate.zoom != zoom;
}

/// 织网视图抽象：controller（逻辑）+ onCardTap（点节点进详情）。
abstract class WeaveSceneView extends StatelessWidget {
  const WeaveSceneView({super.key, required this.controller, required this.onCardTap});

  /// 共享的纯逻辑控制器（含旋转/缩放/命中/聚类）。
  final WeaveSceneController controller;

  /// 点击节点（id）时回调，由画布页打开详情弹层。
  final void Function(int cardId) onCardTap;
}

// ───────────────────────────── 2D（2.5D）视图 ─────────────────────────────

/// 织网 2.5D 画布视图：保留原 CustomPaint 逻辑与交互（拖动旋转/双指缩放/惯性/抖动/
/// 聚类泡），作为 `weave_3d=false` 的降级实现（行为与旧版一致）。
