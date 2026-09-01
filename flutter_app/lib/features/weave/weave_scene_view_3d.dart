// F7-c-5（2026-09-01）自 screens/weave/weave_scene_view.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:math' as math;

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
import 'package:ai_companion/l10n/app_localizations.dart';
import 'weave_scene_painters.dart';

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
            unnamed: AppLocalizations.of(context)!.unnamed,
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
