// 只读工作台（M1a-2）· 角色日记面板：
// 列表 GET /api/v1/diary/{cid}（无分页，一次全量，客户端只分段展示、不冒充后端分页），
// 月导航 GET /{cid}/dates?month=YYYY-MM（prev/next 含跨年进位）。
// 纪律：diary_date 是纯日期字符串 YYYY-MM-DD，**原样展示、不做任何时区换算**；
// 日记正文是角色内容，不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

class CharDiaryPane extends StatefulWidget {
  const CharDiaryPane({
    super.key,
    required this.character,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final Character character;
  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<CharDiaryPane> createState() => _CharDiaryPaneState();
}

class _CharDiaryPaneState extends State<CharDiaryPane> {
  static const int _segment = 20;

  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderDiaryEntry>> _loader =
      ReaderLoader<List<ReaderDiaryEntry>>();
  late final ReaderLoader<List<String>> _datesLoader =
      ReaderLoader<List<String>>();

  late String _month; // 'YYYY-MM' 纯字符串
  int _showCount = _segment;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _month =
        '${now.year.toString().padLeft(4, '0')}-${now.month.toString().padLeft(2, '0')}';
    _reload();
  }

  @override
  void dispose() {
    _loader.dispose();
    _datesLoader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load(
      (ct) => _api.listCharacterDiary(widget.character.id, cancelToken: ct),
      isEmpty: (l) => l.isEmpty,
    );
    _reloadDates();
  }

  void _reloadDates() {
    _datesLoader.load(
      (ct) => _api.getDiaryDates(widget.character.id, _month, cancelToken: ct),
      isEmpty: (l) => l.isEmpty,
    );
  }

  /// 月份平移（纯年月算术，跨年进位；与时区无关）。
  void _shiftMonth(int delta) {
    final y = int.parse(_month.substring(0, 4));
    final m = int.parse(_month.substring(5, 7));
    final total = y * 12 + (m - 1) + delta;
    setState(() {
      _month =
          '${(total ~/ 12).toString().padLeft(4, '0')}-${(total % 12 + 1).toString().padLeft(2, '0')}';
      _showCount = _segment;
    });
    _reloadDates();
  }

  void _openEntry(ReaderDiaryEntry e) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.85,
        child: SafeArea(
          child: ListView(padding: const EdgeInsets.all(16), children: [
            Text(e.diaryDate, style: Theme.of(ctx).textTheme.titleMedium),
            const SizedBox(height: 8),
            SelectableText(e.content),
          ]),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Column(children: [
      // 月导航（prev/next）
      Row(mainAxisAlignment: MainAxisAlignment.center, children: [
        IconButton(
          tooltip: rs.t('prevMonth'),
          icon: const Icon(Icons.chevron_left),
          onPressed: () => _shiftMonth(-1),
        ),
        Text(_month, style: theme.textTheme.titleMedium),
        IconButton(
          tooltip: rs.t('nextMonth'),
          icon: const Icon(Icons.chevron_right),
          onPressed: () => _shiftMonth(1),
        ),
      ]),
      // 当月有日记的日期（纯日期字符串原样展示，点了看正文）
      AnimatedBuilder(
        animation: _datesLoader,
        builder: (context, _) {
          final dates = _datesLoader.data ?? const <String>[];
          if (dates.isEmpty) {
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Text(rs.t('noDiaryDates'), style: theme.textTheme.bodySmall),
            );
          }
          return SizedBox(
            height: 44,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                for (final d in dates)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ActionChip(
                      label: Text(d),
                      onPressed: () {
                        final items = _loader.data ?? const <ReaderDiaryEntry>[];
                        for (final e in items) {
                          if (e.diaryDate == d) {
                            _openEntry(e);
                            return;
                          }
                        }
                      },
                    ),
                  ),
              ],
            ),
          );
        },
      ),
      const Divider(height: 1),
      Expanded(
        child: ReaderAsyncView<List<ReaderDiaryEntry>>(
          loader: _loader,
          rs: rs,
          cs: widget.cs,
          onRetry: _reload,
          dataBuilder: (items) {
            final monthItems =
                items.where((e) => e.diaryDate.startsWith(_month)).toList();
            if (monthItems.isEmpty) {
              return Center(child: Text(rs.t('noDiaryDates')));
            }
            final shown = monthItems.take(_showCount).toList();
            return ListView(children: [
              for (final e in shown)
                ListTile(
                  title: Text(e.diaryDate),
                  subtitle: Text(e.content.split('\n').first,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () => _openEntry(e),
                ),
              if (shown.length < monthItems.length)
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Center(
                    child: OutlinedButton(
                      onPressed: () =>
                          setState(() => _showCount += _segment),
                      child: Text(rs.t('loadMore')),
                    ),
                  ),
                ),
            ]);
          },
        ),
      ),
    ]);
  }
}
