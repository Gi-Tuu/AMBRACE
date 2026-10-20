// 个人日记面板（M1a-3）：GET /api/v1/user/diaries（无分页，diary_date 倒序）
// ＋ **本人条目**的新建 / 编辑 / 删除。
//
// 与只读「角色日记」是**不同数据**：本面板绝不接角色日记的生成/修改。
//
// 纪律：
// - 日期一律**纯字符串** `YYYY-MM-DD`：**不做任何 UTC 换算**（不能把日期挪到前后一天）；
// - 编辑初值**绑定所选具体 id/user_id/date**，绝不盲信 `GET /{date}` 选出的家庭最新行——
//   `GET /{date}` 是家庭共享口径（同日取 updated_at 最新），拿它当本人初值会把家人的正文
//   写成本人的同日日记；本人初值只从**列表行**（带 user_id）取；
// - 新建日期若本人已有条目 ⇒ 转为编辑本人那条并明确提示（服务端 upsert 语义）；
//   共享家人同日条目**不**作为本人编辑初值或保存目标，也不因本人写入而改变；
// - 删除必须 `DELETE /user/diaries/{数值 id}`（不是日期）；取消不发请求、成功才移除；
// - 身份（profile id）未就绪 ⇒ 不给写入口；初始化/刷新/重试 GET 绝不触发写。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../l10n/editor_strings.dart';
import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import '../editor/personal_editor_sheet.dart';
import 'reader_state.dart';

class MyDiaryPane extends StatefulWidget {
  const MyDiaryPane({
    super.key,
    required this.client,
    required this.editor,
    required this.myUserId,
    required this.rs,
    required this.cs,
    required this.es,
  });

  final AmbApiClient client;
  final EditorApi editor;
  final int? myUserId;
  final ReaderStrings rs;
  final CoreStrings cs;
  final EditorStrings es;

  @override
  State<MyDiaryPane> createState() => _MyDiaryPaneState();
}

class _MyDiaryPaneState extends State<MyDiaryPane> {
  static const int _segment = 20;

  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderUserDiary>> _loader =
      ReaderLoader<List<ReaderUserDiary>>();

