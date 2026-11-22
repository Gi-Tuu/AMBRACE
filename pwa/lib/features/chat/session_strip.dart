// 会话工作台（M1a-3）· 会话条：当前角色的会话列表（选中/加载/空/失败/重试）
// ＋显式「新建会话」入口。
//
// 纯展示组件：状态与请求全部由壳（WorkspacePage）持有，本组件只画与回调；
// 只展示后端**真实返回**的字段（标题/日期/稳定 id），**不伪造**「最新消息预览」。
//
// 会话搜索（2026-11-22 批 8）：客户端过滤（标题/会话 id）——服务端无搜索端点，
// 与 App 会话搜索同口径，不伪造。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/session_strings.dart';

class SessionStrip extends StatefulWidget {
  const SessionStrip({
    super.key,
    required this.sessions,
    required this.selectedId,
    required this.status, // 'loading' | 'ready' | 'error'
    required this.errorText,
    required this.creating,
    required this.onSelect,
    required this.onCreate,
    required this.onRetry,
    required this.ss,
  });

  final List<ChatSessionSummary> sessions;
  final int? selectedId;
  final String status;
  final String errorText;
  final bool creating;
  final void Function(ChatSessionSummary) onSelect;
  final VoidCallback onCreate;
  final VoidCallback onRetry;
  final SessionStrings ss;

  /// 标题为空 ⇒ 双语系统占位；有标题则**原文**展示（用户内容不翻译）。
  String labelOf(ChatSessionSummary s) =>
      (s.title != null && s.title!.isNotEmpty)
          ? s.title!
          : ss.t('untitledSession');

  @override
  State<SessionStrip> createState() => _SessionStripState();
}

class _SessionStripState extends State<SessionStrip> {
  /// 会话搜索过滤词（客户端过滤：标题/会话 id；服务端无搜索端点）。
  String _filter = '';
  bool _searchOpen = false;

  String _label(ChatSessionSummary s) =>
      (s.title != null && s.title!.isNotEmpty)
          ? s.title!
          : widget.ss.t('untitledSession');

  String _date(String raw) => raw.length >= 10 ? raw.substring(0, 10) : raw;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final q = _filter.trim().toLowerCase();
    final shown = q.isEmpty
        ? widget.sessions
        : widget.sessions
            .where((s) =>
                (s.title ?? '').toLowerCase().contains(q) ||
                '#${s.id}'.contains(q))
            .toList();
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(widget.ss.t('sessions'), style: theme.textTheme.labelLarge),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              widget.status == 'loading'
                  ? widget.ss.t('loadingSessions')
                  : widget.status == 'error'
                      ? '${widget.ss.t('loadSessionsFailed')}${widget.errorText}'
                      : '${shown.length}${_filter.trim().isEmpty ? '' : ' / ${widget.sessions.length}'}',
              style: theme.textTheme.bodySmall,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (widget.status == 'error')
            TextButton(
                onPressed: widget.onRetry,
                child: Text(widget.ss.t('retry'))),
          const SizedBox(width: 4),
          IconButton(
            tooltip: widget.ss.t('sessionSearch'),
            isSelected: _searchOpen,
            icon: const Icon(Icons.search, size: 18),
            onPressed: () => setState(() => _searchOpen = !_searchOpen),
          ),
          const SizedBox(width: 4),
          OutlinedButton(
            onPressed: widget.creating ? null : widget.onCreate,
            child: Text(widget.creating
                ? widget.ss.t('loadingEarlier')
                : widget.ss.t('newSession')),
          ),
        ]),
        if (_searchOpen)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: TextField(
              autofocus: true,
              onChanged: (v) => setState(() => _filter = v),
              decoration: InputDecoration(
                isDense: true,
                prefixIcon: const Icon(Icons.search, size: 18),
                hintText: widget.ss.t('sessionSearch'),
                border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(8)),
              ),
            ),
          ),
        if (widget.status == 'ready' && widget.sessions.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(widget.ss.t('noSessions'),
                style: theme.textTheme.bodySmall),
          ),
        if (widget.status == 'ready' &&
            widget.sessions.isNotEmpty &&
            shown.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(widget.ss.t('noMatchSessions'),
                style: theme.textTheme.bodySmall),
          ),
        if (shown.isNotEmpty)
          SizedBox(
            height: 56,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final s in shown)
                  Padding(
                    padding: const EdgeInsets.only(right: 8, top: 6),
                    child: FilterChip(
                      selected: s.id == widget.selectedId,
                      label: Text(
                          '${_label(s)} · ${_date(s.createdAt)} · #${s.id}'),
                      onSelected: (_) => widget.onSelect(s),
                    ),
                  ),
              ],
            ),
          ),
      ]),
    );
  }
}
