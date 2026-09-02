// 织网 3D · 渲染层接口抽象 —— WeaveSceneController（纯逻辑，可测）
//
// 2026-08-24（织网 3D P0）：把原 weave_canvas_screen.dart 里的画布状态与布局逻辑
// 拆成可复用/可测的纯逻辑控制器（节点/边/布局缓存/旋转/缩放/抖动速度/命中/聚类泡）；
// 2D 与 3D 两个视图共用同一套 node 数据与旋转/缩放/命中语义，避免双份逻辑漂移。
//
// 说明：本文件只依赖 dart:math / dart:ui（Size/Offset）与 utils/sphere_projection.dart 的
// 纯函数，不依赖 Flutter 渲染（CustomPaint/SceneView/材质），保持可单测。
import 'dart:math' as math;
import 'dart:ui' show Offset, Size;

import 'package:flutter/foundation.dart' show ChangeNotifier;

import 'package:ai_companion/utils/sphere_projection.dart';

/// 织网节点模型（画布/3D 视图共用）。
class WeaveSceneNode {
  final int id;
  final int characterId;
  final List<int> characterIds;
  final String characterName;
  final String title;
  final String summary;
  final double importance;
  final String mood;
  final DateTime? createdAt;
  final double lat; // 纬度（弧度）
  final double lon; // 经度（弧度）
  /// 私域增强（Phase 3）：生活类型 life_event/reflection/note 与命中兴趣热标签
  final String lifeType;
  final List<String> hotTags;

  const WeaveSceneNode({
    required this.id,
    required this.characterId,
    required this.characterIds,
    this.characterName = '',
    required this.title,
    required this.summary,
    required this.importance,
    this.mood = '',
    this.createdAt,
    required this.lat,
    required this.lon,
    this.lifeType = '',
    this.hotTags = const [],
  });
}

/// 织网连线模型。
class WeaveSceneEdge {
  final int source;
  final int target;
  final double strength;

  const WeaveSceneEdge({
    required this.source,
    required this.target,
    required this.strength,
  });
}

/// 单个节点投影结果：2D 屏幕坐标（供 CustomPaint）+ 旋转后的单位球坐标（供 3D）。
class WeaveNodeProjection {
  final WeaveSceneNode node;
  final double x;
  final double y;
  final double scale;
  final double depth;
  /// 归一化深度 [0,1]（近=1、远=0），供缩放/字号/透明度单调变化
  final double depthNorm;
  /// 经 phi/theta 旋转后的单位球坐标分量（3D 视图落地点）
  final double ux;
  final double uy;
  final double uz;

  const WeaveNodeProjection({
    required this.node,
    required this.x,
    required this.y,
    required this.scale,
    required this.depth,
    required this.depthNorm,
    required this.ux,
    required this.uy,
    required this.uz,
  });
}

/// 球面聚类产生的「聚类泡」投影结果（2D 坐标 + 3D 单位坐标）。
class WeaveBubbleProjection {
  final int clusterId;
  final List<WeaveSceneNode> members;
  final double x;
  final double y;
  final double scale;
  final double depth;
  final double depthNorm;
  final double ux;
  final double uy;
  final double uz;
  /// true=收起（实心泡）；false=展开（淡环，仍可点按收起）
  final bool collapsed;

  const WeaveBubbleProjection({
    required this.clusterId,
    required this.members,
    required this.x,
    required this.y,
    required this.scale,
    required this.depth,
    required this.depthNorm,
    required this.ux,
    required this.uy,
    required this.uz,
    required this.collapsed,
  });
}

/// 一帧的完整布局（节点投影 + 聚类泡投影）。
class WeaveSceneLayout {
  final List<WeaveNodeProjection> nodes;
  final List<WeaveBubbleProjection> bubbles;

  const WeaveSceneLayout({required this.nodes, required this.bubbles});
}

