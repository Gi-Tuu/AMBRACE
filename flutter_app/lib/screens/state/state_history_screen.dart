import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../widgets/spider_chart.dart';
import '../../widgets/ios_card_group.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import "package:ai_companion/theme/tokens.dart";

/// 状态历史页（Phase 2）：八维状态趋势曲线 + 任意两时间点蛛网对比
class StateHistoryScreen extends StatefulWidget {
  final int characterId;
  final String characterName;
  const StateHistoryScreen({super.key, required this.characterId, required this.characterName});

  @override
  State<StateHistoryScreen> createState() => _StateHistoryScreenState();
}

class _StateHistoryScreenState extends State<StateHistoryScreen> {
  static const _dimKeys = ['mood', 'body_temp', 'desire', 'possessiveness', 'fatigue', 'sensitivity', 'comfort', 'anger'];

  List<(String, String)> get _dims {
    final l10n = AppLocalizations.of(context)!;
    return [
      ('mood', l10n.mood),
      ('body_temp', l10n.stateTemp),
      ('desire', l10n.stateDesire),
      ('possessiveness', l10n.statePossessiveness),
      ('fatigue', l10n.stateFatigue),
      ('sensitivity', l10n.stateSensitivity),
      ('comfort', l10n.stateComfort),
      ('anger', l10n.stateAnger),
    ];
  }

  List<Map<String, dynamic>> _points = [];
  bool _loading = true;
  String _dim = 'mood';
  int? _compareA;
  int? _compareB;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().getStateHistory(widget.characterId, days: 30);
      final points = (data['points'] as List? ?? []).cast<Map<String, dynamic>>().reversed.toList();
      if (!mounted) return;
      setState(() {
        _points = points;
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _fmtTime(String iso) {
    if (iso.length < 16) return iso;
    try {
      final dt = DateTime.parse(iso.replaceAll(' ', 'T')).toUtc().add(const Duration(hours: 8));
      return '${dt.month}/${dt.day} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    } catch (_) {
      return iso.substring(5, 16);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.stateHistTrendTitle(widget.characterName))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _points.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(32),
                    child: Text(l10n.stateHistEmpty,
                        textAlign: TextAlign.center, style: const TextStyle(color: IosCardColors.subtitle)),
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    // 维度选择
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final (key, cn) in _dims)
                          ChoiceChip(
                            label: Text(cn, style: const TextStyle(fontSize: 12)),
                            selected: _dim == key,
                            onSelected: (_) => setState(() => _dim = key),
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    _buildChartCard(context),
                    const SizedBox(height: 16),
                    _buildCompareCard(context),
                  ],
                ),
    );
  }

  /// 趋势折线图（CustomPaint）
  Widget _buildChartCard(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final cn = _dims.firstWhere((d) => d.$1 == _dim).$2;
    final values = [for (final pt in _points) ((pt['values'] as Map<String, dynamic>)[_dim] as num?)?.toInt() ?? 50];
    return IosCardGroup(
      children: [
        Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l10n.stateHistCurve(cn), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text(l10n.stateHistRecentSnapshots(values.length), style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            const SizedBox(height: 12),
            SizedBox(
              height: 160,
              child: CustomPaint(
                size: Size.infinite,
                painter: _TrendPainter(
                  values: values,
                  times: [for (final pt in _points) _fmtTime(pt['created_at'] as String? ?? '')],
                  color: Theme.of(context).colorScheme.primary,
                ),
              ),
            ),
          ],
        ),
        ),
      ],
    );
  }

  /// 两时间点蛛网对比（选择器 + 双蛛网）
  Widget _buildCompareCard(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    if (_points.length < 2) {
      return IosCardGroup(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(l10n.stateHistInsufficientSnapshots, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
          ),
        ],
      );
    }
    final idxA = _compareA ?? 0;
    final idxB = _compareB ?? _points.length - 1;
    final va = _valuesAt(idxA);
    final vb = _valuesAt(idxB);
    return IosCardGroup(
      children: [
        Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l10n.stateHistSpiderCompare, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              Text('${_fmtTime(_points[idxA]['created_at'] as String? ?? '')}  vs  ${_fmtTime(_points[idxB]['created_at'] as String? ?? '')}',
                  style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: _timeDropdown(idxA, (v) => setState(() => _compareA = v), l10n.stateHistEarlier),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _timeDropdown(idxB, (v) => setState(() => _compareB = v), l10n.stateHistLater),
                ),
              ],
            ),
            const SizedBox(height: 8),
            AspectRatio(
              aspectRatio: 1,
              child: SpiderChart(
                values: va, // 较早（主组=蓝）
                compareValues: vb, // 较晚（对比组=橙）
                labels: _dims.map((d) => d.$2).toList(),
                colors: List.filled(8, Colors.blue),
                primaryColor: Colors.blue,
                compareColor: AppColors.compareOrange,
              ),
            ),
            const SizedBox(height: 4),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Container(width: 12, height: 12, decoration: const BoxDecoration(color: Colors.blue, shape: BoxShape.circle)),
                const SizedBox(width: 4),
                Text(l10n.stateHistEarlierAt(_fmtTime(_points[idxA]['created_at'] as String? ?? '')), style: const TextStyle(fontSize: 11)),
                const SizedBox(width: 14),
                Container(width: 12, height: 12, decoration: const BoxDecoration(color: AppColors.compareOrange, shape: BoxShape.circle)),
                const SizedBox(width: 4),
                Text(l10n.stateHistLaterAt(_fmtTime(_points[idxB]['created_at'] as String? ?? '')), style: const TextStyle(fontSize: 11)),
              ],
            ),
          ],
        ),
        ),
      ],
    );
  }

  Widget _timeDropdown(int current, ValueChanged<int?> onChanged, String hint) {
    return DropdownButtonFormField<int>(
      value: current,
      isExpanded: true,
      decoration: InputDecoration(isDense: true, contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6)),
      items: [
        for (var i = 0; i < _points.length; i++)
          DropdownMenuItem(value: i, child: Text(_fmtTime(_points[i]['created_at'] as String? ?? ''), style: const TextStyle(fontSize: 12))),
      ],
      onChanged: onChanged,
    );
  }

  List<double> _valuesAt(int idx) {
    final v = _points[idx]['values'] as Map<String, dynamic>;
    return [for (final key in _dimKeys) (v[key] as num?)?.toDouble() ?? 50];
  }
}

