// F7-c-5（2026-09-01）自 screens/weave/weave_scene_view.dart 拆分迁入；逻辑逐字节保持。

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart' show Ticker;

import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
import 'weave_scene_painters.dart';
import 'weave_canvas_painter.dart' show WeaveCanvasPainter;

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