  late String _month; // 'YYYY-MM'
  int _showCount = _segment;
  String _status = '';

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _month =
        '${now.year.toString().padLeft(4, '0')}-${now.month.toString().padLeft(2, '0')}';
    _reload(); // GET only
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load(
      (ct) => _api.listUserDiaries(cancelToken: ct),
      isEmpty: (l) => l.isEmpty,
    );
  }

  void _shiftMonth(int delta) {
    final y = int.parse(_month.substring(0, 4));
    final m = int.parse(_month.substring(5, 7));
    final total = y * 12 + (m - 1) + delta;
    setState(() {
      _month =
          '${(total ~/ 12).toString().padLeft(4, '0')}-${(total % 12 + 1).toString().padLeft(2, '0')}';
      _showCount = _segment;
    });
  }

  /// 本机「今天」的纯日期串（**不经** UTC 转换，避免挪到前后一天）。
  static String _todayLocal() {
    final n = DateTime.now();
    String two(int v) => v.toString().padLeft(2, '0');
    return '${n.year.toString().padLeft(4, '0')}-${two(n.month)}-${two(n.day)}';
  }

  bool _isMine(ReaderUserDiary d) =>
      widget.myUserId != null &&
      d.userId != null &&
      d.userId == widget.myUserId;

  /// 本人指定日期的条目（只来自**列表行**，不调用家庭共享的 GET /{date}）。
  ReaderUserDiary? _mineOn(String date) {
    final items = _loader.data ?? const <ReaderUserDiary>[];
    for (final d in items) {
      if (d.diaryDate == date && _isMine(d)) return d;
    }
    return null;
  }

  Future<void> _openEditor({required String date, ReaderUserDiary? editing}) async {
    final es = widget.es;
    final isNew = editing == null;
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      isDismissible: false,
      enableDrag: false,
      builder: (_) => PersonalEditorSheet(
        es: es,
        kind: 'diary',
        title: isNew ? es.t('newDiary') : es.t('editDiary'),
        initialDate: date,
        initialContent: editing?.content ?? '',
        hintForDate: (d) =>
            _mineOn(d) != null ? es.t('existingMineWillEdit') : null,
        onSubmit: (t, content, d) async {
          try {
            final ReaderUserDiary r =
                await widget.editor.upsertUserDiary(d, content: content);
            // 采用返回对象校验归属：不是本人 / 日期与提交不符 ⇒ 明确错误，不当作成功
            if (widget.myUserId != null &&
                r.userId != null &&
                r.userId != widget.myUserId) {
              return es.t('onlyMineEditable');
            }
            if (r.diaryDate != d) return es.t('onlyMineEditable');
            return null;
          } on Object catch (e) {
            return '${es.t('saveFailed')}：${localizedError(e, widget.cs)}';
          }
        },
      ),
    );
    if (ok == true && mounted) _reload();
  }

  Future<void> _deleteDiary(ReaderUserDiary d) async {
    final es = widget.es;
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(es.t('deleteTitle')),
        content: Text(es.t('deleteDiaryBody')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text(es.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: Text(es.t('delete'))),
        ],
      ),
    );
    if (yes != true) return; // 取消：不发请求
    try {
      await widget.editor.deleteUserDiaryById(d.id); // 必须按数值 id
      if (!mounted) return;
      final cur = _loader.data ?? const <ReaderUserDiary>[];
      _loader.commitData([
        for (final x in cur)
          if (x.id != d.id) x
      ], isEmpty: (l) => l.isEmpty); // 成功才移除
    } on Object catch (e) {
      if (!mounted) return;
      setState(() =>
          _status = '${es.t('deleteFailed')}：${localizedError(e, widget.cs)}');
    }
  }

  void _openDay(String diaryDate) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.85,
        child: _UserDiaryDaySheet(
            diaryDate: diaryDate, api: _api, rs: widget.rs, cs: widget.cs),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final es = widget.es;
    final theme = Theme.of(context);
    final ready = widget.myUserId != null;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
        child: Row(children: [
          Expanded(
            child: Text(rs.t('modMyDiary'), style: theme.textTheme.labelLarge),
          ),
          if (!ready)
            Text(es.t('identityNotReady'), style: theme.textTheme.bodySmall),
          const SizedBox(width: 8),
          OutlinedButton(
            onPressed: ready
                ? () => _openEditor(date: _todayLocal(), editing: _mineOn(_todayLocal()))
                : null,
            child: Text(es.t('newDiary')),
          ),
        ]),
      ),
      if (_status.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 6, left: 12, right: 12),
          child: Text(_status, style: TextStyle(color: theme.colorScheme.error)),
        ),
      if (!ready)
        Padding(
          padding: const EdgeInsets.only(top: 2, left: 12, right: 12),
          child: Text(es.t('onlyMineEditable'), style: theme.textTheme.bodySmall),
        ),
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
      const Divider(height: 1),
      Expanded(
        child: ReaderAsyncView<List<ReaderUserDiary>>(
          loader: _loader,
          rs: rs,
          cs: widget.cs,
          onRetry: _reload,
          dataBuilder: (items) {
            final monthItems =
                items.where((e) => e.diaryDate.startsWith(_month)).toList();
            if (monthItems.isEmpty) {
              return Center(child: Text(rs.t('empty')));
            }
            final shown = monthItems.take(_showCount).toList();
            return ListView(children: [
              for (final e in shown)
                ListTile(
                  title: Text(e.diaryDate),
                  subtitle: Text(e.content.split('\n').first,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  onTap: () => _openDay(e.diaryDate),
                  trailing: _isMine(e)
                      ? Row(mainAxisSize: MainAxisSize.min, children: [
                          IconButton(
                            tooltip: es.t('edit'),
                            icon: const Icon(Icons.edit_outlined),
                            onPressed: () => _openEditor(
                                date: e.diaryDate, editing: e),
                          ),
                          IconButton(
                            tooltip: es.t('delete'),
                            icon: const Icon(Icons.delete_outline),
                            onPressed: () => _deleteDiary(e),
                          ),
                        ])
                      : null,
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

/// 单日个人日记详情抽屉：实时拉 `GET /user/diaries/{diary_date}`（独立加载态，
/// 404/服务错误显示本地化错误+重试，不伪装成空内容）。
///
/// ⚠ 这是**家庭共享口径**的只读查看（同日取 updated_at 最新的一条）；
/// 编辑入口不经过它 —— 本人编辑初值只从列表行（带 user_id）取，避免拿家人正文当本人初值。
class _UserDiaryDaySheet extends StatefulWidget {
  const _UserDiaryDaySheet({
    required this.diaryDate,
    required this.api,
    required this.rs,
    required this.cs,
  });

  final String diaryDate;
  final ReaderApi api;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<_UserDiaryDaySheet> createState() => _UserDiaryDaySheetState();
}

class _UserDiaryDaySheetState extends State<_UserDiaryDaySheet> {
  late final ReaderLoader<ReaderUserDiary> _loader =
      ReaderLoader<ReaderUserDiary>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load(
        (CancelToken ct) =>
            widget.api.getUserDiary(widget.diaryDate, cancelToken: ct),
        isEmpty: (d) => d.content.isEmpty);
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: ReaderAsyncView<ReaderUserDiary>(
          loader: _loader,
          rs: widget.rs,
          cs: widget.cs,
          onRetry: _reload,
          dataBuilder: (d) => ListView(children: [
            Text(d.diaryDate, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            SelectableText(d.content),
          ]),
        ),
      ),
    );
  }
}
