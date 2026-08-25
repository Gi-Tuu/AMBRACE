// 织网 3D · 渲染层接口抽象 —— WeaveSceneView
//
// 2026-08-24（织网 3D P0）：把画布「数据/布局」与「渲染」解耦。
// - WeaveSceneController（weave_scene_controller.dart）= 纯逻辑（节点/旋转/缩放/命中/聚类）；
// - WeaveSceneView = 抽象视图接口（controller + onCardTap 回调）；
// - WeaveSceneView2D = 原 2.5D CustomPaint 画布（行为与旧版一致，作为降级实现）；
// - WeaveSceneView3D = flutter_scene 3D（P1：卡片纹理 + 精确拾取 + 动效 + 选中高亮）。
//
// 2026-08-24（织网 3D P1）：
// - 卡片纹理：离屏渲染文字卡片（标题+摘要+情绪色）→ Texture2D 贴到节点小球；LRU 纹理池控制内存，
//   节点数超阈值降级为纯色圆点（与 2.5D 聚类泡语义一致）。
// - 精确拾取：优先用 flutter_scene 真实命中测试（camera.screenPointToRay + Scene.raycast），
//   命中不到时用 3D 世界坐标投影到屏幕的最近邻兜底（命中半径放大）。
// - 动效：节点 pop-in（逐个错峰）、点中节点高亮一段时间后恢复。
// - 惯性缓动手感与 2.5D 一致（复用 controller 速度/衰减算法）。
//
// 2026-08-24（织网 3D P3，真机灰块修复 + 性能优化）：
// - 卡片纹理改 2 的幂 256×256（NPOT+mipmap 采样异常是灰块高概率根因）；预热分批渐进，
//   避免生成峰值触发帧率降级；帧率降级加暖机宽限。
// - 连线由「每边一个圆柱 SceneMesh」改为按透明度分档的 LineSegmentsGeometry 批量 render，
//   draw calls 从 N 降到个位数；降级 SnackBar 携带具体原因（渲染异常/持续低帧率/节点数超限）。
//
// 2026-08-24（织网 3D 低端机体验修复）:
// - 2.5D 也放开 theta（_WeaveScene2DHost initState setThetaLimit(null)），可自由翻转不卡手；
//   背面由 projectPoint 透视收敛 + nodeDepthOpacity 背面淡化（backFar 0.30→0.22）+ edgeAlpha 深度淡化
//   自然呈现，翻转观感自然、聚类泡/连线不错乱。
// - 3D 分档降级 full→light→2D（一次性下坡不抖动）：full（纹理+连线+节点球）持续约 2s <30fps
//   降 light（纯色圆点+低细分球+连线+聚类泡，提示「已切换轻量模式」）；light 持续约 2s <20fps
//   才回退 2.5D（提示「持续低帧率」）；light 不回 full。
// - light 档不预热纹理、清空纹理缓存；节点球用低细分共享几何体（segments 8/rings 6）。
//
// 硬性约束：禁用 apply_patch；本文件只用 Python 脚本/scripts\edit.py 修改（保留换行）。
import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart' show Ticker;
import 'package:vector_math/vector_math.dart' show Vector3, Vector4;
import 'package:flutter_scene/scene.dart'
    show
        AlphaMode,
        Camera,
        LineSegmentData,
        LineSegmentsGeometry,
        PerspectiveCamera,
        Scene,
        SceneMesh,
        SceneView,
        SphereGeometry,
        UnlitMaterial;

import 'package:ai_companion/theme/tokens.dart';
import 'package:ai_companion/utils/sphere_projection.dart';
import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
import 'package:ai_companion/screens/weave/weave_card_texture.dart';
import 'package:ai_companion/screens/weave/weave_edge_render.dart';
import 'package:ai_companion/screens/weave/weave_perf_monitor.dart';
import 'package:ai_companion/screens/weave/weave_view_mode.dart';

// ─────────────────────────── A' 法线标签 + 深空背景（屏幕层） ───────────────────────────
//
// 2026-08-25（织网 3D 视觉终局改造 A/C）：节点小球不再贴卡片文字纹理（改为纯色/图标球，
// weave_card_texture 的 buildTexture 保留供卡片详情用），文字改由「屏幕层投影」绘制 A' 法线
// 方向标签（锚点=节点中心+法线方向偏移，转到节点正面才可见）；并给 3D/2.5D 统一加固定深空
// 星空背景 + 球下柔影/光晕（浮空感）。本段全部为纯数据/纯函数/painter，无 GPU 依赖。

/// 同时显示的前 N 个标签数（避免满屏标签压盖）。
const int kWeaveLabelMax = 10;
/// 标签锚点超出节点外壳的距离（世界单位）：节点中心沿法线方向再往外推，把标签立到外壳外侧。
const double kWeaveLabelAnchorGap = 0.12; // 2026-08-25：标签贴球壳（用户反馈离球太远→0.85→0.12）
/// 节点法线与「节点→相机」视线方向点积低于此值 → 标签隐藏（约偏离 78°）。
const double kWeaveLabelCosHidden = 0.20;
/// 点积高于此值 → 标签不透明度拉满（约偏离 50° 以内）。
const double kWeaveLabelCosFull = 0.64;
/// 标签标题最大字符数（长标题截断）。
const int kWeaveLabelTitleMaxChars = 10;

