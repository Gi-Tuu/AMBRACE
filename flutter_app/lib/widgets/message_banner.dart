import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import '../global_keys.dart';
import '../models/character.dart';
import '../providers/chat_provider.dart';
import '../providers/characters_provider.dart';
import '../services/notification_service.dart';
import '../screens/chat/chat_screen.dart';
import 'ai_avatar.dart';

/// 顶部横幅数据：新消息（角色 + 内容）
class MessageBannerData {
  final int characterId;
  final String content;
  final int sessionId;
  const MessageBannerData({
    required this.characterId,
    required this.content,
    required this.sessionId,
  });
}

OverlayEntry? _currentEntry;

/// 弹出顶部横幅（任意页面可用），5 秒自动消失，点击跳转对应聊天页
void showNewMessageBanner(MessageBannerData data) {
  final overlay = appNavigatorKey.currentState?.overlay;
  if (overlay == null) return;

  _currentEntry?.remove();
  late OverlayEntry entry;
  var removed = false;
  void close() {
    if (removed) return;
    removed = true;
    if (entry.mounted) entry.remove();
    if (identical(_currentEntry, entry)) _currentEntry = null;
  }

  entry = OverlayEntry(
    builder: (context) => _MessageBanner(data: data, onClose: close),
  );
  _currentEntry = entry;
  overlay.insert(entry);

  Timer(const Duration(seconds: 5), close);
}

class _MessageBanner extends StatefulWidget {
  final MessageBannerData data;
  final VoidCallback onClose;
  const _MessageBanner({required this.data, required this.onClose});

  @override
  State<_MessageBanner> createState() => _MessageBannerState();
}

class _MessageBannerState extends State<_MessageBanner>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<Offset> _slide;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 250),
    );
    _slide = Tween<Offset>(begin: const Offset(0, -1.5), end: Offset.zero)
        .animate(CurvedAnimation(parent: _controller, curve: Curves.easeOut));
    _controller.forward();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  AICharacter? _findCharacter(BuildContext context) {
    final chars = context.read<CharactersProvider>().characters;
    for (final c in chars) {
      if (c.id == widget.data.characterId) return c;
    }
    return null;
  }

  Future<void> _openChat(BuildContext context) async {
    widget.onClose();
    final nav = appNavigatorKey.currentState;
    if (nav == null) return;
    final prev = NotificationService().activeScreen;
    final char = _findCharacter(context);
    if (char == null) return;
    context.read<ChatProvider>().setCharacter(char);
    NotificationService().setActiveScreen(ActiveScreen.chat, characterId: char.id);
    await nav.push(MaterialPageRoute(builder: (_) => const ChatScreen()));
    NotificationService().setActiveScreen(prev);
  }

  @override
  Widget build(BuildContext context) {
    final char = _findCharacter(context);
    final title = char?.name ?? AppLocalizations.of(context)!.aiFriendFallback;
    final avatarUrl = char?.avatarUrl;
    return Positioned(
      top: 0,
      left: 0,
      right: 0,
      child: SafeArea(
        child: SlideTransition(
          position: _slide,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(8, 4, 8, 0),
            child: Material(
              elevation: 6,
              borderRadius: BorderRadius.circular(14),
              color: Theme.of(context).colorScheme.surface,
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => _openChat(context),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Row(
                    children: [
                      AIAvatar(name: title, size: 36, imageUrl: avatarUrl),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              title,
                              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              widget.data.content,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.close, size: 18),
                        onPressed: widget.onClose,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
