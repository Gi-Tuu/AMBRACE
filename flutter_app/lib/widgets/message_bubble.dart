import "package:flutter/material.dart";
import "package:url_launcher/url_launcher.dart";
import "package:audioplayers/audioplayers.dart";
import "../utils/stage_text.dart";
import "../utils/beijing_time.dart";
import "../theme/tokens.dart";
import "package:ai_companion/l10n/app_localizations.dart";

class MessageBubble extends StatelessWidget {
  final String message;
  final bool isUser;
  final String time;
  final String? imageUrl;
  final String? serverUrl;
  final String? aiAvatarUrl;
  final String? userAvatarUrl;
  final VoidCallback? onContinue;
  final Map<String, dynamic>? quoteMeta;
  final bool quoteDeleted;
  final ValueChanged<Offset>? onMenu;
  final bool showTime;
  final Map<String, dynamic>? fileMeta;
  final Map<String, dynamic>? voiceMeta;
  final Map<String, dynamic>? ttsMeta;
  final VoidCallback? onOpenFile;
  final String? reasoning;
  final List<String>? tools;
  /// 状态更新小字（2026-08-14：显示在气泡内容文本下方）
  final String? statusUpdate;
  final bool showReasoning;
  final bool showTools;
  /// SSE 真流式进行中：正文末尾显示闪烁光标（打字机效果）
  final bool isStreaming;

  const MessageBubble({
    super.key,
    required this.message,
    required this.isUser,
    this.time = "",
    this.imageUrl,
    this.serverUrl,
    this.aiAvatarUrl,
    this.userAvatarUrl,
    this.onContinue,
    this.quoteMeta,
    this.quoteDeleted = false,
    this.onMenu,
    this.showTime = true,
    this.fileMeta,
    this.voiceMeta,
    this.ttsMeta,
    this.onOpenFile,
    this.reasoning,
    this.tools,
    this.statusUpdate,
    this.showReasoning = false,
    this.showTools = false,
    this.isStreaming = false,
  });

  /// 打开文件：优先回调（下载/预览），否则尝试系统打开 URL
  void _openFile(BuildContext context) {
    if (onOpenFile != null) {
      onOpenFile!();
      return;
    }
    final meta = fileMeta;
    if (meta == null) return;
    final url = meta['url'] as String? ?? '';
    if (url.isEmpty) return;
    final resolved = url.startsWith('http') ? url : (serverUrl ?? '').replaceAll(RegExp(r'/+$'), '') + url;
    launchUrl(Uri.parse(resolved), mode: LaunchMode.externalApplication);
  }

  Widget _buildFileCard(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final meta = fileMeta ?? const {};
    final fname = meta['name'] as String? ?? l10n.file;
    final fsize = meta['size'] as String? ?? '';
    final ftype = meta['type'] as String? ?? 'file';
    final expired = meta['expired'] == true;
    return InkWell(
      onTap: expired
          ? () {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(content: Text(l10n.msgFileExpired)),
              );
            }
          : () => _openFile(context),
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surface.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: Theme.of(context).colorScheme.outlineVariant),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(_fileIcon(ftype), size: 26, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 8),
            Flexible(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(fname, style: const TextStyle(fontSize: AppTypography.helperSize, fontWeight: FontWeight.w600), maxLines: 2, overflow: TextOverflow.ellipsis),
                  if (fsize.isNotEmpty)
                    Text(expired ? l10n.msgFileSizeExpired(fsize) : fsize, style: TextStyle(fontSize: AppTypography.captionSize, color: expired ? AppColors.error : AppColors.textSecondary)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  IconData _fileIcon(String type) {
    final t = type.toLowerCase();
    if (t.contains('pdf')) return Icons.picture_as_pdf;
    if (t.contains('doc')) return Icons.description;
    if (t.contains('xls') || t.contains('csv')) return Icons.table_chart;
    if (t.contains('ppt')) return Icons.slideshow;
    if (t.contains('zip') || t.contains('rar') || t.contains('7z')) return Icons.folder_zip;
    if (t.contains('txt') || t.contains('md') || t.contains('json') || t.contains('log')) return Icons.article;
    return Icons.insert_drive_file;
  }

  Widget _buildVoiceBubble(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final meta = voiceMeta ?? const {};
    final dur = (meta['duration'] as num?)?.toInt() ?? 0;
    final url = (meta['url'] as String?) ?? '';
    return _AudioPlayable(
      url: url,
      serverUrl: serverUrl ?? '',
      label: l10n.voice,
      duration: dur,
      isUser: isUser,
    );
  }

  Widget _buildTtsRow(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final meta = ttsMeta ?? const {};
    final url = (meta['url'] as String?) ?? '';
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: _AudioPlayable(
        url: url,
        serverUrl: serverUrl ?? '',
        label: l10n.voiceReply,
        duration: 0,
        isUser: isUser,
      ),
    );
  }

  Widget _buildAvatar(String? url, IconData fallback, String base) {
    if (url == null || url.isEmpty) {
      return CircleAvatar(radius: 16, child: Icon(fallback, size: 18));
    }
    var resolved = url;
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      resolved = base.replaceAll(RegExp(r'/+$'), '') + url;
    }
    return ClipOval(
      child: Image.network(
        resolved,
        width: 32,
        height: 32,
        fit: BoxFit.cover,
        errorBuilder: (context, error, stack) => CircleAvatar(radius: 16, child: Icon(fallback, size: 18)),
      ),
    );
  }

