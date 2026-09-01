// F7-c-0b（2026-08-31）自 chat_emoji_panel.dart 二次拆分：四个 body 构建方法转公开
// Stateless widget（逻辑逐字节保持，状态动作经回调回面板 State）。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';

/// 市场 tab 主体：包列表（名称/描述/版本/图标/已装标记 + 下载/卸载按钮）
class EmojiMarketListBody extends StatelessWidget {
  final List<Map<String, dynamic>>? packs;
  final bool loading;
  final bool error;
  final ValueChanged<int> onDownload;
  final ValueChanged<int> onUninstall;
  final ValueChanged<String> onOpenInstalled;
  final Set<String> downloading;

  const EmojiMarketListBody({
    super.key,
    required this.packs,
    required this.loading,
    required this.error,
    required this.onDownload,
    required this.onUninstall,
    required this.onOpenInstalled,
    required this.downloading,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    if (loading && (packs == null)) {
      return const Center(child: CircularProgressIndicator());
    }
    if (error && (packs == null || packs!.isEmpty)) {
      return Center(
        child: Text(l10n.emojiMarketUnavailable, style: const TextStyle(fontSize: 13, color: Colors.grey)),
      );
    }
    final list = packs ?? [];
    if (list.isEmpty) {
      return Center(
        child: Text(l10n.emojiMarketEmpty, style: const TextStyle(fontSize: 13, color: Colors.grey)),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: list.length,
      itemBuilder: (ctx, i) => _MarketTile(
        pack: list[i],
        index: i,
        downloading: downloading,
        onDownload: onDownload,
        onUninstall: onUninstall,
        onOpenInstalled: onOpenInstalled,
      ),
    );
  }
}

class _MarketTile extends StatelessWidget {
  final Map<String, dynamic> pack;
  final int index;
  final Set<String> downloading;
  final ValueChanged<int> onDownload;
  final ValueChanged<int> onUninstall;
  final ValueChanged<String> onOpenInstalled;

  const _MarketTile({
    required this.pack,
    required this.index,
    required this.downloading,
    required this.onDownload,
    required this.onUninstall,
    required this.onOpenInstalled,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final installed = pack['installed'] as bool? ?? false;
    final packId = pack['id'] as String;
    final isDownloading = downloading.contains(packId);
    final iconUrl = pack['icon_url'] as String? ?? '';
    final count = pack['emoji_count'] as int? ?? 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        onTap: installed ? () => onOpenInstalled(packId) : null,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Row(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: iconUrl.isEmpty
                    ? Container(
                        width: 48, height: 48, color: Colors.grey.shade200,
                        child: const Icon(Icons.emoji_emotions_outlined, size: 22, color: Colors.grey),
                      )
                    : Image.network(
                        ApiClient().resolveUrl(iconUrl),
                        width: 48, height: 48, fit: BoxFit.cover,
                        errorBuilder: (c, e, s) => Container(
                          width: 48, height: 48, color: Colors.grey.shade200,
                          child: const Icon(Icons.broken_image, size: 20, color: Colors.grey),
                        ),
                      ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Flexible(
                        child: Text(
                          pack['name'] as String? ?? '',
                          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                          maxLines: 1, overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text('v${pack['version'] ?? ''}', style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                    ]),
                    const SizedBox(height: 2),
                    Text(
                      pack['description'] as String? ?? '',
                      style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                      maxLines: 2, overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(l10n.emojiMarketEmojiCount(count), style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              if (installed)
                Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(l10n.installed, style: const TextStyle(fontSize: 11, color: Colors.green)),
                    TextButton(
                      onPressed: isDownloading ? null : () => onUninstall(index),
                      style: TextButton.styleFrom(
                        foregroundColor: Colors.red.shade300,
                        minimumSize: const Size(0, 32),
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                      ),
                      child: Text(l10n.emojiMarketUninstall, style: const TextStyle(fontSize: 12)),
                    ),
                  ],
                )
              else
                FilledButton(
                  onPressed: isDownloading ? null : () => onDownload(index),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 36)),
                  child: isDownloading ? Text(l10n.emojiMarketDownloading) : Text(l10n.download),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 已下载市场包贴图网格（点击发送：复用图片消息通道）
class EmojiMarketPackGrid extends StatelessWidget {
  final Map<String, dynamic> pack;
  final void Function(String url, String name, String meaning) onSend;
  final void Function(Map<String, dynamic> pack) onUninstall;

  const EmojiMarketPackGrid({
    super.key,
    required this.pack,
    required this.onSend,
    required this.onUninstall,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final emojis = (pack['emojis'] as List? ?? []).cast<Map<String, dynamic>>();
    if (emojis.isEmpty) {
      return Center(
        child: Text(l10n.noEmoji, style: const TextStyle(fontSize: 13, color: Colors.grey)),
      );
    }
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 3,
        mainAxisSpacing: 8,
        crossAxisSpacing: 8,
      ),
      itemCount: emojis.length + 1,
      itemBuilder: (ctx, i) {
        if (i == emojis.length) {
          return InkWell(
            onTap: () => onUninstall(pack),
            borderRadius: BorderRadius.circular(10),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.delete_outline, color: Colors.grey.shade400, size: 22),
                const SizedBox(height: 2),
                Text(l10n.delete, style: TextStyle(fontSize: 10, color: Colors.grey.shade400)),
              ],
            ),
          );
        }
        final item = emojis[i];
        final url = item['url'] as String? ?? '';
        final name = item['name'] as String? ?? l10n.chatEmoji;
        final meaning = item['meaning'] as String? ?? name;
        return InkWell(
          onTap: () => onSend(url, name, meaning),
          borderRadius: BorderRadius.circular(10),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  ApiClient().resolveUrl(url),
                  width: 54, height: 54, fit: BoxFit.cover,
                  errorBuilder: (c, e, s) => Container(
                    width: 54, height: 54, color: Colors.grey.shade200,
                    child: const Icon(Icons.broken_image, size: 20, color: Colors.grey),
                  ),
                ),
              ),
              const SizedBox(height: 3),
              Text(
                name,
                style: TextStyle(fontSize: 10, color: Colors.grey.shade500),
                maxLines: 1, overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        );
      },
    );
  }
}

/// 我的表情网格（自定义 emoji，长按删除）
class EmojiCustomGrid extends StatelessWidget {
  final List<Map<String, dynamic>> custom;
  final VoidCallback onAdd;
  final ValueChanged<Map<String, dynamic>> onSend;
  final ValueChanged<Map<String, dynamic>> onDelete;

  const EmojiCustomGrid({
    super.key,
    required this.custom,
    required this.onAdd,
    required this.onSend,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
      ),
      itemCount: custom.length + 1,
      itemBuilder: (ctx, i) {
        if (i == custom.length) {
          return InkWell(
            onTap: onAdd,
            borderRadius: BorderRadius.circular(10),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.add_circle_outline, color: Theme.of(context).colorScheme.primary, size: 30),
                const SizedBox(height: 4),
                Text(l10n.chatEmojiAdd, style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
              ],
            ),
          );
        }
        final emoji = custom[i];
        final url = (emoji['url'] as String? ?? '');
        final name = (emoji['name'] as String? ?? l10n.chatEmoji);
        return InkWell(
          onTap: () => onSend(emoji),
          onLongPress: () => onDelete(emoji),
          borderRadius: BorderRadius.circular(10),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  ApiClient().resolveUrl(url),
                  width: 46,
                  height: 46,
                  fit: BoxFit.cover,
                  errorBuilder: (c, e, s) => Container(
                    width: 46,
                    height: 46,
                    color: Colors.black.withValues(alpha: 0.05),
                    child: const Icon(Icons.broken_image, size: 20, color: Colors.grey),
                  ),
                ),
              ),
              const SizedBox(height: 3),
              Text(name, style: TextStyle(fontSize: 10, color: Colors.grey.shade500), maxLines: 1, overflow: TextOverflow.ellipsis),
            ],
          ),
        );
      },
    );
  }
}