/// 织网画布逻辑控制器（纯逻辑，不依赖 Flutter 渲染）。
///
/// 持有：节点/边列表、旋转角（phi/theta）、缩放；并提供
/// `rotate` / `zoom` / `project` / `hitTest` / 聚类泡切换 等纯逻辑方法。
/// 视图（2D/3D）只负责把 `project(size)` 的结果画出来，把手势转成 `rotate/zoom`。
class WeaveSceneController extends ChangeNotifier {
  /// 透视强度（0.5~0.6），与 2.5D 一致，让近/远尺寸比更明显
  static const double perspective = 0.55;

  /// 当前可见节点（已由画布页按筛选维度过滤）。
  List<WeaveSceneNode> _nodes = const [];
  List<WeaveSceneNode> get nodes => _nodes;

  List<WeaveSceneEdge> _edges = const [];
  List<WeaveSceneEdge> get edges => _edges;

  double _phi = 0.0;
  double _theta = 0.35;
  double _zoom = 1.0;

  /// theta（仰角）旋转限幅上界（弧度）。
  ///
  /// - 默认 ±1.35（约 ±77°）为保守兜底（视图尚未挂载/未显式放开时）；
  /// - 2.5D 与 3D 视图挂载时都放开（设为 null），可自由旋转翻到球背面，惯性同样按放开范围衰减
  ///   （低端机体验修复：2.5D 不再卡在 ±77°，可自由翻转）。
  /// 通过 [setThetaLimit] 设置（视图挂载时按 2D/3D 统一放开），避免共享同一控制器时语义漂移。
  double? thetaLimit = 1.35;

  /// 手势起始时的基准缩放（双指缩放参考点）
  double _baseZoom = 1.0;

  double _vPhi = 0.0;
  double _vTheta = 0.0;
  double _lastDx = 0.0;
  double _lastDy = 0.0;

  /// 球面聚类缓存：仅当筛选后的节点集变化时才重算（避免每帧重算）
  List<SphereCluster>? _clusterCache;
  String? _clusterKey;
  /// 已展开的聚类泡（clusterId），收起态/展开态可点按切换
  final Set<int> _expandedClusters = <int>{};

  double get phi => _phi;
  double get theta => _theta;
  double get zoom => _zoom;

  WeaveSceneController();

  /// 设置可见节点与边；节点集变化会重置聚类缓存/展开态。
  void setGraph({
    required List<WeaveSceneNode> nodes,
    List<WeaveSceneEdge> edges = const [],
  }) {
    _nodes = nodes;
    _edges = edges;
    _clusterCache = null;
    _clusterKey = null;
    _expandedClusters.clear();
    notifyListeners();
  }

  /// 仅改节点（筛选变化），保留当前旋转/缩放视角。
  void setNodes(List<WeaveSceneNode> nodes) {
    _nodes = nodes;
    _clusterCache = null;
    _clusterKey = null;
    _expandedClusters.clear();
    notifyListeners();
  }

  // ── 手势：旋转 / 缩放 / 惯性 ──

  /// 拖动旋转：delta 为指针位移（px），按现有手感换算成角度增量。
  /// theta 按 [thetaLimit] 限幅（默认 ±1.35；3D 放开为 null 时不限）。
  void rotate(double dx, double dy) {
    _phi += dx / 220.0;
    _theta = _applyThetaLimit(_theta + dy / 220.0);
    notifyListeners();
  }

  /// 双指缩放开：记录基准缩放（从当前缩放开始），并停掉惯性。
  void onScaleStart() {
    _vPhi = 0.0;
    _vTheta = 0.0;
    _baseZoom = _zoom;
  }

  /// 双指缩放更新：scale 为相对手势起点的倍率。
  void updateScale(double scale) {
    _zoom = (_baseZoom * scale).clamp(0.3, 3.5).toDouble();
    notifyListeners();
  }

  /// 拖动结束（记录最后位移用于惯性速度）；返回是否应启动惯性。
  bool onScaleEnd() {
    _vPhi = _lastDx / 220.0 * 8.0;
    _vTheta = _lastDy / 220.0 * 8.0;
    return _vPhi.abs() > 0.001 || _vTheta.abs() > 0.001;
  }

