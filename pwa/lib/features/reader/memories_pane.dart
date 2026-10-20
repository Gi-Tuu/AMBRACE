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
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

class MemoriesPane extends StatefulWidget {
  const MemoriesPane({
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
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: Row(children: [
          Text(rs.t('memType')),
          const SizedBox(width: 12),
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
          dataBuilder: (items) => ListView(children: [
            for (final m in items)
              ListTile(
                title: Text(_titleOf(m), maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text(
                  '${m.memoryType} · ${m.sourceLabel ?? rs.t('unknown')} · ${_stars(m.importance)}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: m.isPinned ? const Icon(Icons.push_pin, size: 16) : null,
                onTap: () => _openDetail(m),
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
          ]),
        ),
      ),
    ]);
  }

  void _openDetail(ReaderMemory m) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.85,
        child: _MemoryDetailSheet(memory: m, api: _api, rs: widget.rs, cs: widget.cs),
      ),
    );
  }
}

/// 记忆详情抽屉：正文/元信息 + 链条 + 事实修正历史（各自独立加载态，互不误伤）。
class _MemoryDetailSheet extends StatefulWidget {
  const _MemoryDetailSheet({
    required this.memory,
    required this.api,
    required this.rs,
    required this.cs,
  });

  final ReaderMemory memory;
  final ReaderApi api;
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

  @override
  void initState() {
    super.initState();
    _loadChain();
    _loadHistory();
  }

  @override
  void dispose() {
    _chain.dispose();
    _history.dispose();
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
            ]),
          ),
        ),
      ]),
    );
  }
}
