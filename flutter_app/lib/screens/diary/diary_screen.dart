import "dart:collection";
import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/diary_entry.dart";
import "../../services/api_client.dart";
import "../../theme/aurora_tokens.dart";
import "../../widgets/aurora_card.dart";
import "../../widgets/empty_state.dart";
import "../../widgets/privacy_lock_view.dart";
import "package:ai_companion/theme/tokens.dart";

class DiaryScreen extends StatefulWidget {
  final int characterId;
  final String characterName;
  const DiaryScreen({super.key, required this.characterId, required this.characterName});
  @override
  State<DiaryScreen> createState() => _DiaryScreenState();
}

class _DiaryScreenState extends State<DiaryScreen> {
  final _api = ApiClient();
  List<DiaryEntry> _entries = [];
  bool _loading = true;
  final Set<String> _expandedYears = {};
  final Set<String> _expandedMonths = {};
  final Set<String> _expandedDays = {};
  Map<String, dynamic>? _privacyStatus; // {enabled, locked, cooldown_remaining, unlock_until}
  bool _privacyUnlocked = false; // 本次会话已获准查看

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    try {
      final entries = await _api.getDiary(widget.characterId);
      Map<String, dynamic>? status;
      try {
        status = await _api.getPrivacyStatus(widget.characterId, "diary");
      } catch (_) {}
      if (mounted) {
        setState(() {
          _entries = entries;
          _privacyStatus = status;
          _loading = false;
          var now = DateTime.now();
          _expandedYears.add(now.year.toString());
          _expandedMonths.add("${now.year}-${now.month.toString().padLeft(2, "0")}");
          _expandedDays.add("${now.year}-${now.month.toString().padLeft(2, "0")}-${now.day.toString().padLeft(2, "0")}");
        });
      }
    } catch (_) { if (mounted) setState(() => _loading = false); }
  }

  /// 隐私上锁判定：开关开 + 无有效解锁 + 本会话未获准
  bool _isPrivacyLocked() {
    final s = _privacyStatus;
    if (s == null) return false;
    if (_privacyUnlocked) return false;
    return s["enabled"] == true && s["locked"] == true;
  }

  SplayTreeMap<String, SplayTreeMap<String, List<DiaryEntry>>> _groupByYearMonth() {
    var result = SplayTreeMap<String, SplayTreeMap<String, List<DiaryEntry>>>((a, b) => b.compareTo(a));
    for (final entry in _entries) {
      var parts = entry.diaryDate.split("-");
      if (parts.length != 3) continue;
      var year = parts[0];
      var month = parts[1];
      result.putIfAbsent(year, () => SplayTreeMap<String, List<DiaryEntry>>((a, b) => b.compareTo(a)));
      result[year]!.putIfAbsent(month, () => []);
      result[year]![month]!.add(entry);
    }
    return result;
  }

  String _monthLabel(String m) {
    final l10n = AppLocalizations.of(context)!;
    var labels = ["", l10n.monthJan, l10n.monthFeb, l10n.monthMar, l10n.monthApr, l10n.monthMay, l10n.monthJun, l10n.monthJul, l10n.monthAug, l10n.monthSep, l10n.monthOct, l10n.monthNov, l10n.monthDec];
    var idx = int.tryParse(m);
    return (idx != null && idx >= 1 && idx <= 12) ? labels[idx] : l10n.monthNumeric(m);
  }

  String _formatDate(String dateStr) {
    var parts = dateStr.split("-");
    if (parts.length != 3) return dateStr;
    return "${parts[1]}-${parts[2]}";
  }

  /// 从完整 yyyy-MM-dd 中抽取出 day 部分，供 _buildDaySection 重建 yyyy-MM-dd key。
  String _dayPart(String date) => date.split("-").last;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P7 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
        backgroundColor: isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55),
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          bottom: BorderSide(
            color: isDark
                ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                : Colors.black.withValues(alpha: AppGlass.borderAlpha),
            width: 0.5,
          ),
        ),
        title: Text(l10n.diaryTitle(widget.characterName)),
      ),
      body: _loading ? const Center(child: CircularProgressIndicator())
          : _isPrivacyLocked() ? PrivacyLockView(
              characterId: widget.characterId,
              target: "diary",
              contentName: l10n.diary,
              onUnlocked: () => setState(() => _privacyUnlocked = true),
            )
          : _entries.isEmpty
              // Aurora P7：空态 EmptyState 统一渲染
              ? EmptyState(icon: Icons.auto_stories, title: l10n.noDiary)
          : _buildDiaryTree(),
    );
  }

  Widget _buildDiaryTree() {
    var grouped = _groupByYearMonth();
    return ListView(padding: const EdgeInsets.all(12), children: [
      for (var yearEntry in grouped.entries) _buildYearSection(yearEntry.key, yearEntry.value),
    ]);
  }

  Widget _buildYearSection(String year, SplayTreeMap<String, List<DiaryEntry>> months) {
    final l10n = AppLocalizations.of(context)!;
    var isExpanded = _expandedYears.contains(year);
    var totalDays = months.values.fold(0, (sum, days) => sum + days.length);
    return Card(margin: const EdgeInsets.only(bottom: 8), child: Column(children: [
      InkWell(onTap: () {
        setState(() {
          if (isExpanded) {
            _expandedYears.remove(year);
            for (var k in months.keys) {
              _expandedMonths.remove("$year-$k");
            }
            _expandedDays.removeWhere((d) => d.startsWith("$year-"));
          }
          else {
            _expandedYears.add(year);
          }
        });
      }, child: Container(width: double.infinity, padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
        decoration: BoxDecoration(border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor))),
        child: Row(children: [
          Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 20, color: AppColors.separator),
          const SizedBox(width: 8), Text(year, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          const Spacer(), Text(l10n.diaryCount(totalDays), style: const TextStyle(fontSize: 12, color: AppColors.textSecondary)),
        ]),
      )),
      if (isExpanded) ...months.entries.map((mEntry) => _buildMonthSection(year, mEntry.key, mEntry.value)),
    ]));
  }

  Widget _buildMonthSection(String year, String month, List<DiaryEntry> entries) {
    final l10n = AppLocalizations.of(context)!;
    var key = "$year-$month";
    var isExpanded = _expandedMonths.contains(key);
    return Column(children: [
      InkWell(onTap: () { setState(() { if (isExpanded) {
        _expandedMonths.remove(key);
      } else {
        _expandedMonths.add(key);
      } }); },
        child: Container(width: double.infinity, padding: const EdgeInsets.only(left: 24, right: 16, top: 10, bottom: 10),
          decoration: BoxDecoration(border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor))),
          child: Row(children: [
            Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: AppColors.separator),
            const SizedBox(width: 6), Text(_monthLabel(month), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
            const Spacer(), Text(l10n.diaryCount(entries.length), style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
          ]),
        ),
      ),
      if (isExpanded) ..._groupByDay(entries).entries.map((dEntry) => _buildDaySection(year, month, _dayPart(dEntry.key), dEntry.value)),
    ]);
  }

  SplayTreeMap<String, List<DiaryEntry>> _groupByDay(List<DiaryEntry> entries) {
    var result = SplayTreeMap<String, List<DiaryEntry>>((a, b) => b.compareTo(a));
    for (final entry in entries) {
      result.putIfAbsent(entry.diaryDate, () => []);
      result[entry.diaryDate]!.add(entry);
    }
    return result;
  }

  Widget _buildDaySection(String year, String month, String day, List<DiaryEntry> entries) {
    final l10n = AppLocalizations.of(context)!;
    var key = "$year-$month-$day";
    var isExpanded = _expandedDays.contains(key);
    return Column(children: [
      InkWell(onTap: () { setState(() { if (isExpanded) {
        _expandedDays.remove(key);
      } else {
        _expandedDays.add(key);
      } }); },
        child: Container(width: double.infinity, padding: const EdgeInsets.only(left: 40, right: 16, top: 10, bottom: 10),
          decoration: BoxDecoration(border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor))),
          child: Row(children: [
            Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 14, color: AppColors.separator),
            const SizedBox(width: 6), Text(_formatDate(key), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
            const Spacer(), Text(l10n.diaryCount(entries.length), style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
          ]),
        ),
      ),
      if (isExpanded) ...entries.map((entry) => _buildDiaryEntry(entry)),
    ]);
  }

  Widget _buildDiaryEntry(DiaryEntry entry) {
    // Aurora P7：日记条目 → AuroraCard
    return Padding(
      padding: const EdgeInsets.only(left: 36, right: 8, top: 4, bottom: 4),
      child: AuroraCard(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.auto_stories, size: 16, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 6),
            Text(_formatDate(entry.diaryDate), style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: Theme.of(context).colorScheme.primary)),
          ]),
          const SizedBox(height: 8),
          Text(entry.content, style: const TextStyle(fontSize: 14, height: 1.6)),
        ]),
      ),
    );
  }
}
