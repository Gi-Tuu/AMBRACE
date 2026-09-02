// F7-b（2026-08-31）自 features/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/message.dart';
import '../../providers/chat_provider.dart';
import '../../services/api_client.dart';
import '../../utils/stage_text.dart';
import '../../widgets/app_page_route.dart';
import '../../widgets/floating_sheet.dart';
import 'voice_call_screen.dart';
import 'chat_emoji_panel.dart';
import 'chat_voice_record_sheet.dart';

/// 消息/媒体动作流：长按菜单与删除、事件时钟、语音通话入口、图片/文件/语音/表情入口。
/// mixin on State；_quote 由屏幕 State 持有，经 [applyQuote] 抽象回写。
mixin ChatMessageMediaActions<T extends StatefulWidget> on State<T> {
  /// 设置待发送引用（长按气泡-引用；由屏幕 State 实现并 setState）。
  void applyQuote(covariant dynamic msg);
  Future<void> deleteMessage(int messageId) async {
    try {
      await context.read<ChatProvider>().deleteMessage(messageId);
    } catch (_) {}
  }

  /// 事件时钟：展示未到期的定时承诺，允许用户删除（2026-08-15）
  Future<void> showEventClockDialog(int characterId) async {
    final l10n = AppLocalizations.of(context)!;
    List<Map<String, dynamic>> items = [];
    String? error;
    try {
      items = await ApiClient().listTimers(characterId);
    } catch (e) {
      error = '$e';
    }
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.eventClockTitle),
        content: SizedBox(
          width: double.maxFinite,
          child: error != null
              ? Text('${l10n.loadFailed}: $error', style: const TextStyle(fontSize: 13, color: Colors.grey))
              : items.isEmpty
                  ? Text(l10n.eventClockEmpty, style: const TextStyle(fontSize: 13, color: Colors.grey))
                  : Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(l10n.eventClockHint, style: const TextStyle(fontSize: 12, color: Colors.grey)),
                        const SizedBox(height: 8),
                        Flexible(
                          child: ListView.builder(
                            shrinkWrap: true,
                            itemCount: items.length,
                            itemBuilder: (_, i) {
                              final it = items[i];
                              final owner = it['owner'] == 'user' ? l10n.userPromised : l10n.aiPromised;
                              final hint = it['content_hint'] as String? ?? l10n.doSomething;
                              final left = '${it['left_minutes']}${l10n.minutesLater}（${it['due_at']}）';
                              return ListTile(
                                dense: true,
                                contentPadding: EdgeInsets.zero,
                                leading: Icon(
                                  it['owner'] == 'user'
                                      ? Icons.person_outline
                                      : Icons.smart_toy_outlined,
                                  size: 20,
                                ),
                                title: Text('$owner「$hint」', maxLines: 2, overflow: TextOverflow.ellipsis),
                                subtitle: Text(left, style: const TextStyle(fontSize: 12)),
                                trailing: IconButton(
                                  icon: const Icon(Icons.delete_outline, size: 20),
                                  tooltip: l10n.deleteTimerTooltip,
                                  onPressed: () async {
                                    try {
                                      await ApiClient().deleteTimer(characterId, it['id'] as int);
                                      if (ctx.mounted) Navigator.pop(ctx);
                                      if (mounted) {
                                        ScaffoldMessenger.of(context).showSnackBar(
                                          SnackBar(content: Text(l10n.timerDeleted)),
                                        );
                                      }
                                    } catch (_) {
                                      if (ctx.mounted) {
                                        ScaffoldMessenger.of(ctx).showSnackBar(
                                          SnackBar(content: Text(l10n.deleteFailed)),
                                        );
                                      }
                                    }
                                  },
                                ),
                              );
                            },
                          ),
                        ),
                      ],
                    ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.close)),
        ],
      ),
    );
  }

  /// 长按气泡菜单：气泡式小框（非抽屉），删除（仅最后一条）/引用/复制
  Future<void> showBubbleMenu(Offset position, ChatMessage msg, int index) async {
    final l10n = AppLocalizations.of(context)!;
    final chat = context.read<ChatProvider>();
    final isLast = index == chat.messages.length - 1;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox?;
    final size = overlay?.size ?? const Size(0, 0);
    final result = await showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(
        position.dx,
        position.dy,
        size.width - position.dx,
        size.height - position.dy,
      ),
      items: [
        if (isLast)
          PopupMenuItem<String>(
            value: 'delete',
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.delete_outline, size: 18),
              SizedBox(width: 8),
              Text(l10n.delete),
            ]),
          ),
        PopupMenuItem<String>(
          value: 'quote',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.format_quote, size: 18),
            SizedBox(width: 8),
            Text(l10n.quote),
          ]),
        ),
        PopupMenuItem<String>(
          value: 'copy',
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.copy, size: 18),
            SizedBox(width: 8),
            Text(l10n.copy),
          ]),
        ),
      ],
    );
    if (result == null || !mounted) return;
    switch (result) {
      case 'delete':
        await _confirmDelete(msg);
        break;
      case 'quote':
        applyQuote(msg);
        break;
      case 'copy':
        final stripped = StageText.parse(msg.content).text;
        await Clipboard.setData(ClipboardData(text: stripped.isEmpty ? msg.content : stripped));
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.copied), duration: const Duration(seconds: 1)),
          );
        }
        break;
    }
  }

  /// 删除确认（文案统一「删除」；删除为物理删除，连带小字/引用一并消失）
  Future<void> _confirmDelete(ChatMessage msg) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMessageTitle),
        content: Text(l10n.deleteMessageConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok == true && mounted) await deleteMessage(msg.id);
  }

  /// 语音通话入口：进入电话式界面（基于既有 WS /api/v1/voice/stream）。
  /// 需先有会话与角色；否则提示先选择角色。
  Future<void> startVoiceCall() async {
    final chat = context.read<ChatProvider>();
    final char = chat.currentCharacter;
    if (chat.sessionId == null || char == null) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chooseFriendFirst)));
      }
      return;
    }
    // 语音通话期间避免与文本输入态冲突
    FocusManager.instance.primaryFocus?.unfocus();
    await Navigator.push(
      context,
      AppPageRoute(
        builder: (_) => VoiceCallScreen(
          baseUrl: ApiClient().baseUrl,
          token: ApiClient().token,
          sessionId: chat.sessionId!,
          character: char,
        ),
      ),
    );
  }

  /// 文件入口：选择本地文件 → 上传（后端提取摘要，AI 可读）
  Future<void> pickAndSendFile() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    try {
      final result = await FilePicker.pickFiles();
      if (result.isEmpty) return;
      final path = result.single.path;
      if (path == null || !mounted) return;
      final file = File(path);
      if (file.lengthSync() > 20 * 1024 * 1024) {
        if (mounted) {
          final l10n = AppLocalizations.of(context)!;
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.fileTooLarge)));
        }
        return;
      }
      await chat.uploadFile(file);
    } catch (e) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatFileSendFail(e))));
      }
    }
  }

  /// 表情包入口：底部面板（包 tab + 下载 + 点击发送 emoji）
  void showEmojiPanel() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => const ChatEmojiPanelSheet(),
    );
  }

  /// 语音发送：弹出按住说话录音面板
  Future<void> showVoiceRecorder() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    if (!mounted) return;
    // 先收起键盘再弹录音面板：避免键盘 inset 与弹层开/合动画互相干扰
    // （曾在语音发送成功后残留大面积灰色块，刷新才消失）
    FocusManager.instance.primaryFocus?.unfocus();
    await Future<void>.delayed(const Duration(milliseconds: 150));
    if (!mounted) return;
    // B3：录音面板改 FloatingSheet 风格（毛玻璃 + 拖拽条），按住说话交互不变
    await showFloatingSheet(
      context: context,
      expandable: false,
      maxHeightFraction: 0.35,
      child: ChatVoiceRecordSheet(chat: chat),
    );
  }

  Future<void> pickAndUploadImage() async {
    final chat = context.read<ChatProvider>();
    if (chat.sessionId == null) return;
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null || !mounted) return;
    // 选图后弹出配文框：气泡只显示图片+配文，OCR 内容只进 AI 上下文
    final caption = await _showImageCaptionDialog(File(picked.path));
    if (caption == null || !mounted) return;
    // 等待弹窗关闭动画完全结束再上传：若在动画期间立即 notifyListeners 重建页面，
    // 会触发 InheritedElement._dependents 断言红屏（_dependents.isEmpty is not true）
    await Future<void>.delayed(const Duration(milliseconds: 350));
    if (!mounted) return;
    await chat.uploadImage(File(picked.path), caption: caption);
  }

  /// 图片配文输入弹窗：图预览 + 文字输入。返回 null 表示取消。
  Future<String?> _showImageCaptionDialog(File imageFile) async {
    final l10n = AppLocalizations.of(context)!;
    final captionController = TextEditingController();
    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.chatSendImage),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 260, maxHeight: 180),
                child: Image.file(imageFile, fit: BoxFit.contain),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: captionController,
              maxLines: 3,
              maxLength: 500,
              decoration: InputDecoration(
                hintText: l10n.chatImageCaption,
                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text(l10n.cancel),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, captionController.text.trim()),
            child: Text(l10n.send),
          ),
        ],
      ),
    );
    // 弹窗退出动画结束后再释放 controller（约 200ms 动画），
    // 避免 TextField 元素卸载前访问已销毁的 controller 触发框架断言
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      captionController.dispose();
    });
    return result;
  }
}
