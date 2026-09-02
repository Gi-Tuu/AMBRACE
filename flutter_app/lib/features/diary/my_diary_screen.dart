import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/user_content.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import 'my_diary_edit_screen.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';

/// 我的日记：用户写的日记，AI 好友聊天时可阅读。
class MyDiaryScreen extends StatefulWidget {
  const MyDiaryScreen({super.key});
  @override
  State<MyDiaryScreen> createState() => _MyDiaryScreenState();
}

class _MyDiaryScreenState extends State<MyDiaryScreen> {
  List<UserDiaryEntry> _diaries = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // 注意：不可在此访问 l10n（inherited）——initState 触发时尚未完成首帧
    setState(() { _loading = true; _error = null; });
    try {
      final list = await ApiClient().getDiaries();
      if (!mounted) return;
      setState(() { _diaries = list; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      // Aurora P7：initState 触发的加载可能在首帧前完成，l10n 访问延迟到帧后
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        setState(() {
          _error = AppLocalizations.of(context)!.loadFailedErr(e.toString());
          _loading = false;
        });
      });
    }
  }

  Future<void> _openEditor({UserDiaryEntry? entry}) async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => MyDiaryEditScreen(entry: entry)),
    );
    await _load();
  }

  Future<void> _deleteDiary(UserDiaryEntry entry) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteDiaryTitle),
        content: Text(l10n.deleteDiaryConfirm(entry.diaryDate)),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await ApiClient().deleteDiary(entry.id);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailedErr(e.toString()))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P7 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
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
        title: Text(l10n.myDiary),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _openEditor(),
        tooltip: l10n.writeTodayDiary,
        child: const Icon(Icons.edit_note),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _diaries.isEmpty
                  // Aurora P7：空态 EmptyState 统一渲染
                  ? EmptyState(
                      icon: Icons.menu_book_outlined,
                      title: l10n.noDiaryHint,
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView.builder(
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        itemCount: _diaries.length,
                        itemBuilder: (context, i) {
                          final d = _diaries[i];
                          final scheme = Theme.of(context).colorScheme;
                          // Aurora P7：列表条目 → AuroraCard（长按删除包外层 GestureDetector）
                          return Padding(
                            padding: const EdgeInsets.only(left: 12, right: 12, bottom: 10),
                            child: GestureDetector(
                              onLongPress: () => _deleteDiary(d),
                              child: AuroraCard(
                                onTap: () => _openEditor(entry: d),
                                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                                child: Row(
                                  children: [
                                    Icon(Icons.menu_book_outlined, color: scheme.primary, size: 22),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(d.diaryDate,
                                              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                                          const SizedBox(height: 3),
                                          Text(d.content,
                                              maxLines: 3,
                                              overflow: TextOverflow.ellipsis,
                                              style: TextStyle(fontSize: 13, color: scheme.onSurfaceVariant)),
                                        ],
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    const Icon(Icons.chevron_right, size: 20, color: IosCardColors.chevron),
                                  ],
                                ),
                              ),
                            ),
                          );
                        },
                      ),
                    ),
    );
  }
}
