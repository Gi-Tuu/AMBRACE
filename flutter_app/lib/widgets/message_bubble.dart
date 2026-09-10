import "package:flutter/material.dart";
import "package:provider/provider.dart";
import "package:url_launcher/url_launcher.dart";
import "package:audioplayers/audioplayers.dart";
import "../utils/stage_text.dart";
import "../utils/beijing_time.dart";
import "../theme/tokens.dart";
import "../theme/skins/skin_colors.dart";
import "../providers/settings_provider.dart";
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
  /// MCP 工具结果列表（A1，#59 流式路径 MCP 工具循环；观察区可折叠展示）
  final List<Map<String, dynamic>>? toolResults;
  /// 状态更新小字（2026-08-14：显示在气泡内容文本下方）
  final String? statusUpdate;
  final bool showReasoning;
  final bool showTools;
  /// AI 生图图片消息（类型角标，始终显示；由调用处据 ChatMessage.isAiGeneratedImage 传入）
  final bool isAiGeneratedImage;
  /// SSE 真流式进行中：正文末尾显示闪烁光标（打字机效果）
  final bool isStreaming;
  /// AI 消息是否显示头像（B3：连续 AI 消息仅组首条显示，调用方传入）
  final bool showAiAvatar;

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
    this.toolResults,
    this.statusUpdate,
    this.showReasoning = false,
    this.showTools = false,
    this.isAiGeneratedImage = false,
    this.isStreaming = false,
    this.showAiAvatar = true,
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
    // §6.6：仅 aegean 皮肤给 AI 气泡加 0.8 金发丝边；用户气泡与你消息流不加。
    // 未包裹 Provider 的测试环境按 false 兜底（非 aegean 路径零变化）。
    final isAegean = Provider.of<SettingsProvider?>(context, listen: false)?.skinId == 'aegean';
    final stage = StageText.parse(message);
    // 皮肤色：有 SkinColors 扩展时用皮肤气泡色，否则回退到 Material3 默认。
    // B3：用户气泡在皮肤色之上叠加主题色渐变（primary → primary@0.85）+ 白色文字；
    // AI 气泡保持皮肤语义不变（paper 仍白、glass 仍半透明）。
    final skinColors = Theme.of(context).extension<SkinColors>();
    final bubbleColor = isUser
        ? (skinColors?.bubbleUser ?? Theme.of(context).colorScheme.primaryContainer)
        : (skinColors?.bubbleAi ?? Theme.of(context).colorScheme.surfaceContainerHighest);
    final bubbleTextColor = isUser
        ? Colors.white
        : (skinColors?.bubbleAiText ?? Theme.of(context).colorScheme.onSurfaceVariant);
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
        color: bubbleColor,
        gradient: isUser
            ? LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  Theme.of(context).colorScheme.primary,
                  Theme.of(context).colorScheme.primary.withValues(alpha: 0.85),
                ],
              )
            : null,
        boxShadow: AppShadow.light,
        border: (isAegean && !isUser)
            ? Border.all(
                color: Theme.of(context).colorScheme.outlineVariant,
                width: 0.8,
              )
            : null,
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(18),
          topRight: const Radius.circular(18),
          bottomLeft: isUser ? const Radius.circular(18) : Radius.zero,
          bottomRight: isUser ? Radius.zero : const Radius.circular(18),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 思考过程/调用能力（AI 消息顶部，仅产生且开关打开时显示，默认折叠）
          if (!isUser && showReasoning && (reasoning ?? '').isNotEmpty)
            _CollapsibleMeta(
              icon: Icons.auto_awesome,
              label: l10n.innerThoughts,
              detail: reasoning!,
              monologue: true,
            ),
          // R6（2026-09-09）：能力列表 chip 化（后端已归一为中文短列表、去重、上限 6 项），
          // 不再 join('、') 成一坨；超过 3 个折叠为「等 N 项」，点击展开换行排列。
          if (!isUser && showTools && (tools ?? const []).isNotEmpty)
            _AbilityChipsMeta(labels: tools!),
          // MCP 工具结果（A1，#59 流式路径 MCP 工具循环；观察区可折叠，成功/失败各一块）
          if (!isUser && (toolResults ?? const []).isNotEmpty)
            for (final tr in toolResults!)
              _ToolResultMeta(result: tr, label: l10n.toolResult),
          if (quoteMeta != null) _buildQuoteBlock(context, bubbleTextColor, skinColors),
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
            // ── 消息类型角标：始终显示（不受 showTools 控制），等同语音时长/文件名的类型说明 ──
            if (isAiGeneratedImage)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    l10n.aiGeneratedImage,
                    style: TextStyle(
                      fontSize: AppTypography.captionSize,
                      height: AppTypography.captionHeight,
                      fontStyle: FontStyle.italic,
                      color: bubbleTextColor.withValues(alpha: 0.5),
                    ),
                  ),
                ),
              ),
            // ── IMG_TEXT 配文：小字斜体；为空不渲染（后端已允许空配文）──
            if (stage.text.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    stage.text,
                    style: TextStyle(
                      fontSize: AppTypography.captionSize,
                      height: AppTypography.captionHeight,
                      fontStyle: FontStyle.italic,
                      color: bubbleTextColor.withValues(alpha: 0.78),
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
          // 纯图片消息的配文已在图片块内以小字渲染，这里跳过；纯文本/其它消息维持 15 号正文
          if (stage.text.isNotEmpty && !(imageUrl != null && imageUrl!.isNotEmpty))
            Text(
              stage.text,
              style: TextStyle(fontSize: 15, color: bubbleTextColor),
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
                    color: bubbleTextColor.withValues(alpha: 0.55),
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
                style: TextStyle(fontSize: AppTypography.captionSize, color: bubbleTextColor.withValues(alpha: 0.6)),
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
                // #3 连续 AI 消息隐藏头像时补占位（头像32+间距8=40），与组首气泡对齐、不贴屏幕边缘
                if (!isUser && !showAiAvatar) const SizedBox(width: 40),
                if (!isUser && showAiAvatar) ...[chatAvatar, const SizedBox(width: 8)],
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
  Widget _buildQuoteBlock(BuildContext context, Color bubbleTextColor, SkinColors? skinColors) {
    final l10n = AppLocalizations.of(context)!;
    final q = quoteMeta ?? const <String, dynamic>{};
    final content = q['content'] as String? ?? '';
    final label = q['sender'] == 'user' ? l10n.me : l10n.ta;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: bubbleTextColor.withValues(alpha: 0.07),
        borderRadius: BorderRadius.circular(6),
        border: Border(
          left: BorderSide(
            width: 3,
            color: isUser
                ? Theme.of(context).colorScheme.primary
                : bubbleTextColor.withValues(alpha: 0.3),
          ),
        ),
      ),
      child: Text(
        quoteDeleted ? l10n.quoteDeleted : l10n.msgQuoteLine(label, content),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          fontSize: AppTypography.captionSize,
          color: bubbleTextColor.withValues(alpha: 0.75),
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
  /// 内心活动变体（思考第一人称化，2026-09-10）：斜体 + 左侧竖线 + 更淡底色，
  /// 与工具结果/能力 chip 等系统日志观感区分开
  final bool monologue;

  const _CollapsibleMeta({
    required this.icon,
    required this.label,
    required this.detail,
    this.monologue = false,
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
                        style: TextStyle(
                          fontSize: AppTypography.captionSize,
                          color: fg.withValues(alpha: 0.7),
                          fontStyle: widget.monologue ? FontStyle.italic : null,
                        ),
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
              padding: widget.monologue
                  ? const EdgeInsets.symmetric(vertical: 6)
                  : const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(
                // 内心活动变体底色更淡（0.04），普通折叠块维持 0.05
                color: scheme.onSurface.withValues(alpha: widget.monologue ? 0.04 : 0.05),
                borderRadius: BorderRadius.circular(6),
              ),
              // 左侧竖线（摘句感）只给内心活动变体；非均匀 border 不能与 borderRadius 同层，
              // 故用内层 Container 承载
              child: widget.monologue
                  ? Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      decoration: BoxDecoration(
                        border: Border(
                          left: BorderSide(
                            color: scheme.primary.withValues(alpha: 0.5),
                            width: 2,
                          ),
                        ),
                      ),
                      child: _detailText(fg),
                    )
                  : _detailText(fg),
            ),
        ],
      ),
    );
  }

  Widget _detailText(Color fg) => Text(
        widget.detail,
        style: TextStyle(
          fontSize: AppTypography.captionSize,
          color: fg.withValues(alpha: 0.85),
          height: 1.4,
          fontStyle: widget.monologue ? FontStyle.italic : null,
        ),
      );
}

