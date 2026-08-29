import 'dart:async';

import 'package:flutter/material.dart';

import '../models/character.dart';
import '../theme/aurora_tokens.dart';
import '../theme/tokens.dart';
import 'ai_avatar.dart';
import 'aurora_card.dart';

/// AI 好友横向 Aurora 卡片（Phase 2 B2）。
///
/// 布局：左侧圆形头像 56px（AIAvatar）+ 名字/副标题（personality 最多 2 行）+
/// 右侧未读红点（`NotificationService().unreadCounts`，逻辑零改动）。
///
/// 说明：
/// - 点击进聊天 / 长按进编辑由调用方传入，卡片只负责渲染；
/// - 长按手势包在 AuroraCard 外层（AuroraCard 只提供 onTap，内部按压缩放不变）；
/// - 在线状态绿点 / 最后消息 / 时间：列表 API 无对应字段，不虚构数据，后续有字段再补。
class CharacterListCard extends StatelessWidget {
  final AICharacter character;

  /// 未读数（null = 无未读，不渲染红点）。
  final int? unread;

  final VoidCallback onTap;

  final VoidCallback onLongPress;

  const CharacterListCard({
    super.key,
    required this.character,
    required this.unread,
    required this.onTap,
    required this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return GestureDetector(
      onLongPress: onLongPress,
      child: AuroraCard(
        onTap: onTap,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          children: [
            AIAvatar(name: character.name, size: 56, imageUrl: character.avatarUrl),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    character.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: AppTypography.titleSize,
                      fontWeight: AppTypography.titleWeight,
                      color: scheme.onSurface,
                    ),
                  ),
                  if (character.personality != null &&
                      character.personality!.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      character.personality!,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: AppTypography.helperSize,
                        color: scheme.onSurfaceVariant,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            if (unread != null)
              Container(
                key: const Key('characterUnreadBadge'),
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppColors.error,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Text(
                  '$unread',
                  style: const TextStyle(
                      color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
                ),
              ),
            const SizedBox(width: 4),
            Icon(Icons.chevron_right, color: scheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }
}

/// 列表错峰入场包装：淡入 + 上移。
///
/// - 每项错峰 [staggerMs]（规格定值 50ms，最多 10 项；第 10 项之后直接显示）；
/// - 单次动画时长/曲线只取 [AppMotion]；
/// - [enabled] = false（reduceMotion / 系统 disableAnimations / 已播过入场）时
///   直接渲染子组件，不产生任何动画与计时器。
class StaggeredEntrance extends StatefulWidget {
  final int index;
  final bool enabled;
  final Widget child;

  /// 错峰间隔（规格 B2 定值 50ms）。
  static const int staggerMs = 50;

  /// 最多参与入场动效的项数（规格 B2 定值）。
  static const int maxItems = 10;

  const StaggeredEntrance({
    super.key,
    required this.index,
    required this.enabled,
    required this.child,
  });

  @override
  State<StaggeredEntrance> createState() => _StaggeredEntranceState();
}

class _StaggeredEntranceState extends State<StaggeredEntrance> {
  bool _shown = false;
  Timer? _delay;

  @override
  void initState() {
    super.initState();
    if (widget.enabled && widget.index < StaggeredEntrance.maxItems) {
      _delay = Timer(
        Duration(milliseconds: StaggeredEntrance.staggerMs * widget.index),
        () {
          if (mounted) setState(() => _shown = true);
        },
      );
    } else {
      _shown = true;
    }
  }

  @override
  void didUpdateWidget(StaggeredEntrance oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 已在计时等待时被判定为禁用（如切到 reduceMotion）→ 立即显示
    if (!widget.enabled && !_shown) {
      _delay?.cancel();
      _shown = true;
    }
  }

  @override
  void dispose() {
    _delay?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final animated = widget.enabled && widget.index < StaggeredEntrance.maxItems;
    if (!animated) {
      return widget.child;
    }
    // 隐式动画：先以 opacity 0 / 下移状态挂载，_shown 翻转后过渡到可见
    return AnimatedOpacity(
      opacity: _shown ? 1 : 0,
      duration: AppMotion.normal,
      curve: AppMotion.emphasized,
      child: AnimatedSlide(
        offset: _shown ? Offset.zero : const Offset(0, 0.08),
        duration: AppMotion.normal,
        curve: AppMotion.emphasized,
        child: widget.child,
      ),
    );
  }
}
