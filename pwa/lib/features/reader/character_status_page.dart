import 'dart:math' as math;

import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'reader_state.dart';
import '../../widgets/reader_async_view.dart';

/// 角色八维状态（对齐 App VisualStateScreen）：正八边形蛛网图 + 八维数值条。
class CharacterStatusPage extends StatefulWidget {
  const CharacterStatusPage({
    super.key,
    required this.client,
    required this.characterId,
    required this.characterName,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<CharacterStatusPage> createState() => _CharacterStatusPageState();
}

class _CharacterStatusPageState extends State<CharacterStatusPage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<Map<String, dynamic>> _loader =
      ReaderLoader<Map<String, dynamic>>();

  static const List<(String, String, Color)> _dims = [
    ('mood', 'moodLabel', Color(0xFFEF5350)),
    ('body_temp', 'stateTemp', Color(0xFFFB8C00)),
    ('desire', 'stateDesire', Color(0xFFEC407A)),
    ('possessiveness', 'statePossessiveness', Color(0xFFAB47BC)),
    ('fatigue', 'stateFatigue', Color(0xFF7E57C2)),
    ('sensitivity', 'stateSensitivity', Color(0xFF42A5F5)),
    ('comfort', 'stateComfort', Color(0xFF26A69A)),
    ('anger', 'stateAnger', Color(0xFF66BB6A)),
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _loader.load((ct) => _api.getCharacterStates(widget.characterId,
        cancelToken: ct));
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  List<int> _values(Map<String, dynamic> s) => [
        for (final (key, _, _) in _dims)
          (s[key] as num? ?? 50).toInt(),
      ];

  String _fmtTime(String? raw) {
    if (raw == null || raw.isEmpty) return '';
    final dt = DateTime.tryParse(raw.endsWith('Z') ? raw : '${raw}Z');
    if (dt == null) return raw;
    final local = dt.toUtc().add(const Duration(hours: 8));
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}-${two(local.month)}-${two(local.day)} '
        '${two(local.hour)}:${two(local.minute)}';
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return Scaffold(
      appBar: AppBar(title: Text('${rs.t('statusTitle')} · ${widget.characterName}')),
      body: ReaderAsyncView<Map<String, dynamic>>(
        loader: _loader,
        rs: rs,
        cs: widget.cs,
        onRetry: _load,
        dataBuilder: (state) {
          final values = _values(state);
          final trust = state['trust'];
          final updated = _fmtTime(state['updated_at'] as String?);
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              AspectRatio(
                aspectRatio: 1,
                child: CustomPaint(
                  painter: _RadarPainter(
                    values: values,
                    labels: [for (final (_, labelKey, _) in _dims) rs.t(labelKey)],
                    colors: [for (final (_, _, color) in _dims) color],
                    axisColor: Theme.of(context).dividerColor,
                    textColor: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
              ),
              const SizedBox(height: 16),
              for (var i = 0; i < _dims.length; i++) ...[
                Row(children: [
                  Container(
                    width: 8,
                    height: 8,
                    decoration: BoxDecoration(
                        color: _dims[i].$3, shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 8),
                  Expanded(child: Text(rs.t(_dims[i].$2))),
                  Text('${values[i]}',
                      style: Theme.of(context).textTheme.titleSmall),
                ]),
                const SizedBox(height: 4),
                LinearProgressIndicator(
                  value: (values[i] / 100).clamp(0.0, 1.0),
                  color: _dims[i].$3,
                ),
                const SizedBox(height: 12),
              ],
              if (trust is num)
                Align(
                  alignment: Alignment.centerLeft,
                  child: Chip(
                    label: Text('${rs.t('stateTrust')} ${trust.toInt()}'),
                    visualDensity: VisualDensity.compact,
                  ),
                ),
              if (updated.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text('${rs.t('stateUpdatedAt')} $updated',
                      style: Theme.of(context).textTheme.bodySmall),
                ),
            ],
          );
        },
      ),
    );
  }
}

/// 正八边形蛛网图（0-100/轴）。
class _RadarPainter extends CustomPainter {
  _RadarPainter({
    required this.values,
    required this.labels,
    required this.colors,
    required this.axisColor,
    required this.textColor,
  });

  final List<int> values;
  final List<String> labels;
  final List<Color> colors;
  final Color axisColor;
  final Color textColor;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = math.min(size.width, size.height) / 2 - 28;
    final n = values.length;
    if (n == 0) return;

    final grid = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.8
      ..color = axisColor.withValues(alpha: 0.4);
    for (var ring = 1; ring <= 4; ring++) {
      final r = radius * ring / 4;
      final path = Path();
      for (var i = 0; i <= n; i++) {
        final a = -math.pi / 2 + 2 * math.pi * (i % n) / n;
        final p = center + Offset(math.cos(a) * r, math.sin(a) * r);
        if (i == 0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      canvas.drawPath(path, grid);
    }

    final axis = Paint()
      ..strokeWidth = 0.7
      ..color = axisColor.withValues(alpha: 0.5);
    for (var i = 0; i < n; i++) {
      final a = -math.pi / 2 + 2 * math.pi * i / n;
      canvas.drawLine(center, center + Offset(math.cos(a) * radius, math.sin(a) * radius), axis);
    }

    final poly = Path();
    for (var i = 0; i <= n; i++) {
      final idx = i % n;
      final a = -math.pi / 2 + 2 * math.pi * idx / n;
      final r = radius * (values[idx].clamp(0, 100) / 100);
      final p = center + Offset(math.cos(a) * r, math.sin(a) * r);
      if (i == 0) {
        poly.moveTo(p.dx, p.dy);
      } else {
        poly.lineTo(p.dx, p.dy);
      }
    }
    final fill = Paint()
      ..style = PaintingStyle.fill
      ..color = colors.isEmpty
          ? Colors.black12
          : colors.first.withValues(alpha: 0.18);
    canvas.drawPath(poly, fill);
    canvas.drawPath(
        poly,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.6
          ..color = colors.isEmpty ? Colors.black54 : colors.first);

    for (var i = 0; i < n; i++) {
      final a = -math.pi / 2 + 2 * math.pi * i / n;
      final r = radius * (values[i].clamp(0, 100) / 100);
      final p = center + Offset(math.cos(a) * r, math.sin(a) * r);
      canvas.drawCircle(p, 2.6, Paint()..color = colors[i]);
    }

    for (var i = 0; i < n; i++) {
      final a = -math.pi / 2 + 2 * math.pi * i / n;
      final p = center + Offset(math.cos(a) * (radius + 16), math.sin(a) * (radius + 16));
      final tp = TextPainter(
        text: TextSpan(
            text: labels[i],
            style: TextStyle(fontSize: 10, color: textColor)),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, p - Offset(tp.width / 2, tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _RadarPainter old) =>
      old.values != values || old.labels != labels;
}
