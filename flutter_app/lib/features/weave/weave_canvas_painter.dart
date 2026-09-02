// F7-c-5（2026-09-01）自 features/weave/weave_scene_view.dart 拆分迁入；逻辑逐字节保持。
import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:ai_companion/theme/tokens.dart';
import 'package:ai_companion/utils/sphere_projection.dart';
import 'package:ai_companion/features/weave/weave_scene_controller.dart';
import 'package:ai_companion/features/weave/weave_card_texture.dart';


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
