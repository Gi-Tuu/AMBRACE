import 'package:flutter/material.dart';

import '../../l10n/attachment_strings.dart';
import 'attachment_state.dart';

/// 待发附件卡（M1a-4）：文件名 / 本地已知大小 / 阶段（准备·上传·等待处理·成功·未确认·取消）
/// ＋按阶段给出**准确的**操作（明确发送 / 移除 / 取消本地等待 / 只 GET 核对历史）。
///
/// 纯展示组件：状态与请求全部由壳（WorkspacePage）持有。
class AttachmentPendingCard extends StatelessWidget {
  const AttachmentPendingCard({
    super.key,
    required this.pending,
    required this.as_,
    required this.canSend,
    required this.onSend,
    required this.onRemove,
    required this.onCancel,
    required this.onVerify,
  });

  final PendingAttachment pending;
  final AttachmentStrings as_;

  /// 会话就绪且未在忙 ⇒ 允许点「发送附件」。
  final bool canSend;
  final VoidCallback onSend;
  final VoidCallback onRemove;
  final VoidCallback onCancel;
  final VoidCallback onVerify;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final size = pending.file.size > 0
        ? humanSize(pending.file.size)
        : as_.t('sizeUnknown');
    final fraction = pending.fraction;

    return Card(
      margin: const EdgeInsets.fromLTRB(12, 4, 12, 4),
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            const Icon(Icons.attach_file, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                // 文件名是用户内容，原文展示
                '${pending.file.name} · $size',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium,
              ),
            ),
            Text(as_.t(attachPhaseKey(pending.phase)),
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.primary)),
          ]),
          if (pending.phase == AttachPhase.uploading) ...[
            const SizedBox(height: 8),
            if (fraction != null)
              LinearProgressIndicator(value: fraction)
            else
              const LinearProgressIndicator(), // total 未知 ⇒ 不定进度，不伪算
            const SizedBox(height: 4),
            Text(
              fraction != null
                  ? '${(fraction * 100).toStringAsFixed(0)}%'
                  : as_.t('sizeUnknown'),
              style: theme.textTheme.bodySmall,
            ),
          ],
          const SizedBox(height: 8),
          Row(children: [
            if (pending.phase == AttachPhase.ready) ...[
              Expanded(
                child: FilledButton(
                  onPressed: canSend ? onSend : null,
                  child: Text(as_.t('send')),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(onPressed: onRemove, child: Text(as_.t('remove'))),
            ] else if (pending.inFlight) ...[
              OutlinedButton(onPressed: onCancel, child: Text(as_.t('cancelWait'))),
            ] else if (pending.phase == AttachPhase.unconfirmed) ...[
              Expanded(
                child: FilledButton(onPressed: onVerify, child: Text(as_.t('verifyHistory'))),
              ),
              const SizedBox(width: 8),
              OutlinedButton(onPressed: onRemove, child: Text(as_.t('remove'))),
            ] else ...[
              // failed / cancelled / sent：只给移除（任何再次发送都必须是新的明确动作）
              OutlinedButton(onPressed: onRemove, child: Text(as_.t('remove'))),
              if (pending.phase == AttachPhase.failed) ...[
                const SizedBox(width: 8),
                Expanded(
                  child: Text(as_.t('retrySend'), style: theme.textTheme.bodySmall),
                ),
              ],
            ],
          ]),
        ]),
      ),
    );
  }
}
