// F7-c-4a（2026-08-31）自 features/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:intl/intl.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/ios_card_group.dart';
import '../../services/api/phone_desktop_api.dart';
import '../../utils/beijing_time.dart';

/// 小手机应用页集合：相册 / 应用市场 / 日历 / 浏览器 / 主题 / 设置（2026-08-11）

/// Aurora P3 统一玻璃 AppBar（手机内页模式：半透明底 + 0.5px 描边，无 BackdropFilter）
AppBar _phoneGlassAppBar(
  BuildContext context, {
  Widget? title,
  List<Widget> actions = const [],
  PreferredSizeWidget? bottom,
}) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  return AppBar(
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
    title: title,
    actions: actions,
    bottom: bottom,
  );
}

// ── 相册：AI 生成图片 + 用户上传（iOS 图库风格：网格 → 全屏预览，保存/删除） ──
class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key, required this.characterId});
  final int characterId;
  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  late DateTime _month;
  List<Map<String, dynamic>> _notes = [];
  List<Map<String, dynamic>> _schedules = [];

  @override
  void initState() {
    super.initState();
    _month = DateTime(DateTime.now().year, DateTime.now().month);
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait<List<Map<String, dynamic>>>([
        ApiClient().getCalendarNotes(
          widget.characterId,
          month: DateFormat('yyyy-MM').format(_month),
        ),
        ApiClient().getLifeSchedules(widget.characterId, limit: 60),
      ]);
      if (!mounted) return;
      setState(() {
        _notes = results[0];
        _schedules = results[1];
      });
    } catch (_) {}
  }

  List<Map<String, dynamic>> _notesOf(String date) =>
      _notes.where((n) => n['date'] == date).toList();

  /// 某天（YYYY-MM-DD，北京时间）的 AI 日程
  List<Map<String, dynamic>> _schedulesOf(String date) => _schedules
      .where((s) => formatInTz(s['start_time'] as String? ?? '').startsWith(date))
      .toList();

  /// 备注显示：尾部带记录者署名（-xxx），避免记忆混乱（2026-08-14）
  String _noteTitle(Map<String, dynamic> n) {
    final text = (n['text'] as String? ?? '').trim();
    final author = (n['author'] as String? ?? '').trim();
    return author.isEmpty ? text : '$text  -  $author';
  }

  /// 日程开始时间显示（P0-10 修复：空串/短串不越界）
  String _schedTimeText(String raw) {
    final t = formatInTz(raw);
    return t.length >= 16 ? t.substring(11, 16) : t;
  }

  String _schedStatus(String status) {
    final l10n = AppLocalizations.of(context)!;
    return switch (status) {
      'active' => l10n.inProgress,
      'completed' => l10n.completed,
      'cancelled' => l10n.cancelled,
      'overdue' => l10n.notCompleted,
      _ => l10n.todo,
    };
  }

  Future<void> _openDay(DateTime day) async {
    final l10n = AppLocalizations.of(context)!;
    final date = DateFormat('yyyy-MM-dd').format(day);
    final dayNotes = _notesOf(date);
    final daySchedules = _schedulesOf(date);
    final ctrl = TextEditingController();
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 16, right: 16, top: 16,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 16,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.dateNotes(date), style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            const SizedBox(height: 8),
            if (daySchedules.isNotEmpty) ...[
              Text(l10n.aiSchedule, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: Theme.of(ctx).colorScheme.primary)),
              const SizedBox(height: 4),
              ...daySchedules.map((s) => ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.event, size: 18),
                    title: Text(s['title'] as String? ?? '', style: const TextStyle(fontSize: 14)),
                    subtitle: Text('${_schedTimeText(s['start_time'] as String? ?? '')} · ${s['source'] == 'fixed_routine' ? l10n.routine : s['source'] == 'goal_derived' ? l10n.goal : l10n.arrangement}'),
                    trailing: Text(_schedStatus(s['status'] as String? ?? 'scheduled'),
                        style: TextStyle(fontSize: 12, color: Theme.of(ctx).colorScheme.primary)),
                  )),
              const SizedBox(height: 8),
            ],
            if (dayNotes.isNotEmpty)
              ...dayNotes.map((n) => ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(_noteTitle(n)),
                    trailing: IconButton(
                      icon: const Icon(Icons.delete_outline, size: 18, color: Colors.red),
                      onPressed: () async {
                        await ApiClient().deleteCalendarNote(n['id'] as int);
                        if (ctx.mounted) Navigator.pop(ctx);
                        _load();
                      },
                    ),
                  )),
            TextField(
              controller: ctrl,
              maxLines: 2,
              maxLength: 200,
              decoration: InputDecoration(
                hintText: l10n.noteHint,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () async {
                  final text = ctrl.text.trim();
                  if (text.isEmpty) return;
                  await ApiClient().addCalendarNote(widget.characterId, date, text);
                  if (ctx.mounted) Navigator.pop(ctx);
                  _load();
                },
                child: Text(l10n.saveNote),
              ),
            ),
          ],
        ),
      ),
    );
    ctrl.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final now = DateTime.now();
    final first = DateTime(_month.year, _month.month, 1);
    final lead = first.weekday - 1; // 周一开头
    final daysInMonth = DateTime(_month.year, _month.month + 1, 0).day;
    return Scaffold(
      appBar: _phoneGlassAppBar(
        context,
        title: Text(l10n.calendarTitle(_month.year, _month.month)),
        actions: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: () => setState(() {
              _month = DateTime(_month.year, _month.month - 1);
              _load();
            }),
          ),
          IconButton(
            icon: const Icon(Icons.chevron_right),
            onPressed: () => setState(() {
              _month = DateTime(_month.year, _month.month + 1);
              _load();
            }),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [l10n.weekday1, l10n.weekday2, l10n.weekday3, l10n.weekday4, l10n.weekday5, l10n.weekday6, l10n.weekday7]
                  .map((w) => SizedBox(width: 40, child: Text(w, textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle))))
                  .toList(),
            ),
          ),
          Expanded(
            child: GridView.builder(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: 7,
                mainAxisSpacing: 4,
                crossAxisSpacing: 4,
              ),
              itemCount: lead + daysInMonth,
              itemBuilder: (_, i) {
                if (i < lead) return const SizedBox.shrink();
                final day = i - lead + 1;
                final dt = DateTime(_month.year, _month.month, day);
                final date = DateFormat('yyyy-MM-dd').format(dt);
                final hasNotes = _notesOf(date).isNotEmpty;
                final isToday = dt.year == now.year && dt.month == now.month && dt.day == now.day;
                return InkWell(
                  onTap: () => _openDay(dt),
                  borderRadius: BorderRadius.circular(8),
                  child: Container(
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: hasNotes
                          ? Theme.of(context).colorScheme.primaryContainer.withValues(alpha: 0.7)
                          : isToday
                              ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.18)
                              : null,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text('$day', style: TextStyle(
                          fontSize: 14,
                          fontWeight: isToday ? FontWeight.bold : null,
                          color: isToday ? Theme.of(context).colorScheme.primary : null,
                        )),
                        if (hasNotes)
                          Container(
                            width: 5, height: 5,
                            decoration: const BoxDecoration(
                              color: Colors.orange, shape: BoxShape.circle),
                          ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(8),
            child: Text(l10n.calendarHint,
                style: TextStyle(
                    fontSize: 11,
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ),
        ],
      ),
    );
  }
}

// ── 浏览器：搜索记录 + 历史（7 天保留，AI 上下文可见） ──