/// A' 法线方向标签（屏幕层数据）：节点 id、标题、屏幕中心、缩放、可见度。
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
String _truncateLabel(String title, int maxChars) {
  final t = title.trim();
  if (t.isEmpty) return '未命名';
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
      title: _truncateLabel(c.title, kWeaveLabelTitleMaxChars),
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
class WeaveSceneView2D extends WeaveSceneView {
  const WeaveSceneView2D({super.key, required super.controller, required super.onCardTap});

  @override
  Widget build(BuildContext context) {
    return _WeaveScene2DHost(controller: controller, onCardTap: onCardTap);
  }
}

class _WeaveScene2DHost extends StatefulWidget {
  const _WeaveScene2DHost({required this.controller, required this.onCardTap});

  final WeaveSceneController controller;
  final void Function(int cardId) onCardTap;

  @override
  State<_WeaveScene2DHost> createState() => _WeaveScene2DHostState();
}

class _WeaveScene2DHostState extends State<_WeaveScene2DHost>
    with TickerProviderStateMixin {
  late final AnimationController _jitterCtrl;
  late final Ticker _inertiaTicker;

  @override
  void initState() {
    super.initState();
    // 2.5D 与 3D 一样放开 theta（可自由旋转翻到球背面）；背面由 projectPoint 的透视收敛
    // （w=1-perspective*z，z<0 时缩小向中心汇聚）+ nodeDepthOpacity 背面淡化（backFar 0.22）+
    // edgeAlpha 深度淡化自然呈现，已核对放开后翻转观感自然、聚类泡/连线不错乱（低端机体验修复）。
    widget.controller.setThetaLimit(null);
    _jitterCtrl =
        AnimationController(vsync: this, duration: const Duration(seconds: 5))
          ..repeat();
    _inertiaTicker = createTicker(_tickInertia);
  }

  @override
  void dispose() {
    _jitterCtrl.dispose();
    _inertiaTicker.dispose();
    super.dispose();
  }

  void _tickInertia(Duration _) {
    final still = widget.controller.tickInertia();
    if (!still) _inertiaTicker.stop();
  }

  void _onScaleStart(ScaleStartDetails d) {
    _inertiaTicker.stop();
    widget.controller.onScaleStart();
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    widget.controller.trackDrag(d.focalPointDelta.dx, d.focalPointDelta.dy);
    widget.controller.rotate(d.focalPointDelta.dx, d.focalPointDelta.dy);
    widget.controller.updateScale(d.scale);
  }

  void _onScaleEnd(ScaleEndDetails d) {
    if (widget.controller.onScaleEnd()) {
      _inertiaTicker.start();
    }
  }

  void _onTapUp(TapUpDetails d, Size size) {
    final id = widget.controller.hitTest(d.localPosition, size);
    if (id != null) {
      widget.onCardTap(id);
      return;
    }
    final clusterId = widget.controller.hitTestCluster(d.localPosition, size);
    if (clusterId != null) widget.controller.toggleCluster(clusterId);
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        return LayoutBuilder(builder: (context, constraints) {
          final size = Size(constraints.maxWidth, constraints.maxHeight);
          final layout = widget.controller.project(size);
          // 2.5D 与 3D 统一：深空星空背景（固定深空色，不随深浅色主题）+ 连线/节点深度淡化，
          // 使 2.5D 降级模式与 3D 观感一致（终局视觉改造）。
          return Stack(
            fit: StackFit.expand,
            children: [
              CustomPaint(
                painter: WeaveSpaceBackgroundPainter(
                  zoom: widget.controller.zoom,
                ),
              ),
              GestureDetector(
                onScaleStart: _onScaleStart,
                onScaleUpdate: _onScaleUpdate,
                onScaleEnd: _onScaleEnd,
                onTapUp: (d) => _onTapUp(d, size),
                child: CustomPaint(
                  size: size,
                  painter: WeaveCanvasPainter(
                    nodes: layout.nodes,
                    bubbles: layout.bubbles,
                    edges: widget.controller.edges,
                    jitterT: _jitterCtrl.value * 5.0,
                  ),
                ),
              ),
            ],
          );
        });
      },
    );
  }
}

/// 织网 2.5D 画布 painter（公开类：保留 `WeaveCanvas` 命名，供既有测试按类型查找）。
class WeaveCanvasPainter extends CustomPainter {
  final List<WeaveNodeProjection> nodes;
  final List<WeaveBubbleProjection> bubbles;
  final List<WeaveSceneEdge> edges;
  final double jitterT;

