import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'step_dialog.dart';
import '../../services/phone_perception_service.dart';

/// 工作流节点连线画布（2026-08-14 方案 B+C）
/// 节点=步骤，连线=执行顺序/条件分支；点节点编辑、长按拖动布局、点 x 删除；
/// 从节点底部圆点拖线到另一节点顶部圆点创建连线；点连线中间标签编辑条件；
/// 右上角「应用」返回画布 graph（{nodes, edges}）。
class WorkflowCanvasScreen extends StatefulWidget {
  final List<Map<String, dynamic>> steps;
  final Map<String, dynamic>? graph;
  const WorkflowCanvasScreen({super.key, required this.steps, this.graph});

  @override
  State<WorkflowCanvasScreen> createState() => _WorkflowCanvasScreenState();
}

class _NodeView {
  _NodeView(this.id, this.step, this.dx, this.dy);
  final String id;
  Map<String, dynamic> step;
  double dx;
  double dy;
}

class _EdgeView {
  _EdgeView(this.from, this.to, this.type, [this.target = '']);
  final String from;
  final String to;
  String type; // success / fail / always / screen_has / screen_empty
  String target;
}

const double _kNodeW = 240;
const double _kNodeH = 84;
const double _kGapY = 160;
const double _kMarginX = 56;
const double _kPortR = 8;

String _edgeLabel(AppLocalizations l10n, _EdgeView e) {
  switch (e.type) {
    case 'fail':
      return l10n.workflowEdgeFail;
    case 'always':
      return l10n.workflowEdgeAlways;
    case 'screen_has':
      return l10n.workflowEdgeScreenHas(e.target);
    case 'screen_empty':
      return l10n.workflowEdgeScreenEmpty(e.target);
    default:
      return l10n.workflowEdgeSuccess;
  }
}

Color _edgeColor(String type) {
  switch (type) {
    case 'fail':
      return Colors.redAccent;
    case 'always':
      return Colors.grey;
    case 'screen_has':
    case 'screen_empty':
      return Colors.blue;
    default:
      return Colors.green;
  }
}

class _WorkflowCanvasScreenState extends State<WorkflowCanvasScreen> {
  late final List<_NodeView> _nodes;
  late final List<_EdgeView> _edges;
  late int _idSeq;
  bool _dragging = false;
  int _dragIndex = -1;
  Offset _dragOrigin = Offset.zero;
  String? _linkingFrom;
  Offset? _linkPoint;

  @override
  void initState() {
    super.initState();
    final g = widget.graph;
    if (g != null && g['nodes'] is List && (g['nodes'] as List).isNotEmpty) {
      final rawNodes = g['nodes'].cast<Map>();
      _nodes = rawNodes.asMap().entries.map((e) {
        final m = Map<String, dynamic>.from(e.value as Map);
        final id = m['id'] as String? ?? 'n${e.key}';
        m.remove('id');
        return _NodeView(id, m, _kMarginX, 40.0 + e.key * _kGapY);
      }).toList();
      _idSeq = _nodes.length;
      _edges = (g['edges'] as List? ?? const [])
          .map((e) => _EdgeView(
                e['from'] as String? ?? '',
                e['to'] as String? ?? '',
                e['type'] as String? ?? 'success',
                e['target'] as String? ?? '',
              ))
          .toList();
    } else {
      _nodes = widget.steps.asMap().entries.map((e) {
        return _NodeView('n${e.key}', Map<String, dynamic>.from(e.value),
            _kMarginX, 40.0 + e.key * _kGapY);
      }).toList();
      _idSeq = _nodes.length;
      _edges = [
        for (var i = 0; i < _nodes.length - 1; i++)
          _EdgeView(_nodes[i].id, _nodes[i + 1].id, 'success'),
      ];
    }
  }

  double get _canvasWidth => math.max(360, _kMarginX * 2 + _kNodeW + 40);
  double get _canvasHeight => math.max(480, 80.0 + _nodes.length * _kGapY);

  String _newId() {
    while (true) {
      final id = 'n${_idSeq++}';
      if (!_nodes.any((n) => n.id == id)) return id;
    }
  }

