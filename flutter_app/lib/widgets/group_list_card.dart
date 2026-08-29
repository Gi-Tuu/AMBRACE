import 'package:flutter/material.dart';

import '../theme/tokens.dart';
import 'aurora_card.dart';

/// AI好友主页中的「群聊」卡片：与 CharacterListCard 同构，保证列表视觉统一。
class GroupListCard extends StatelessWidget {
  final String name;
  final String subtitle; // 成员名，已用、连接
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  const GroupListCard({
    super.key,
    required this.name,
    required this.subtitle,
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
            Container(
              width: 56,
              height: 56,
              decoration: BoxDecoration(
                color: scheme.primaryContainer,
                borderRadius: BorderRadius.circular(AppRadius.md),
              ),
              child: Icon(Icons.groups, color: scheme.onPrimaryContainer, size: 28),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: AppTypography.titleSize,
                      fontWeight: AppTypography.titleWeight,
                      color: scheme.onSurface,
                    ),
                  ),
                  if (subtitle.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      subtitle,
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
            Icon(Icons.chevron_right, color: scheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }
}
