import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "package:provider/provider.dart";
import "../models/moment.dart";
import "../models/moment_comment.dart";
import "../providers/settings_provider.dart";
import "../services/api_client.dart";
import "../theme/aurora_tokens.dart";
import "../utils/beijing_time.dart";
import "package:ai_companion/theme/tokens.dart";
import "aurora_card.dart";

/// 朋友圈头像（图片 + 首字兜底）
class MomentAvatar extends StatelessWidget {
  final String? avatarUrl;
  final String name;
  final double radius;
  const MomentAvatar({super.key, required this.avatarUrl, required this.name, required this.radius});

  @override
  Widget build(BuildContext context) {
    if (avatarUrl != null && avatarUrl!.isNotEmpty) {
      return ClipOval(
        child: Image.network(
          ApiClient().resolveUrl(avatarUrl!),
          width: radius * 2,
          height: radius * 2,
          fit: BoxFit.cover,
          errorBuilder: (context, error, stack) => CircleAvatar(radius: radius, child: Text(name.isNotEmpty ? name[0] : "?", style: TextStyle(fontSize: radius * 0.8))),
        ),
      );
    }
    return CircleAvatar(radius: radius, child: Text(name.isNotEmpty ? name[0] : "?", style: TextStyle(fontSize: radius * 0.8)));
  }
}

/// 朋友圈图片（点击全屏查看）
class MomentImageView extends StatelessWidget {
  final String imageUrl;
  const MomentImageView({super.key, required this.imageUrl});

  void _viewImage(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => Dialog(
        backgroundColor: Colors.black,
        insetPadding: const EdgeInsets.all(8),
        child: GestureDetector(
          onTap: () => Navigator.pop(ctx),
          child: InteractiveViewer(
            child: Image.network(
              ApiClient().resolveUrl(imageUrl),
              fit: BoxFit.contain,
              errorBuilder: (context, error, stack) => const Center(child: Icon(Icons.broken_image_outlined, color: Colors.white, size: 48)),
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final maxW = MediaQuery.of(context).size.width * 0.6;
    final imgWidth = maxW > 240 ? 240.0 : maxW;
    return Align(
      alignment: Alignment.centerLeft,
      child: GestureDetector(
        onTap: () => _viewImage(context),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: Image.network(
            ApiClient().resolveUrl(imageUrl),
            width: imgWidth,
            fit: BoxFit.cover,
            loadingBuilder: (context, child, progress) {
              if (progress == null) return child;
              return Container(
                height: 180,
                color: Colors.grey.shade100,
                alignment: Alignment.center,
                child: const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2)),
              );
            },
            errorBuilder: (context, error, stack) => Container(
              height: 120,
              color: Colors.grey.shade100,
              alignment: Alignment.center,
              child: Icon(Icons.broken_image_outlined, color: Colors.grey.shade400, size: 32),
            ),
          ),
        ),
      ),
    );
  }
}

/// 评论区块（回复树 + 展开/收起 + 发表评论入口），展开状态自持
class MomentCommentsSection extends StatefulWidget {
  final List<MomentComment> comments;
  final VoidCallback? onReply;              // 无评论时"发表评论..." / 评论按钮
  final void Function(MomentComment)? onReplyTo;  // 点"回复"
  const MomentCommentsSection({super.key, required this.comments, this.onReply, this.onReplyTo});