  Future<void> _addNode() async {
    final s = await showStepDialog(context);
    if (s == null || !mounted) return;
    setState(() {
      final id = _newId();
      _nodes.add(_NodeView(id, s, _kMarginX, 40.0 + _nodes.length * _kGapY));
      if (_nodes.length > 1) {
        final prev = _nodes[_nodes.length - 2];
        _edges.add(_EdgeView(prev.id, id, 'success'));
      }
    });
  }

  Future<void> _editNode(int i) async {
    final s = await showStepDialog(context, step: _nodes[i].step);
    if (s == null || !mounted) return;
    setState(() => _nodes[i].step = s);
  }

  void _removeNode(int i) {
    final id = _nodes[i].id;
    setState(() {
      _nodes.removeAt(i);
      _edges.removeWhere((e) => e.from == id || e.to == id);
    });
  }

  void _beginDrag(int i) {
    _dragIndex = i;
    _dragOrigin = Offset(_nodes[i].dx, _nodes[i].dy);
    setState(() => _dragging = true);
  }

  void _dragUpdate(Offset delta) {
    if (_dragIndex < 0 || _dragIndex >= _nodes.length) return;
    setState(() {
      final n = _nodes[_dragIndex];
      n.dx = (_dragOrigin.dx + delta.dx).clamp(0.0, _canvasWidth - _kNodeW);
      n.dy = (_dragOrigin.dy + delta.dy).clamp(0.0, _canvasHeight - _kNodeH);
    });
  }

  void _endDrag() {
    _dragIndex = -1;
    setState(() => _dragging = false);
  }

  Offset _outPortOf(_NodeView n) => Offset(n.dx + _kNodeW / 2, n.dy + _kNodeH);
  Offset _inPortOf(_NodeView n) => Offset(n.dx + _kNodeW / 2, n.dy);

  void _beginLink(String fromId, Offset point) {
    setState(() {
      _linkingFrom = fromId;
      _linkPoint = point;
    });
  }

  void _linkUpdate(Offset point) {
    if (_linkingFrom == null) return;
    setState(() => _linkPoint = point);
  }

  void _endLink(Offset point) {
    final from = _linkingFrom;
    _linkingFrom = null;
    _linkPoint = null;
    if (from == null) return;
    for (final n in _nodes) {
      if (n.id == from) continue;
      final center = _inPortOf(n);
      if ((center - point).distance < 48) {
        setState(() {
          _edges.add(_EdgeView(from, n.id, 'success'));
        });
        return;
      }
    }
    setState(() {});
  }

