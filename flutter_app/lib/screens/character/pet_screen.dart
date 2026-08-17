import 'dart:async';
import 'dart:math' as math;
import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "package:provider/provider.dart";
import "../../models/pet.dart";
import "../../providers/pets_provider.dart";
import "../../services/api_client.dart";
import "../../utils/beijing_time.dart";
import "../home/home_screen.dart";

List<(String, String)> _adoptableSpecies(AppLocalizations l10n) => [
  ("cat", l10n.speciesCat),
  ("dog", l10n.speciesDog),
  ("parrot", l10n.speciesParrot),
  ("rabbit", l10n.speciesRabbit),
  ("hamster", l10n.speciesHamster),
  ("snake", l10n.speciesSnake),
  ("gecko", l10n.speciesGecko),
];

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
              Text(l10n.chooseSpecies, style: const TextStyle(fontSize: 13, color: Colors.grey)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  for (final (sp, lb) in _adoptableSpecies(l10n))
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
    if (ok != true) return null;
    final name = nameCtrl.text.trim();
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
      builder: (_) => _AiPetPanel(characters: chars, onChanged: () => p.loadPets()),
    );
    if (mounted) p.loadPets();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final l10n = AppLocalizations.of(context)!;
    final p = context.watch<PetsProvider>();
    return Scaffold(
      appBar: AppBar(
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
                ? _AdoptView(p: p, onAdopt: _adopt)
                : _PetHome(p: p, onRename: _rename, onVisit: () => _visit(p), onAdopt: () => _showAdoptDialog(p)),
      ),
    );
  }
}

class _AdoptView extends StatelessWidget {
  final PetsProvider p;
  final void Function(PetsProvider p, String species, String label) onAdopt;
  const _AdoptView({required this.p, required this.onAdopt});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        Text(l10n.adoptHeading, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 4),
        Text(l10n.adoptSubtitle,
            style: TextStyle(fontSize: 13, color: Colors.grey.shade600)),
        const SizedBox(height: 16),
        GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.82,
          children: [
            for (final (species, label) in _adoptableSpecies(l10n))
              _SpeciesCard(
                species: species,
                label: label,
                locked: false,
                onTap: () => onAdopt(p, species, label),
              ),
          ],
        ),
      ],
    );
  }
}

