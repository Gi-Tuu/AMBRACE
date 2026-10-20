// 个人备忘面板（M1a-3）：GET /api/v1/user/memos（无分页，updated_at 倒序）
// ＋ **本人条目**的新建 / 编辑 / 删除。
//
// 纪律：
// - 读仍走 ReaderApi（只 GET）；写只走 EditorApi（写请求只由明确点击触发）；
// - **编辑/删除入口只对「本人条目」出现**：需 profile id（myUserId）与条目 user_id 同时具备
//   且相等；身份元数据缺失/未就绪 ⇒ 不给写入口（不猜本人），家庭共享他人条目一律只读；
// - 标题可空（双语占位）；正文是用户内容原文，不翻译；前端按后端真实上限（标题 100 / 正文 2000）
//   提示，不让后端静默截断替代前端校验；
// - 提交失败保留原文便于重试；成功采用**返回对象**校验归属后再刷新；删除取消不发请求、成功才移除；
// - 初始化/刷新/重试 GET **绝不**偷偷触发写。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/editor_strings.dart';
import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import '../editor/personal_editor_sheet.dart';
import 'reader_state.dart';

class MemosPane extends StatefulWidget {
  const MemosPane({
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
  final int? myUserId; // GET /api/v1/auth/profile 的本人 id；null = 未就绪
  final ReaderStrings rs;
  final CoreStrings cs;
  final EditorStrings es;

  @override
  State<MemosPane> createState() => _MemosPaneState();
}

class _MemosPaneState extends State<MemosPane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderMemo>> _loader =
      ReaderLoader<List<ReaderMemo>>();

  String _status = '';

  @override
  void initState() {
    super.initState();
    _reload(); // GET only
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load(
      (ct) => _api.listMemos(cancelToken: ct),
      isEmpty: (l) => l.isEmpty,
    );
  }

  /// 是否本人条目：profile id 与条目 user_id 都具备且相等（否则只读，不猜本人）。
  bool _isMine(ReaderMemo m) =>
      widget.myUserId != null &&
      m.userId != null &&
      m.userId == widget.myUserId;

  Future<void> _openEditor({
    required String title,
    required ReaderMemo? editing,
  }) async {
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      isDismissible: false, // 不给「点空白静默丢内容」的机会
      enableDrag: false,
      builder: (_) => PersonalEditorSheet(
        es: widget.es,
        kind: 'memo',
        title: title,
        initialTitle: editing?.title ?? '',
        initialContent: editing?.content ?? '',
        initialDate: '',
        onSubmit: (t, content, _) async {
          try {
            final ReaderMemo r = editing == null
                ? await widget.editor
                    .createMemo(title: t.isEmpty ? null : t, content: content)
                : await widget.editor
                    .updateMemo(editing.id, title: t, content: content);
            // 采用返回对象校验归属：返回行身份与本次操作不符 ⇒ 明确错误，不当作成功
            if (widget.myUserId != null &&
                r.userId != null &&
                r.userId != widget.myUserId) {
              return widget.es.t('onlyMineEditable');
            }
            return null;
          } on Object catch (e) {
            return '${widget.es.t('saveFailed')}：${localizedError(e, widget.cs)}';
          }
        },
      ),
    );
    if (ok == true && mounted) _reload();
  }

  Future<void> _deleteMemo(ReaderMemo m) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.es.t('deleteTitle')),
        content: Text(widget.es.t('deleteMemoBody')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text(widget.es.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: Text(widget.es.t('delete'))),
        ],
      ),
    );
    if (yes != true) return; // 取消：不发请求
    try {
      await widget.editor.deleteMemo(m.id);
      if (!mounted) return;
      final cur = _loader.data ?? const <ReaderMemo>[];
      _loader.commitData([
        for (final x in cur)
          if (x.id != m.id) x
      ], isEmpty: (l) => l.isEmpty); // 成功才移除
    } on Object catch (e) {
      if (!mounted) return;
      setState(() =>
          _status = '${widget.es.t('deleteFailed')}：${localizedError(e, widget.cs)}');
    }
  }

  void _openDetail(ReaderMemo m) {
    final rs = widget.rs;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.85,
        child: SafeArea(
          child: ListView(padding: const EdgeInsets.all(16), children: [
            Text(m.title?.isNotEmpty ?? false ? m.title! : rs.t('untitled'),
                style: Theme.of(ctx).textTheme.titleMedium),
            Text('${rs.t('updatedAt')} ${m.updatedAt}',
                style: Theme.of(ctx).textTheme.bodySmall),
            const SizedBox(height: 8),
            SelectableText(m.content),
          ]),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final es = widget.es;
    final ready = widget.myUserId != null;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
        child: Row(children: [
          Expanded(
            child: Text(rs.t('modMemos'),
                style: Theme.of(context).textTheme.labelLarge),
          ),
          if (!ready)
            Text(es.t('identityNotReady'),
                style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(width: 8),
          OutlinedButton(
            // 身份未就绪 ⇒ 不给写入口
            onPressed: ready ? () => _openEditor(title: es.t('newMemo'), editing: null) : null,
            child: Text(es.t('newMemo')),
          ),
        ]),
      ),
      if (_status.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: 6, left: 12, right: 12),
          child: Text(_status,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ),
      if (!ready)
        Padding(
          padding: const EdgeInsets.only(top: 2, left: 12, right: 12),
          child: Text(es.t('onlyMineEditable'),
              style: Theme.of(context).textTheme.bodySmall),
        ),
      Expanded(
        child: ReaderAsyncView<List<ReaderMemo>>(
          loader: _loader,
          rs: rs,
          cs: widget.cs,
          onRetry: _reload,
          dataBuilder: (items) => ListView(children: [
            for (final m in items)
              ListTile(
                title: Text(
                  m.title?.isNotEmpty ?? false ? m.title! : rs.t('untitled'),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(m.content.split('\n').first,
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                onTap: () => _openDetail(m),
                trailing: _isMine(m)
                    ? Row(mainAxisSize: MainAxisSize.min, children: [
                        IconButton(
                          tooltip: es.t('edit'),
                          icon: const Icon(Icons.edit_outlined),
                          onPressed: () =>
                              _openEditor(title: es.t('editMemo'), editing: m),
                        ),
                        IconButton(
                          tooltip: es.t('delete'),
                          icon: const Icon(Icons.delete_outline),
                          onPressed: () => _deleteMemo(m),
                        ),
                      ])
                    : null,
              ),
          ]),
        ),
      ),
    ]);
  }
}