  Future<void> _editEdge(int index) async {
    final l10n = AppLocalizations.of(context)!;
    final e = _edges[index];
    var type = e.type;
    final targetCtrl = TextEditingController(text: e.target);
    final typeLabels = {
      'success': l10n.workflowEdgeWhenSuccess,
      'fail': l10n.workflowEdgeWhenFail,
      'always': l10n.workflowEdgeWhenAlways,
      'screen_has': l10n.workflowEdgeHasText,
      'screen_empty': l10n.workflowEdgeNoText,
    };
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          title: Text(l10n.workflowEdgeConditionTitle),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DropdownButtonFormField<String>(
                initialValue: type,
                decoration: InputDecoration(labelText: l10n.workflowEdgeWhenLabel),
                items: typeLabels.entries
                    .map((x) => DropdownMenuItem(value: x.key, child: Text(x.value)))
                    .toList(),
                onChanged: (v) {
                  if (v != null) setLocal(() => type = v);
                },
              ),
              if (type == 'screen_has' || type == 'screen_empty') ...[
                const SizedBox(height: 8),
                TextField(
                  controller: targetCtrl,
                  decoration: InputDecoration(
                    labelText: l10n.workflowEdgeScreenTextLabel,
                    hintText: l10n.workflowEdgeScreenTextHint,
                  ),
                ),
              ],
            ],
          ),
          actions: [
            TextButton(
              onPressed: () {
                setState(() => _edges.removeAt(index));
                Navigator.pop(ctx);
              },
              child: Text(l10n.workflowEdgeDelete, style: const TextStyle(color: Colors.redAccent)),
            ),
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
            FilledButton(
              onPressed: () {
                Navigator.pop(ctx, {
                  'type': type,
                  'target': targetCtrl.text.trim(),
                });
              },
              child: Text(l10n.furnitureConfirm),
            ),
          ],
        ),
      ),
    );
    targetCtrl.dispose();
    if (result == null || !mounted) return;
    setState(() {
      e.type = result['type'] as String? ?? 'success';
      e.target = result['target'] as String? ?? '';
    });
  }

  Future<void> _showScreenRange() async {
    final s = await PhonePerceptionService.getScreenSize();
    final w = s['width'] as num? ?? 0;
    final h = s['height'] as num? ?? 0;
    if (!mounted) return;
    final l10n = AppLocalizations.of(context)!;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(l10n.workflowScreenRange(w - 1, h - 1)),
      duration: const Duration(seconds: 3),
    ));
  }

  void _apply() {
    final nodes = _nodes
        .map((n) => {...n.step, 'id': n.id})
        .toList();
    final edges = _edges
        .map((e) => {
              'from': e.from,
              'to': e.to,
              'type': e.type,
              if (e.type == 'screen_has' || e.type == 'screen_empty')
                'target': e.target,
            })
        .toList();
    Navigator.of(context).pop({
      'nodes': nodes,
      'edges': edges,
    });
  }

  Widget _portDot({required bool isInput, required _NodeView n}) {
    final center = isInput ? _inPortOf(n) : _outPortOf(n);
    return Positioned(
      left: center.dx - _kPortR,
      top: center.dy - _kPortR,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: isInput
            ? null
            : (_) => _beginLink(n.id, center),
        onPanUpdate: isInput
            ? null
            : (d) => _linkUpdate(center - const Offset(_kPortR, _kPortR) + d.localPosition),
        onPanEnd: isInput
            ? null
            : (_) => _endLink(_linkPoint ?? center),
        child: Container(
          width: _kPortR * 2,
          height: _kPortR * 2,
          decoration: BoxDecoration(
            color: Colors.white,
            shape: BoxShape.circle,
            border: Border.all(
              color: Theme.of(context).colorScheme.primary,
              width: 2,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildEdgeLabel(int i) {
    final e = _edges[i];
    final from = _nodes.where((n) => n.id == e.from).firstOrNull;
    final to = _nodes.where((n) => n.id == e.to).firstOrNull;
    if (from == null || to == null) return const SizedBox.shrink();
    final p1 = _outPortOf(from);
    final p2 = _inPortOf(to);
    final mid = Offset((p1.dx + p2.dx) / 2, (p1.dy + p2.dy) / 2);
    final l10n = AppLocalizations.of(context)!;
    final label = _edgeLabel(l10n, e);
    return Positioned(
      left: mid.dx - 32,
      top: mid.dy - 10,
      child: GestureDetector(
        onTap: () => _editEdge(i),
        child: Container(
          constraints: const BoxConstraints(maxWidth: 150),
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: _edgeColor(e.type), width: 1),
          ),
          child: Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10, color: _edgeColor(e.type)),
          ),
        ),
      ),
    );
  }

  Widget _buildNodeCard(int i) {
    final l10n = AppLocalizations.of(context)!;
    final n = _nodes[i];
    final s = n.step;
    final action = s['action'] as String? ?? 'click';
    final isDragging = _dragging && _dragIndex == i;
    return Positioned(
      left: n.dx,
      top: n.dy,
      width: _kNodeW,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => _editNode(i),
        onLongPressStart: (_) => _beginDrag(i),
        onLongPressMoveUpdate: (d) => _dragUpdate(d.localOffsetFromOrigin),
        onLongPressEnd: (_) => _endDrag(),
        child: Container(
          decoration: BoxDecoration(
            color: isDragging ? Colors.amber.shade50 : Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.45),
              width: 1.4,
            ),
            boxShadow: const [
              BoxShadow(color: Colors.black12, blurRadius: 6, offset: Offset(0, 2)),
            ],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: Row(
                  children: [
                    Icon(stepIconOf(action), size: 18,
                        color: Theme.of(context).colorScheme.primary),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        '${i + 1}. ${stepLabelOf(l10n, action)}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                      ),
                    ),
                    InkWell(
                      onTap: () => _removeNode(i),
                      child: const Icon(Icons.close, size: 16, color: Colors.redAccent),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(10, 2, 10, 6),
                child: Text(
                  stepSummary(l10n, s),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12, color: Colors.black54),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final primary = Theme.of(context).colorScheme.primary;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.workflowCanvasTitle),
        actions: [
          TextButton(onPressed: _apply, child: Text(l10n.workflowApply)),
        ],
      ),
      body: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 左侧工具条
          Container(
            width: 52,
            color: primary.withValues(alpha: 0.04),
            child: SafeArea(
              child: Column(
                children: [
                  IconButton(
                    tooltip: l10n.workflowGetScreenRange,
                    icon: const Icon(Icons.aspect_ratio, size: 22),
                    onPressed: _showScreenRange,
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: Column(
              children: [
                Container(
                  width: double.infinity,
                  color: primary.withValues(alpha: 0.06),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  child: Text(
                    l10n.workflowCanvasHelp,
                    style: const TextStyle(fontSize: 12, color: Colors.black54),
                  ),
                ),
                Expanded(
                  child: InteractiveViewer(
                    constrained: false,
                    panEnabled: !_dragging && _linkingFrom == null,
                    minScale: 0.4,
                    maxScale: 2.5,
                    boundaryMargin: const EdgeInsets.all(120),
                    child: SizedBox(
                      width: _canvasWidth,
                      height: _canvasHeight,
                      child: Stack(
                        children: [
                          Positioned.fill(
                            child: CustomPaint(
                              painter: _EdgePainter(_nodes, _edges),
                            ),
                          ),
                          for (var i = 0; i < _edges.length; i++) _buildEdgeLabel(i),
                          for (var i = 0; i < _nodes.length; i++) _buildNodeCard(i),
                          for (final n in _nodes) ...[
                            _portDot(isInput: true, n: n),
                            _portDot(isInput: false, n: n),
                          ],
                          if (_linkingFrom != null && _linkPoint != null)
                            CustomPaint(
                              size: Size(_canvasWidth, _canvasHeight),
                              painter: _LinkPainter(_linkPoint!),
                            ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _addNode,
        child: const Icon(Icons.add),
      ),
    );
  }
}

class _EdgePainter extends CustomPainter {
  final List<_NodeView> nodes;
  final List<_EdgeView> edges;
  _EdgePainter(this.nodes, this.edges);

  @override
  void paint(Canvas canvas, Size size) {
    for (final e in edges) {
      final from = nodes.where((n) => n.id == e.from).firstOrNull;
      final to = nodes.where((n) => n.id == e.to).firstOrNull;
      if (from == null || to == null) continue;
      final paint = Paint()
        ..color = _edgeColor(e.type)
        ..strokeWidth = 3
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round;
      final p1 = Offset(from.dx + _kNodeW / 2, from.dy + _kNodeH);
      final p2 = Offset(to.dx + _kNodeW / 2, to.dy);
      final path = Path()
        ..moveTo(p1.dx, p1.dy)
        ..cubicTo(p1.dx, p1.dy + 40, p2.dx, p2.dy - 40, p2.dx, p2.dy);
      canvas.drawPath(path, paint);
      _drawArrow(canvas, p2, _edgeColor(e.type));
    }
  }

  void _drawArrow(Canvas canvas, Offset tip, Color color) {
    final p = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final path = Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(tip.dx - 7, tip.dy - 13)
      ..lineTo(tip.dx + 7, tip.dy - 13)
      ..close();
    canvas.drawPath(path, p);
  }

  // 节点位置/连线集合是可变引用，引用比较无法感知变化，一律重绘
  @override
  bool shouldRepaint(covariant _EdgePainter oldDelegate) => true;
}

/// 拖线中的临时连线
class _LinkPainter extends CustomPainter {
  final Offset point;
  _LinkPainter(this.point);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.blue
      ..strokeWidth = 3
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    final path = Path()
      ..moveTo(point.dx, point.dy)
      ..cubicTo(point.dx, point.dy + 40, point.dx, point.dy + 40, point.dx, point.dy);
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _LinkPainter oldDelegate) => oldDelegate.point != point;
}