/// 折线图画笔：x=时间序，y=0-100 值域
class _TrendPainter extends CustomPainter {
  final List<int> values;
  final List<String> times;
  final Color color;
  _TrendPainter({required this.values, required this.times, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    if (values.isEmpty) return;
    final padL = 26.0, padB = 20.0, padT = 8.0, padR = 8.0;
    final w = size.width - padL - padR;
    final h = size.height - padT - padB;

    // 网格线（0/25/50/75/100）
    final gridPaint = Paint()
      ..color = Colors.grey.withValues(alpha: 0.25)
      ..strokeWidth = 0.6;
    for (var i = 0; i <= 4; i++) {
      final y = padT + h * (1 - i / 4);
      canvas.drawLine(Offset(padL, y), Offset(padL + w, y), gridPaint);
      final tp = TextPainter(
        text: TextSpan(text: '${i * 25}', style: TextStyle(fontSize: 9, color: Colors.grey.shade500)),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(2, y - tp.height / 2));
    }

    if (values.length == 1) {
      final x = padL + w / 2;
      final y = padT + h * (1 - values.first / 100);
      canvas.drawCircle(Offset(x, y), 4, Paint()..color = color);
      return;
    }

    final linePaint = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    final pts = <Offset>[];
    for (var i = 0; i < values.length; i++) {
      final x = padL + w * (i / (values.length - 1));
      final y = padT + h * (1 - (values[i].clamp(0, 100) / 100));
      pts.add(Offset(x, y));
    }
    for (var i = 0; i < pts.length - 1; i++) {
      canvas.drawLine(pts[i], pts[i + 1], linePaint);
    }
    final dotPaint = Paint()..color = color;
    for (final pt in pts) {
      canvas.drawCircle(pt, 3, dotPaint);
    }
    // 首尾时间标签
    final firstTp = TextPainter(
      text: TextSpan(text: times.first, style: TextStyle(fontSize: 9, color: Colors.grey.shade500)),
      textDirection: TextDirection.ltr,
    )..layout();
    firstTp.paint(canvas, Offset(padL, padT + h + 2));
    final lastTp = TextPainter(
      text: TextSpan(text: times.last, style: TextStyle(fontSize: 9, color: Colors.grey.shade500)),
      textDirection: TextDirection.ltr,
    )..layout();
    lastTp.paint(canvas, Offset(padL + w - lastTp.width, padT + h + 2));
  }

  @override
  bool shouldRepaint(covariant _TrendPainter old) =>
      old.values != values || old.color != color;
}
