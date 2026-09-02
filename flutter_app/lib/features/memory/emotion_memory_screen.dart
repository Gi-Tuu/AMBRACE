import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/emotion_event.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 状态情绪记忆内页（Phase 1 只读）：情绪记忆 + 状态触发 + 剧情线 三源时间线
class EmotionMemoryScreen extends StatefulWidget {
  final int characterId;
  final String characterName;

  const EmotionMemoryScreen({super.key, required this.characterId, required this.characterName});

  @override
  State<EmotionMemoryScreen> createState() => _EmotionMemoryScreenState();
}

class _EmotionMemoryScreenState extends State<EmotionMemoryScreen> {
  final _api = ApiClient();
  EmotionTimeline? _timeline;
  bool _loading = true;
  String? _error;
  String _filter = 'all'; // all / emotion / trigger / story
  final Set<int> _pinnedIds = {};

  List<(String, String)> _filters(AppLocalizations l10n) => [
    ('all', l10n.emotionAll),
    ('emotion', l10n.emotionFilter),
    ('trigger', l10n.triggerFilter),
    ('story', l10n.storyFilter),
  ];
  List<String> _weekdays(AppLocalizations l10n) => [l10n.weekdayMon, l10n.weekdayTue, l10n.weekdayWed, l10n.weekdayThu, l10n.weekdayFri, l10n.weekdaySat, l10n.weekdaySun];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final tl = await _api.getEmotionTimeline(widget.characterId);
      if (mounted) setState(() { _timeline = tl; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  /// 后端时间 = UTC naive；补 Z 转 UTC 再加 8 小时得北京时间
  DateTime _toBeijing(DateTime utc) => utc.add(const Duration(hours: 8));

  DateTime _parseUtc(String iso) {
    final s = (iso.length >= 19 && !iso.endsWith('Z') && !iso.contains('+')) ? '${iso}Z' : iso;
    return DateTime.tryParse(s)?.toUtc() ?? DateTime.now().toUtc();
  }


  bool _matchFilter(EmotionEvent e) {
    switch (_filter) {
      case 'emotion':
        return e.source == 'emotion';
      case 'trigger':
        return e.source == 'state_trigger';
      case 'story':
        return e.source == 'storyline';
      default:
        return true;
    }
  }

  (IconData, Color) _sourceStyle(String source) {
    switch (source) {
      case 'emotion':
        return (Icons.mood, Colors.blue.shade400);
      case 'state_trigger':
        return (Icons.bolt, Colors.orange.shade400);
      case 'storyline':
        return (Icons.auto_stories, Colors.purple.shade300);
      default:
        return (Icons.circle, Colors.grey);
    }
  }

  String _sourceLabel(String source) {
    final l10n = AppLocalizations.of(context)!;
    switch (source) {
      case 'emotion':
        return l10n.sourceEmotion;
      case 'state_trigger':
        return l10n.sourceTrigger;
      case 'storyline':
        return l10n.sourceStory;
      default:
        return source;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.emotionMemoryTitle(widget.characterName))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(l10n.loadFailedErr(_error ?? ''), textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  ElevatedButton(onPressed: _load, child: Text(l10n.retry)),
                ]))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      _buildSummaryCard(),
                      const SizedBox(height: 8),
                      _buildFilterChips(),
                      const SizedBox(height: 4),
                      ..._buildTimeline(),
                    ],
                  ),
                ),
    );
  }

  Widget _buildSummaryCard() {
    final l10n = AppLocalizations.of(context)!;
    final s = _timeline!.summary;
    return IosCardGroup(
      children: [
        Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.insights, size: 20, color: Theme.of(context).colorScheme.tertiary),
              const SizedBox(width: 6),
              Text(l10n.weekOverview, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 8),
            Text(s.text, style: const TextStyle(fontSize: 13, height: 1.4)),
            const SizedBox(height: 10),
            Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
              _countItem(s.emotionCount, l10n.sourceEmotion, Colors.blue),
              _countItem(s.triggerCount, l10n.sourceTrigger, Colors.orange),
              _countItem(s.storylineCount, l10n.storyCount, Colors.purple),
            ]),
          ]),
        ),
      ],
    );
  }

  Widget _countItem(int n, String label, Color color) {
    return Column(children: [
      Text('$n', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: color)),
      Text(label, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
    ]);
  }

  Widget _buildFilterChips() {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Row(children: [
        for (final (code, label) in _filters(l10n))
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: GestureDetector(
              onTap: () => setState(() => _filter = code),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 180),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 7),
                decoration: BoxDecoration(
                  color: _filter == code
                      ? scheme.primary.withValues(alpha: 0.12)
                      : scheme.surface,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(
                    color: _filter == code
                        ? scheme.primary.withValues(alpha: 0.5)
                        : Theme.of(context).dividerColor,
                  ),
                ),
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 13,
                    color: _filter == code ? scheme.primary : scheme.onSurfaceVariant,
                    fontWeight: _filter == code ? FontWeight.w600 : FontWeight.normal,
                  ),
                ),
              ),
            ),
          ),
      ]),
    );
  }

  List<Widget> _buildTimeline() {
    final l10n = AppLocalizations.of(context)!;
    final events = _timeline!.events.where(_matchFilter).toList();
    if (events.isEmpty) {
      return [
        const SizedBox(height: 40),
        const Center(child: Icon(Icons.spa, size: 48, color: IosCardColors.subtitle)),
        const SizedBox(height: 12),
        Center(child: Text(l10n.noEmotionRecords, textAlign: TextAlign.center,
            style: const TextStyle(color: IosCardColors.subtitle))),
      ];
    }

    // 按北京时间 年 → 月 → 日 分组（倒序），嵌套折叠：年折叠 > 月折叠 > 日折叠 > 事件卡片
    final Map<int, Map<int, Map<int, List<EmotionEvent>>>> tree = {};
    for (final e in events) {
      final bj = _toBeijing(_parseUtc(e.atIso));
      tree.putIfAbsent(bj.year, () => {});
      tree[bj.year]!.putIfAbsent(bj.month, () => {});
      tree[bj.year]![bj.month]!.putIfAbsent(bj.day, () => []);
      tree[bj.year]![bj.month]![bj.day]!.add(e);
    }
    final years = tree.keys.toList()..sort((a, b) => b.compareTo(a));

    final widgets = <Widget>[];
    var firstDayDone = false; // 最新一天默认展开，其余日折叠

    Widget dayTile(int y, int m, int d, List<EmotionEvent> dayEvents) {
      final bj = _toBeijing(_parseUtc(dayEvents.first.atIso));
      final weekday = _weekdays(l10n)[bj.weekday - 1];
      final dayStr =
          '${bj.month.toString().padLeft(2, '0')}-${bj.day.toString().padLeft(2, '0')}';
      final expand = !firstDayDone;
      firstDayDone = true;
      return ExpansionTile(
        key: ValueKey('y$y-m$m-d$d'),
        initiallyExpanded: expand,
        tilePadding: const EdgeInsets.symmetric(horizontal: 8),
        childrenPadding: const EdgeInsets.only(left: 8),
        leading: Icon(Icons.fiber_manual_record, size: 10, color: IosCardColors.subtitle),
        title: Text('$dayStr $weekday',
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        subtitle: Text(l10n.recordCount(dayEvents.length),
            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [for (final e in dayEvents) _buildEventCard(e)],
      );
    }

    Widget monthTile(int y, int m, Map<int, List<EmotionEvent>> daysMap) {
      final dayKeys = daysMap.keys.toList()..sort((a, b) => b.compareTo(a));
      var monthCount = 0;
      for (final ds in daysMap.values) {
        monthCount += ds.length;
      }
      return ExpansionTile(
        key: ValueKey('y$y-m$m'),
        initiallyExpanded: true,
        tilePadding: const EdgeInsets.symmetric(horizontal: 8),
        childrenPadding: const EdgeInsets.only(left: 8),
        leading: Icon(Icons.calendar_today, size: 16, color: Theme.of(context).colorScheme.primary),
        title: Text(l10n.monthNumeric(m), style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        subtitle: Text(l10n.recordCount(monthCount),
            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [for (final d in dayKeys) dayTile(y, m, d, daysMap[d]!)],
      );
    }

    for (final y in years) {
      final months = tree[y]!;
      final monthKeys = months.keys.toList()..sort((a, b) => b.compareTo(a));
      var yearCount = 0;
      for (final m in monthKeys) {
        for (final ds in months[m]!.values) {
          yearCount += ds.length;
        }
      }
      widgets.add(ExpansionTile(
        key: ValueKey('y$y'),
        initiallyExpanded: true,
        tilePadding: const EdgeInsets.symmetric(horizontal: 8),
        childrenPadding: const EdgeInsets.only(left: 8),
        leading: Icon(Icons.calendar_month, size: 22, color: Theme.of(context).colorScheme.tertiary),
        title: Text(l10n.yearLabel(y), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
        subtitle: Text(l10n.yearCountTotal(yearCount),
            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [for (final m in monthKeys) monthTile(y, m, months[m]!)],
      ));
    }
    return widgets;
  }

  Widget _buildEventCard(EmotionEvent e) {
    final l10n = AppLocalizations.of(context)!;
    final bj = _toBeijing(_parseUtc(e.atIso));
    final (icon, color) = _sourceStyle(e.source);
    final timeStr = '${bj.hour.toString().padLeft(2, '0')}:${bj.minute.toString().padLeft(2, '0')}';
    return IosCardGroup(
      children: [
        Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            leading: CircleAvatar(radius: 16, backgroundColor: color.withValues(alpha: 0.15), child: Icon(icon, size: 18, color: color)),
            title: Row(children: [
              Expanded(child: Text(e.label, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600))),
              Text(timeStr, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            ]),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                e.dimChanges.isEmpty ? (e.content.isNotEmpty ? e.content : l10n.noDetails) : _dimSummary(e),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
              ),
            ),
            childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            expandedCrossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (e.dimChanges.isNotEmpty) ...[
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final c in e.dimChanges)
                    Chip(
                      label: Text(c.from == null ? '${c.cn}→${c.to}' : '${c.cn} ${c.from}→${c.to}',
                          style: const TextStyle(fontSize: 11)),
                      visualDensity: VisualDensity.compact,
                      backgroundColor: Theme.of(context).colorScheme.tertiaryContainer.withValues(alpha: 0.4),
                    ),
                ]),
                const SizedBox(height: 8),
              ],
              Text(l10n.sourcePrefix(_sourceLabel(e.source)), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
            if (e.content.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(e.content, style: const TextStyle(fontSize: 12, height: 1.4)),
            ],
            if (e.source == 'emotion' && e.sourceId > 0) ...[
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: () => _togglePin(e),
                  icon: Icon(_pinnedIds.contains(e.sourceId) ? Icons.star : Icons.star_border, size: 16),
                  label: Text(_pinnedIds.contains(e.sourceId) ? l10n.pinned : l10n.pinEmotion),
                  style: TextButton.styleFrom(visualDensity: VisualDensity.compact, foregroundColor: Colors.amber.shade700),
                ),
              ),
            ],
            ],
          ),
        ),
      ],
    );
  }

  /// 收藏/取消收藏情绪记忆（置顶对应记忆，Phase 3）
  Future<void> _togglePin(EmotionEvent e) async {
    final l10n = AppLocalizations.of(context)!;
    final pinned = _pinnedIds.contains(e.sourceId);
    try {
      await ApiClient().updateMemory(e.sourceId, {'is_pinned': !pinned});
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.opFailedRetry)));
      }
      return;
    }
    if (mounted) {
      setState(() {
        if (pinned) {
          _pinnedIds.remove(e.sourceId);
        } else {
          _pinnedIds.add(e.sourceId);
        }
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(pinned ? l10n.unpinned : l10n.pinnedEmotion)),
      );
    }
  }

  String _dimSummary(EmotionEvent e) {
    final parts = e.dimChanges
        .map((c) => c.from == null ? '${c.cn}${c.to ?? '?'}' : '${c.cn} ${c.from}→${c.to}')
        .toList();
    return parts.join('、');
  }
}
