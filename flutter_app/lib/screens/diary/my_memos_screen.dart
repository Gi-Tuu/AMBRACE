import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/user_content.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';

/// 我的备忘录：用户写给自己/AI 好友看的备忘录，AI 角色聊天时可阅读。
class MyMemosScreen extends StatefulWidget {
  const MyMemosScreen({super.key});
  @override
  State<MyMemosScreen> createState() => _MyMemosScreenState();
}

class _MyMemosScreenState extends State<MyMemosScreen> {
  List<UserMemo> _memos = [];
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
      final list = await ApiClient().getMemos();
      if (!mounted) return;
      setState(() { _memos = list; _loading = false; });
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

  Future<void> _editMemo({UserMemo? memo}) async {
    final l10n = AppLocalizations.of(context)!;
    final titleCtrl = TextEditingController(text: memo?.title ?? "");
    final contentCtrl = TextEditingController(text: memo?.content ?? "");
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(memo == null ? l10n.newMemo : l10n.editMemo),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: titleCtrl,
              decoration: InputDecoration(
                labelText: l10n.memoTitleHint,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(ctx).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: contentCtrl,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: l10n.content,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(ctx).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                alignLabelWithHint: true,
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.save),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) {
      titleCtrl.dispose();
      contentCtrl.dispose();
      return;
    }
    final content = contentCtrl.text.trim();
    final title = titleCtrl.text.trim();
    titleCtrl.dispose();
    contentCtrl.dispose();
    if (content.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.contentRequired)));
      return;
    }
    try {
      if (memo == null) {
        await ApiClient().createMemo(title: title, content: content);
      } else {
        await ApiClient().updateMemo(memo.id, title: title, content: content);
      }
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e.toString()))));
    }
  }

  Future<void> _deleteMemo(UserMemo memo) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMemoTitle),
        content: Text(l10n.deleteMemoConfirm),
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
      await ApiClient().deleteMemo(memo.id);
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
        title: Text(l10n.myMemos),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _editMemo(),
        child: const Icon(Icons.add),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _memos.isEmpty
                  ? EmptyState(
                      icon: Icons.sticky_note_2_outlined,
                      title: l10n.noMemosHint,
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView.builder(
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        itemCount: _memos.length,
                        itemBuilder: (context, i) {
                          final m = _memos[i];
                          final scheme = Theme.of(context).colorScheme;
                          return Padding(
                            padding: const EdgeInsets.only(left: 12, right: 12, bottom: 10),
                            child: GestureDetector(
                              onLongPress: () => _deleteMemo(m),
                              child: AuroraCard(
                                onTap: () => _editMemo(memo: m),
                                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                                child: Row(
                                  children: [
                                    Icon(Icons.sticky_note_2_outlined, color: scheme.tertiary, size: 22),
                                    const SizedBox(width: 12),
                                    Expanded(
                                      child: Column(
                                        crossAxisAlignment: CrossAxisAlignment.start,
                                        children: [
                                          Text(
                                            (m.title?.isNotEmpty ?? false) ? m.title! : l10n.unnamed,
                                            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
                                          ),
                                          const SizedBox(height: 3),
                                          Text(m.content,
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
