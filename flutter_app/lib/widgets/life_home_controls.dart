import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 小家 v3.3 专用控件（独立文件便于 widget 测试）：
/// 纯展示 + 回调，不持有业务状态。
///
/// - [LifeHomeRoomTabBar]：四个房间 Tab 居中一行 + 右侧「家具编辑」按钮（编辑态高亮）
/// - [LifeHomeEditHintBar]：编辑态顶部提示条（「拖动或点选家具进行编辑」+「完成」）
/// - [LifeHomeEditActionBar]：被编辑家具操作栏（回退 / 旋转 / 确定）

/// 房间 Tab 数据（id + 显示名）
class LifeHomeRoom {
  final String id;
  final String name;
  const LifeHomeRoom(this.id, this.name);
}

/// 房间 Tab 栏：水平居中排一行 + 同行右端「家具编辑」图标按钮
class LifeHomeRoomTabBar extends StatelessWidget {
  final List<LifeHomeRoom> rooms;
  final String currentRoomId;
  final bool editing;
  final ValueChanged<String> onSelectRoom;
  final VoidCallback onEditTap;

  const LifeHomeRoomTabBar({
    super.key,
    required this.rooms,
    required this.currentRoomId,
    required this.editing,
    required this.onSelectRoom,
    required this.onEditTap,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return SizedBox(
      height: 46,
      child: Row(
        children: [
          // 四个房间 Tab 居中（窄屏自动缩放到一行）
          Expanded(
            child: Center(
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (var i = 0; i < rooms.length; i++) ...[
                      if (i > 0) const SizedBox(width: 8),
                      ChoiceChip(
                        label: Text(rooms[i].name),
                        selected: rooms[i].id == currentRoomId,
                        onSelected: (_) => onSelectRoom(rooms[i].id),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
          // 家具编辑按钮（编辑态高亮，点击退出编辑态）
          Padding(
            padding: const EdgeInsets.only(right: 6),
            child: InkWell(
              onTap: onEditTap,
              borderRadius: BorderRadius.circular(10),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: editing ? scheme.primaryContainer : Colors.transparent,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      editing ? Icons.edit_off : Icons.edit_outlined,
                      size: 19,
                      color: editing ? scheme.primary : Colors.grey.shade600,
                    ),
                    Text(
                      l10n.furnitureEdit,
                      style: TextStyle(
                        fontSize: 9,
                        color: editing ? scheme.primary : Colors.grey.shade600,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 编辑态顶部提示条：「拖动或点选家具进行编辑」+「完成」按钮（统一保存一次）
class LifeHomeEditHintBar extends StatelessWidget {
  final VoidCallback onDone;
  const LifeHomeEditHintBar({super.key, required this.onDone});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        child: Row(
          children: [
            Icon(Icons.edit, size: 16, color: scheme.primary),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                l10n.furnitureEditHint,
                style: TextStyle(fontSize: 12, color: scheme.onPrimaryContainer),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            FilledButton(
              onPressed: onDone,
              style: FilledButton.styleFrom(
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 14),
              ),
              child: Text(l10n.done),
            ),
          ],
        ),
      ),
    );
  }
}

/// 被编辑家具操作栏：回退 / 旋转 / 确定（浮动在画布底部）
class LifeHomeEditActionBar extends StatelessWidget {
  final VoidCallback onRevert;
  final VoidCallback onRotate;
  final VoidCallback onConfirm;
  const LifeHomeEditActionBar({
    super.key,
    required this.onRevert,
    required this.onRotate,
    required this.onConfirm,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Container(
      color: scheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          _action(context, Icons.undo, l10n.furnitureRevert, onRevert),
          _action(context, Icons.rotate_right, l10n.furnitureRotate, onRotate),
          _action(context, Icons.check_circle_outline, l10n.furnitureConfirm, onConfirm),
        ],
      ),
    );
  }

  Widget _action(BuildContext context, IconData icon, String label, VoidCallback onTap) {
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 22, color: scheme.primary),
            const SizedBox(height: 2),
            Text(label, style: const TextStyle(fontSize: 10)),
          ],
        ),
      ),
    );
  }
}