  @override
  State<MomentCommentsSection> createState() => _MomentCommentsSectionState();
}

class _MomentCommentsSectionState extends State<MomentCommentsSection> {
  bool _showAll = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    var comments = widget.comments;
    var display = _showAll ? comments : comments.take(3).toList();

    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.55),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (var c in display) ...[
          _buildCommentItem(c),
          for (var r in c.replies)
            Padding(
              padding: const EdgeInsets.only(left: 24, top: 4),
              child: _buildCommentItem(r, isReply: true, replyToName: c.senderName),
            ),
        ],
        if (comments.length > 3)
          GestureDetector(
            onTap: () => setState(() => _showAll = !_showAll),
            child: Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(_showAll ? l10n.collapse : l10n.viewAllComments(comments.length), style: TextStyle(fontSize: 13, color: Theme.of(context).colorScheme.primary)),
            ),
          ),
        if (comments.isEmpty)
          GestureDetector(
            onTap: widget.onReply,
            child: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(l10n.addCommentHint, style: TextStyle(fontSize: 13, color: Colors.grey.shade500)),
            ),
          ),
      ]),
    );
  }

  Widget _buildCommentItem(MomentComment c,
      {bool isReply = false, String? replyToName}) {
    final l10n = AppLocalizations.of(context)!;
    const nameBlue = AppColors.linkBlue;
    final theme = Theme.of(context);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // 整块文本等宽：昵称再长也只影响首行内断行，不改变右边界
        Expanded(
          child: Text.rich(
            TextSpan(style: const TextStyle(fontSize: 13), children: [
              TextSpan(
                text: c.senderName,
                style:
                    TextStyle(fontWeight: FontWeight.w500, color: nameBlue),
              ),
              if (c.parentId != null && isReply)
                TextSpan(
                  text: '  ${l10n.reply} @${replyToName ?? '...'}',
                  style: const TextStyle(fontSize: 13, color: nameBlue),
                ),
              TextSpan(
                text: '：',
                style: TextStyle(fontSize: 13, color: theme.colorScheme.onSurface),
              ),
              TextSpan(
                text: c.content,
                style:
                    TextStyle(fontSize: 13, color: theme.colorScheme.onSurface),
              ),
            ]),
          ),
        ),
        GestureDetector(
          onTap: () => widget.onReplyTo?.call(c),
          child: Text('  ${l10n.reply}',
              style: TextStyle(
                  fontSize: 11, color: theme.colorScheme.onSurfaceVariant)),
        ),
      ]),
    );
  }
}

/// 点赞爱心（Aurora P1）：从未点赞 → 点赞时弹性动画 scale 1.3→1.0
/// （`AppMotion.fast` + `AppMotion.spring`）；`reduceMotion` / 系统 disableAnimations
/// 时无动画。公开组件便于测试。
class LikeHeart extends StatefulWidget {
  final bool liked;
  final Color likedColor;
  final Color idleColor;

  const LikeHeart({
    super.key,
    required this.liked,
    this.likedColor = const Color(0xFFFA5151),
    required this.idleColor,
  });

  @override
  State<LikeHeart> createState() => _LikeHeartState();
}

class _LikeHeartState extends State<LikeHeart> {
  /// 弹性动画起点（点赞瞬间 1.3，其余 1.0）
  double _scaleStart = 1.0;

  @override
  void didUpdateWidget(LikeHeart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!oldWidget.liked && widget.liked) {
      setState(() => _scaleStart = 1.3);
    } else if (oldWidget.liked && !widget.liked) {
      // 取消点赞：重置起点，不播动画
      _scaleStart = 1.0;
    }
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.liked ? widget.likedColor : widget.idleColor;
    final icon = Icon(
      widget.liked ? Icons.favorite : Icons.favorite_border,
      size: 17,
      color: color,
    );
    final reduceMotion = MediaQuery.disableAnimationsOf(context) || _maybeReduceMotion(context);
    if (reduceMotion || _scaleStart == 1.0) return icon;
    return TweenAnimationBuilder<double>(
      key: const Key('likeHeartBounce'),
      tween: Tween(begin: _scaleStart, end: 1.0),
      duration: AppMotion.fast,
      curve: AppMotion.spring,
      builder: (context, scale, child) => Transform.scale(scale: scale, child: child),
      child: icon,
    );
  }
}

bool _maybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion;
  } catch (_) {
    return false;
  }
}