class _SpeciesCard extends StatelessWidget {
  final String species;
  final String label;
  final bool locked;
  final VoidCallback onTap;
  const _SpeciesCard({
    required this.species,
    required this.label,
    required this.locked,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final url = ApiClient().resolveUrl("/uploads/pets_assets/$species/idle.png");
    return InkWell(
      onTap: locked ? null : onTap,
      borderRadius: BorderRadius.circular(12),
      child: Opacity(
        opacity: locked ? 0.45 : 1,
        child: Column(
          children: [
            Expanded(
              child: Container(
                width: double.infinity,
                decoration: BoxDecoration(
                  color: Colors.grey.shade100,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      Image.network(
                        url,
                        fit: BoxFit.cover,
                        gaplessPlayback: true,
                        errorBuilder: (c, e, s) => Icon(Icons.pets, size: 44, color: Colors.grey.shade400),
                      ),
                      if (locked)
                        const Center(child: Icon(Icons.lock, color: Colors.white70, size: 28)),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                if (locked) ...[
                  const SizedBox(width: 3),
                  Text(l10n.comingSoon, style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PetHome extends StatefulWidget {
  final PetsProvider p;
  final void Function(PetsProvider p) onRename;
  final VoidCallback onVisit;
  final VoidCallback onAdopt;
  const _PetHome({
    required this.p,
    required this.onRename,
    required this.onVisit,
    required this.onAdopt,
  });

  @override
  State<_PetHome> createState() => _PetHomeState();
}

class _PetHomeState extends State<_PetHome> {
  String? _burstAction;
  int _burstSeq = 0;

  /// 满状态提示：四维已满时互动给出对应提示（不调接口）
  String? _fullHint(String action) {
    final l10n = AppLocalizations.of(context)!;
    final pet = widget.p.selectedPet;
    if (pet == null) return null;
    switch (action) {
      case "feed":
        return pet.hunger >= 100 ? l10n.petFullHunger : null;
      case "play":
        return pet.mood >= 100 && pet.energy >= 100 ? l10n.petFullPlay : null;
      case "clean":
        return pet.cleanliness >= 100 ? l10n.petFullClean : null;
    }
    return null;
  }

  Future<void> _interact(String action) async {
    final hint = _fullHint(action);
    if (hint != null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(hint)));
      }
      return;
    }
    setState(() {
      _burstAction = action;
      _burstSeq++;
    });
    final ok = await widget.p.interact(action);
    if (ok) await widget.p.refreshActivities();
  }

  /// 长按顶部宠物名片 -> 遗弃确认（硬删除；不影响角色与用户记忆，AI 会记得遗弃过）
  Future<void> _confirmAbandon(Pet pet) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.abandonTitle(pet.name)),
        content: Text(l10n.abandonConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.thinkAgain)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.red.shade400),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.abandon),
          ),
        ],
      ),
    );
    if (ok == true && mounted) {
      final success = await widget.p.abandon(pet.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(success ? l10n.abandoned(pet.name) : (widget.p.error ?? l10n.abandonFailed))),
        );
      }
    }
  }

  /// 清洁度每缺 10 点一坨大便（不满 10 点也算一坨）；满清洁度无大便
  int _poopCount(Pet pet) {
    if (pet.cleanliness >= 100) return 0;
    return ((100 - pet.cleanliness) ~/ 10) + 1;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final p = widget.p;
    final pet = p.selectedPet;
    if (pet == null) return Center(child: Text(l10n.noPets));
    final pets = p.pets;
    final poops = _poopCount(pet);
    return Column(
      children: [
        SizedBox(
          height: 44,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: [
              for (final item in pets)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: GestureDetector(
                    onLongPress: () => _confirmAbandon(item),
                    child: ChoiceChip(
                      label: Text(item.name),
                      selected: item.id == pet.id,
                      onSelected: (_) => p.selectPet(item.id),
                    ),
                  ),
                ),
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ActionChip(
                  avatar: const Icon(Icons.add, size: 18),
                  label: Text(l10n.adopt),
                  onPressed: widget.onAdopt,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            children: [
              // 名称行：名字 + 铅笔图标（点击改名），拜访按钮同行最右侧
              Row(
                children: [
                  Expanded(
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Flexible(
                          child: Text(
                            pet.name,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                          ),
                        ),
                        const SizedBox(width: 6),
                        GestureDetector(
                          onTap: () => widget.onRename(p),
                          child: Icon(Icons.edit, size: 18, color: Colors.grey.shade500),
                        ),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(
                            "（${pet.speciesLabel}）",
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
                          ),
                        ),
                      ],
                    ),
                  ),
                  OutlinedButton.icon(
                    onPressed: widget.onVisit,
                    icon: const Icon(Icons.door_front_door_outlined, size: 16),
                    label: Text(l10n.visit),
                    style: OutlinedButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                "Lv.${pet.level} · ${pet.statusText}",
                style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
              ),
              const SizedBox(height: 12),
              // 四维状态条（上移，为互动展示留出下方空间）
              _StatBar(icon: Icons.restaurant, label: l10n.hunger, value: pet.hunger),
              _StatBar(icon: Icons.sentiment_satisfied, label: l10n.mood, value: pet.mood),
              _StatBar(icon: Icons.bolt, label: l10n.energy, value: pet.energy),
              _StatBar(icon: Icons.cleaning_services, label: l10n.cleanliness, value: pet.cleanliness),
              const SizedBox(height: 8),
              // 互动区：点宠物玩耍 / 点食物喂食 / 点大便清洁
              Center(
                child: SizedBox(
                  width: 280,
                  height: 250,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      GestureDetector(
                        onTap: () => _interact("play"),
                        child: _AnimatedPet(
                          pet: pet,
                          action: _burstAction,
                          actionSeq: _burstSeq,
                        ),
                      ),
                      // 食物（宠物身边，右下）
                      Positioned(
                        right: 2,
                        bottom: 10,
                        child: GestureDetector(
                          onTap: () => _interact("feed"),
                          child: const _FoodBubble(),
                        ),
                      ),
                      // 大便（宠物身边，左下）
                      if (poops > 0)
                        Positioned(
                          left: 0,
                          bottom: 30,
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              for (var i = 0; i < poops; i++)
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 2),
                                  child: GestureDetector(
                                    onTap: () => _interact("clean"),
                                    child: const Text("💩", style: TextStyle(fontSize: 26)),
                                  ),
                                ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 4),
              Center(
                child: Text(
                  '${l10n.interactHintBase}${poops > 0 ? l10n.interactHintClean : ''}',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
                ),
              ),
              const SizedBox(height: 2),
              Center(
                child: Text(
                  l10n.longPressAbandon,
                  style: TextStyle(fontSize: 11, color: Colors.grey.shade400),
                ),
              ),
              const SizedBox(height: 18),
              // 互动展示区：任何角色/用户对宠物做了什么都会显示（短时重复仅算一件事）
              Row(
                children: [
                  Icon(Icons.history, size: 16, color: Colors.grey.shade500),
                  const SizedBox(width: 6),
                  Text(l10n.activityLog, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ],
              ),
              const SizedBox(height: 8),
              if (p.activities.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Center(
                    child: Text(
                      l10n.noActivities,
                      style: TextStyle(fontSize: 13, color: Colors.grey.shade500),
                    ),
                  ),
                )
              else
                for (final act in p.activities.take(5)) _ActivityRow(act: act),
            ],
          ),
        ),
      ],
    );
  }
}