/// 调用能力 chip 块（R6，2026-09-09）：每项一个 chip，默认显示前 3 个，
/// 其余折叠为「等 N 项」，点击展开全部（Wrap 自动换行）。思考过程仍用纯文本 _CollapsibleMeta。
class _AbilityChipsMeta extends StatefulWidget {
  final List<String> labels;

  const _AbilityChipsMeta({required this.labels});

  @override
  State<_AbilityChipsMeta> createState() => _AbilityChipsMetaState();
}

class _AbilityChipsMetaState extends State<_AbilityChipsMeta> {
  bool _expanded = false;
  static const int _collapsedShown = 3;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final fg = scheme.onSurfaceVariant;
    final l10n = AppLocalizations.of(context)!;
    final all = widget.labels;
    final shown = _expanded ? all : all.take(_collapsedShown).toList();
    final rest = all.length - shown.length;

    Widget chip(String t) => Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(
            color: fg.withValues(alpha: 0.08),
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(t,
              style: TextStyle(fontSize: AppTypography.captionSize, color: fg)),
        );

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
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.handyman_outlined, size: 13, color: fg),
                const SizedBox(width: 4),
                Text(l10n.calledAbility,
                    style: TextStyle(
                        fontSize: AppTypography.captionSize,
                        color: fg,
                        fontWeight: FontWeight.w500)),
                if (all.length > _collapsedShown)
                  Icon(
                      _expanded
                          ? Icons.keyboard_arrow_up
                          : Icons.keyboard_arrow_down,
                      size: 14,
                      color: fg.withValues(alpha: 0.6)),
              ]),
            ),
          ),
          const SizedBox(height: 4),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final t in shown) chip(t),
            if (!_expanded && rest > 0) chip(l10n.abilityMore(rest)),
          ]),
        ],
      ),
    );
  }
}

/// MCP 工具结果折叠块（A1，#59）：单个工具结果，成功绿/失败红图标，默认折叠展开看摘要。
class _ToolResultMeta extends StatelessWidget {
  final Map<String, dynamic> result;
  final String label;

  const _ToolResultMeta({required this.result, required this.label});

  String _toolName() {
    final t = result['tool'];
    return t is String && t.isNotEmpty ? t : label;
  }

  bool _ok() => result['ok'] == true;

  String _detail() {
    final summary = result['summary'];
    final sum = summary is String ? summary : '';
    final err = result['error'];
    final errTxt = err is String && err.isNotEmpty ? err : '';
    final tool = _toolName();
    final parts = <String>[tool];
    if (sum.isNotEmpty) parts.add(sum);
    if (errTxt.isNotEmpty) parts.add(errTxt);
    return parts.join('\n');
  }

  @override
  Widget build(BuildContext context) {
    final ok = _ok();
    final name = result['tool'];
    final short = name is String ? name : label;
    return _CollapsibleMeta(
      icon: ok ? Icons.check_circle_outline : Icons.error_outline,
      label: '$label · $short',
      detail: _detail(),
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


