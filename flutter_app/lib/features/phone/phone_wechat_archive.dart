// F7-c-2（2026-08-31）自 features/phone/ai_interaction_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';
import 'phone_wechat_chat.dart' show wechatGlassAppBar;

class WechatArchiveScreen extends StatefulWidget {
  const WechatArchiveScreen({super.key, required this.self, required this.other});

  final AICharacter self;
  final AICharacter other;

  @override
  State<WechatArchiveScreen> createState() => WechatArchiveScreenState();
}

class WechatArchiveScreenState extends State<WechatArchiveScreen> {
  List<AIChat> _chats = [];
  bool _loading = true;
  String? _error;
  final Set<String> _expandedYears = {};
  final Set<String> _expandedMonths = {};
  final Set<String> _expandedDays = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final chats = await ApiClient().getAiChats(
        limit: 500,
        charA: widget.self.id,
        charB: widget.other.id,
      );
      if (!mounted) return;
      setState(() {
        _chats = chats;
        _loading = false;
        _error = null; // Aurora P3：重试成功后清除错误标记（原实现失败后永不恢复）
        final now = DateTime.now();
        _expandedYears.add(now.year.toString());
        _expandedMonths.add('${now.year}-${now.month.toString().padLeft(2, '0')}');
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.loadFailed;
        _loading = false;
      });
    }
  }

  /// 年 → 月(yyyy-MM) → 日(yyyy-MM-dd) → 消息（时间正序）
  Map<String, Map<String, Map<String, List<AIChat>>>> _grouped() {
    final result = <String, Map<String, Map<String, List<AIChat>>>>{};
    for (final c in _chats) {
      final d = c.createdAt;
      final year = d.year.toString();
      final month = '${d.year}-${d.month.toString().padLeft(2, '0')}';
      final day =
          '$year-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
      result.putIfAbsent(year, () => {});
      result[year]!.putIfAbsent(month, () => {});
      result[year]![month]!.putIfAbsent(day, () => []);
      result[year]![month]![day]!.add(c);
    }
    return result;
  }

  String _monthLabel(String m) {
    final l10n = AppLocalizations.of(context)!;
    final labels = <String>['', l10n.month1, l10n.month2, l10n.month3, l10n.month4, l10n.month5, l10n.month6, l10n.month7, l10n.month8, l10n.month9, l10n.month10, l10n.month11, l10n.month12];
    final idx = int.tryParse(m.split('-')[1]);
    return (idx != null && idx >= 1 && idx <= 12) ? labels[idx] : l10n.monthNumFallback(m.split('-')[1]);
  }

  String _dayLabel(String d) {
    final l10n = AppLocalizations.of(context)!;
    final p = d.split('-');
    return l10n.dayLabel(int.parse(p[1]), int.parse(p[2]));
  }

  String _timeLabel(DateTime t) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final day = DateTime(t.year, t.month, t.day);
    if (day == today) return DateFormat('HH:mm').format(t);
    return DateFormat('MM-dd HH:mm').format(t);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: wechatGlassAppBar(
        context,
        title: Text(l10n.archiveTitle(widget.self.name, widget.other.name)),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              // Aurora P3：错误态 EmptyState + 重试
              ? ListView(
                  physics: const AlwaysScrollableScrollPhysics(),
                  children: [
                    SizedBox(height: MediaQuery.of(context).size.height * 0.22),
                    EmptyState(
                      icon: Icons.cloud_off_rounded,
                      title: _error!,
                      action: TextButton.icon(
                        onPressed: _load,
                        icon: const Icon(Icons.refresh_rounded, size: 18),
                        label: Text(l10n.retry),
                      ),
                    ),
                  ],
                )
              : _chats.isEmpty
                  // Aurora P3：空态 EmptyState
                  ? ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      children: [
                        SizedBox(height: MediaQuery.of(context).size.height * 0.22),
                        EmptyState(
                          icon: Icons.inventory_2_outlined,
                          title: l10n.noArchive,
                        ),
                      ],
                    )
                  : _buildTree(),
    );
  }

  Widget _buildTree() {
    final grouped = _grouped();
    final years = grouped.keys.toList()..sort((a, b) => b.compareTo(a));
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        for (final year in years) _buildYearSection(year, grouped[year]!),
      ],
    );
  }

  Widget _buildYearSection(
      String year, Map<String, Map<String, List<AIChat>>> months) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final isExpanded = _expandedYears.contains(year);
    final totalDays =
        months.values.fold<int>(0, (sum, days) => sum + days.length);
    final monthKeys = months.keys.toList()..sort((a, b) => b.compareTo(a));
    // Aurora P3：年区块 IosCardGroup → AuroraCard（零 padding，内部自排）
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AuroraCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            InkWell(
              onTap: () {
                setState(() {
                  if (isExpanded) {
                    _expandedYears.remove(year);
                    for (final k in monthKeys) {
                      _expandedMonths.remove(k);
                    }
                  } else {
                    _expandedYears.add(year);
                  }
                });
              },
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(
                  border: Border(bottom: BorderSide(color: scheme.outlineVariant)),
                ),
                child: Row(
                  children: [
                    Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 20),
                    const SizedBox(width: 8),
                    Text(year, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                    const Spacer(),
                    Text(l10n.daysCount(totalDays), style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
                  ],
                ),
              ),
            ),
            if (isExpanded) ...[
              // 分隔线主题化（原 IosCardDivider）
              Container(
                height: 0.5,
                margin: const EdgeInsets.only(left: 16),
                color: scheme.outlineVariant,
              ),
              ...monthKeys.map((m) => _buildMonthSection(m, months[m]!)),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildMonthSection(
      String month, Map<String, List<AIChat>> days) {
    final l10n = AppLocalizations.of(context)!;
    final isExpanded = _expandedMonths.contains(month);
    final totalMsg = days.values.fold<int>(0, (sum, list) => sum + list.length);
    final dayKeys = days.keys.toList()..sort((a, b) => b.compareTo(a));
    return Column(
      children: [
        InkWell(
          onTap: () {
            setState(() {
              if (isExpanded) {
                _expandedMonths.remove(month);
              } else {
                _expandedMonths.add(month);
              }
            });
          },
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.only(left: 24, right: 16, top: 10, bottom: 10),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
            ),
            child: Row(
              children: [
                Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: IosCardColors.subtitle),
                const SizedBox(width: 6),
                Text(_monthLabel(month), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                const Spacer(),
                Text(l10n.msgCount(totalMsg), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
              ],
            ),
          ),
        ),
        if (isExpanded)
          ...dayKeys.map((d) => _buildDaySection(d, days[d]!)),
      ],
    );
  }

  Widget _buildDaySection(String day, List<AIChat> messages) {
    final l10n = AppLocalizations.of(context)!;
    final isExpanded = _expandedDays.contains(day);
    return Container(
      margin: const EdgeInsets.only(left: 36, right: 8, top: 4, bottom: 4),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: () {
              setState(() {
                if (isExpanded) {
                  _expandedDays.remove(day);
                } else {
                  _expandedDays.add(day);
                }
              });
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
              ),
              child: Row(
                children: [
                  Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: IosCardColors.subtitle),
                  const SizedBox(width: 4),
                  Text(_dayLabel(day), style: const TextStyle(fontWeight: FontWeight.w500, fontSize: 13, color: IosCardColors.subtitle)),
                  const Spacer(),
                  Text(l10n.msgCountShort(messages.length), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                ],
              ),
            ),
          ),
          if (isExpanded)
            ...messages.map((c) {
              final mine = c.speakerId == widget.self.id;
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(_timeLabel(c.createdAt),
                        style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle, fontFamily: 'monospace')),
                    const SizedBox(width: 6),
                    Text(
                      c.speakerName,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: mine ? Colors.blue : Colors.green,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(c.content, style: const TextStyle(fontSize: 13, height: 1.3)),
                    ),
                  ],
                ),
              );
            }),
        ],
      ),
    );
  }
}

/// 畅聊气泡消息
