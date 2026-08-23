import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../utils/sphere_projection.dart';
import '../../widgets/weave_detail_sheet.dart';
import "package:ai_companion/theme/tokens.dart";

/// 织库 · 无限画布（Phase B/C，2026-08-12；2.5D 加强 2026-08-23）
/// 2.5D 球面投影：拖动旋转 + 惯性 + 节点抖动；双指缩放（透视半径变化）；
/// 时间/心情/角色维度筛选 chips；真透视除法 + 深度归一化 + 背面淡化 + 连线随深度淡化；
/// 卡片 >80 走球面聚类（聚类泡），不再降级 2D 螺旋（聚类失败才兜底螺旋）。
class WeaveCanvasScreen extends StatefulWidget {
  final int? initialCharacterId;
  /// 织库双域（2026-08-12）：shared=全·织库 / private=私·织库
  final String domain;

  const WeaveCanvasScreen({super.key, this.initialCharacterId, this.domain = 'shared'});

  @override
  State<WeaveCanvasScreen> createState() => _WeaveCanvasScreenState();
}

class _CanvasNode {
  final int id;
  final int characterId;
  final List<int> characterIds;
  final String characterName;
  final String title;
  final String summary;
  final double importance;
  final String mood;
  final DateTime? createdAt;
  final double lat;
  final double lon;
  /// 私域增强（Phase 3）：生活类型 life_event/reflection/note 与命中的兴趣热标签
  final String lifeType;
  final List<String> hotTags;

