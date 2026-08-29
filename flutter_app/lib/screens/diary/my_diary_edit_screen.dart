import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/user_content.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/ios_card_group.dart';

/// 日记编辑页：日期（默认今天）+ 内容，保存按 upsert 覆盖当天。
class MyDiaryEditScreen extends StatefulWidget {
  final UserDiaryEntry? entry;
  const MyDiaryEditScreen({super.key, this.entry});
  @override
  State<MyDiaryEditScreen> createState() => _MyDiaryEditScreenState();
}

class _MyDiaryEditScreenState extends State<MyDiaryEditScreen> {
  late final TextEditingController _dateCtrl;
  late final TextEditingController _contentCtrl;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    final today = "${now.year.toString().padLeft(4, '0')}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}";
    _dateCtrl = TextEditingController(text: widget.entry?.diaryDate ?? today);
    _contentCtrl = TextEditingController(text: widget.entry?.content ?? "");
  }

  @override
  void dispose() {
    _dateCtrl.dispose();
    _contentCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    final date = _dateCtrl.text.trim();
    final content = _contentCtrl.text.trim();
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(date)) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.dateFormatHint)));
      return;
    }
    if (content.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.contentRequired)));
      return;
    }
    setState(() { _saving = true; });
    try {
      await ApiClient().upsertDiary(date, content);
      if (mounted) {
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() { _saving = false; });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFailedErr(e.toString()))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
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
        title: Text(l10n.editDiary),
        actions: [
          TextButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l10n.save),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          _auroraGroup(
            title: l10n.date,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: TextField(
                  controller: _dateCtrl,
                  keyboardType: TextInputType.datetime,
                  decoration: InputDecoration(
                    labelText: l10n.date,
                    prefixIcon: const Icon(Icons.calendar_today),
                    hintText: "YYYY-MM-DD",
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: BorderSide.none,
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: BorderSide(
                          color: scheme.outlineVariant.withValues(alpha: 0.4)),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: BorderSide(color: scheme.primary, width: 1.5),
                    ),
                    filled: true,
                    fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  ),
                ),
              ),
            ],
          ),
          _auroraGroup(
            title: l10n.contentAiHint,
            children: [
              SizedBox(
                height: 320,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: TextField(
                    controller: _contentCtrl,
                    maxLines: null,
                    expands: true,
                    textAlignVertical: TextAlignVertical.top,
                    decoration: InputDecoration(
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide.none,
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide(
                            color: scheme.outlineVariant.withValues(alpha: 0.4)),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide:
                            BorderSide(color: scheme.primary, width: 1.5),
                      ),
                      filled: true,
                      fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.5),
                      contentPadding: const EdgeInsets.all(12),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}


/// Aurora P7 分组：AuroraCard 版 IosCardGroup（标题视觉保留）
Widget _auroraGroup({required String title, required List<Widget> children}) {
  return Padding(
    padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(left: 16, bottom: 6),
          child: Text(title,
              style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: IosCardColors.subtitle)),
        ),
        AuroraCard(
          padding: EdgeInsets.zero,
          child: Material(
            type: MaterialType.transparency,
            child: Column(children: children),
          ),
        ),
      ],
    ),
  );
}
