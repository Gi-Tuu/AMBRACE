import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/skins/aegean/aegean_architects.dart';
import '../theme/skins/aegean/aegean_palette.dart';
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
    // §6.6：aegean 下与角色卡共用同一套语言（金角框包卡 + 头像一圈金环 + 古金名字）。
    // 判定写法与 character_list_card.dart 一致：金环按各自头像形状走（圆/圆角方），
    // 于是两类卡的头像盒同为 56 + 2×(2 + 1.2) = 62.4，行高不再差 6.4px。
    final isAegean = Provider.of<SettingsProvider?>(context, listen: false)?.skinId == 'aegean';
    final b = Theme.of(context).brightness;
    final nameColor = isAegean ? scheme.primary : scheme.onSurface;

    Widget avatar = Container(
      width: 56,
      height: 56,
      decoration: BoxDecoration(
        color: scheme.primaryContainer,
        borderRadius: BorderRadius.circular(AppRadius.md),
      ),
      child: Icon(Icons.groups, color: scheme.onPrimaryContainer, size: 28),
    );
    if (isAegean) {
      avatar = Container(
        padding: const EdgeInsets.all(2),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AppRadius.md + 2),
          border: Border.all(color: AegeanPalette.goldPale, width: 1.2),
        ),
        child: avatar,
      );
    }

    Widget card = GestureDetector(
      onLongPress: onLongPress,
      child: AuroraCard(
        onTap: onTap,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        child: Row(
          children: [
            avatar,
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
                      color: nameColor,
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
            const SizedBox(width: 12),
            Icon(Icons.chevron_right, color: scheme.onSurfaceVariant),
          ],
        ),
      ),
    );

    return isAegean
        ? AegeanCardFrame(
            radius: 20,
            hairColor: AegeanPalette.goldDeep(b),
            arcColor: AegeanPalette.gold(b),
            child: card,
          )
        : card;
  }
}