  _CanvasNode({
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

class _CanvasEdge {
  final int source;
  final int target;
  final double strength;

  _CanvasEdge(
      {required this.source, required this.target, required this.strength});
}

class _NodeLayout {
  final _CanvasNode node;
  final double x;
  final double y;
  final double scale;
  final double depth;
  /// 归一化深度 [0,1]（近=1、远=0），供缩放/字号/透明度单调变化
  final double depthNorm;

  _NodeLayout(this.node, this.x, this.y, this.scale, this.depth,
      [this.depthNorm = 0.5]);
}

/// 球面聚类产生的「聚类泡」布局（织网 2.5D 加强，2026-08-23）
class _BubbleLayout {
  final int clusterId;
  final List<_CanvasNode> members;
  final double x;
  final double y;
  final double scale;
  final double depth;
  final double depthNorm;
  /// true=收起（实心泡）；false=展开（淡环，仍可点按收起）
  final bool collapsed;

  _BubbleLayout({
    required this.clusterId,
    required this.members,
    required this.x,
    required this.y,
    required this.scale,
    required this.depth,
    required this.depthNorm,
    required this.collapsed,
  });
}

class _CharacterMeta {
  final int id;
  final String name;

  _CharacterMeta(this.id, this.name);
}

const _palette = <Color>[
  AppColors.accent,
  AppColors.success,
  Color(0xFFFF9500),
  Color(0xFFFF2D55),
  Color(0xFFAF52DE),
  Color(0xFF00C7BE),
  Color(0xFFFFCC00),
  Color(0xFF5AC8FA),
];

class _WeaveCanvasScreenState extends State<WeaveCanvasScreen>
    with TickerProviderStateMixin {
  final ApiClient _api = ApiClient();

  List<_CanvasNode> _nodes = [];
  List<_CanvasEdge> _edges = [];
  List<_CharacterMeta> _characters = [];
  bool _loading = true;
  String? _error;

  double _phi = 0.0;
  double _theta = 0.35;
  double _vPhi = 0.0;
  double _vTheta = 0.0;
  double _zoom = 1.0;
  double _baseZoom = 1.0;
  double _lastDx = 0.0;
  double _lastDy = 0.0;

  // 织网 2.5D 立体感加强（2026-08-23）
  /// 透视强度（0.5~0.6），配合真透视除法让近/远尺寸比更明显
  static const double _perspective = 0.55;
  /// 球面聚类缓存：仅当筛选后的节点集变化时才重算（避免每帧重算）
  List<SphereCluster>? _clusterCache;
  String? _clusterKey;
  /// 已展开的聚类泡（clusterId），收起态/展开态可点按切换
  final Set<int> _expandedClusters = <int>{};

  // 维度筛选：时间（all/7d/30d）、角色（null=全部）、心情（null=全部）
  String _timeFilter = 'all';
  int? _charFilter;
  String? _moodFilter;
  /// 私域增强：生活类型筛选（null=全部）
  String? _lifeTypeFilter;

  late final AnimationController _jitterCtrl;
  late final AnimationController _inertiaCtrl;

  @override
  void initState() {
    super.initState();
    _charFilter = widget.initialCharacterId;
    _jitterCtrl =
        AnimationController(vsync: this, duration: const Duration(seconds: 5))
          ..repeat();
    _inertiaCtrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 16))
      ..addListener(_tickInertia);
    _loadGraph();
  }

  @override
  void dispose() {
    _jitterCtrl.dispose();
    _inertiaCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadGraph() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await _api.getWeaveGraph(domain: widget.domain);
      if (!mounted) return;
      final nodesJson = (data['nodes'] as List?) ?? const [];
      final edgesJson = (data['edges'] as List?) ?? const [];
      final charsJson = (data['characters'] as List?) ?? const [];
      final pts = fibonacciSphere(nodesJson.length);
      final nodes = <_CanvasNode>[];
      for (var i = 0; i < nodesJson.length; i++) {
        final j = nodesJson[i] as Map<String, dynamic>;
        final cid = j['character_id'] as int? ?? 0;
        final cids =
            (j['character_ids'] as List?)?.map((e) => e as int).toList();
        nodes.add(
          _CanvasNode(
            id: j['id'] as int,
            characterId: cid,
            characterIds: (cids != null && cids.isNotEmpty) ? cids : [cid],
            characterName: j['character_name'] as String? ?? '',
            title: j['title'] as String? ?? '',
            summary: j['summary'] as String? ?? '',
            importance: (j['importance'] as num?)?.toDouble() ?? 0,
            mood: j['mood'] as String? ?? '',
            createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
            lat: pts[i].lat,
            lon: pts[i].lon,
            lifeType: j['life_type'] as String? ?? '',
            hotTags: (j['hot_tags'] as List?)?.map((e) => e.toString()).toList() ?? const [],
          ),
        );
      }
      final edges = <_CanvasEdge>[
        for (final e in edgesJson)
          _CanvasEdge(
            source: (e as Map<String, dynamic>)['source'] as int,
            target: e['target'] as int,
            strength: (e['strength'] as num?)?.toDouble() ?? 0,
          ),
      ];
      final characters = <_CharacterMeta>[
        for (final c in charsJson)
          _CharacterMeta(
            (c as Map<String, dynamic>)['id'] as int,
            c['name'] as String? ?? AppLocalizations.of(context)!.memorySourceCharacter,
          ),
      ];
      if (!mounted) return;
      setState(() {
        _nodes = nodes;
        _edges = edges;
        _characters = characters;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.weaveLoadFail;
        _loading = false;
      });
    }
  }

  /// 当前筛选后的节点（时间/角色/心情维度）
  List<_CanvasNode> _filteredNodes() {
    final now = DateTime.now().toUtc();
    return _nodes.where((n) {
      if (_charFilter != null && !n.characterIds.contains(_charFilter)) {
        return false;
      }
      if (_moodFilter != null && n.mood.trim() != _moodFilter) {
        return false;
      }
      if (_timeFilter != 'all' && n.createdAt != null) {
        final days = now.difference(n.createdAt!).inDays;
        final limit = _timeFilter == '7d' ? 7 : 30;
        if (days > limit) return false;
      }
      if (_lifeTypeFilter != null && n.lifeType != _lifeTypeFilter) {
        return false;
      }
      return true;
    }).toList();
  }

  List<String> _distinctMoods() {
    final set = <String>{};
    for (final n in _nodes) {
      final m = n.mood.trim();
      if (m.isNotEmpty && m != '不详') set.add(m);
    }
    final list = set.toList()..sort();
    return list;
  }

  void _tickInertia() {
    _vPhi *= 0.94;
    _vTheta *= 0.94;
    _phi += _vPhi;
    _theta = (_theta + _vTheta).clamp(-1.35, 1.35).toDouble();
    if (_vPhi.abs() < 0.0003 && _vTheta.abs() < 0.0003) {
      _inertiaCtrl.stop();
    }
    setState(() {});
  }

