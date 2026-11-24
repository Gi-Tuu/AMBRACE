import 'dart:async';
// 只读工作台（M1a-2）· 记忆浏览面板：
// 列表 GET /api/v1/memories（character_id + 可选 memory_type + skip/limit 真分页），
// 详情抽屉含正文/来源/认知状态/意义 + 链条 GET /{id}/chain + 事实历史 GET /{id}/history。
// 纪律：
// - memory_type 透传不校验——筛选选项从已加载数据**动态收集**，不硬编码枚举；
// - 续页 = **独立原始窗口游标**（服务端已消费的行数）+ 独立 CancelToken + 代次归属：
//   去重只影响展示、**不作 offset**；刷新/换筛选作废在飞续页并清游标，旧页 2 不得混入新筛选；
// - epistemic_status 原文展示（INFERRED 不升格为 FACT）；source_label 后端原文展示；
// - 全部只读：不调用任何 POST/删除/归档接口。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/pwa_entrance.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

class MemoriesPane extends StatefulWidget {
  const MemoriesPane({
    super.key,
    required this.character,
    required this.client,
    required this.editor,
    required this.rs,
    required this.cs,
  });

  final Character character;
  final AmbApiClient client;
  final EditorApi editor;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<MemoriesPane> createState() => _MemoriesPaneState();
}

class _MemoriesPaneState extends State<MemoriesPane> {
  static const int _limit = 50;

  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderMemory>> _loader =
      ReaderLoader<List<ReaderMemory>>();

  int _total = 0;
  /// 服务端**原始窗口游标**：已消费掉的行数（skip 的来源）。
  /// 不等于 `_items.length`——去重只改展示，游标必须跟着服务端窗口走，不能回退。
  int _serverSkip = 0;
  bool _hasMore = false;
  String? _type;

  bool _loadingMore = false;
  String? _moreError;
  final TextEditingController _searchCtrl = TextEditingController();
  Timer? _searchDebounce;
  String _search = '';
  CancelToken? _moreCt;
  Object? _moreBusyOwner;

  /// 首屏/刷新/切筛选代次：续页按它判定归属（旧的整页丢弃，含元数据）。
  int _gen = 0;