  /// 惯性衰减一帧：更新 phi/theta；返回是否仍在滑动（供调速器继续 tick）。
  bool tickInertia() {
    _vPhi *= 0.94;
    _vTheta *= 0.94;
    _phi += _vPhi;
    _theta = _applyThetaLimit(_theta + _vTheta);
    notifyListeners();
    return _vPhi.abs() >= 0.0003 || _vTheta.abs() >= 0.0003;
  }

  /// 设置 theta 限幅并立即对当前 theta 重新钳制（如 3D→2.5D 切换时把拉出范围的角度
  /// 收回 ±[thetaLimit]，避免切换到 2.5D 后出现镜像/翻转怪异的投影）。
  void setThetaLimit(double? limit) {
    thetaLimit = limit;
    _theta = _applyThetaLimit(_theta);
    notifyListeners();
  }

  /// 对 theta 应用当前限幅：un-limit（null）原样返回，否则钳制到 ±limit。
  double _applyThetaLimit(double t) {
    final lim = thetaLimit;
    if (lim == null) return t;
    return t.clamp(-lim, lim).toDouble();
  }

  /// 供视图在 onScaleUpdate 记录最后位移（供 onScaleEnd 计算惯性速度）。
  void trackDrag(double dx, double dy) {
    _lastDx = dx;
    _lastDy = dy;
  }

  // ── 布局 / 命中 ──

  /// 计算当前可见节点的完整布局（≤80 逐个球面投影；>80 走球面聚类，
  /// 聚类失败兜底 2D 螺旋）。聚类结果缓存、仅在节点集变化时重算。
  WeaveSceneLayout project(Size size) {
    if (_nodes.isEmpty) return const WeaveSceneLayout(nodes: [], bubbles: []);
    final cx = size.width / 2;
    final cy = size.height / 2;
    final radius = math.min(size.width, size.height) * 0.34 * _zoom;
    final nodeLayouts = <WeaveNodeProjection>[];
    final bubbleLayouts = <WeaveBubbleProjection>[];

    if (_nodes.length <= kWeaveDirectSphereMax) {
      for (final n in _nodes) {
        nodeLayouts.add(_projectNode(n, cx, cy, radius));
      }
      return WeaveSceneLayout(nodes: nodeLayouts, bubbles: bubbleLayouts);
    }

    // 高密度：球面聚类（根治 >80 被拍平 2D）
    final clusters = _getClusters(_nodes);
    if (clusters == null || clusters.isEmpty) {
      // 聚类失败兜底：保留原有 2D 螺旋
      return WeaveSceneLayout(
        nodes: _spiralLayout(_nodes, cx, cy, radius),
        bubbles: const [],
      );
    }
    for (var ci = 0; ci < clusters.length; ci++) {
      final cl = clusters[ci];
      final expanded = _expandedClusters.contains(ci);
      final pc = _projectRaw(cl.lat, cl.lon, cx, cy, radius);
      final memberNodes = [for (final i in cl.members) _nodes[i]];
      bubbleLayouts.add(WeaveBubbleProjection(
        clusterId: ci,
        members: memberNodes,
        x: pc.x,
        y: pc.y,
        scale: pc.scale,
        depth: pc.depth,
        depthNorm: pc.depthNorm,
        ux: pc.ux,
        uy: pc.uy,
        uz: pc.uz,
        collapsed: !expanded,
      ));
      if (expanded) {
        for (final n in memberNodes) {
          nodeLayouts.add(_projectNode(n, cx, cy, radius));
        }
      }
    }
    return WeaveSceneLayout(nodes: nodeLayouts, bubbles: bubbleLayouts);
  }

  WeaveNodeProjection _projectNode(WeaveSceneNode n, double cx, double cy,
      double radius) {
    final p = _projectRaw(n.lat, n.lon, cx, cy, radius);
    return WeaveNodeProjection(
      node: n,
      x: p.x,
      y: p.y,
      scale: p.scale,
      depth: p.depth,
      depthNorm: p.depthNorm,
      ux: p.ux,
      uy: p.uy,
      uz: p.uz,
    );
  }

