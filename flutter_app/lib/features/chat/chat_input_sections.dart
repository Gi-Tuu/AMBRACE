// 深拆（F7-c 收官，2026-09-01）自 features/chat/chat_screen.dart 迁入；
// 输入区三段（权限卡/引用条/更多面板）——纯展示，状态与动作经参数/回调上抛，逻辑逐字节保持。
import 'package:flutter/material.dart';

import 'package:ai_companion/l10n/app_localizations.dart';

import '../../theme/tokens.dart';

/// AI 能力权限询问卡片（权限=每次询问 时显示，允许/拒绝后消失）
class PermissionCard extends StatelessWidget {
  final String scopeLabel;
  final String prompt;
  final VoidCallback onDeny;
  final VoidCallback onAllow;

  const PermissionCard({
    super.key,
    required this.scopeLabel,
    required this.prompt,
    required this.onDeny,
    required this.onAllow,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF8E6),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFFFFD57A)),
      ),
      child: Row(
        children: [
          const Icon(Icons.help_outline, size: 16, color: Color(0xFFB7791F)),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${l10n.aiWantsToCall}【$scopeLabel】',
                  style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: Color(0xFF7A5B12)),
                ),
                if (prompt.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      prompt,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11.5, color: Color(0xFF8A6D1F)),
                    ),
                  ),
              ],
            ),
          ),
          TextButton(
            onPressed: onDeny,
            child: Text(l10n.deny, style: const TextStyle(fontSize: 12.5, color: AppColors.textSecondary)),
          ),
          TextButton(
            onPressed: onAllow,
            child: Text(l10n.allow, style: const TextStyle(fontSize: 12.5, color: AppColors.accent, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }
}

/// 输入框上方引用条（可关闭）
class QuoteBar extends StatelessWidget {
  final String content;
  final bool senderIsUser;
  final VoidCallback onClose;

  const QuoteBar({
    super.key,
    required this.content,
    required this.senderIsUser,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final sender = senderIsUser ? l10n.me : l10n.ta;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(Icons.format_quote, size: 14, color: Colors.grey.shade600),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '${l10n.quotePrefix} $sender：$content',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
            ),
          ),
          InkWell(
            onTap: onClose,
            child: const Padding(
              padding: EdgeInsets.all(2),
              child: Icon(Icons.close, size: 16, color: Colors.grey),
            ),
          ),
        ],
      ),
    );
  }
}

/// 更多功能面板：图片 / 文件 / 语音通话（表情已剥离到「切换」小框）
class MorePanel extends StatelessWidget {
  final VoidCallback onPickImage;
  final VoidCallback onPickFile;
  final VoidCallback onVoiceCall;

  const MorePanel({
    super.key,
    required this.onPickImage,
    required this.onPickFile,
    required this.onVoiceCall,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          _panelAction(
            icon: Icons.image_outlined,
            color: scheme.primary,
            label: l10n.image,
            sub: l10n.sendImage,
            onTap: onPickImage,
          ),
          const SizedBox(width: 16),
          _panelAction(
            icon: Icons.insert_drive_file_outlined,
            color: scheme.primary,
            label: l10n.file,
            sub: l10n.sendDoc,
            onTap: onPickFile,
          ),
          const SizedBox(width: 16),
          // 语音通话（Phase 1 恢复；基于既有 WS /api/v1/voice/stream）
          _panelAction(
            icon: Icons.phone_outlined,
            color: scheme.primary,
            label: l10n.voiceCallEntry,
            sub: l10n.voiceCallEntrySub,
            onTap: onVoiceCall,
          ),
        ],
      ),
    );
  }

  Widget _panelAction({
    required IconData icon,
    required Color color,
    required String label,
    required String sub,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 30, color: color),
            const SizedBox(height: 4),
            Text(label, style: const TextStyle(fontSize: 12)),
            Text(sub, style: const TextStyle(fontSize: 10, color: Colors.grey)),
          ],
        ),
      ),
    );
  }
}