  void _onScaleStart(ScaleStartDetails d) {
    _inertiaCtrl.stop();
    _baseZoom = _zoom;
  }

  void _onScaleUpdate(ScaleUpdateDetails d) {
    _lastDx = d.focalPointDelta.dx;
    _lastDy = d.focalPointDelta.dy;
    setState(() {
      _phi += d.focalPointDelta.dx / 220.0;
      _theta =
          (_theta + d.focalPointDelta.dy / 220.0).clamp(-1.35, 1.35).toDouble();
      _zoom = (_baseZoom * d.scale).clamp(0.3, 3.5).toDouble();
    });
  }

  void _onScaleEnd(ScaleEndDetails d) {
    _vPhi = _lastDx / 220.0 * 8.0;
    _vTheta = _lastDy / 220.0 * 8.0;
    if (_vPhi.abs() > 0.001 || _vTheta.abs() > 0.001) {
      _inertiaCtrl.repeat();
    }
  }

  /// 布局：≤80 逐个球面投影；>80 走球面聚类（聚类失败兜底 2D 螺旋）。
  /// 返回可见节点布局 + 聚类泡布局；聚类结果缓存、仅在节点集变化时重算。
  ({List<_NodeLayout> nodes, List<_BubbleLayout> bubbles}) _computeLayout(
      Size size) {
    final nodes = _filteredNodes();
    final cx = size.width / 2;
    final cy = size.height / 2;
    final radius = math.min(size.width, size.height) * 0.34 * _zoom;
    final nodeLayouts = <_NodeLayout>[];
    final bubbleLayouts = <_BubbleLayout>[];

    if (nodes.length <= kWeaveDirectSphereMax) {
      for (final n in nodes) {
        final p = projectSphere(
          n.lat,
          n.lon,
          phi: _phi,
          theta: _theta,
          cx: cx,
          cy: cy,
          radius: radius,
          perspective: _perspective,
        );
        nodeLayouts.add(
            _NodeLayout(n, p.x, p.y, p.scale, p.depth, p.depthNorm));
      }
      return (nodes: nodeLayouts, bubbles: bubbleLayouts);
    }

    // 高密度：球面聚类（根治 >80 被拍平 2D）
    final clusters = _getClusters(nodes);
    if (clusters == null || clusters.isEmpty) {
      // 聚类失败兜底：保留原有 2D 螺旋
      return (nodes: _spiralLayout(nodes, cx, cy, radius), bubbles: const []);
    }
    for (var ci = 0; ci < clusters.length; ci++) {
      final cl = clusters[ci];
      final expanded = _expandedClusters.contains(ci);
      final pc = projectSphere(
        cl.lat,
        cl.lon,
        phi: _phi,
        theta: _theta,
        cx: cx,
        cy: cy,
        radius: radius,
        perspective: _perspective,
      );
      final memberNodes = [for (final i in cl.members) nodes[i]];
      bubbleLayouts.add(_BubbleLayout(
        clusterId: ci,
        members: memberNodes,
        x: pc.x,
        y: pc.y,
        scale: pc.scale,
        depth: pc.depth,
        depthNorm: pc.depthNorm,
        collapsed: !expanded,
      ));
      if (expanded) {
        for (final n in memberNodes) {
          final p = projectSphere(
            n.lat,
            n.lon,
            phi: _phi,
            theta: _theta,
            cx: cx,
            cy: cy,
            radius: radius,
            perspective: _perspective,
          );
          nodeLayouts.add(
              _NodeLayout(n, p.x, p.y, p.scale, p.depth, p.depthNorm));
        }
      }
    }
    return (nodes: nodeLayouts, bubbles: bubbleLayouts);
  }