  List<ReaderMemory> get _items => _loader.data ?? const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _gen++; // 作废在飞续页
    _cancelMore();
    _searchDebounce?.cancel();
    _searchCtrl.dispose();
    _loader.dispose();
    super.dispose();
  }

  void _cancelMore() {
    final c = _moreCt;
    _moreCt = null;
    if (c != null && !c.isCancelled) c.cancel('superseded');
  }

  /// 首屏 / 刷新 / 切筛选：作废在飞续页 + 清游标；total/游标只在本次结果**被采纳后**提交
  /// （旧刷新的元数据不许写进新列表）。
  Future<void> _reload() async {
    final gen = ++_gen;
    _cancelMore();
    _moreBusyOwner = null;
    _loadingMore = false;
    _moreError = null;
    ReaderMemoryPage? page;
    final ok = await _loader.load((ct) async {
      page = await _api.listMemories(
        characterId: widget.character.id,
        memoryType: _type,
        search: _search,
        skip: 0,
        limit: _limit,
        cancelToken: ct,
      );
      return page!.items;
    }, isEmpty: (l) => l.isEmpty);
    if (!ok || !mounted || gen != _gen) return;
    setState(() {
      _serverSkip = page!.items.length; // 首屏消费掉的行数
      _total = page!.total;
      _hasMore = page!.items.length == _limit && _serverSkip < _total;
    });
  }

  /// 续页：独立 CancelToken + 代次/角色/筛选归属 + busy 归属；
  /// offset 用原始窗口游标推进，重复页按 id 去重但**不回退游标、不死循环**。
  Future<void> _loadMore() async {
    if (_loadingMore || !_hasMore) return; // 同一页不重入
    final gen = _gen;
    final owner = Object();
    final ct = CancelToken();
    _moreCt = ct;
    _moreBusyOwner = owner;
    setState(() {
      _loadingMore = true;
      _moreError = null;
    });
    try {
      final page = await _api.listMemories(
        characterId: widget.character.id,
        memoryType: _type,
        search: _search,
        skip: _serverSkip,
        limit: _limit,
        cancelToken: ct,
      );
      if (!mounted || gen != _gen) return; // 旧筛选/旧刷新：整页丢弃（连元数据一起）
      final merged = <ReaderMemory>[..._items];
      final seen = merged.map((e) => e.id).toSet();
      for (final m in page.items) {
        if (seen.add(m.id)) merged.add(m); // 去重只影响展示
      }
      _loader.commitData(merged);
      setState(() {
        _serverSkip = _serverSkip + page.items.length; // 原始窗口推进，与去重无关
        _total = page.total;
        _hasMore = page.items.length == _limit && _serverSkip < _total;
      });
    } on Object catch (e) {
      if (!mounted || gen != _gen || isReaderCancel(e)) return;
      setState(() => _moreError = localizedError(e, widget.cs));
    } finally {
      if (identical(_moreCt, ct)) _moreCt = null;
      if (identical(_moreBusyOwner, owner)) {
        _moreBusyOwner = null;
        if (mounted) setState(() => _loadingMore = false); // 旧 finally 不得清新 busy
      }
    }
  }

  String _titleOf(ReaderMemory m) {
    if (m.title?.isNotEmpty ?? false) return m.title!;
    final first = m.content.split('\n').first.trim();
    return first.length <= 40 ? first : '${first.substring(0, 40)}…';
  }

  String _stars(int n) => '★' * n.clamp(0, 5).toInt();

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    // 筛选选项从已加载数据动态收集（含当前选中值，防止下拉值缺失）
    final types = _items.map((e) => e.memoryType).where((t) => t.isNotEmpty).toSet().toList()
      ..sort();
    if (_type != null && !types.contains(_type)) types.add(_type!);
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
        child: Row(children: [
          Expanded(
            child: TextField(
              controller: _searchCtrl,
              onChanged: (v) {
                _searchDebounce?.cancel();
                _searchDebounce = Timer(const Duration(milliseconds: 350), () {
                  if (!mounted) return;
                  setState(() => _search = v.trim());
                  _reload();
                });
              },
              decoration: InputDecoration(
                isDense: true,
                hintText: rs.t('searchMemories'),
                prefixIcon: const Icon(Icons.search, size: 20),
                suffixIcon: _search.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () {
                          _searchCtrl.clear();
                          _searchDebounce?.cancel();
                          setState(() => _search = '');
                          _reload();
                        },
                      ),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(22)),
              ),
            ),
          ),
          const SizedBox(width: 8),
          DropdownButton<String?>(
            value: _type,
            items: [
              DropdownMenuItem<String?>(
                  value: null, child: Text(rs.t('filterAllTypes'))),
              for (final t in types)
                DropdownMenuItem<String?>(value: t, child: Text(t)),
            ],
            onChanged: (v) {
              setState(() => _type = v);
              _reload();
            },
          ),
        ]),
      ),
      const Divider(height: 1),
      Expanded(
        child: ReaderAsyncView<List<ReaderMemory>>(
          loader: _loader,
          rs: rs,
          cs: widget.cs,
          onRetry: _reload,
          dataBuilder: (items) => PwaListEntrance(
            builder: (animate) => ListView(children: [
            for (final m in items)
              PwaEntrance(
                key: ValueKey(m.id),
                animate: animate,
                child: ListTile(
                title: Text(_titleOf(m), maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(
                  '${m.memoryType} · ${m.sourceLabel ?? rs.t('unknown')} · ${_stars(m.importance)}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: m.isPinned ? const Icon(Icons.push_pin, size: 16) : null,
                onTap: () => _openDetail(m),
                ),
              ),
            if (_loadingMore)
              const Padding(
                padding: EdgeInsets.all(12),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_moreError != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
                  Flexible(child: Text(_moreError!)),
                  const SizedBox(width: 8),
                  OutlinedButton(onPressed: _loadMore, child: Text(rs.t('retry'))),
                ]),
              )
            else if (_hasMore)
              Padding(
                padding: const EdgeInsets.all(12),
                child: Center(
                  child: OutlinedButton(
                      onPressed: _loadMore, child: Text(rs.t('loadMore'))),
                ),
              ),
            ])),
        ),
      ),
    ]);
  }

  Future<void> _openDetail(ReaderMemory m) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.9,
        child: _MemoryDetailSheet(
          memory: m,
          api: _api,
          editor: widget.editor,
          rs: widget.rs,
          cs: widget.cs,
        ),
      ),
    );
    if (changed == true && mounted) _reload();
  }
}