  String _resolveImageUrl() {
    final url = imageUrl ?? '';
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    final base = serverUrl ?? '';
    return base.replaceAll(RegExp(r'/+$'), '') + url;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final stage = StageText.parse(message);
    // 状态更新/日历备注/备忘小字行（2026-08-14：标记保留在正文，前端剥离为气泡下方小字；兼容旧消息无 meta）
    final markerLines = <String>[...stage.markers];
    if (statusUpdate != null &&
        statusUpdate!.isNotEmpty &&
        !markerLines.any((l) => l.startsWith('状态更新：'))) {
      markerLines.add('状态更新：$statusUpdate');
    }
    var chatAvatar = _buildAvatar(aiAvatarUrl, Icons.smart_toy, serverUrl ?? '');
    var userAvatar = _buildAvatar(userAvatarUrl, Icons.person, serverUrl ?? '');

    final bubble = Container(
      constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.7),
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
      decoration: BoxDecoration(
        color: isUser
            ? Theme.of(context).colorScheme.primaryContainer
            : Theme.of(context).colorScheme.surfaceContainerHighest,
        boxShadow: AppShadow.light,
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(16),
          topRight: const Radius.circular(16),
          bottomLeft: isUser ? const Radius.circular(16) : Radius.zero,
          bottomRight: isUser ? Radius.zero : const Radius.circular(16),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 思考过程/调用能力（AI 消息顶部，仅产生且开关打开时显示，默认折叠）
          if (!isUser && showReasoning && (reasoning ?? '').isNotEmpty)
            _CollapsibleMeta(
              icon: Icons.psychology_outlined,
              label: l10n.thinkingProcess,
              detail: reasoning!,
            ),
          if (!isUser && showTools && (tools ?? const []).isNotEmpty)
            _CollapsibleMeta(
              icon: Icons.handyman_outlined,
              label: l10n.calledAbility,
              detail: tools!.join('、'),
            ),
          if (quoteMeta != null) _buildQuoteBlock(context),
          if (imageUrl != null && imageUrl!.isNotEmpty) ...[
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  maxWidth: MediaQuery.of(context).size.width * 0.6,
                  maxHeight: 280,
                ),
                child: Image.network(
                  _resolveImageUrl(),
                  fit: BoxFit.contain,
                  loadingBuilder: (ctx, child, progress) {
                    if (progress == null) return child;
                    return Container(
                      width: 200,
                      height: 160,
                      color: Colors.black.withValues(alpha: 0.05),
                      child: const Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
                    );
                  },
                  errorBuilder: (ctx, e, st) => Container(
                    width: 200,
                    height: 140,
                    color: Colors.black.withValues(alpha: 0.05),
                    child: Center(child: Text(l10n.imageLoadFailed, style: const TextStyle(fontSize: AppTypography.captionSize, color: AppColors.textSecondary))),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 6),
          ],
          if (fileMeta != null) ...[
            _buildFileCard(context),
            if (stage.text.isNotEmpty) const SizedBox(height: 6),
          ],
          if (stage.text.isNotEmpty)
            Text(
              stage.text,
              style: const TextStyle(fontSize: 15),
            ),
          // SSE 真流式：AI 正文末尾闪烁光标（打字机）
          if (isStreaming && !isUser)
            const _StreamingCursor(),
          // 标记小字（气泡内部底部：状态更新/日历备注/备忘，2026-08-14）
          for (final markerLine in markerLines)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  markerLine,
                  style: TextStyle(
                    fontSize: AppTypography.captionSize,
                    height: AppTypography.captionHeight,
                    fontStyle: FontStyle.italic,
                    color: AppColors.textMuted,
                  ),
                ),
              ),
            ),
          // 语音条放在识别文字下方（用户语音：文字在上、波形在下）
          if (voiceMeta != null) ...[
            if (stage.text.isNotEmpty) const SizedBox(height: 6),
            _buildVoiceBubble(context),
          ],
          if (!isUser && ttsMeta != null && (ttsMeta!['url'] as String? ?? '').isNotEmpty)
            _buildTtsRow(context),
          if (showTime && time.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                formatTimeOnly(time),
                style: TextStyle(fontSize: AppTypography.captionSize, color: Theme.of(context).colorScheme.onSurfaceVariant),
              ),
            ),
        ],
      ),
    );

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onLongPressStart: onMenu == null
            ? null
            : (d) => onMenu!(d.globalPosition),
        child: Column(
          crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          children: [
            // 非对话文本（上方小字：前导 + 中间；箭头↓指向下方气泡，宽度≤屏幕一半）
            if (stage.above.isNotEmpty)
              Padding(
                padding: EdgeInsets.only(
                  left: isUser ? 0 : 40,
                  right: isUser ? 48 : 0,
                  bottom: 2,
                ),
                child: _buildStageLine(context, stage.aboveLine, above: true, isUser: isUser),
              ),
            Row(
              mainAxisAlignment: isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
              // 头像顶部与气泡顶部持平（微信群聊格式）
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (!isUser) ...[chatAvatar, const SizedBox(width: 8)],
                Flexible(child: bubble),
                // 「继续」按钮移至气泡右侧
                if (!isUser && onContinue != null)
                  Padding(
                    padding: const EdgeInsets.only(left: 4),
                    child: TextButton.icon(
                      onPressed: onContinue,
                      icon: const Icon(Icons.play_arrow, size: 12),
                      label: Text(l10n.continueLabel, style: const TextStyle(fontSize: AppTypography.captionSize)),
                      style: TextButton.styleFrom(
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        minimumSize: const Size(0, 24),
                        foregroundColor: AppColors.textSecondary,
                        visualDensity: VisualDensity.compact,
                      ),
                    ),
                  ),
                if (isUser) ...[const SizedBox(width: 8), userAvatar],
              ],
            ),
            // 非对话文本（下方小字：收尾）
            if (stage.below.isNotEmpty)
              Padding(
                padding: EdgeInsets.only(
                  left: isUser ? 0 : 40,
                  right: isUser ? 48 : 0,
                  top: 2,
                ),
                child: _buildStageLine(context, stage.belowLine, above: false, isUser: isUser),
              ),
          ],
        ),
      ),
    );
  }

  /// 非对话文本小字行（v2.0.1）：箭头指向归属气泡——气泡上方小字箭头在最下端（↓），
  /// 气泡下方小字箭头在最上端（↑）；角色（AI）箭头靠左、用户箭头靠右；
  /// 小字展示宽度限制为屏幕一半，过长时换行（避免观感拥挤）。
  Widget _buildStageLine(BuildContext context, String line,
      {required bool above, required bool isUser}) {
    final arrow = Text(
      above ? '↓' : '↑',
      style: TextStyle(fontSize: AppTypography.captionSize, color: AppColors.textSecondary),
    );
    final content = ConstrainedBox(
      constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width / 2),
      child: Text(
        line,
        style: TextStyle(
          fontSize: AppTypography.captionSize,
          fontStyle: FontStyle.italic,
          color: AppColors.textSecondary,
        ),
      ),
    );
    return Column(
      crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
      children: above ? [content, arrow] : [arrow, content],
    );
  }

  /// 引用块（气泡内顶部）：左侧竖线 + 摘录；被引用消息已删则显示"原消息已删除"
  Widget _buildQuoteBlock(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final q = quoteMeta ?? const <String, dynamic>{};
    final content = q['content'] as String? ?? '';
    final label = q['sender'] == 'user' ? l10n.me : l10n.ta;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(6),
        border: Border(
          left: BorderSide(
            width: 3,
            color: isUser
                ? Theme.of(context).colorScheme.primary
                : Theme.of(context).colorScheme.outlineVariant,
          ),
        ),
      ),
      child: Text(
        quoteDeleted ? l10n.quoteDeleted : l10n.msgQuoteLine(label, content),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          fontSize: AppTypography.captionSize,
          color: Theme.of(context).colorScheme.onSurfaceVariant,
        ),
      ),
    );
  }

}

