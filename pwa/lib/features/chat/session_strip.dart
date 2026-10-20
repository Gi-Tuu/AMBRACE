// 会话工作台（M1a-3）· 会话条：当前角色的会话列表（选中/加载/空/失败/重试）
// ＋显式「新建会话」入口。
//
// 纯展示组件：状态与请求全部由壳（WorkspacePage）持有，本组件只画与回调；
// 只展示后端**真实返回**的字段（标题/日期/稳定 id），**不伪造**「最新消息预览」。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/session_strings.dart';

class SessionStrip extends StatelessWidget {
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
  String label(ChatSessionSummary s) =>
      (s.title != null && s.title!.isNotEmpty) ? s.title! : ss.t('untitledSession');

  String _date(String raw) => raw.length >= 10 ? raw.substring(0, 10) : raw;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Text(ss.t('sessions'), style: theme.textTheme.labelLarge),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              status == 'loading'
                  ? ss.t('loadingSessions')
                  : status == 'error'
                      ? '${ss.t('loadSessionsFailed')}$errorText'
                      : '${sessions.length}',
              style: theme.textTheme.bodySmall,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (status == 'error')
            TextButton(onPressed: onRetry, child: Text(ss.t('retry'))),
          const SizedBox(width: 4),
          OutlinedButton(
            onPressed: creating ? null : onCreate,
            child: Text(creating ? ss.t('loadingEarlier') : ss.t('newSession')),
          ),
        ]),
        if (status == 'ready' && sessions.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(ss.t('noSessions'), style: theme.textTheme.bodySmall),
          ),
        if (sessions.isNotEmpty)
          SizedBox(
            height: 56,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final s in sessions)
                  Padding(
                    padding: const EdgeInsets.only(right: 8, top: 6),
                    child: FilterChip(
                      selected: s.id == selectedId,
                      label: Text(
                          '${label(s)} · ${_date(s.createdAt)} · #${s.id}'),
                      onSelected: (_) => onSelect(s),
                    ),
                  ),
              ],
            ),
          ),
      ]),
    );
  }
}
