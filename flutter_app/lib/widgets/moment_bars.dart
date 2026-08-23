import "dart:io";
import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";

/// 朋友圈发布输入条
class MomentPublishBar extends StatelessWidget {
  final TextEditingController controller;
  final File? pendingImage;
  final bool uploading;
  final VoidCallback onRemoveImage;
  final VoidCallback onShowMore;
  final VoidCallback onPublish;
  const MomentPublishBar({super.key, required this.controller, this.pendingImage, this.uploading = false, required this.onRemoveImage, required this.onShowMore, required this.onPublish});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: Colors.grey.shade50, border: Border(bottom: BorderSide(color: Colors.grey.shade200))),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (pendingImage != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: Image.file(File(pendingImage!.path), width: 64, height: 64, fit: BoxFit.cover),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(l10n.imageSelected, style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    tooltip: l10n.removeImage,
                    onPressed: onRemoveImage,
                    visualDensity: VisualDensity.compact,
                  ),
                ],
              ),
            ),
          Row(
            children: [
              IconButton(
                icon: const Icon(Icons.add_circle_outline, size: 20),
                tooltip: l10n.moreFunctions,
                onPressed: onShowMore,
                visualDensity: VisualDensity.compact,
              ),
              Expanded(
                child: TextField(
                  controller: controller,
                  decoration: InputDecoration(hintText: l10n.momentHint, border: InputBorder.none, contentPadding: const EdgeInsets.symmetric(horizontal: 8)),
                  maxLines: 2,
                  minLines: 1,
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton(
                onPressed: uploading ? null : onPublish,
                child: uploading
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(l10n.publish),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 朋友圈评论输入条（回复/评论）
class MomentCommentBar extends StatelessWidget {
  final TextEditingController controller;
  final bool replyingToComment;
  final VoidCallback onSend;
  final VoidCallback onClose;
  final VoidCallback onShowMore;
  const MomentCommentBar({super.key, required this.controller, this.replyingToComment = false, required this.onSend, required this.onClose, required this.onShowMore});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        boxShadow: [BoxShadow(color: Colors.grey.withValues(alpha: 0.2), blurRadius: 4, offset: const Offset(0, -2))],
      ),
      child: Row(
        children: [
          IconButton(
            icon: const Icon(Icons.add_circle_outline, size: 20),
            tooltip: l10n.moreFunctions,
            onPressed: onShowMore,
            visualDensity: VisualDensity.compact,
          ),
          if (replyingToComment)
            Container(
              margin: const EdgeInsets.only(right: 8),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(color: Theme.of(context).colorScheme.secondaryContainer, borderRadius: BorderRadius.circular(4)),
              child: Text(l10n.replying, style: TextStyle(fontSize: 11, color: Theme.of(context).colorScheme.onSecondaryContainer)),
            ),
          Expanded(
            child: TextField(
              controller: controller,
              decoration: InputDecoration(
                hintText: l10n.commentHint,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(20)),
                contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                isDense: true,
              ),
              onSubmitted: (v) => onSend(),
            ),
          ),
          const SizedBox(width: 4),
          IconButton(
            icon: const Icon(Icons.send, size: 18),
            onPressed: onSend,
          ),
          IconButton(
            icon: const Icon(Icons.close, size: 18),
            onPressed: onClose,
          ),
        ],
      ),
    );
  }
}