  /// 球面坐标 → 旋转 → 2D 投影 + 旋转后单位球坐标（3D 用）。
  ({double x, double y, double scale, double depth, double depthNorm,
      double ux, double uy, double uz}) _projectRaw(
          double lat, double lon, double cx, double cy, double radius) {
    final c = sphereToCartesian(lat, lon);
    final r = rotatePoint(c.$1, c.$2, c.$3, _phi, _theta);
    final p = projectPoint(r.$1, r.$2, r.$3, cx, cy, radius,
        perspective: perspective);
    return (
      x: p.x,
      y: p.y,
      scale: p.scale,
      depth: p.depth,
      depthNorm: p.depthNorm,
      ux: r.$1,
      uy: r.$2,
      uz: r.$3,
    );
  }

  /// 获取（并缓存）当前筛选节点的球面聚类；仅当节点集变化时重算。
  List<SphereCluster>? _getClusters(List<WeaveSceneNode> nodes) {
    final key = nodes.map((n) => n.id).join(',');
    if (_clusterCache != null && _clusterKey == key) return _clusterCache;
    final pts = [for (final n in nodes) SpherePoint(n.lat, n.lon)];
    final result = clusterSphereBubbles(
      points: pts,
      targetClusters: kWeaveClusterTargetMax,
    );
    if (result.isEmpty) {
      _clusterCache = null;
      _clusterKey = null;
      return null;
    }
    // 节点集变化 → 原展开簇索引失效，重置展开态
    _expandedClusters.clear();
    _clusterCache = result;
    _clusterKey = key;
    return result;
  }

  /// 2D 螺旋（原 >80 降级分支），仅作为聚类失败时的兜底（scale=1.0 depth=0.0）。
  List<WeaveNodeProjection> _spiralLayout(
      List<WeaveSceneNode> nodes, double cx, double cy, double radius) {
    return [
      for (var i = 0; i < nodes.length; i++)
        () {
          final t = i / math.max(1, nodes.length);
          final angle = i * 2.39996;
          final rad = radius * math.sqrt(t);
          final n = nodes[i];
          final c = sphereToCartesian(n.lat, n.lon);
          return WeaveNodeProjection(
            node: n,
            x: cx + math.cos(angle) * rad,
            y: cy + math.sin(angle) * rad,
            scale: 1.0,
            depth: 0.0,
            depthNorm: 0.5,
            ux: c.$1,
            uy: c.$2,
            uz: c.$3,
          );
        }(),
    ];
  }

  /// 切换聚类泡展开/收起。
  void toggleCluster(int clusterId) {
    if (_expandedClusters.contains(clusterId)) {
      _expandedClusters.remove(clusterId);
    } else {
      _expandedClusters.add(clusterId);
    }
    notifyListeners();
  }

  /// 命中测试：返回 offset 处最近节点的 id（最近优先，命中半径随 scale 放大）。
  int? hitTest(Offset offset, Size size) {
    final layout = project(size);
    WeaveNodeProjection? best;
    var bestDist = double.infinity;
    for (final l in layout.nodes) {
      final dx = l.x - offset.dx;
      final dy = l.y - offset.dy;
      final dist = math.sqrt(dx * dx + dy * dy);
      final hitR = 30.0 * l.scale;
      if (dist < hitR && dist < bestDist) {
        bestDist = dist;
        best = l;
      }
    }
    return best?.node.id;
  }

  /// 命中聚类泡（收起=实心泡，展开=淡环）：返回命中的 clusterId；无则 null。
  /// 用于画布页在未命中节点时切换聚类泡展开态（与 2.5D 交互一致）。
  int? hitTestCluster(Offset offset, Size size) {
    final layout = project(size);
    WeaveBubbleProjection? best;
    var bestDist = double.infinity;
    for (final b in layout.bubbles) {
      final dx = b.x - offset.dx;
      final dy = b.y - offset.dy;
      final dist = math.sqrt(dx * dx + dy * dy);
      final hitR = (b.collapsed ? 28.0 : 22.0) * b.scale;
      if (dist < hitR && dist < bestDist) {
        bestDist = dist;
        best = b;
      }
    }
    return best?.clusterId;
  }
}