/// 食物气泡：宠物身边的食物，点击视为喂食
class _FoodBubble extends StatelessWidget {
  const _FoodBubble();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 56,
      height: 56,
      decoration: BoxDecoration(
        color: Colors.orange.shade50,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.orange.shade200),
      ),
      child: const Center(
        child: Text("🍖", style: TextStyle(fontSize: 28)),
      ),
    );
  }
}

/// 互动记录行：内容 + 北京时间
class _ActivityRow extends StatelessWidget {
  final Map<String, dynamic> act;
  const _ActivityRow({required this.act});

  @override
  Widget build(BuildContext context) {
    final content = act['content'] as String? ?? "";
    final createdAt = act['created_at'] as String? ?? "";
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text("· ", style: TextStyle(fontSize: 13, color: Colors.grey)),
          Expanded(
            child: Text(content, style: const TextStyle(fontSize: 13)),
          ),
          const SizedBox(width: 8),
          Text(
            _shortTime(createdAt),
            style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
          ),
        ],
      ),
    );
  }

  /// UTC -> 北京时间 "MM-dd HH:mm"
  String _shortTime(String iso) {
    if (iso.length < 19) return "";
    try {
      final bj = formatBeijingTime(iso);
      return bj.length >= 16 ? bj.substring(5, 16) : bj;
    } catch (_) {
      return "";
    }
  }
}

class _AnimatedPet extends StatefulWidget {
  final Pet pet;
  final String? action;
  final int actionSeq;
  const _AnimatedPet({required this.pet, this.action, required this.actionSeq});

  @override
  State<_AnimatedPet> createState() => _AnimatedPetState();
}

class _AnimatedPetState extends State<_AnimatedPet> with TickerProviderStateMixin {
  late final AnimationController _idle;
  late final AnimationController _burst;
  late int _lastSeq;
  _PetPose _pose = _PetPose.idle;
  Timer? _poseTimer;

  static const int _actionMs = 3000;    // 行为帧停留 3 秒后按精力高低切回睡觉/待机

