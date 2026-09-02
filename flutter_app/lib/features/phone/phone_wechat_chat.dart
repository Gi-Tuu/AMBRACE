// F7-c-2（2026-08-31）自 features/phone/ai_interaction_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:ui' show ImageFilter;
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/ai_chat.dart';
import '../../models/character.dart';
import '../../theme/aurora_tokens.dart';
import '../../theme/skins/skin_colors.dart';
import '../../widgets/ai_avatar.dart';
import '../../widgets/ios_card_group.dart';
import 'phone_tiles.dart' show maybeReduceBlur;
import 'phone_wechat_archive.dart';

class WechatChatScreen extends StatefulWidget {
  const WechatChatScreen({super.key, 
    required this.self,
    required this.other,
    required this.chats,
  });

  final AICharacter self;
  final AICharacter other;
  final List<AIChat> chats;

  @override
  State<WechatChatScreen> createState() => WechatChatScreenState();
}

/// 畅聊聊天窗口（该角色视角，只读）：只展示最近 50 条，进入自动滚动到最新消息；右上角聊天记录箱看完整记录
class WechatChatScreenState extends State<WechatChatScreen> {
  static const int _maxDisplay = 50;
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _jumpToLatest());
    _markRead();
  }

  /// 进入对话即视为已读（红点依据本地已读标记判断）
  Future<void> _markRead() async {
    try {
      if (widget.chats.isEmpty) return;
      final sp = await SharedPreferences.getInstance();
      await sp.setInt(
        'wechat_read_${widget.self.id}_${widget.other.id}',
        widget.chats.last.id,
      );
    } catch (_) {}
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _jumpToLatest() {
    if (!mounted || !_scroll.hasClients) return;
    _scroll.jumpTo(_scroll.position.maxScrollExtent);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final self = widget.self;
    final other = widget.other;
    final all = widget.chats;
    final chats =
        all.length > _maxDisplay ? all.sublist(all.length - _maxDisplay) : all;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.surfaceContainerLowest,
      appBar: wechatGlassAppBar(
        context,
        title: Row(
          children: [
            AIAvatar(name: other.name, size: 28, imageUrl: other.avatarUrl),
            const SizedBox(width: 8),
            Text(other.name),
          ],
        ),
        actions: [
          IconButton(
            tooltip: l10n.archiveBox,
            icon: const Icon(Icons.inventory_2),
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => WechatArchiveScreen(self: self, other: other),
                ),
              );
            },
          ),
        ],
      ),
      body: ListView.builder(
        controller: _scroll,
        padding: const EdgeInsets.all(12),
        itemCount: chats.length,
        itemBuilder: (context, i) {
          final chat = chats[i];
          final mine = chat.speakerId == self.id;
          return _WechatBubble(
            chat: chat,
            mine: mine,
            avatar: mine ? self.avatarUrl : other.avatarUrl,
          );
        },
      ),
      // Aurora P3：只读输入栏主题化（皮肤 inputBarBg 优先；否则半透明 + 顶部描边，
      // BackdropFilter 全页仅此 1 处）
      bottomNavigationBar: Builder(
        builder: (context) {
          final scheme = Theme.of(context).colorScheme;
          final isDark = Theme.of(context).brightness == Brightness.dark;
          final skinInput = Theme.of(context).extension<SkinColors>()?.inputBarBg;
          final useBlur = skinInput == null;
          final barColor = skinInput ??
              (isDark
                  ? Colors.black.withValues(alpha: 0.30)
                  : Colors.white.withValues(alpha: 0.55));
          final borderColor = isDark
              ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
              : Colors.black.withValues(alpha: AppGlass.borderAlpha);
          final sigma =
              AppGlass.effectiveBlur(AppGlass.blurMedium, reduceBlur: maybeReduceBlur(context));
          Widget bar = Container(
            decoration: BoxDecoration(
              color: barColor,
              border: Border(top: BorderSide(color: borderColor, width: 0.5)),
            ),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            child: Row(
              children: [
                Icon(Icons.add_circle_outline,
                    size: 26, color: scheme.onSurfaceVariant),
                const SizedBox(width: 8),
                Expanded(
                  child: Container(
                    height: 34,
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    alignment: Alignment.centerLeft,
                    decoration: BoxDecoration(
                      color: scheme.surfaceContainerHighest,
                      borderRadius: BorderRadius.circular(17),
                    ),
                    child: Text(
                      l10n.readOnly,
                      style:
                          const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Icon(Icons.mood, size: 24, color: scheme.onSurfaceVariant),
              ],
            ),
          );
          if (!useBlur) return bar;
          return ClipRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
              child: bar,
            ),
          );
        },
      ),
    );
  }
}

/// 畅聊聊天记录箱：按 年→月→日 折叠展示该角色对完整聊天记录（只读）

class _WechatBubble extends StatelessWidget {
  const _WechatBubble({
    required this.chat,
    required this.mine,
    required this.avatar,
  });

  final AIChat chat;
  final bool mine;
  final String? avatar;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // Aurora P3：气泡对齐 B3 聊天页——mine 侧主题渐变（primary → primary@0.85）+ 白字，
    // 非 mine 用 surfaceContainerHighest；圆角 10 → 18；持续动效不做
    final bubbleDecoration = mine
        ? BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [
                scheme.primary,
                scheme.primary.withValues(alpha: 0.85),
              ],
            ),
            borderRadius: BorderRadius.circular(18),
          )
        : BoxDecoration(
            color: scheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(18),
          );
    final contentColor = mine ? Colors.white : scheme.onSurface;
    return Align(
      alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!mine) ...[
              AIAvatar(name: chat.speakerName, size: 36, imageUrl: avatar),
              const SizedBox(width: 8),
            ],
            Flexible(
              child: Column(
                crossAxisAlignment:
                    mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
                children: [
                  Text(
                    '${chat.speakerName} · ${DateFormat('MM-dd HH:mm').format(chat.createdAt)}',
                    style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle),
                  ),
                  const SizedBox(height: 3),
                  Container(
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.62,
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: bubbleDecoration,
                    child: Text(
                      chat.content,
                      style: TextStyle(fontSize: 14, height: 1.35, color: contentColor),
                    ),
                  ),
                ],
              ),
            ),
            if (mine) ...[
              const SizedBox(width: 8),
              AIAvatar(name: chat.speakerName, size: 36, imageUrl: avatar),
            ],
          ],
        ),
      ),
    );
  }
}

/// 畅聊风格时间：今天显示 HH:mm，昨天显示"昨天"，更早显示 MM-dd
String wechatTime(DateTime t, AppLocalizations l10n) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  if (day == today) return DateFormat('HH:mm').format(t);
  if (day == today.subtract(const Duration(days: 1))) return l10n.yesterday;
  return DateFormat('MM-dd').format(t);
}


AppBar wechatGlassAppBar(
  BuildContext context, {
  Widget? title,
  List<Widget> actions = const [],
}) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  return AppBar(
    backgroundColor: isDark
        ? Colors.black.withValues(alpha: 0.30)
        : Colors.white.withValues(alpha: 0.55),
    elevation: 0,
    scrolledUnderElevation: 0,
    surfaceTintColor: Colors.transparent,
    shape: Border(
      bottom: BorderSide(
        color: isDark
            ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
            : Colors.black.withValues(alpha: AppGlass.borderAlpha),
        width: 0.5,
      ),
    ),
    title: title,
    actions: actions,
  );
}

/// 小手机：角色"手机"方块网格，点开进入该角色的手机桌面（目前仅「畅聊」应用）