  /// 获取（并缓存）当前筛选节点的球面聚类；仅当节点集变化时重算，
  /// 避免每个绘制帧重复聚类（CustomPaint 性能可控）。
  List<SphereCluster>? _getClusters(List<_CanvasNode> nodes) {
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
  List<_NodeLayout> _spiralLayout(
      List<_CanvasNode> nodes, double cx, double cy, double radius) {
    return [
      for (var i = 0; i < nodes.length; i++)
        () {
          final t = i / math.max(1, nodes.length);
          final angle = i * 2.39996;
          final rad = radius * math.sqrt(t);
          return _NodeLayout(nodes[i], cx + math.cos(angle) * rad,
              cy + math.sin(angle) * rad, 1.0, 0.0, 0.5);
        }(),
    ];
  }

  void _toggleCluster(int clusterId) {
    setState(() {
      if (_expandedClusters.contains(clusterId)) {
        _expandedClusters.remove(clusterId);
      } else {
        _expandedClusters.add(clusterId);
      }
    });
  }

  void _onTapUp(TapUpDetails d, Size size) {
    final layout = _computeLayout(size);
    final pos = d.localPosition;
    // 1) 命中可见节点：最近优先 → 打开详情（原有交互保留）
    _CanvasNode? best;
    var bestDist = double.infinity;
    for (final l in layout.nodes) {
      final dx = l.x - pos.dx;
      final dy = l.y - pos.dy;
      final dist = math.sqrt(dx * dx + dy * dy);
      final hitR = 30.0 * l.scale;
      if (dist < hitR && dist < bestDist) {
        bestDist = dist;
        best = l.node;
      }
    }
    if (best != null) {
      _openNode(best);
      return;
    }
    // 2) 命中聚类泡（收起=实心泡，展开=淡环）：切换展开/收起
    _BubbleLayout? bestBubble;
    var bestBDist = double.infinity;
    for (final b in layout.bubbles) {
      final dx = b.x - pos.dx;
      final dy = b.y - pos.dy;
      final dist = math.sqrt(dx * dx + dy * dy);
      final hitR = (b.collapsed ? 28.0 : 22.0) * b.scale;
      if (dist < hitR && dist < bestBDist) {
        bestBDist = dist;
        bestBubble = b;
      }
    }
    if (bestBubble != null) _toggleCluster(bestBubble.clusterId);
  }

  Future<void> _openNode(_CanvasNode node) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final detail = await _api.getWeaveCardDetail(node.id);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (_) => WeaveDetailSheet(
          card: detail,
          onDelete: () async {
            try {
              await _api.deleteWeaveCard(node.id);
              if (mounted) await _loadGraph();
            } catch (_) {
              if (mounted) {
                ScaffoldMessenger.of(context)
                    .showSnackBar(SnackBar(content: Text(l10n.deleteFail)));
              }
            }
          },
        ),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.weaveDetailLoadFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        centerTitle: true,
        title: Text(
          l10n.weaveCanvasTitle,
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
        ),
        actions: [
          IconButton(
            tooltip: l10n.refresh,
            icon: const Icon(Icons.refresh, color: AppColors.accent),
            onPressed: _loadGraph,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    final l10n = AppLocalizations.of(context)!;
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              _error!,
              style: const TextStyle(fontSize: 14, color: AppColors.textSecondary),
            ),
            TextButton(onPressed: _loadGraph, child: Text(l10n.retry)),
          ],
        ),
      );
    }
    if (_nodes.isEmpty) {
      return Center(
        child: Text(
          l10n.weaveNoCards,
          style: const TextStyle(fontSize: 14, color: AppColors.textSecondary),
        ),
      );
    }
    return Column(
      children: [
        _buildFilterBar(),
        Expanded(
          child: LayoutBuilder(
            builder: (context, constraints) {
              final size = Size(constraints.maxWidth, constraints.maxHeight);
              final layout = _computeLayout(size);
              return GestureDetector(
                onScaleStart: _onScaleStart,
                onScaleUpdate: _onScaleUpdate,
                onScaleEnd: _onScaleEnd,
                onTapUp: (d) => _onTapUp(d, size),
                child: CustomPaint(
                  size: size,
                  painter: _WeaveCanvasPainter(
                    nodes: layout.nodes,
                    bubbles: layout.bubbles,
                    edges: _edges,
                    jitterT: _jitterCtrl.value * 5.0,
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }

  bool get _isPrivate => widget.domain == 'private';

  Widget _buildFilterBar() {
    final l10n = AppLocalizations.of(context)!;
    final moods = _distinctMoods();
    final showCharDivider = _characters.isNotEmpty;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        child: Row(
          children: [
            _chip(l10n.emotionAll, _timeFilter == 'all',
                () => setState(() => _timeFilter = 'all')),
            _chip(l10n.weaveNear7Days, _timeFilter == '7d',
                () => setState(() => _timeFilter = '7d')),
            _chip(l10n.weaveNear30Days, _timeFilter == '30d',
                () => setState(() => _timeFilter = '30d')),
            _separator(),
            _chip(l10n.weaveAllCharacters, _charFilter == null,
                () => setState(() => _charFilter = null)),
            for (final c in _characters)
              _chip(c.name, _charFilter == c.id,
                  () => setState(() => _charFilter = c.id)),
            if (showCharDivider) _separator(),
            _chip(l10n.weaveAllMoods, _moodFilter == null,
                () => setState(() => _moodFilter = null)),
            for (final m in moods)
              _chip(m, _moodFilter == m, () => setState(() => _moodFilter = m)),
            if (_isPrivate) ...[
              _separator(),
              _chip(l10n.weaveAllTypes, _lifeTypeFilter == null,
                  () => setState(() => _lifeTypeFilter = null)),
              _chip(l10n.lifeTypeLife, _lifeTypeFilter == 'life_event',
                  () => setState(() => _lifeTypeFilter = 'life_event')),
              _chip(l10n.lifeTypeReflection, _lifeTypeFilter == 'reflection',
                  () => setState(() => _lifeTypeFilter = 'reflection')),
              _chip(l10n.artifactNote, _lifeTypeFilter == 'note',
                  () => setState(() => _lifeTypeFilter = 'note')),
            ],
          ],
        ),
      ),
    );
  }

  Widget _separator() {
    return Container(
      width: 1,
      height: 16,
      margin: const EdgeInsets.symmetric(horizontal: 8),
      color: const Color(0xFFE5E5EA),
    );
  }

  Widget _chip(String label, bool selected, VoidCallback onTap) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: ChoiceChip(
        label: Text(label,
            style: TextStyle(
                fontSize: 11.5,
                color: selected ? Colors.white : AppColors.textGray)),
        selected: selected,
        onSelected: (_) => onTap(),
        showCheckmark: false,
        visualDensity: VisualDensity.compact,
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        backgroundColor: AppColors.bgLight,
        selectedColor: AppColors.accent,
        side: BorderSide.none,
      ),
    );
  }
}

class _WeaveCanvasPainter extends CustomPainter {
  final List<_NodeLayout> nodes;
  final List<_BubbleLayout> bubbles;
  final List<_CanvasEdge> edges;
  final double jitterT;

  _WeaveCanvasPainter({
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
          ..strokeWidth = 1.2,
      );
    }

    // 汇总绘制单元：远处先画（depth 升序）→ 前端置于最上层（画家算法）
    final draws = <({double depth, _NodeLayout? node, _BubbleLayout? bubble})>[
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

  void _drawNode(Canvas canvas, _NodeLayout l) {
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
          color: AppColors.textPrimary.withValues(alpha: opacity),
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final cardW = math.max(tp.width, 42.0 * s) + 10.0 * s;
    final cardH = tp.height + 9.0 * s;
    final color = _palette[l.node.characterId % _palette.length];
    // 私域增强：生活类型着色（生活=蓝/反思=紫/笔记=绿），热标签节点描边加亮
    final lifeColor = l.node.lifeType == 'reflection'
        ? const Color(0xFFAF52DE)
        : l.node.lifeType == 'note'
            ? AppColors.success
            : color;
    final isHot = l.node.hotTags.isNotEmpty;
    final rrect = RRect.fromRectAndRadius(
      Rect.fromCenter(
        center: Offset(x, y),
        width: cardW,
        height: cardH,
      ),
      Radius.circular(9 * s),
    );
    // 填充：随深度衰减（近实、远虚）
    canvas.drawRRect(
      rrect,
      Paint()
        ..color = lifeColor.withValues(alpha: (0.12 + 0.06 * s) * opacity),
    );
    // 描边：随深度衰减（正面亮、背面淡）
    canvas.drawRRect(
      rrect,
      Paint()
        ..color = lifeColor.withValues(alpha: (isHot ? 0.95 : 0.55) * opacity)
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

  void _drawBubble(Canvas canvas, _BubbleLayout b) {
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
    final color = _palette[b.clusterId % _palette.length];
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
  bool shouldRepaint(covariant _WeaveCanvasPainter oldDelegate) => true;
}
