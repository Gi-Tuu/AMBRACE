import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../utils/sphere_projection.dart';
import '../../widgets/weave_detail_sheet.dart';

/// 织库 · 无限画布（Phase B/C，2026-08-12）
/// 2.5D 球面投影：拖动旋转 + 惯性 + 节点抖动；双指缩放（透视半径变化）；
/// 时间/心情/角色维度筛选 chips；卡片 >80 降级 2D 螺旋布局保帧率。
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

  _NodeLayout(this.node, this.x, this.y, this.scale, this.depth);
}

class _CharacterMeta {
  final int id;
  final String name;

  _CharacterMeta(this.id, this.name);
}

const _palette = <Color>[
  Color(0xFF007AFF),
  Color(0xFF34C759),
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
            c['name'] as String? ?? '角色',
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
        _error = '画布加载失败，请重试';
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

  /// 布局：>80 张卡降级 2D 螺旋；否则球面投影（半径随缩放变化）
  List<_NodeLayout> _computeLayout(Size size) {
    final nodes = _filteredNodes();
    final cx = size.width / 2;
    final cy = size.height / 2;
    final radius = math.min(size.width, size.height) * 0.34 * _zoom;
    if (nodes.length > 80) {
      return [
        for (var i = 0; i < nodes.length; i++)
          () {
            final t = i / math.max(1, nodes.length);
            final angle = i * 2.39996;
            final rad = radius * math.sqrt(t);
            return _NodeLayout(
              nodes[i],
              cx + math.cos(angle) * rad,
              cy + math.sin(angle) * rad,
              1.0,
              0.0,
            );
          }(),
      ];
    }
    return [
      for (final n in nodes)
        () {
          final c = sphereToCartesian(n.lat, n.lon);
          final r = rotatePoint(c.$1, c.$2, c.$3, _phi, _theta);
          final p = projectPoint(r.$1, r.$2, r.$3, cx, cy, radius);
          return _NodeLayout(n, p.x, p.y, p.scale, p.depth);
        }(),
    ];
  }

  void _onTapUp(TapUpDetails d, Size size) {
    final layout = _computeLayout(size);
    _CanvasNode? best;
    var bestDist = double.infinity;
    for (final l in layout) {
      final dx = l.x - d.localPosition.dx;
      final dy = l.y - d.localPosition.dy;
      final dist = math.sqrt(dx * dx + dy * dy);
      final hitR = 30.0 * l.scale;
      if (dist < hitR && dist < bestDist) {
        bestDist = dist;
        best = l.node;
      }
    }
    if (best != null) _openNode(best);
  }

  Future<void> _openNode(_CanvasNode node) async {
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
                    .showSnackBar(const SnackBar(content: Text('删除失败')));
              }
            }
          },
        ),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('详情加载失败')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F7),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        centerTitle: true,
        title: const Text(
          '织库画布',
          style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
        ),
        actions: [
          IconButton(
            tooltip: '刷新',
            icon: const Icon(Icons.refresh, color: Color(0xFF007AFF)),
            onPressed: _loadGraph,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
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
              style: const TextStyle(fontSize: 14, color: Color(0xFF8E8E93)),
            ),
            TextButton(onPressed: _loadGraph, child: const Text('重试')),
          ],
        ),
      );
    }
    if (_nodes.isEmpty) {
      return const Center(
        child: Text(
          '还没有卡片，先去列表页整理生成吧',
          style: TextStyle(fontSize: 14, color: Color(0xFF8E8E93)),
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
              return GestureDetector(
                onScaleStart: _onScaleStart,
                onScaleUpdate: _onScaleUpdate,
                onScaleEnd: _onScaleEnd,
                onTapUp: (d) => _onTapUp(d, size),
                child: CustomPaint(
                  size: size,
                  painter: _WeaveCanvasPainter(
                    layout: _computeLayout(size),
                    edges: _edges,
                    nodes: _filteredNodes(),
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
            _chip('全部', _timeFilter == 'all',
                () => setState(() => _timeFilter = 'all')),
            _chip('近7天', _timeFilter == '7d',
                () => setState(() => _timeFilter = '7d')),
            _chip('近30天', _timeFilter == '30d',
                () => setState(() => _timeFilter = '30d')),
            _separator(),
            _chip('全部角色', _charFilter == null,
                () => setState(() => _charFilter = null)),
            for (final c in _characters)
              _chip(c.name, _charFilter == c.id,
                  () => setState(() => _charFilter = c.id)),
            if (showCharDivider) _separator(),
            _chip('全部心情', _moodFilter == null,
                () => setState(() => _moodFilter = null)),
            for (final m in moods)
              _chip(m, _moodFilter == m, () => setState(() => _moodFilter = m)),
            if (_isPrivate) ...[
              _separator(),
              _chip('全部类型', _lifeTypeFilter == null,
                  () => setState(() => _lifeTypeFilter = null)),
              _chip('生活', _lifeTypeFilter == 'life_event',
                  () => setState(() => _lifeTypeFilter = 'life_event')),
              _chip('反思', _lifeTypeFilter == 'reflection',
                  () => setState(() => _lifeTypeFilter = 'reflection')),
              _chip('笔记', _lifeTypeFilter == 'note',
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
                color: selected ? Colors.white : const Color(0xFF666666))),
        selected: selected,
        onSelected: (_) => onTap(),
        showCheckmark: false,
        visualDensity: VisualDensity.compact,
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        backgroundColor: const Color(0xFFF2F2F7),
        selectedColor: const Color(0xFF007AFF),
        side: BorderSide.none,
      ),
    );
  }
}

class _WeaveCanvasPainter extends CustomPainter {
  final List<_NodeLayout> layout;
  final List<_CanvasEdge> edges;
  final List<_CanvasNode> nodes;
  final double jitterT;

  _WeaveCanvasPainter({
    required this.layout,
    required this.edges,
    required this.nodes,
    required this.jitterT,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (layout.isEmpty) return;
    final byId = {for (final l in layout) l.node.id: l};
    final visibleIds = {for (final n in nodes) n.id};
    // 连线（强度越高越明显；两端均可见才画）
    for (final e in edges) {
      final a = byId[e.source];
      final b = byId[e.target];
      if (a == null || b == null) continue;
      if (!visibleIds.contains(e.source) || !visibleIds.contains(e.target)) {
        continue;
      }
      final alpha = 0.10 + 0.30 * e.strength;
      final paint = Paint()
        ..color = const Color(0xFF007AFF).withValues(alpha: alpha)
        ..strokeWidth = 1.2;
      canvas.drawLine(
        Offset(a.x, a.y),
        Offset(b.x, b.y),
        paint,
      );
    }
    // 节点：远处先画（depth 升序）
    final sorted = [...layout]..sort((a, b) => a.depth.compareTo(b.depth));
    for (final l in sorted) {
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
            fontSize: 10.5 * l.scale,
            fontWeight: FontWeight.w600,
            color: const Color(0xFF1C1C1E),
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      final cardW = math.max(tp.width, 42.0 * l.scale) + 10.0 * l.scale;
      final cardH = tp.height + 9.0 * l.scale;
      final color = _palette[l.node.characterId % _palette.length];
      // 私域增强：生活类型着色（生活=蓝/反思=紫/笔记=绿），热标签节点描边加亮
      final lifeColor = l.node.lifeType == 'reflection'
          ? const Color(0xFFAF52DE)
          : l.node.lifeType == 'note'
              ? const Color(0xFF34C759)
              : color;
      final isHot = l.node.hotTags.isNotEmpty;
      final rrect = RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: Offset(x, y),
          width: cardW,
          height: cardH,
        ),
        Radius.circular(9 * l.scale),
      );
      canvas.drawRRect(
        rrect,
        Paint()
          ..color = lifeColor.withValues(alpha: 0.10 + 0.05 * l.scale),
      );
      canvas.drawRRect(
        rrect,
        Paint()
          ..color = lifeColor.withValues(alpha: isHot ? 0.95 : 0.55)
          ..style = PaintingStyle.stroke
          ..strokeWidth = (isHot ? 2.0 : 1.2) * l.scale,
      );
      tp.paint(canvas, Offset(x - tp.width / 2, y - tp.height / 2));
      // 兴趣热标记：右上角小圆点（热度徽标）
      if (isHot) {
        final dot = Offset(x + cardW / 2 - 2, y - cardH / 2 + 2);
        canvas.drawCircle(
          dot,
          3.2 * l.scale,
          Paint()..color = const Color(0xFFFF3B30),
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant _WeaveCanvasPainter oldDelegate) => true;
}
