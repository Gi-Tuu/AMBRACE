// F7-c-4a（2026-08-31）自 screens/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../services/api/phone_desktop_api.dart';
import 'app_settings.dart' show fmtTime;

/// 小手机应用页集合：相册 / 应用市场 / 日历 / 浏览器 / 主题 / 设置（2026-08-11）

/// Aurora P3 统一玻璃 AppBar（手机内页模式：半透明底 + 0.5px 描边，无 BackdropFilter）
AppBar _phoneGlassAppBar(
  BuildContext context, {
  Widget? title,
  List<Widget> actions = const [],
  PreferredSizeWidget? bottom,
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
    bottom: bottom,
  );
}

// ── 相册：AI 生成图片 + 用户上传（iOS 图库风格：网格 → 全屏预览，保存/删除） ──
class MemoScreen extends StatefulWidget {
  const MemoScreen({super.key, required this.characterId});
  final int characterId;
  @override
  State<MemoScreen> createState() => _MemoScreenState();
}

class _MemoScreenState extends State<MemoScreen> {
  final _textCtrl = TextEditingController();
  List<Map<String, dynamic>> _memos = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _textCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final items = await ApiClient().getPhoneMemos(widget.characterId);
      if (!mounted) return;
      setState(() {
        _memos = items;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  /// 备忘录显示：尾部带记录者署名（-xxx），避免记忆混乱（2026-08-14）
  String _memoTitle(Map<String, dynamic> m) {
    final text = (m['text'] as String? ?? '').trim();
    final author = (m['author'] as String? ?? '').trim();
    return author.isEmpty ? text : '$text  -  $author';
  }

  Future<void> _add() async {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    _textCtrl.clear();
    try {
      await ApiClient().addPhoneMemo(widget.characterId, text);
      _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.saveFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.memoTitle)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            // Aurora P3：输入框主题化（圆角 14 + surfaceContainerHighest 填充）
            child: TextField(
              controller: _textCtrl,
              maxLines: 2,
              maxLength: 300,
              decoration: InputDecoration(
                hintText: l10n.memoHint,
                filled: true,
                fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.6),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(14),
                  borderSide: BorderSide.none,
                ),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.save_outlined),
                  onPressed: _add,
                ),
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _memos.isEmpty
                    // Aurora P3：空态 EmptyState
                    ? EmptyState(
                        icon: Icons.sticky_note_2_outlined,
                        title: l10n.noMemos,
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        itemCount: _memos.length,
                        itemBuilder: (_, i) {
                          final m = _memos[i];
                          // Aurora P3：ListTile → AuroraCard 行
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: AuroraCard(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 12, vertical: 8),
                              child: Row(
                                children: [
                                  const Icon(Icons.sticky_note_2_outlined,
                                      size: 20, color: Colors.orange),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(_memoTitle(m),
                                            maxLines: 2,
                                            overflow: TextOverflow.ellipsis,
                                            style: TextStyle(
                                                fontSize: 14,
                                                color: scheme.onSurface)),
                                        Text(
                                          fmtTime(
                                              m['created_at'] as String? ?? ''),
                                          style: TextStyle(
                                              fontSize: 11,
                                              color: scheme.onSurfaceVariant),
                                        ),
                                      ],
                                    ),
                                  ),
                                  IconButton(
                                    icon: Icon(Icons.delete_outline,
                                        size: 18, color: scheme.error),
                                    onPressed: () async {
                                      await ApiClient()
                                          .deletePhoneMemo(m['id'] as int);
                                      _load();
                                    },
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}

// ── 设置：虚拟手机愿景占位 ──