  WeaveCanvasPainter({
    required this.nodes,
    required this.bubbles,
    required this.edges,
    required this.jitterT,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (nodes.isEmpty && bubbles.isEmpty) return;
    final byId = {for (final l in nodes) l.node.id: l};
    final highDensity = edges.length > 100;

    // 连线（最底层）：两端均可见才画，按两端深度淡化，减少高 N 时杂乱
    for (final e in edges) {
      final a = byId[e.source];
      final b = byId[e.target];
      if (a == null || b == null) continue;
      final base = edgeAlpha(a.depthNorm, b.depthNorm, e.strength);
      // 高密度时弱边大幅衰减
      final factor = (highDensity && e.strength < 0.5) ? 0.35 : 1.0;
      final alpha = (base * factor).clamp(0.0, 1.0);
      if (alpha <= 0.01) continue;
      canvas.drawLine(
        Offset(a.x, a.y),
        Offset(b.x, b.y),
        Paint()
          ..color = AppColors.accent.withValues(alpha: alpha)
          ..strokeWidth = 1.7,
      );
    }

    // 汇总绘制单元：远处先画（depth 升序）→ 前端置于最上层（画家算法）
    final draws = <({double depth, WeaveNodeProjection? node,
        WeaveBubbleProjection? bubble})>[
      for (final n in nodes) (depth: n.depth, node: n, bubble: null),
      for (final b in bubbles) (depth: b.depth, node: null, bubble: b),
    ]..sort((x, y) => x.depth.compareTo(y.depth));
    for (final d in draws) {
      if (d.node != null) {
        _drawNode(canvas, d.node!);
      } else {
        _drawBubble(canvas, d.bubble!);
      }
    }
  }

  void _drawNode(Canvas canvas, WeaveNodeProjection l) {
    final s = clampScale(l.scale); // 缩放钳制，避免近端过大/远端过小
    final opacity = nodeDepthOpacity(l.depth); // 近=实、远=虚；z<0 淡化
    final jx = jitterOffset(jitterT, l.node.id.toDouble(),
        amplitude: 2.0, freq: 2.2);
    final jy = jitterOffset(
      jitterT,
      l.node.id.toDouble() * 1.7 + 0.3,
      amplitude: 1.4,
      freq: 2.6,
    );
    final x = l.x + jx;
    final y = l.y + jy;
    final title = l.node.title.length > 5
        ? '${l.node.title.substring(0, 5)}…'
        : l.node.title;
    final tp = TextPainter(
      text: TextSpan(
        text: title,
        style: TextStyle(
          fontSize: 10.5 * s,
          fontWeight: FontWeight.w600,
          color: AppColors.white.withValues(alpha: opacity),
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final cardW = math.max(tp.width, 42.0 * s) + 10.0 * s;
    final cardH = tp.height + 9.0 * s;
    final color = weaveNodeColor(l.node);
    // 私域增强：生活类型着色（生活=蓝/反思=紫/笔记=绿），热标签节点描边加亮
    final isHot = l.node.hotTags.isNotEmpty;
    final rrect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(x, y),
        width: cardW,
        height: cardH,
      ),
      Radius.circular(9 * s),
    );
    // 深空背景上的浅色可读标签：深底 + 白字 + 节点色描边（保节点颜色身份）。
    // 填充：深色底随深度衰减（近实、远虚），保证白字可读。
    canvas.drawRRect(
      rrect,
      Paint()
        ..color = const Color(0xFF0B1020).withValues(alpha: 0.62 * opacity),
    );
    // 描边：随深度衰减（正面亮、背面淡）
    canvas.drawRRect(
      rrect,
      Paint()
        ..color = color.withValues(alpha: (isHot ? 0.95 : 0.55) * opacity)
        ..style = PaintingStyle.stroke
        ..strokeWidth = (isHot ? 2.0 : 1.2) * s,
    );
    tp.paint(canvas, Offset(x - tp.width / 2, y - tp.height / 2));
    // 兴趣热标记：右上角小圆点（热度徽标）
    if (isHot) {
      final dot = Offset(x + cardW / 2 - 2, y - cardH / 2 + 2);
      canvas.drawCircle(
        dot,
        3.2 * s,
        Paint()..color = AppColors.error,
      );
    }
  }

  void _drawBubble(Canvas canvas, WeaveBubbleProjection b) {
    final s = clampScale(b.scale);
    final opacity = nodeDepthOpacity(b.depth);
    final jx = jitterOffset(jitterT, b.clusterId.toDouble(),
        amplitude: 1.6, freq: 2.2);
    final jy = jitterOffset(
      jitterT,
      b.clusterId.toDouble() * 1.7 + 0.3,
      amplitude: 1.2,
      freq: 2.6,
    );
    final x = b.x + jx;
    final y = b.y + jy;
    final color = kWeavePalette[b.clusterId % kWeavePalette.length];
    // 泡半径随成员数单调增大（对数，避免极端）
    final r = (16.0 + 4.0 * math.log(b.members.length + 1.0)) * s;
    if (!b.collapsed) {
      // 展开态：淡环，可点按收起（成员节点由 _drawNode 绘制在最上层）
      canvas.drawCircle(
        Offset(x, y),
        r,
        Paint()
          ..color = color.withValues(alpha: 0.20 * opacity)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2 * s,
      );
      return;
    }
    // 收起态：实心聚类泡（数量标签）
    canvas.drawCircle(
      Offset(x, y),
      r,
      Paint()..color = color.withValues(alpha: 0.30 * opacity),
    );
    canvas.drawCircle(
      Offset(x, y),
      r,
      Paint()
        ..color = color.withValues(alpha: 0.95 * opacity)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0 * s,
    );
    final tp = TextPainter(
      text: TextSpan(
        text: '${b.members.length}',
        style: TextStyle(
          fontSize: 12.0 * s,
          fontWeight: FontWeight.w700,
          color: AppColors.white.withValues(alpha: opacity),
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(x - tp.width / 2, y - tp.height / 2));
  }

  @override
  bool shouldRepaint(covariant WeaveCanvasPainter oldDelegate) => true;
}

// ───────────────────────────── 3D 视图（P1） ─────────────────────────────

/// 3D → 2.5D 降级的原因（2026-08-24 织网 3D P3，C.降级体验）：用于 SnackBar 给用户带上
/// 具体原因，方便真机反馈定位。一次性降级（不抖动），原因只决定文案。
enum WeaveFallbackReason {
  /// 3D 渲染初始化/首帧异常（scene 静态资源加载失败、GPU 上下文异常等）。
  renderError,

  /// 约 2s 窗口平均帧率持续低于阈值（低端机/性能不足）。
  lowFps,

  /// 可见节点数超限（3D 逐节点 mesh 对低端机太重，直接走 2.5D 的聚类更合适）。
  nodesExceed,
}

/// 把降级原因映射为稳定的标识符（供本地化取词/单测校验；l10n 用一组合 key）。
String weaveFallbackReasonKey(WeaveFallbackReason reason) {
  switch (reason) {
    case WeaveFallbackReason.renderError:
      return 'renderError';
    case WeaveFallbackReason.lowFps:
      return 'lowFps';
    case WeaveFallbackReason.nodesExceed:
      return 'nodesExceed';
  }
}

/// 织网 3D 视图（flutter_scene，P1）：节点小球贴离屏卡片纹理，真实命中拾取，
/// pop-in 动效 + 选中高亮；拖动旋转（theta 放开）、双指缩放、惯性（复用 controller）。
///
/// 2026-08-24（织网 3D P2）：低端机自动降级 2.5D——scene 初始化/渲染异常或持续低帧率时，
/// 经 [onFallbackTo2D] 通知画布页切到 2D 视图并提示（降级状态放本视图 State，不进后端 flag）。
///
/// 2026-08-24（织网 3D P3，灰块修复 + 性能优化）：卡片纹理改 2 的幂、预热分批渐进、
/// 连线改 LineSegmentsGeometry 批量、降级原因透传。
class WeaveSceneView3D extends WeaveSceneView {
  const WeaveSceneView3D({
    super.key,
    required super.controller,
    required super.onCardTap,
    this.mode = WeaveViewMode.auto,
    this.onFallbackTo2D,
    this.onDegradeToLight,
  });

  /// 手动渲染档位模式（默认 auto=全自动，保持自动降级检测）。
  /// 手动模式（full3d/light3d/twoD）与自动降级检测互斥；auto 时按既有帧率检测降级。
  final WeaveViewMode mode;

  /// 3D 初始化/渲染异常、持续约 2 秒平均帧率低于阈值、或节点数超限时回退 2.5D 的回调
  /// （画布页切视图 + 提示，携带 [WeaveFallbackReason]）。
  final void Function(WeaveFallbackReason reason)? onFallbackTo2D;

  /// full 档持续低帧率降到 light 档（3D 简化渲染）时的回调（画布页提示「已切换轻量模式」，
  /// 但不切 2.5D 视图）。一次性下坡，不抖动。
  final void Function()? onDegradeToLight;

  @override
  Widget build(BuildContext context) {
    return _WeaveScene3DHost(
      controller: controller,
      onCardTap: onCardTap,
      mode: mode,
      onFallbackTo2D: onFallbackTo2D,
      onDegradeToLight: onDegradeToLight,
    );
  }
}

/// 3D 拾取兜底：把一组物体的世界坐标经相机投影到屏幕，返回 offset 附近（命中半径
/// [hitRadiusPx] 可放大）最近物体的 id；未命中返回 null。
///
/// 用于在 `Scene.raycast` 未命中（小卡片球体的近失）时按「3D 世界坐标投影到屏幕」拾取，
/// 而非旧版 2D 投影最近邻。对象在相机之后（`worldToScreen` 返回 null）会被自动丢弃。
int? pickNearest3DScreen<T>({
  required Offset offset,
  required Size size,
  required Camera camera,
  required List<T> items,
  required Vector3 Function(T) worldOf,
  required int Function(T) idOf,
  double hitRadiusPx = 44.0,
}) {
  int? bestId;
  var bestDist = double.infinity;
  for (final it in items) {
    final screen = camera.worldToScreen(worldOf(it), size);
    if (screen == null) continue;
    final dx = screen.dx - offset.dx;
    final dy = screen.dy - offset.dy;
    final dist = math.sqrt(dx * dx + dy * dy);
    if (dist > hitRadiusPx) continue;
    if (dist < bestDist) {
      bestDist = dist;
      bestId = idOf(it);
    }
  }
  return bestId;
}

class _WeaveScene3DHost extends StatefulWidget {
  const _WeaveScene3DHost({
    required this.controller,
    required this.onCardTap,
    this.mode = WeaveViewMode.auto,
    this.onFallbackTo2D,
    this.onDegradeToLight,
  });

  final WeaveSceneController controller;
  final void Function(int cardId) onCardTap;
  final WeaveViewMode mode;
  final void Function(WeaveFallbackReason reason)? onFallbackTo2D;
  final void Function()? onDegradeToLight;

  @override
  State<_WeaveScene3DHost> createState() => _WeaveScene3DHostState();
}

class _WeaveScene3DHostState extends State<_WeaveScene3DHost>
    with TickerProviderStateMixin {
  /// 整体节点球半径（世界单位）。
  static const double _sphereRadius = 2.0;
  /// 单个节点小球半径。2026-08-24（P2）由 0.05 提到 0.09，配合卡片纹理让文字更可读。
  ///
  /// 2026-08-25（织网 3D 修复）：卡片纹理贴字在真机上 0.09 仍太小——256×256 卡片被 mip 平均成
  /// 近纯色、文字不可读（用户反馈全量档「纯色球，看不出卡片细节」）。提到 0.30 让卡片纹理
  /// （标题+摘要+标签）在节点球面上可读；≤80 节点的球面间隔（≥0.8 世界单位）仍大于球径 0.60，
  /// 不会互相重叠（>80 走球面聚类泡，成员节点布局与 2.5D 一致）。
  static const double _nodeRadius = 0.30;
  /// 聚类泡小球半径。
  static const double _bubbleRadius = 0.14;
  /// 3D 连线（LineSegmentsGeometry 相机朝向飘带）的宽度（世界单位）。比原圆柱直径 0.016
  /// 略粗一档（0.02），让细带在低端机上更易看清（织网 3D P3：连线改造为批量飘带）。
  static const double _edgeWidth3D = 0.02;
  /// 3D 视图可见节点数上限：超过即回退 2.5D（节点数超限——逐节点 mesh 对低端机太重，
  /// 2.5D 的聚类/纯色点更合适）。高于 [kWeaveTextureDegradeAbove]=150（>150 已降纯色点）。
  static const int _nodeFallbackMax = 220;
  /// 帧率降级的暖机宽限帧数：首帧 shader 编译/分批纹理预热等瞬态低谷不计入帧率判定，
  /// 避免一进 3D 就误触发降级（灰块→自动降级 2.5D 的直接诱因之一）。
  static const int _degradeWarmupFrames = 30;
  /// 选中高亮持续时间，之后恢复。
  static const Duration _selectHighlightDuration = Duration(milliseconds: 1200);
  /// 3D 拾取兜底：命中半径（逻辑像素，适度放大以覆盖小卡片球体的近失）。
  static const double _pickFallbackRadius = 44.0;

  /// 共享的节点小球几何体（同一实例复用，避免每帧重建 GPU 资源）。
  static final SphereGeometry _nodeGeometry =
      SphereGeometry(radius: _nodeRadius, segments: 12, rings: 8);
  /// light 档共享的低细分节点球几何体（segments 8 / rings 6，比 full 12/8 更省顶点；light 档
  /// 节点为纯色圆点，无需额外细分）。低端机降 light 后用，减轻 GPU 顶点/绘制负担。
  static final SphereGeometry _lightNodeGeometry =
      SphereGeometry(radius: _nodeRadius, segments: 8, rings: 6);
  static final SphereGeometry _bubbleGeometry =
      SphereGeometry(radius: _bubbleRadius, segments: 16, rings: 10);

  /// 按节点 id 缓存的材质（颜色由当前状态决定）。
  final Map<int, UnlitMaterial> _nodeMaterials = {};
  /// 按 clusterId 缓存的聚类泡材质。
  final Map<int, UnlitMaterial> _bubbleMaterials = {};
  /// 按透明度分档索引缓存的连线批量材质（档数 = [kWeaveEdgeRenderTiers]，个位数）。
  final Map<int, UnlitMaterial> _edgeBatchMaterials = {};

  /// 由本 State 拥有的场景（便于 raycast）；懒创建（测试环境无 GPU）。
  Scene? _scene;
  /// 本帧渲染相机（供拾取时复用同一投影）。
  Camera? _camera;
  /// 本帧逻辑尺寸（供拾取）。
  Size _size = Size.zero;

  /// 场景资源是否已就绪（base shader bundle 加载完才能建连线几何体/材质）。
  bool _sceneReady = false;
  /// 是否已回退 2.5D（一次性，避免多次触发切视图/提示）。
  bool _degradedTo2D = false;

  /// 低端机两段式降级判定（full→light→2.5D）：full 约 2s <30fps → 切 light；
  /// light 约 2s <20fps → 回退 2.5D。一次性下坡不抖动；full 带暖机宽限。
  final WeaveRenderDegrader _renderDegrader =
      WeaveRenderDegrader(warmupFrames: _degradeWarmupFrames);
  late final Ticker _fpsTicker;

  /// 选中高亮：当前选中的节点 id + 恢复计时器。
  int? _selectedId;
  Timer? _selectTimer;

  /// 分批预热（织网 3D P3）已随「球面不贴卡片纹理」移除：节点球一律纯色/图标球，故不再预热
  /// 卡片纹理（weave_card_texture 的 buildTexture 保留供卡片详情用）。

  /// pop-in 动效：节点逐个错峰弹出。
  late final AnimationController _popInCtrl;
  /// 当前节点集的指纹（id 序列），检测「节点集变化」以重启 pop-in / 预热纹理。
  List<int> _lastNodeIds = const [];

  /// 惯性 ticker（复用 controller 速度/衰减算法，手感与 2.5D 对齐）。
  late final Ticker _inertiaTicker;
  /// 触发重建的 listenable：控制器 + pop-in（两者任一变化都重绘）。
  late final Listenable _repaintListenable;

  @override
  void initState() {
    super.initState();
    // 3D 视图 theta 完全放开（可自由旋转翻到球背面）；惯性照放开范围衰减。
    widget.controller.setThetaLimit(null);
    // 手动固定档：初始即指定渲染档（light3d → light；full3d/auto → full），不参与自动降级。
    _renderDegrader.startAt(widget.mode.initialTier);
    _inertiaTicker = createTicker(_tickInertia);
    // 帧率自动降级只在「全自动」（auto）模式生效；手动固定档不启动帧率检测（停用自动降级）。
    _fpsTicker = createTicker(_onFpsTick);
    if (widget.mode.enableAutoDegrade) _fpsTicker.start();
    _popInCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 650),
    );
    _repaintListenable = Listenable.merge([widget.controller, _popInCtrl]);
    widget.controller.addListener(_onControllerChanged);
    // 场景资源（base shader bundle）加载完才构建连线几何体；加载异常回退 2.5D（渲染异常）。
    Scene.initializeStaticResources().then((_) {
      if (!mounted || _sceneReady) return;
      setState(() => _sceneReady = true);
    }).catchError((Object _) {
      // 手动固定档不自动回退（用户已显式选择该档）；仅在 auto 下回退 2.5D。
      if (!widget.mode.enableAutoDegrade) return;
      _fallbackTo2D(WeaveFallbackReason.renderError);
    });
    // 首次挂载可能已有节点（数据先加载后建视图）：立即跑一次 pop-in/预热检测。
    _onControllerChanged();
  }

  @override
  void dispose() {
    _inertiaTicker.dispose();
    _fpsTicker.dispose();
    _popInCtrl.dispose();
    _selectTimer?.cancel();
    widget.controller.removeListener(_onControllerChanged);
    _nodeMaterials.clear();
    _bubbleMaterials.clear();
    _edgeBatchMaterials.clear();
    super.dispose();
  }

  void _tickInertia(Duration _) {
    final still = widget.controller.tickInertia();
    if (!still) _inertiaTicker.stop();
  }

  /// 帧率监测：每帧记录单调毫秒，按两段式判定（full→light→2.5D）。
  /// - full 约 2s <30fps → 切 light（停预热/清纹理，提示轻量模式）；
  /// - light 约 2s <20fps → 回退 2.5D（提示持续低帧率）。
  void _onFpsTick(Duration elapsed) {
    if (_degradedTo2D) return;
    if (!widget.mode.enableAutoDegrade) return; // 手动固定档不参与自动降级
    final event = _renderDegrader.recordFrame(elapsed.inMilliseconds);
    switch (event) {
      case WeaveDegradeEvent.switchedToLight:
        _applyLightTier();
        break;
      case WeaveDegradeEvent.fallbackTo2D:
        _fallbackTo2D(WeaveFallbackReason.lowFps);
        break;
      case WeaveDegradeEvent.none:
        break;
    }
  }

  /// 回退 2.5D：通知画布页切视图并提示（一次性，自旋一次后停掉相关 ticker 与预热）。
  /// [reason] 决定画布页 SnackBar 的文案（渲染异常/持续低帧率/节点数超限）。
  void _fallbackTo2D(WeaveFallbackReason reason) {
    if (!mounted || _degradedTo2D) return;
    _degradedTo2D = true;
    _fpsTicker.stop();
    _inertiaTicker.stop();
    widget.onFallbackTo2D?.call(reason);
  }

  /// 切到 light 档：通知画布页提示「已切换轻量模式」。自「球面不贴卡片纹理」后，降 light 只需
  /// 换低细分几何体（节点球为纯色），无需清纹理缓存/停预热。一次性下坡（不回 full），不切 2.5D 视图。
  void _applyLightTier() {
    if (!mounted) return;
    widget.onDegradeToLight?.call();
    setState(() {});
  }

  /// 控制器变化：节点集（可见节点 id 序列）变化时重启 pop-in 并预热纹理。
  /// 节点数超限直接回退 2.5D（3D 逐节点 mesh 对低端机太重）。
  void _onControllerChanged() {
    final ids = [for (final n in widget.controller.nodes) n.id];
    if (_listEquals(ids, _lastNodeIds)) return;
    _lastNodeIds = ids;
    if (_degradedTo2D) return;
    if (widget.mode.enableAutoDegrade && ids.length > _nodeFallbackMax) {
      _fallbackTo2D(WeaveFallbackReason.nodesExceed);
      return;
    }
    _popInCtrl.forward(from: 0);
  }

  bool _listEquals(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  // ── 手势 ──

  void _onScaleStart(ScaleStartDetails d) {
    _inertiaTicker.stop();
    widget.controller.onScaleStart();
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    widget.controller.trackDrag(d.focalPointDelta.dx, d.focalPointDelta.dy);
    widget.controller.rotate(d.focalPointDelta.dx, d.focalPointDelta.dy);
    widget.controller.updateScale(d.scale);
  }

  void _onScaleEnd(ScaleEndDetails d) {
    if (widget.controller.onScaleEnd()) {
      _inertiaTicker.start();
    }
  }

  // ── 拾取 ──

  void _onTapUp(TapUpDetails d) {
    final camera = _camera;
    final scene = _scene;
    if (camera == null || scene == null || _size.isEmpty) return;

    // 优先：flutter_scene 真实命中测试（camera.screenPointToRay + Scene.raycastAll）。
    // 用 raycastAll 并跳过连线批次（'e:' 前缀），避免细带几何体挡住节点/泡的命中。
    final ray = camera.screenPointToRay(d.localPosition, _size);
    for (final hit in scene.raycastAll(ray)) {
      final name = hit.node.name;
      if (name.startsWith('e:')) continue; // 连线批次：不可交互
      if (name.startsWith('n:')) {
        final id = int.tryParse(name.substring(2));
        if (id != null) {
          _select(id);
          widget.onCardTap(id);
          return;
        }
      } else if (name.startsWith('b:')) {
        final cid = int.tryParse(name.substring(2));
        if (cid != null) {
          widget.controller.toggleCluster(cid);
          return;
        }
      }
    }

    // 兜底：3D 世界坐标投影到屏幕的最近邻（放大命中半径），仍按 node/cluster 语义处理。
    final layout = widget.controller.project(_size);
    final nodeId = pickNearest3DScreen<WeaveNodeProjection>(
      offset: d.localPosition,
      size: _size,
      camera: camera,
      items: layout.nodes,
      worldOf: (p) => Vector3(
        p.ux * _sphereRadius,
        p.uy * _sphereRadius,
        p.uz * _sphereRadius,
      ),
      idOf: (p) => p.node.id,
      hitRadiusPx: _pickFallbackRadius,
    );
    if (nodeId != null) {
      _select(nodeId);
      widget.onCardTap(nodeId);
      return;
    }
    final clusterId = pickNearest3DScreen<WeaveBubbleProjection>(
      offset: d.localPosition,
      size: _size,
      camera: camera,
      items: layout.bubbles,
      worldOf: (b) => Vector3(
        b.ux * _sphereRadius,
        b.uy * _sphereRadius,
        b.uz * _sphereRadius,
      ),
      idOf: (b) => b.clusterId,
      hitRadiusPx: _pickFallbackRadius,
    );
    if (clusterId != null) widget.controller.toggleCluster(clusterId);
  }

  /// 选中高亮：记录节点并安排恢复。
  void _select(int id) {
    _selectTimer?.cancel();
    _selectedId = id;
    _selectTimer = Timer(_selectHighlightDuration, () {
      if (!mounted) return;
      setState(() => _selectedId = null);
    });
    setState(() {});
  }

  // ── 材质 ──

  /// 节点材质：纯色/图标球。终局改造——球面不再贴卡片文字纹理（改由屏幕层 A' 法线标签带文字），
  /// 故颜色 = [weaveNodeColor] 纯色球 + 深度淡化；选中高亮提亮。weave_card_texture 的 buildTexture
  /// 保留供卡片详情用。
  /// - 深度淡化：3D 相机在 (0,0,-dist) 看向 +Z，故 uz=-1 最近（近）、+1 最远（远/背）。用
  ///   [nodeDepthOpacity](-uz) 得到「近=实、远=虚、背面更淡」（与 2.5D 同曲线，按 3D 相机近/远取向），
  ///   写入 baseColorFactor 第 4 分量 + alphaMode=blend（深度排序透明 pass）。
  UnlitMaterial _nodeMaterial(WeaveNodeProjection p, {required bool selected}) {
    final id = p.node.id;
    final c = weaveNodeColor(p.node);
    final m = _nodeMaterials.putIfAbsent(id, () {
      final mm = UnlitMaterial();
      mm.alphaMode = AlphaMode.blend;
      mm.baseColorFactor = Vector4(1, 1, 1, 1);
      return mm;
    });
    // 深度淡化（近=实、远=虚、背面更淡）：用 -uz 把「3D 相机距离」换算成 nodeDepthOpacity 的 z。
    final depthA = nodeDepthOpacity(-p.uz);
    // 半透明材质：baseColorFactor 第 4 分量作 alpha，走深度排序的 translucent pass。
    m.alphaMode = AlphaMode.blend;
    // 选中高亮：提亮（金色）；否则节点色。alpha 统一乘深度淡化。
    if (selected) {
      m.baseColorFactor = Vector4(1.2, 1.12, 0.72, depthA);
    } else {
      // Color.r/g/b 为 [0,1] 的 double；直接用作线性 baseColorFactor。
      m.baseColorFactor = Vector4(c.r, c.g, c.b, depthA);
    }
    return m;
  }

  UnlitMaterial _bubbleMaterial(WeaveBubbleProjection b) {
    final c = kWeavePalette[b.clusterId % kWeavePalette.length];
    final m = _bubbleMaterials.putIfAbsent(b.clusterId, () {
      final mm = UnlitMaterial();
      mm.baseColorFactor = Vector4(1, 1, 1, 1);
      return mm;
    });
    // 深度淡化（近=实、远=虚、背面更淡，与节点一致）：相机在 -Z→+Z，uz=-1 最近、+1 最远。
    final depthA = nodeDepthOpacity(-b.uz);
    // 收起=实心泡；展开=淡环；两者都走深度排序的 translucent pass（深度淡化需 alpha<1）。
    final expanded = !b.collapsed;
    m.alphaMode = AlphaMode.blend;
    m.baseColorFactor = Vector4(c.r, c.g, c.b, (expanded ? 0.28 : 0.95) * depthA);
    return m;
  }

  /// 连线批量材质：按透明度分档索引缓存（档数 = [kWeaveEdgeRenderTiers]，个位数）。
  /// 半透明（AlphaMode.blend）；LineSegmentsGeometry 的顶点色恒为白色（v_color=vec4(1)），
  /// baseColorTexture 为空（white placeholder），故最终颜色 = baseColorFactor = accent + 档位透明度。
  UnlitMaterial _edgeBatchMaterial(int tierIndex, double alpha) {
    final m = _edgeBatchMaterials.putIfAbsent(tierIndex, () {
      final mm = UnlitMaterial();
      mm.alphaMode = AlphaMode.blend; // 半透明细带（深度排序的 translucent pass）
      mm.baseColorFactor = Vector4(1, 1, 1, 1);
      return mm;
    });
    m.baseColorFactor = Vector4(
      AppColors.accent.r,
      AppColors.accent.g,
      AppColors.accent.b,
      alpha,
    );
    return m;
  }

  /// 3D 连线（批量，织网 3D P3）：把全部边按透明度分档，每档一份 LineSegmentsGeometry
  /// （端点 = 两端节点世界坐标）+ 一份半透明 UnlitMaterial。draw calls 从「每边一个圆柱
  /// SceneMesh」（N 个）降到「每档一个 batch」（≤ [kWeaveEdgeRenderTiers]，个位数）。
  ///
  /// 路由/材质要点（已核对 flutter_scene 0.20.0 源码）：
  /// - LineSegmentsGeometry 在顶点着色器里把每条线段按「相机朝向」展开成固定宽度的飘带，
  ///   且 flutter_scene_line_segments.vert 第 42-47 行显式把每段展开三角形定向为「前向」
  ///   （perp 按 cross(perp,dir)·view 的符号翻转），保证配 back-face 剔除的材质也不会丢带；
  /// - 顶点色恒为白色（v_color=vec4(1)），材质 baseColorFactor = accent + 档位透明度；
  /// - 端点是世界坐标（两端节点单位坐标×球半径），batch 挂在原点（无 position/rotation/scale）。
  /// - 节点旋转（controller 重算节点坐标）时布局变化，几何体按当前布局重建：每帧仅新分配
  ///   一个小的端点 Buffer（共享单位 quad 是静态缓存的），draw calls 恒定个位数。
  List<Widget> _buildEdgeBatches(WeaveSceneLayout layout) {
    if (layout.nodes.isEmpty) return const [];
    // 连线透明度按「3D 相机距离」取深度：buildWeaveEdges3D 用 depthNorm（近=1、远=0）作
    // closeness，而 2.5D 的 depthNorm 近/远与 3D 相机相反，故传入按 3D 取向重算的投影。
    final byId = _threeDEdgeProjections(layout);
    final edges = buildWeaveEdges3D(
      edges: widget.controller.edges,
      byId: byId,
      sphereRadius: _sphereRadius,
    );
    if (edges.isEmpty) return const [];
    final buckets = bucketWeaveEdges3D(edges, tiers: kWeaveEdgeRenderTiers);
    final batches = <Widget>[];
    for (var i = 0; i < buckets.length; i++) {
      final b = buckets[i];
      if (b.edges.isEmpty) continue;
      final positions = tierEdgePositions(b.edges);
      batches.add(SceneMesh(
        name: 'e:batch:$i',
        geometry: LineSegmentsGeometry(
          LineSegmentData(positions: positions),
          width: _edgeWidth3D,
        ),
        material: _edgeBatchMaterial(i, b.alpha),
      ));
    }
    return batches;
  }

  /// 3D 连线深度：把节点投影的 depthNorm 重算成「3D 相机距离」语义。
  ///
  /// 相机在 (0,0,-dist) 看向 +Z → uz=-1 最近、+1 最远；而 buildWeaveEdges3D 用 depthNorm
  /// （近=1、远=0，2.5D 语义）作 closeness，与 3D 相机近/远相反（会让近端连线反而更淡）。
  /// 这里用 [normalizeDepth](-uz) 让 near(uz=-1)→1.0、far(uz=+1)→0.12，与节点/泡深度一致，
  /// 连线呈现近实远虚。ux/uy/uz（世界坐标）不变。
  Map<int, WeaveNodeProjection> _threeDEdgeProjections(WeaveSceneLayout layout) {
    final byId = <int, WeaveNodeProjection>{};
    for (final l in layout.nodes) {
      byId[l.node.id] = WeaveNodeProjection(
        node: l.node,
        x: l.x,
        y: l.y,
        scale: l.scale,
        depth: l.depth,
        depthNorm: normalizeDepth(-l.uz),
        ux: l.ux,
        uy: l.uy,
        uz: l.uz,
      );
    }
    return byId;
  }

  // ── pop-in 缩放 ──

  double _popScale(int index) {
    final t = _popInCtrl.value;
    const step = 0.03;
    final start = index * step;
    final span = 1.0 - start;
    if (span <= 0) return 1.0;
    final local = ((t - start) / span).clamp(0.0, 1.0);
    return _easeOutBack(local);
  }

  double _easeOutBack(double x) {
    const c1 = 1.70158;
    const c3 = c1 + 1;
    final t = x - 1;
    return 1 + c3 * t * t * t + c1 * t * t;
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _repaintListenable,
      builder: (context, _) {
        return LayoutBuilder(builder: (context, constraints) {
          final size = Size(constraints.maxWidth, constraints.maxHeight);
          final layout = widget.controller.project(size);
          final zoom = widget.controller.zoom;
          // 双指缩放 → 相机距离（越大越近，近大远小）；钳制避免穿过近平面/贴脸。
          // 基准 6.0→5.0（球体占屏更饱满，配合节点半径 0.09 让卡片文字更可读）。
          final dist = (5.0 / zoom).clamp(2.8, 12.0);
          final camera = PerspectiveCamera(
            position: Vector3(0, 0, -dist),
            target: Vector3.zero(),
          );
          _camera = camera;
          _size = size;
          _scene ??= Scene();

          // 性能分级（light 档用低细分共享球体；节点球一律纯色——终局改造后球面不再贴卡片纹理）。
          final isLight = _renderDegrader.tier == WeaveRenderTier.light;

          // 展开聚类的成员节点 + 收起泡的成员都不单独显示；这里按当前布局逐个落位。
          final meshNodes = <Widget>[
            for (var i = 0; i < layout.nodes.length; i++)
              _buildNodeMesh(layout.nodes[i], i, light: isLight),
            for (final b in layout.bubbles) _buildBubbleMesh(b),
            // 3D 连线（场景资源就绪后才构建；按透明度分档的 LineSegmentsGeometry 批量）。
            if (_sceneReady) ..._buildEdgeBatches(layout),
          ];

          // A' 法线方向标签（屏幕层投影；纯数据，交给上层 WeaveLabelPainter 绘制）。
          final labels = buildWeaveLabels(
            camera: camera,
            nodes: layout.nodes,
            size: size,
            sphereRadius: _sphereRadius,
            nodeRadius: _nodeRadius,
          );

          // 深空背景（固定深空色，不随深浅主题）→ Scene（默认透明清屏，星空透出）→ 标签覆盖层。
          return Stack(
            fit: StackFit.expand,
            children: [
              CustomPaint(painter: WeaveSpaceBackgroundPainter(zoom: zoom)),
              GestureDetector(
                onScaleStart: _onScaleStart,
                onScaleUpdate: _onScaleUpdate,
                onScaleEnd: _onScaleEnd,
                onTapUp: _onTapUp,
                child: SceneView(
                  _scene!,
                  camera: camera,
                  autoTick: false,
                  children: meshNodes,
                ),
              ),
              if (labels.isNotEmpty)
                Positioned.fill(
                  child: IgnorePointer(
                    child: CustomPaint(painter: WeaveLabelPainter(labels: labels)),
                  ),
                ),
            ],
          );
        });
      },
    );
  }

  Widget _buildNodeMesh(WeaveNodeProjection p, int index,
      {required bool light}) {
    final selected = p.node.id == _selectedId;
    final baseScale = _popScale(index);
    // 选中高亮：略放大（配合材质提亮）
    final s = baseScale * (selected ? 1.45 : 1.0);
    // light 档用低细分共享几何体（8/6）以减负；full 档用 12/8（保球面圆滑）。
    final geometry = light ? _lightNodeGeometry : _nodeGeometry;
    return SceneMesh(
      name: 'n:${p.node.id}',
      geometry: geometry,
      material: _nodeMaterial(p, selected: selected),
      position: Vector3(
        p.ux * _sphereRadius,
        p.uy * _sphereRadius,
        p.uz * _sphereRadius,
      ),
      scale: Vector3(s, s, s),
    );
  }

  Widget _buildBubbleMesh(WeaveBubbleProjection b) {
    return SceneMesh(
      name: 'b:${b.clusterId}',
      geometry: _bubbleGeometry,
      material: _bubbleMaterial(b),
      position: Vector3(
        b.ux * _sphereRadius,
        b.uy * _sphereRadius,
        b.uz * _sphereRadius,
      ),
    );
  }
}