/// 表情包网格（内置/已下载包；市场包转发到 [EmojiMarketPackGrid]）
class EmojiPackGrid extends StatelessWidget {
  final Map<String, dynamic> pack;
  final int index;
  final void Function(int index, String emoji, String name) onSend;
  final ValueChanged<int> onRemove;
  final ValueChanged<int> onDownload;

  /// 市场包子网格的两个专用通道（图片消息发送 / 网格内卸载），与普通包的文本发送不同
  final void Function(String url, String name, String meaning) onMarketSend;
  final void Function(Map<String, dynamic> pack) onUninstallMarketBody;

  const EmojiPackGrid({
    super.key,
    required this.pack,
    required this.index,
    required this.onSend,
    required this.onRemove,
    required this.onDownload,
    required this.onMarketSend,
    required this.onUninstallMarketBody,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    if ((pack['type'] as String? ?? '') == 'market') {
      return EmojiMarketPackGrid(pack: pack, onSend: onMarketSend, onUninstall: onUninstallMarketBody);
    }
    final downloaded = pack['downloaded'] as bool? ?? false;
    final emojis = (pack['emojis'] as List? ?? []).cast<Map<String, dynamic>>();
    if (emojis.isEmpty) {
      // 兜底：无明细（旧缓存/异常）时保留手动下载入口
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(pack['description'] as String? ?? l10n.noEmoji, style: const TextStyle(fontSize: 13, color: Colors.grey)),
            const SizedBox(height: 14),
            FilledButton.icon(
              onPressed: () => onDownload(index),
              icon: const Icon(Icons.download),
              label: Text(l10n.downloadPack),
            ),
          ],
        ),
      );
    }
    return Column(
      children: [
        if (!downloaded)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
            child: Text(
              l10n.chatEmojiHint(pack['description'] ?? ''),
              style: const TextStyle(fontSize: 12, color: Colors.grey),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        Expanded(
          child: GridView.builder(
            padding: const EdgeInsets.all(12),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 5,
              mainAxisSpacing: 8,
              crossAxisSpacing: 8,
            ),
            itemCount: emojis.length + 1,
            itemBuilder: (ctx, i) {
              if (i == emojis.length) {
                // 删除包按钮（内置包不显示）
                final builtin = pack['builtin'] as bool? ?? false;
                if (builtin) return const SizedBox.shrink();
                return InkWell(
                  onTap: () => onRemove(index),
                  borderRadius: BorderRadius.circular(10),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(Icons.delete_outline, color: Colors.grey.shade400, size: 22),
                      const SizedBox(height: 2),
                      Text(l10n.delete, style: TextStyle(fontSize: 10, color: Colors.grey.shade400)),
                    ],
                  ),
                );
              }
              final item = emojis[i];
              final emoji = item['emoji'] as String? ?? '';
              final name = item['name'] as String? ?? '';
              return InkWell(
                onTap: () => onSend(index, emoji, name),
                borderRadius: BorderRadius.circular(10),
                child: Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(emoji, style: const TextStyle(fontSize: 30)),
                      const SizedBox(height: 2),
                      Text(name, style: TextStyle(fontSize: 10, color: Colors.grey.shade500), maxLines: 1, overflow: TextOverflow.ellipsis),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}
