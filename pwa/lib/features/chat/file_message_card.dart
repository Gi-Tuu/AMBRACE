import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/attachment_strings.dart';

/// 历史/即时**文件消息卡**（M1a-4）。
///
/// 只展示 [ChatMessage.fileMeta] 里真实存在的字段：原名 / 可用大小·类型 / 失效或未知状态。
/// **不**自动展开 `summary` 全文当聊天正文；**不**用裸链接打开发起下载/预览；
/// 元数据缺失或 `expired` 时降级为双语状态，绝不把错误 URL 当可用附件。
class FileMessageCard extends StatelessWidget {
  const FileMessageCard({super.key, required this.message, required this.as_});

  final ChatMessage message;
  final AttachmentStrings as_;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final meta = message.fileMeta;
    final rawName = (meta?['name'] as String?)?.trim();
    final name = (rawName != null && rawName.isNotEmpty) ? rawName : message.content;
    final size = (meta?['size'] as String?)?.trim();
    final type = (meta?['type'] as String?)?.trim();
    final url = (meta?['url'] as String?)?.trim();
    final expired = meta?['expired'] == true;
    final usable = meta != null && url != null && url.isNotEmpty && !expired;
    final status = meta == null
        ? as_.t('fileUnknownMeta')
        : (usable ? null : as_.t('fileUnavailable'));

    final parts = <String>[];
    if (size != null && size.isNotEmpty) {
      parts.add(size);
    }
    if (type != null && type.isNotEmpty) {
      parts.add(type);
    }

    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Icon(Icons.description_outlined, size: 20),
      const SizedBox(width: 8),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(name, maxLines: 1, overflow: TextOverflow.ellipsis,
              style: theme.textTheme.bodyMedium),
          if (parts.isNotEmpty)
            Text(parts.join(' · '), style: theme.textTheme.bodySmall),
          if (status != null)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(status,
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.colorScheme.error)),
            ),
        ]),
      ),
    ]);
  }
}
