import 'dart:async';
import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "package:provider/provider.dart";
import "../../theme/aurora_tokens.dart";
import "../../theme/tokens.dart";
import "../../providers/pets_provider.dart";
import "../../services/api_client.dart";
import "../home/home_screen.dart";
import '../../features/character/pet_adopt_views.dart';
import '../../features/character/pet_home_view.dart';
import '../../features/character/pet_ai_visit.dart';


class PetScreen extends StatefulWidget {
  const PetScreen({super.key});
  @override
  State<PetScreen> createState() => _PetScreenState();
}

class _PetScreenState extends State<PetScreen> with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;
  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      if (mounted) context.read<PetsProvider>().loadPets();
    });
  }

  /// 从物种卡领养：先选名字（最多 5 个字），再执行领养
  Future<void> _adopt(PetsProvider p, String species, String label) async {
    final l10n = AppLocalizations.of(context)!;
    final name = await _promptName(context, l10n.adoptSpecies(label), l10n.petNameHint);
    if (name == null) return;
    if (!mounted) return;
    final success = await p.adopt(species, name);
    if (mounted && !success) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(p.error ?? l10n.adoptFailed)));
    }
  }

  /// 已养宠物界面的"领养"入口：弹物种+名字一步对话框（不再 push 新页面，修复叠加）
  Future<void> _showAdoptDialog(PetsProvider p) async {
    final l10n = AppLocalizations.of(context)!;
    if (p.pets.length >= 3) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.petLimit3)));
      }
      return;
    }
    var species = "cat";
    final nameCtrl = TextEditingController();
    final ok = await showDialog<dynamic>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.adoptNewPet),
        content: StatefulBuilder(
          builder: (ctx2, setDlg) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(l10n.chooseSpecies, style: const TextStyle(fontSize: 13, color: AppColors.textSecondary)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  for (final (sp, lb) in adoptableSpecies(l10n))
                    ChoiceChip(
                      label: Text(lb),
                      selected: species == sp,
                      onSelected: (_) => setDlg(() => species = sp),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              TextField(
                controller: nameCtrl,
                autofocus: true,
                maxLength: 5,
                decoration: InputDecoration(hintText: l10n.petNameHint, counterText: ""),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () {
              final name = nameCtrl.text.trim();
              if (name.isEmpty) {
                ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.nameRequired)));
                return;
              }
              if (name.characters.length > 5) {
                ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(l10n.nameTooLong)));
                return;
              }
              Navigator.pop(ctx, name);
            },
            child: Text(l10n.adopt),
          ),
        ],
      ),
    );
    nameCtrl.dispose();
    if (ok is String && mounted) {
      final success = await p.adopt(species, ok);
      if (mounted && !success) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(p.error ?? l10n.adoptFailed)));
      }
    }
  }

  /// 通用起名/改名对话框；返回输入文本或 null（取消）
  Future<String?> _promptName(BuildContext context, String title, String hint) async {
    final l10n = AppLocalizations.of(context)!;
    final nameCtrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: nameCtrl,
          autofocus: true,
          maxLength: 5,
          decoration: InputDecoration(hintText: hint, counterText: ""),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.save)),
        ],
      ),
    );
    if (ok != true) {
      nameCtrl.dispose();
      return null;
    }
    final name = nameCtrl.text.trim();
    nameCtrl.dispose();
    if (name.isEmpty) return null;
    if (name.characters.length > 5) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.nameTooLong)));
      }
      return null;
    }
    return name;
  }

  Future<void> _rename(PetsProvider p) async {
    final l10n = AppLocalizations.of(context)!;
    final pet = p.selectedPet;
    if (pet == null) return;
    final name = await _promptName(context, l10n.rename, l10n.renameHint);
    if (name == null || !mounted) return;
    final success = await p.rename(name);
    if (mounted && !success) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(p.error ?? l10n.renameFailed)));
    }
  }

  /// 拜访：查看角色们的宠物（互动 / 代为领养）
  Future<void> _visit(PetsProvider p) async {
    final l10n = AppLocalizations.of(context)!;
    if (!mounted) return;
    List<Map<String, dynamic>> chars;
    try {
      chars = await ApiClient().getAiPets();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.loadPetFailed)));
      }
      return;
    }
    if (!mounted) return;
    if (chars.isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.noCharacters)));
      return;
    }
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => AiPetPanel(characters: chars, onChanged: () => p.loadPets()),
    );
    if (mounted) p.loadPets();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final l10n = AppLocalizations.of(context)!;
    final p = context.watch<PetsProvider>();
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P4 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
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
        leading: IconButton(
          icon: const Icon(Icons.menu),
          onPressed: () => AppDrawerController.toggle(),
          tooltip: l10n.menu,
        ),
        title: Text(l10n.tabPets),
      ),
      body: RefreshIndicator(
        onRefresh: () => p.loadPets(),
        child: p.loading && p.pets.isEmpty
            ? const Center(child: CircularProgressIndicator())
            : p.pets.isEmpty
                ? AdoptView(p: p, onAdopt: _adopt)
                : PetHome(p: p, onRename: _rename, onVisit: () => _visit(p), onAdopt: () => _showAdoptDialog(p)),
      ),
    );
  }
}