/// 朋友圈卡片（列表模式）
class MomentCard extends StatelessWidget {
  final Moment moment;
  final List<MomentComment> comments;
  final VoidCallback? onLike;
  final VoidCallback? onReply;
  final void Function(MomentComment)? onReplyTo;
  final VoidCallback? onDelete;
  const MomentCard({super.key, required this.moment, this.comments = const [], this.onLike, this.onReply, this.onReplyTo, this.onDelete});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final displayName = moment.senderType == 'user' ? l10n.me : moment.characterName;
    final scheme = theme.colorScheme;
    // Aurora P1：容器换 AuroraCard（圆角 20；卡片整体不可点，不启用按压缩放）
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AuroraCard(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 10),
        child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 微信风格：左侧头像
          MomentAvatar(avatarUrl: moment.avatarUrl, name: displayName, radius: 22),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              // 昵称 + 删除（删除恒居最右）
              Row(children: [
                Expanded(
                  child: Row(children: [
                    Flexible(
                      child: Text(
                        displayName,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 15,
                            color: AppColors.linkBlue),
                      ),
                    ),
                    if (moment.senderType == "user")
                      Container(
                        margin: const EdgeInsets.only(left: 6),
                        padding:
                            const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                        decoration: BoxDecoration(
                          color: scheme.secondaryContainer,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(l10n.me,
                            style: TextStyle(
                                fontSize: 10,
                                color: scheme.onSecondaryContainer)),
                      ),
                  ]),
                ),
                if (onDelete != null)
                  InkWell(
                    onTap: onDelete,
                    child: Padding(
                      padding: const EdgeInsets.all(2),
                      child: Icon(Icons.delete_outline,
                          size: 17, color: scheme.onSurfaceVariant),
                    ),
                  ),
              ]),
              const SizedBox(height: 4),
              // 内容
              Text(moment.content, style: TextStyle(fontSize: 15, height: 1.5, color: scheme.onSurface)),
              if (moment.imageUrl != null && moment.imageUrl!.isNotEmpty) ...[
                const SizedBox(height: 8),
                MomentImageView(imageUrl: moment.imageUrl!),
              ],
              const SizedBox(height: 8),
              // 时间 + 点赞/评论操作
              Row(children: [
                Text(
                  _formatDate(moment.createdAt, l10n),
                  style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
                ),
                const Spacer(),
                InkWell(
                  onTap: onLike,
                  borderRadius: BorderRadius.circular(4),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      LikeHeart(
                        liked: moment.likedByMe,
                        likedColor: const Color(0xFFFA5151),
                        idleColor: scheme.onSurfaceVariant,
                      ),
                      if (moment.likesCount > 0) ...[
                        const SizedBox(width: 3),
                        Text('${moment.likesCount}',
                            style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
                      ],
                    ]),
                  ),
                ),
                InkWell(
                  onTap: onReply,
                  borderRadius: BorderRadius.circular(4),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    child: Row(mainAxisSize: MainAxisSize.min, children: [
                      Icon(Icons.chat_bubble_outline,
                          size: 16, color: scheme.onSurfaceVariant),
                      if (comments.isNotEmpty) ...[
                        const SizedBox(width: 3),
                        Text('${comments.length}',
                            style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
                      ],
                    ]),
                  ),
                ),
              ]),
              // 点赞区（微信灰条）
              if (moment.likers.isNotEmpty)
                Container(
                  width: double.infinity,
                  margin: const EdgeInsets.only(top: 8),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: scheme.surfaceContainerHighest.withValues(alpha: 0.55),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    "♥ ${_likersText(moment.likers, l10n)}",
                    style: const TextStyle(fontSize: 13, color: AppColors.linkBlue),
                  ),
                ),
              if (comments.isNotEmpty)
                MomentCommentsSection(comments: comments, onReply: onReply, onReplyTo: onReplyTo),
            ]),
          ),
        ],
      ),
      ),
    );
  }

  String _likersText(List<String> likers, AppLocalizations l10n) {
    final names = likers.take(3).join("、");
    if (likers.length <= 3) return l10n.likersText1(names);
    return l10n.likersTextMany(names, likers.length);
  }

  String _formatDate(String isoDate, AppLocalizations l10n) {
    try {
      // 时间按动态作者所在地区（时区）显示
      var d = DateTime.parse(formatInTz(isoDate, offset: moment.authorTzOffset));
      final hhmm =
          "${d.hour.toString().padLeft(2, "0")}:${d.minute.toString().padLeft(2, "0")}";
      return l10n.momentDateFull(d.year, d.month, d.day, hhmm);
    } catch (_) {
      return isoDate.length >= 16 ? isoDate.substring(5, 16) : isoDate;
    }
  }
}