  @override
  void initState() {
    super.initState();
    _idle = AnimationController(vsync: this, duration: const Duration(seconds: 4))..repeat();
    _burst = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400), value: 1);
    _lastSeq = widget.actionSeq;
  }

  @override
  void didUpdateWidget(covariant _AnimatedPet oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.action != null && widget.actionSeq != _lastSeq) {
      _lastSeq = widget.actionSeq;
      _burst.forward(from: 0);
      // 触发对应行为切换状态帧：喂食->进食 / 玩耍->玩耍 / 清洁->行走 / 其他->待机
      _poseTimer?.cancel();
      final next = switch (widget.action) {
        'feed' => _PetPose.eating,
        'play' => _PetPose.playing,
        'clean' => _PetPose.walking,
        _ => _PetPose.idle,
      };
      setState(() => _pose = next);
      if (next != _PetPose.idle) {
        _poseTimer = Timer(const Duration(milliseconds: _actionMs), () {
          if (mounted) setState(() => _pose = _PetPose.idle);
        });
      }
    }
  }

  @override
  void dispose() {
    _poseTimer?.cancel();
    _idle.dispose();
    _burst.dispose();
    super.dispose();
  }

  /// 宠物形象素材相对路径（按物种派生，已养宠物同样生效）
  String _asset(String name) =>
      ApiClient().resolveUrl('/uploads/pets_assets/${widget.pet.species}/$name');

  /// 当前应显示的状态帧：行为帧优先；无行为且精力低 -> 睡觉
  _PetPose _effectivePose() {
    if (_pose != _PetPose.idle) return _pose;
    if (_PetMotionState.from(widget.pet).sleepy) return _PetPose.sleeping;
    return _PetPose.idle;
  }

  Widget _poseImage(_PetPose pose) {
    switch (pose) {
      case _PetPose.idle:
        return Image.network(_asset('idle.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: Colors.grey.shade400)));
      case _PetPose.eating:
        return Image.network(_asset('eating.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: Colors.grey.shade400)));
      case _PetPose.playing:
        return Image.network(_asset('playing.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: Colors.grey.shade400)));
      case _PetPose.walking:
        return Image.network(_asset('walking.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: Colors.grey.shade400)));
      case _PetPose.sleeping:
        return Image.network(_asset('sleeping.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: Colors.grey.shade400)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final pet = widget.pet;
    final pose = _effectivePose();
    return SizedBox(
      width: 220,
      height: 220,
      child: AnimatedBuilder(
        animation: Listenable.merge([_idle, _burst]),
        builder: (context, _) {
          final t = _idle.value;
          final motion = _PetMotionState.from(pet).compute(t);
          final burstP = _burst.value;
          final bursting = _burst.isAnimating;
          double hop = 0;
          if (bursting && burstP < 0.3) {
            hop = -20 * math.sin(math.pi * (burstP / 0.3));
          }
          return Stack(
            alignment: Alignment.center,
            children: [
              if (pet.needAttention) _AttentionHalo(t: t),
              Transform(
                alignment: Alignment.bottomCenter,
                transform: Matrix4.identity()
                  ..translate(0.0, motion.dy + hop, 0.0)
                  ..rotateZ(motion.rotate)
                  ..scale(motion.scaleX, motion.scaleY),
                child: Container(
                  width: 190,
                  height: 190,
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.grey.shade100,
                    borderRadius: BorderRadius.circular(24),
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(16),
                    child: _poseImage(pose),
                  ),
                ),
              ),
              if (bursting) _ParticleBurst(progress: burstP, action: widget.action ?? ''),
            ],
          );
        },
      ),
    );
  }
}

/// 宠物行为状态帧：待机 / 进食 / 玩耍 / 行走 / 睡觉（静态图，行为帧 3 秒后按精力回落）
enum _PetPose { idle, eating, playing, walking, sleeping }

class _PetMotion {
  final double scaleX;
  final double scaleY;
  final double rotate;
  final double dy;
  const _PetMotion({required this.scaleX, required this.scaleY, required this.rotate, required this.dy});
}

class _PetMotionState {
  final bool lively;
  final bool droopy;
  final bool sad;
  final bool sleepy;

  const _PetMotionState({required this.lively, required this.droopy, required this.sad, required this.sleepy});

  factory _PetMotionState.from(Pet pet) {
    return _PetMotionState(
      lively: pet.mood >= 60 && pet.energy >= 60 && pet.hunger >= 60,
      droopy: pet.hunger < 30 || pet.energy < 30,
      sad: pet.mood < 30,
      sleepy: pet.energy < 30,
    );
  }

  _PetMotion compute(double t) {
    final base = t * 2 * math.pi;
    double bFreq = 1.0, bAmp = 0.03;
    if (sleepy) {
      bFreq = 0.5;
      bAmp = 0.016;
    } else if (droopy) {
      bFreq = 0.7;
      bAmp = 0.02;
    }
    if (lively) {
      bFreq = 1.8;
      bAmp = 0.05;
    }
    final breath = 1 + bAmp * math.sin(base * bFreq);
    final sec = t * 4;
    final bp = (sec % 3.2) / 3.2;
    const closed = 0.05;
    double blink = 1;
    if (bp < closed) {
      blink = 1 - 0.92 * Curves.easeInOut.transform(bp / closed);
    } else if (bp < closed * 2) {
      blink = 0.08 + 0.92 * Curves.easeInOut.transform((bp - closed) / closed);
    }
    double sFreq = 1.0, sAmp = 0.02;
    if (sad) {
      sFreq = 0.5;
      sAmp = 0.011;
    }
    if (droopy && !lively) {
      sAmp = 0.014;
    }
    if (lively) {
      sFreq = 2.2;
      sAmp = 0.032;
    }
    final rotate = sAmp * math.sin(base * sFreq) + (droopy ? 0.04 : 0);
    double dy = 2.5 * math.sin(base);
    if (lively) {
      dy = 5.5 * math.sin(base * 2);
    } else if (sleepy) {
      dy = 1.2 * math.sin(base * 0.5);
    } else if (droopy) {
      dy = 1.5 * math.sin(base * 0.7) + 3;
    }
    return _PetMotion(scaleX: breath, scaleY: breath * blink, rotate: rotate, dy: dy);
  }
}

class _AttentionHalo extends StatelessWidget {
  final double t;
  const _AttentionHalo({required this.t});

  @override
  Widget build(BuildContext context) {
    final pulse = (math.sin(t * 2 * math.pi * 2) + 1) / 2;
    return Container(
      width: 190 + 22 * pulse,
      height: 190 + 22 * pulse,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: Colors.orange.withValues(alpha: 0.08 + 0.07 * pulse),
      ),
    );
  }
}

class _ParticleData {
  final String emoji;
  final double dx;
  final double dy;
  final double size;
  final double delay;
  const _ParticleData({required this.emoji, required this.dx, required this.dy, required this.size, required this.delay});
}

class _ParticleBurst extends StatelessWidget {
  final double progress;
  final String action;
  const _ParticleBurst({required this.progress, required this.action});

  @override
  Widget build(BuildContext context) {
    final List<String> emojis = action == 'feed'
        ? const ['❤️', '🍖', '🍎', '🧀']
        : action == 'play'
            ? const ['⭐', '✨', '🎈', '🌟']
            : action == 'clean'
                ? const ['💧', '🫧', '✨', '💦']
                : const ['✨'];
    final rnd = math.Random(7);
    final particles = List.generate(12, (i) {
      final angle = rnd.nextDouble() * math.pi;
      final dist = 60 + rnd.nextDouble() * 110;
      final dx = math.cos(angle) * dist * (rnd.nextBool() ? 1 : -1);
      final dy = -(40 + rnd.nextDouble() * 130);
      return _ParticleData(
        emoji: emojis[i % emojis.length],
        dx: dx,
        dy: dy,
        size: 16 + rnd.nextDouble() * 16,
        delay: rnd.nextDouble() * 0.22,
      );
    });
    return Stack(
      children: [
        for (final pt in particles)
          Positioned(
            left: 110 - pt.size / 2,
            top: 110 - pt.size / 2,
            child: Transform.translate(
              offset: _offset(pt),
              child: Opacity(
                opacity: _opacity(pt),
                child: Text(pt.emoji, style: TextStyle(fontSize: pt.size)),
              ),
            ),
          ),
      ],
    );
  }

  Offset _offset(_ParticleData pt) {
    final p = (progress - pt.delay) / (1 - pt.delay);
    if (p <= 0) return Offset.zero;
    final q = Curves.easeOutCubic.transform(p.clamp(0.0, 1.0).toDouble());
    return Offset(pt.dx * q, pt.dy * q);
  }

  double _opacity(_ParticleData pt) {
    final p = (progress - pt.delay) / (1 - pt.delay);
    if (p <= 0) return 0;
    final q = p.clamp(0.0, 1.0).toDouble();
    if (q < 0.18) return q / 0.18;
    return (1 - q) / 0.82;
  }
}

class _StatBar extends StatelessWidget {
  final IconData icon;
  final String label;
  final int value;
  const _StatBar({required this.icon, required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final color = value < 30
        ? Colors.red.shade400
        : value < 60
            ? Colors.orange.shade400
            : Colors.green.shade400;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(icon, size: 20, color: Colors.grey.shade600),
          const SizedBox(width: 8),
          SizedBox(width: 52, child: Text(label, style: const TextStyle(fontSize: 13))),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: LinearProgressIndicator(
                value: value.clamp(0, 100) / 100.0,
                minHeight: 10,
                backgroundColor: Colors.grey.shade200,
                color: color,
              ),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(width: 30, child: Text("$value", textAlign: TextAlign.right, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}

/// 拜访面板：列出角色及其 AI 宠物（互动 / 代为领养）
class _AiPetPanel extends StatefulWidget {
  final List<Map<String, dynamic>> characters;
  final void Function() onChanged;
  const _AiPetPanel({required this.characters, required this.onChanged});

  @override
  State<_AiPetPanel> createState() => _AiPetPanelState();
}

class _AiPetPanelState extends State<_AiPetPanel> {
  late List<Map<String, dynamic>> _chars = List.of(widget.characters);
  bool _busy = false;

  Future<void> _refresh() async {
    try {
      final chars = await ApiClient().getAiPets();
      if (mounted) setState(() => _chars = chars);
    } catch (_) {}
  }

  Future<void> _interact(Map<String, dynamic> pet, String action, String label) async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      await ApiClient().petAction((pet['id'] as num).toInt(), action);
      widget.onChanged();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${pet['name']}：${l10n.actionSucceeded(label)}')));
      }
      await _refresh();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.interactFailed)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _adopt(Map<String, dynamic> c) async {
    final l10n = AppLocalizations.of(context)!;
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _AiAdoptDialog(characterName: c['character_name'] as String? ?? 'TA'),
    );
    if (result == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await ApiClient().aiAdopt(
        (c['character_id'] as num).toInt(),
        result['species'] as String,
        result['name'] as String,
      );
      widget.onChanged();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.adoptForChar(c['character_name'] as String? ?? 'TA'))));
      }
      await _refresh();
    } catch (e) {
      final msg = e.toString();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(msg.contains("已经养了宠物") ? msg : l10n.adoptFailedRetry)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    return SafeArea(
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        maxChildSize: 0.92,
        builder: (ctx, scrollCtrl) => ListView(
          controller: scrollCtrl,
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
          children: [
            Text(l10n.aiPetsTitle, style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            Text(l10n.aiPetsSubtitle,
                style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
            const SizedBox(height: 12),
            if (_busy) const LinearProgressIndicator(minHeight: 2),
            const SizedBox(height: 8),
            for (final c in _chars) ...[
              _buildCharCard(c),
              const SizedBox(height: 12),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildCharCard(Map<String, dynamic> c) {
    final l10n = AppLocalizations.of(context)!;
    final pet = c['pet'] as Map<String, dynamic>?;
    final charName = c['character_name'] as String? ?? 'TA';
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: pet == null
            ? Row(
                children: [
                  Expanded(
                    child: Text(l10n.noPetForChar(charName), style: const TextStyle(fontSize: 14)),
                  ),
                  FilledButton.tonal(
                    onPressed: _busy ? null : () => _adopt(c),
                    child: Text(l10n.adoptForTa),
                  ),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.network(
                          ApiClient().resolveUrl(pet['avatar_url'] as String? ?? '/uploads/pets_assets/cat/idle.png'),
                          width: 44,
                          height: 44,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => const Icon(Icons.pets, size: 36, color: Colors.grey),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(l10n.charPetTitle(charName, pet['name'] as String? ?? ''),
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                            Text("${pet['species_label']} · ${pet['status_text']}",
                                style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    children: [
                      _actionChip(Icons.restaurant, l10n.feed, () => _interact(pet, 'feed', l10n.feed)),
                      _actionChip(Icons.sports_esports, l10n.play, () => _interact(pet, 'play', l10n.play)),
                      _actionChip(Icons.cleaning_services, l10n.clean, () => _interact(pet, 'clean', l10n.clean)),
                    ],
                  ),
                ],
              ),
      ),
    );
  }

  Widget _actionChip(IconData icon, String label, VoidCallback onTap) {
    return ActionChip(
      avatar: Icon(icon, size: 16),
      label: Text(label),
      visualDensity: VisualDensity.compact,
      onPressed: _busy ? null : onTap,
    );
  }
}

/// 代为领养对话框：选物种 + 起名（<=5 字）
class _AiAdoptDialog extends StatefulWidget {
  final String characterName;
  const _AiAdoptDialog({required this.characterName});

  @override
  State<_AiAdoptDialog> createState() => _AiAdoptDialogState();
}

class _AiAdoptDialogState extends State<_AiAdoptDialog> {
  final _nameCtrl = TextEditingController();
  String _species = 'cat';

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return AlertDialog(
      title: Text(l10n.adoptPetFor(widget.characterName)),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final (sp, label) in _adoptableSpecies(l10n))
                ChoiceChip(
                  label: Text(label),
                  selected: _species == sp,
                  onSelected: (_) => setState(() => _species = sp),
                ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _nameCtrl,
            maxLength: 5,
            decoration: InputDecoration(
              labelText: l10n.petNameLabel,
              isDense: true,
            ),
          ),
        ],
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
        FilledButton(
          onPressed: () {
            final name = _nameCtrl.text.trim();
            if (name.isEmpty) {
              ScaffoldMessenger.of(context)
                  .showSnackBar(SnackBar(content: Text(l10n.petNameRequired)));
              return;
            }
            Navigator.pop(context, {'species': _species, 'name': name});
          },
          child: Text(l10n.adopt),
        ),
      ],
    );
  }
}