/// 记忆详情抽屉：正文/元信息 + 链条 + 事实修正历史（各自独立加载态，互不误伤）。
class _MemoryDetailSheet extends StatefulWidget {
  const _MemoryDetailSheet({
    required this.memory,
    required this.api,
    required this.editor,
    required this.rs,
    required this.cs,
  });

  final ReaderMemory memory;
  final ReaderApi api;
  final EditorApi editor;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<_MemoryDetailSheet> createState() => _MemoryDetailSheetState();
}

class _MemoryDetailSheetState extends State<_MemoryDetailSheet> {
  late final ReaderLoader<List<ReaderMemoryChainNode>> _chain =
      ReaderLoader<List<ReaderMemoryChainNode>>();
  late final ReaderLoader<ReaderFactHistory> _history =
      ReaderLoader<ReaderFactHistory>();
  late final ReaderLoader<ReaderMemoryDerivations> _derivations =
      ReaderLoader<ReaderMemoryDerivations>();
  late final ReaderLoader<ReaderMemoryWhy?> _why =
      ReaderLoader<ReaderMemoryWhy?>();
  late final ReaderLoader<ReaderMemoryLifecycle?> _lifecycle =
      ReaderLoader<ReaderMemoryLifecycle?>();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _loadChain();
    _loadHistory();
    _loadDerivations();
    _loadWhy();
    _loadLifecycle();
  }

  @override
  void dispose() {
    _chain.dispose();
    _history.dispose();
    _derivations.dispose();
    _why.dispose();
    _lifecycle.dispose();
    super.dispose();
  }

  void _loadChain() {
    _chain.load(
        (CancelToken ct) =>
            widget.api.getMemoryChain(widget.memory.id, cancelToken: ct),
        isEmpty: (l) => l.isEmpty);
  }

  void _loadHistory() {
    _history.load(
        (CancelToken ct) =>
            widget.api.getMemoryHistory(widget.memory.id, cancelToken: ct),
        isEmpty: (h) => h.factContent.isEmpty && h.corrections.isEmpty);
  }

  void _loadDerivations() {
    _derivations.load(
        (ct) => widget.api.getMemoryDerivations(widget.memory.id, cancelToken: ct),
        isEmpty: (d) => d.parents.items.isEmpty && d.children.items.isEmpty);
  }

  void _loadWhy() {
    _why.load(
        (ct) => widget.api.getMemoryWhy(widget.memory.id, cancelToken: ct),
        isEmpty: (w) => w == null || (!w.hasSource && w.sources.isEmpty));
  }

  void _loadLifecycle() {
    _lifecycle.load(
        (ct) => widget.api.getMemoryLifecycle(widget.memory.id, cancelToken: ct),
        isEmpty: (l) => l == null);
  }

  Future<void> _editContent() async {
    final ctrl = TextEditingController(text: widget.memory.content);
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('editMemoryTitle')),
        content: TextField(
          controller: ctrl,
          maxLines: 8,
          autofocus: true,
          decoration: InputDecoration(hintText: widget.rs.t('content')),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(widget.rs.t('cancel'))),
          FilledButton(
            onPressed: () {
              final v = ctrl.text.trim();
              if (v.isEmpty) return;
              Navigator.pop(ctx, v);
            },
            child: Text(widget.rs.t('save')),
          ),
        ],
      ),
    );
    // 不在此处 dispose：对话框退场动画期间 TextField 仍可能监听 controller；
    // 控制器生命周期随短命对话框由 GC 回收，避免 used-after-dispose。
    if (text == null || text == widget.memory.content) return;
    await _run(() => widget.editor.updateMemoryContent(widget.memory.id, text));
  }

  Future<void> _togglePin() async {
    await _run(() => widget.editor.updateMemory(
        widget.memory.id, {'is_pinned': !widget.memory.isPinned}));
  }

  Future<void> _archive() async {
    final ok = await _confirm(widget.rs.t('archive'), widget.rs.t('archiveMemoryBody'));
    if (!ok) return;
    await _run(() => widget.editor.archiveMemory(widget.memory.id));
  }

  Future<void> _delete() async {
    final ok = await _confirm(widget.rs.t('delete'), widget.rs.t('deleteMemoryBody'));
    if (!ok) return;
    await _run(() => widget.editor.deleteMemory(widget.memory.id));
  }

  Future<bool> _confirm(String title, String body) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(widget.rs.t('cancel'))),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(widget.rs.t('confirm'))),
        ],
      ),
    );
    return ok == true;
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Widget _meta(String label, String value) => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text('$label：$value', style: const TextStyle(fontSize: 13)),
      );

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final m = widget.memory;
    final theme = Theme.of(context);
    return SafeArea(
      child: ListView(padding: const EdgeInsets.all(16), children: [
        Text(m.title?.isNotEmpty ?? false ? m.title! : rs.t('detail'),
            style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        SelectableText(m.content),
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 4, children: [
          OutlinedButton.icon(
            onPressed: _busy ? null : _editContent,
            icon: const Icon(Icons.edit_outlined, size: 18),
            label: Text(rs.t('editContent')),
          ),
          TextButton(
            onPressed: _busy ? null : _togglePin,
            child: Text(m.isPinned ? rs.t('unpin') : rs.t('pin')),
          ),
          TextButton(
            onPressed: _busy ? null : _archive,
            child: Text(rs.t('archive')),
          ),
          TextButton(
            onPressed: _busy ? null : _delete,
            child: Text(rs.t('delete'), style: TextStyle(color: theme.colorScheme.error)),
          ),
        ]),
        const Divider(height: 24),
        _meta(rs.t('memType'), m.memoryType),
        _meta(rs.t('source'), m.sourceLabel ?? rs.t('unknown')),
        _meta(rs.t('epistemic'), m.epistemicStatus ?? rs.t('unknown')),
        _meta(rs.t('importance'),
            '${'★' * m.importance.clamp(0, 5).toInt()} (${m.importancePct.toStringAsFixed(0)})'),
        if (m.isPinned) _meta(rs.t('pinned'), '✓'),
        _meta(rs.t('createdAt'), m.createdAt),
        _meta(rs.t('updatedAt'), m.updatedAt),
        if (m.whyItMatters?.isNotEmpty ?? false)
          _meta(rs.t('whyMatters'), m.whyItMatters!),
        const Divider(height: 24),
        Text(rs.t('memoryChain'), style: theme.textTheme.titleSmall),
        SizedBox(
          height: 160,
          child: ReaderAsyncView<List<ReaderMemoryChainNode>>(
            loader: _chain,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadChain,
            dataBuilder: (nodes) => ListView(children: [
              for (final n in nodes)
                ListTile(
                  dense: true,
                  leading: n.isCurrent
                      ? const Icon(Icons.arrow_right)
                      : const SizedBox(width: 24),
                  title: Text(n.content,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          fontWeight:
                              n.isCurrent ? FontWeight.bold : FontWeight.normal)),
                  subtitle: Text('${n.memoryType} · ${n.createdAt}'),
                ),
            ]),
          ),
        ),
        const Divider(height: 24),
        Text(rs.t('memoryHistory'), style: theme.textTheme.titleSmall),
        SizedBox(
          height: 200,
          child: ReaderAsyncView<ReaderFactHistory>(
            loader: _history,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadHistory,
            dataBuilder: (h) => ListView(children: [
              if (h.factContent.isNotEmpty)
                ListTile(
                  dense: true,
                  title: Text(h.factContent),
                  subtitle: Text(
                      '${rs.t('factNow')} · ${h.factEpistemic ?? rs.t('unknown')}'),
                ),
              for (final c in h.corrections)
                ListTile(
                  dense: true,
                  title: Text('${c.oldValue} → ${c.newValue}'),
                  subtitle: Text([
                    if (c.changedAt != null) c.changedAt!,
                    if (c.reason != null) '${rs.t('correctionReason')}：${c.reason}',
                  ].join(' · ')),
                ),
              if (h.truncated)
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(rs.t('truncated'),
                      style: theme.textTheme.bodySmall),
                ),
              Text(rs.t('derivations'), style: theme.textTheme.titleSmall),
        SizedBox(
          height: 180,
          child: ReaderAsyncView<ReaderMemoryDerivations>(
            loader: _derivations,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadDerivations,
            dataBuilder: (d) {
              final rows = <Widget>[
                if (d.parents.items.isNotEmpty)
                  _meta(rs.t('parents'),
                      d.parents.items.map((e) => e.content).join('；')),
                if (d.children.items.isNotEmpty)
                  _meta(rs.t('children'),
                      d.children.items.map((e) => e.content).join('；')),
              ];
              return ListView(children: [
                if (rows.isEmpty)
                  Padding(padding: const EdgeInsets.all(8), child: Text(rs.t('noRelations')))
                else ...rows,
              ]);
            },
          ),
        ),
        const Divider(height: 24),
        Text(rs.t('whyRemember'), style: theme.textTheme.titleSmall),
        SizedBox(
          height: 160,
          child: ReaderAsyncView<ReaderMemoryWhy?>(
            loader: _why,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadWhy,
            dataBuilder: (w) => ListView(children: [
              if (w == null || !w.hasSource)
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(w?.note.isNotEmpty == true ? w!.note : rs.t('noSource')),
                )
              else ...[
                for (final s in w.sources)
                  ListTile(
                    dense: true,
                    title: Text(s.messageExcerpt ?? s.kind),
                    subtitle: Text([if (s.messageCreatedAt != null) s.messageCreatedAt!,
                      if (s.eventAt != null) s.eventAt!].join(' · ')),
                  ),
              ],
            ]),
          ),
        ),
        const Divider(height: 24),
        Text(rs.t('lifecycle'), style: theme.textTheme.titleSmall),
        SizedBox(
          height: 180,
          child: ReaderAsyncView<ReaderMemoryLifecycle?>(
            loader: _lifecycle,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadLifecycle,
            dataBuilder: (l) => ListView(children: [
              if (l != null) ...[
                _meta(rs.t('lifecycleStatus'), l.lifecycleStatus ?? '—'),
                _meta(rs.t('version'), '${l.version}'),
                if (l.supersededBy != null) _meta(rs.t('supersededBy'), '${l.supersededBy}'),
                if (l.validTo != null) _meta(rs.t('validTo'), l.validTo!),
                _meta(rs.t('derivedCount'), '${l.derivedCount}'),
                for (final r in l.receipts)
                  ListTile(
                    dense: true,
                    title: Text(r.action),
                    subtitle: Text([if (r.reason != null) r.reason!,
                      if (r.createdAt != null) r.createdAt!].join(' · ')),
                  ),
              ],
            ]),
          ),
        ),
      ]),
          ),
        ),
      ]),
    );
  }
}