/// 思考过程/调用能力折叠块（默认折叠，点击展开看细节；灰色小字，非纯黑）
class _CollapsibleMeta extends StatefulWidget {
  final IconData icon;
  final String label;
  final String detail;

  const _CollapsibleMeta({
    required this.icon,
    required this.label,
    required this.detail,
  });

  @override
  State<_CollapsibleMeta> createState() => _CollapsibleMetaState();
}

class _CollapsibleMetaState extends State<_CollapsibleMeta> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final fg = scheme.onSurfaceVariant;
    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      width: double.infinity,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () => setState(() => _expanded = !_expanded),
            borderRadius: BorderRadius.circular(6),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(widget.icon, size: 13, color: fg),
                  const SizedBox(width: 4),
                  Text(
                    widget.label,
                    style: TextStyle(
                      fontSize: AppTypography.captionSize,
                      color: fg,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(width: 4),
                  if (!_expanded)
                    Flexible(
                      child: Text(
                        widget.detail,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: AppTypography.captionSize, color: fg.withValues(alpha: 0.7)),
                      ),
                    ),
                  Icon(
                    _expanded ? Icons.keyboard_arrow_up : Icons.keyboard_arrow_down,
                    size: 14,
                    color: fg.withValues(alpha: 0.6),
                  ),
                ],
              ),
            ),
          ),
          if (_expanded)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(
                color: scheme.onSurface.withValues(alpha: 0.05),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                widget.detail,
                style: TextStyle(
                  fontSize: AppTypography.captionSize,
                  color: fg.withValues(alpha: 0.85),
                  height: 1.4,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _AudioPlayable extends StatefulWidget {
  final String url;
  final String serverUrl;
  final String label;
  final int duration;
  final bool isUser;

  const _AudioPlayable({
    required this.url,
    required this.serverUrl,
    required this.label,
    required this.duration,
    required this.isUser,
  });

  @override
  State<_AudioPlayable> createState() => _AudioPlayableState();
}

class _AudioPlayableState extends State<_AudioPlayable> {
  final AudioPlayer _player = AudioPlayer();
  bool _playing = false;
  bool _loading = false;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _player.onPlayerComplete.listen((_) {
      if (mounted) setState(() => _playing = false);
    });
    _player.onPlayerStateChanged.listen((state) {
      if (!mounted) return;
      if (state == PlayerState.completed || state == PlayerState.stopped) {
        setState(() => _playing = false);
      }
    });
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  String _resolve() {
    final u = widget.url;
    if (u.startsWith('http://') || u.startsWith('https://')) return u;
    return widget.serverUrl.replaceAll(RegExp(r'/+$'), '') + u;
  }

  Future<void> _toggle() async {
    if (widget.url.isEmpty || _loading) return;
    if (_playing) {
      await _player.stop();
      if (mounted) setState(() => _playing = false);
      return;
    }
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      await _player.play(UrlSource(_resolve()));
      if (mounted) setState(() => _playing = true);
    } catch (_) {
      if (mounted) {
        setState(() {
          _failed = true;
          _loading = false;
        });
      }
    } finally {
      if (mounted && _loading) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final fg = widget.isUser ? Colors.white : Theme.of(context).colorScheme.onSurfaceVariant;
    final fgDim = widget.isUser ? Colors.white70 : Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.7);
    return GestureDetector(
      onTap: _toggle,
      behavior: HitTestBehavior.opaque,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _playing
              ? const Icon(Icons.stop_circle_outlined, size: 18, color: AppColors.error)
              : Icon(_failed ? Icons.error_outline : Icons.play_circle_outline, size: 18, color: _failed ? AppColors.warning : fg),
          const SizedBox(width: 6),
          Text(
            _failed ? l10n.playFailed : widget.label,
            style: TextStyle(fontSize: AppTypography.helperSize, color: _failed ? AppColors.warning : fg),
          ),
          if (widget.duration > 0) ...[
            const SizedBox(width: 4),
            Text('${widget.duration}"', style: TextStyle(fontSize: AppTypography.captionSize, color: fgDim)),
          ],
          if (_loading) ...[
            const SizedBox(width: 6),
            SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 1.6, color: fgDim)),
          ],
        ],
      ),
    );
  }
}

/// SSE 真流式打字机光标：正文末尾一个闪烁竖条。
class _StreamingCursor extends StatefulWidget {
  const _StreamingCursor();

  @override
  State<_StreamingCursor> createState() => _StreamingCursorState();
}

class _StreamingCursorState extends State<_StreamingCursor>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: _controller,
      child: const Text(
        '▍',
        style: TextStyle(
          fontSize: 15,
          color: AppColors.textSecondary,
        ),
      ),
    );
  }
}


