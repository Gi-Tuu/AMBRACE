// F7-b（2026-08-31）自 features/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../providers/chat_provider.dart';
import '../../services/api_client.dart';
import '../../services/api/emojis_api.dart';
import 'chat_emoji_panel_bodies.dart';
// ── 表情包面板：包 tab（下载/切换）+ emoji 网格（点击即发送）──
class ChatEmojiPanelSheet extends StatefulWidget {
  const ChatEmojiPanelSheet({super.key});

  @override
  State<ChatEmojiPanelSheet> createState() => _ChatEmojiPanelSheetState();
}

class _ChatEmojiPanelSheetState extends State<ChatEmojiPanelSheet> {
  List<Map<String, dynamic>>? _packs;
  List<Map<String, dynamic>> _custom = [];
  int _selected = -1; // -1 = 我的表情
  bool _loading = true;
  // 表情市场（2026-08-23）：市场 tab 列表 / 下载中状态
  List<Map<String, dynamic>>? _marketPacks;
  bool _marketTab = false; // 当前是否显示「市场」tab
  bool _marketLoading = false;
  bool _marketError = false;
  final Set<String> _downloading = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        ApiClient().getEmojiPacks(),
        ApiClient().getCustomEmojis(),
      ]);
      if (!mounted) return;
      setState(() {
        _packs = results[0].cast<Map<String, dynamic>>();
        _custom = (results[1] as List).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _download(int index) async {
    final l10n = AppLocalizations.of(context)!;
    final pack = _packs![index];
    final ok = await ApiClient().downloadEmojiPack(pack['id'] as String);
    if (ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatEmojiDownloaded(pack['name']))));
      await _load();
    }
  }

  Future<void> _remove(int index) async {
    final pack = _packs![index];
    final ok = await ApiClient().removeEmojiPack(pack['id'] as String);
    if (ok && mounted) {
      await _load();
    }
  }

  // ── 表情市场（2026-08-23）──

  /// 拉取市场包列表（懒加载；失败标记错误态并在界面展示）
  Future<void> _loadMarket() async {
    if (mounted) setState(() { _marketLoading = true; _marketError = false; });
    try {
      final list = await ApiClient().getMarketEmojiPacks();
      if (!mounted) return;
      setState(() {
        _marketPacks = list.cast<Map<String, dynamic>>();
        _marketLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() { _marketLoading = false; _marketError = true; });
    }
  }

  /// 下载并安装市场包；成功后刷新已下载列表并自动切回对应表情包 tab
  Future<void> _downloadMarket(int index) async {
    final l10n = AppLocalizations.of(context)!;
    final pack = _marketPacks![index];
    final packId = pack['id'] as String;
    setState(() => _downloading.add(packId));
    try {
      await ApiClient().downloadMarketEmojiPack(packId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatEmojiDownloaded(pack['name']))));
      await _load();
      await _loadMarket();
      if (!mounted) return;
      final idx = _packs?.indexWhere((p) => p['id'] == 'market:$packId') ?? -1;
      if (idx >= 0) {
        setState(() { _marketTab = false; _selected = idx; });
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.emojiMarketDownloadFail)));
      }
    } finally {
      if (mounted) setState(() => _downloading.remove(packId));
    }
  }

  /// 从市场列表卸载；成功后刷新已下载列表 + 市场列表
  Future<void> _uninstallMarket(int index) async {
    final l10n = AppLocalizations.of(context)!;
    final pack = _marketPacks![index];
    final ok = await ApiClient().removeMarketEmojiPack(pack['id'] as String);
    if (ok && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.emojiMarketUninstalled)));
      await _load();
      await _loadMarket();
      if (mounted) setState(() { if (_selected >= (_packs?.length ?? 0)) _selected = -1; });
    }
  }

  /// 从已下载市场包网格卸载
  Future<void> _uninstallMarketFromBody(Map<String, dynamic> pack) async {
    final raw = (pack['id'] as String).replaceFirst('market:', '');
    final ok = await ApiClient().removeMarketEmojiPack(raw);
    if (ok && mounted) {
      await _load();
      await _loadMarket();
      if (mounted) setState(() { if (_selected >= (_packs?.length ?? 0)) _selected = -1; });
    }
  }

  /// 从市场列表点击已安装包 → 切到对应表情包网格 tab
  void _openInstalledMarketPack(String packId) {
    final idx = _packs?.indexWhere((p) => p['id'] == 'market:$packId') ?? -1;
    if (idx >= 0) {
      setState(() { _marketTab = false; _selected = idx; });
    }
  }

  /// 点击已下载市场包贴图：复用现有「图片消息」发送通道（JSON 写入 meaning/name）
  void _sendMarketEmoji(String url, String name, String meaning) {
    final chat = context.read<ChatProvider>();
    Navigator.pop(context);
    chat.sendEmoji(url, name, meaning: meaning);
  }

  // 点击表情：未下载的包先自动下载再发送（后端全量返回明细，未下载也能预览）
  Future<void> _sendFromPack(int index, String emoji, String name) async {
    final pack = _packs![index];
    final downloaded = pack['downloaded'] as bool? ?? false;
    if (!downloaded) {
      final ok = await ApiClient().downloadEmojiPack(pack['id'] as String);
      if (!ok || !mounted) return;
      await _load();
    }
    if (mounted) _send(emoji, name);
  }

  void _send(String emoji, String name) {
    final chat = context.read<ChatProvider>();
    Navigator.pop(context);
    chat.sendMessage('$emoji $name');
  }

  void _sendCustom(Map<String, dynamic> emoji) {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    Navigator.pop(context);
    chat.sendEmoji(emoji['url'] as String? ?? '', emoji['name'] as String? ?? l10n.chatEmoji);
  }

  Future<void> _pickCustomEmoji() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final res = await FilePicker.pickFiles(
        type: FileType.image,
        // allowMultiple 已弃用（单文件默认），移除
      );
      if (res.isEmpty || res.single.path == null) return;
      final file = File(res.single.path!);
      await ApiClient().uploadCustomEmoji(file, l10n.myEmoji);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.emojiAdded)));
        await _load();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${l10n.addFailed}: $e')));
      }
    }
  }

  Future<void> _deleteCustom(Map<String, dynamic> emoji) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteEmojiTitle),
        content: Text(l10n.deleteEmojiConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    await ApiClient().deleteCustomEmoji(emoji['id'] as int);
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final safe = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: safe),
      child: SizedBox(
        height: MediaQuery.of(context).size.height * 0.45,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
              child: Row(
                children: [
                  Text(l10n.emojiPack, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
            ),
            if (_loading)
              const Expanded(child: Center(child: CircularProgressIndicator()))
            else ...[
              SizedBox(
                height: 40,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        avatar: const Icon(Icons.person_outline, size: 16),
                        label: Text(l10n.mine),
                        selected: !_marketTab && _selected == -1,
                        onSelected: (_) => setState(() { _selected = -1; _marketTab = false; }),
                      ),
                    ),
                    for (var i = 0; i < (_packs?.length ?? 0); i++)
                      Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          label: Text(_packs![i]['name'] as String? ?? ''),
                          selected: !_marketTab && _selected == i,
                          onSelected: (_) => setState(() { _selected = i; _marketTab = false; }),
                        ),
                      ),
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        avatar: const Icon(Icons.storefront_outlined, size: 16),
                        label: Text(l10n.emojiMarketTab),
                        selected: _marketTab,
                        onSelected: (_) {
                          setState(() => _marketTab = true);
                          if (_marketPacks == null && !_marketLoading) _loadMarket();
                        },
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 6),
              Expanded(
                child: _marketTab
                    ? EmojiMarketListBody(
                        packs: _marketPacks,
                        loading: _marketLoading,
                        error: _marketError,
                        downloading: _downloading,
                        onDownload: _downloadMarket,
                        onUninstall: _uninstallMarket,
                        onOpenInstalled: _openInstalledMarketPack,
                      )
                    : _selected == -1
                        ? EmojiCustomGrid(
                            custom: _custom,
                            onAdd: _pickCustomEmoji,
                            onSend: _sendCustom,
                            onDelete: _deleteCustom,
                          )
                        : (_selected >= 0 && _selected < (_packs?.length ?? 0))
                            ? EmojiPackGrid(
                                pack: _packs![_selected],
                                index: _selected,
                                onSend: _sendFromPack,
                                onRemove: _remove,
                                onDownload: _download,
                                onMarketSend: _sendMarketEmoji,
                                onUninstallMarketBody: _uninstallMarketFromBody,
                              )
                            : EmojiCustomGrid(
                                custom: _custom,
                                onAdd: _pickCustomEmoji,
                                onSend: _sendCustom,
                                onDelete: _deleteCustom,
                              ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  // 市场 tab 主体：包列表（名称/描述/版本/图标/已装标记 + 下载/卸载按钮）
}
