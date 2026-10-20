// 只读工作台（M1a-2）· 角色详情面板：GET /api/v1/characters/{id} 的展示白名单字段。
// 纪律：system_prompt / LLM 配置等内部字段不进 DTO 也不展示；头像不加载图片（仅占位）；
// 空值字段直接不显示，不编造占位文本；角色名/简介等用户内容原文展示、不翻译。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

class ProfilePane extends StatefulWidget {
  const ProfilePane({
    super.key,
    required this.character,
    required this.client,
    required this.rs,
    required this.cs,
    this.onEdit,
    this.editLabel = 'Edit profile',
  });

  final Character character;
  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  /// 编辑入口回调（null ⇒ 不显示入口；权限由服务端判定，不在此按 owner 猜）。
  final VoidCallback? onEdit;
  final String editLabel;

  @override
  State<ProfilePane> createState() => _ProfilePaneState();
}

class _ProfilePaneState extends State<ProfilePane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<ReaderCharacter> _loader =
      ReaderLoader<ReaderCharacter>();

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
      (ct) => _api.getCharacter(widget.character.id, cancelToken: ct),
      isEmpty: (_) => false,
    );
  }

  Widget _row(BuildContext context, String label, String value) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: theme.textTheme.labelSmall),
        const SizedBox(height: 2),
        SelectableText(value, style: theme.textTheme.bodyMedium),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    return ReaderAsyncView<ReaderCharacter>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (c) {
        final rows = <(String, String)>[
          (rs.t('name'), c.name),
          if (c.bio?.isNotEmpty ?? false) (rs.t('bio'), c.bio!),
          if (c.selfStatement?.isNotEmpty ?? false)
            (rs.t('selfStatement'), c.selfStatement!),
          if (c.personality?.isNotEmpty ?? false)
            (rs.t('personality'), c.personality!),
          if (c.relationshipSummary?.isNotEmpty ?? false)
            (rs.t('relationship'), c.relationshipSummary!),
          if (c.currentStatus?.isNotEmpty ?? false)
            (rs.t('currentStatus'), c.currentStatus!),
          if (c.gender?.isNotEmpty ?? false) (rs.t('gender'), c.gender!),
          if (c.birthday?.isNotEmpty ?? false) (rs.t('birthday'), c.birthday!),
          if (c.height != null) (rs.t('height'), '${c.height}'),
          if (c.weight != null) (rs.t('weight'), '${c.weight}'),
          if (c.appearance?.isNotEmpty ?? false)
            (rs.t('appearance'), c.appearance!),
          if (c.talkativeness != null)
            (rs.t('talkativeness'), '${c.talkativeness}'),
          if (c.timezoneOffset != null)
            (rs.t('timezone'), '${c.timezoneOffset}'),
          (rs.t('createdAt'), c.createdAt),
        ];
        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (widget.onEdit != null)
              Align(
                alignment: Alignment.centerRight,
                child: OutlinedButton.icon(
                  onPressed: widget.onEdit,
                  icon: const Icon(Icons.edit, size: 18),
                  label: Text(widget.editLabel),
                ),
              ),
            // 头像占位（本单不加载图片）
            CircleAvatar(
              radius: 32,
              child: Text(c.name.isEmpty ? '?' : c.name.characters.first),
            ),
            const SizedBox(height: 12),
            for (final (label, value) in rows) _row(context, label, value),
          ],
        );
      },
    );
  }
}
