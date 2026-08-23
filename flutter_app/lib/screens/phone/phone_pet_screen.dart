import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import '../../models/pet.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';

/// 小手机「宠物」应用（2026-08-14）：展示机主（AI 角色）的宠物，只读；
/// 照顾由 AI 亲自操作，用户想帮忙请通过主页宠物页拜访（暂定）。
class PhonePetScreen extends StatefulWidget {
  final int characterId;
  final String characterName;
  const PhonePetScreen({
    super.key,
    required this.characterId,
    required this.characterName,
  });

  @override
  State<PhonePetScreen> createState() => _PhonePetScreenState();
}

class _PhonePetScreenState extends State<PhonePetScreen> {
  Pet? _pet;
  List<Map<String, dynamic>> _activities = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final chars = await ApiClient().getAiPets();
      Map<String, dynamic>? found;
      for (final c in chars) {
        if ((c['character_id'] as num?)?.toInt() == widget.characterId) {
          found = c;
          break;
        }
      }
      final petJson = found?['pet'] as Map<String, dynamic>?;
      final pet = petJson == null ? null : Pet.fromJson(petJson);
      var acts = <Map<String, dynamic>>[];
      if (pet != null) {
        // 只显示角色（机主）自己照顾的记录
        acts = await ApiClient().getPetActivities(pet.id, limit: 10, actor: 'ai');
      }
      if (!mounted) return;
      setState(() {
        _pet = pet;
        _activities = acts;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.tabPets)),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? _ErrorView(message: _error!, onRetry: _load)
                : _pet == null
                    ? _NoPetView(name: widget.characterName)
                    : _buildPetView(),
      ),
    );
  }

  Widget _buildPetView() {
    final l10n = AppLocalizations.of(context)!;
    final pet = _pet!;
    final theme = Theme.of(context);
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        // 宠物信息卡
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: theme.colorScheme.primary.withValues(alpha: 0.3)),
          ),
          child: Column(
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(20),
                child: Image.network(
                  ApiClient().resolveUrl(pet.avatarUrl ?? '/uploads/pets_assets/cat/idle.png'),
                  width: 120,
                  height: 120,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) =>
                      Icon(Icons.pets, size: 80, color: Colors.grey.shade300),
                ),
              ),
              const SizedBox(height: 12),
              Text(pet.name,
                  style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              Text('${pet.speciesLabel} · Lv.${pet.level}',
                  style: TextStyle(fontSize: 13, color: Colors.grey.shade600)),
              if (pet.statusText.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(pet.statusText,
                    style: TextStyle(fontSize: 13, color: Colors.grey.shade500)),
              ],
            ],
          ),
        ),
        const SizedBox(height: 12),
        // AI 亲自照顾提示
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: Colors.green.withValues(alpha: 0.08),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Row(
            children: [
              Icon(Icons.volunteer_activism, size: 18, color: Colors.green.shade600),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  l10n.phonePetCareHint(widget.characterName),
                  style: TextStyle(fontSize: 12, color: Colors.green.shade800),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        // 状态
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            children: [
              _StatBar(icon: Icons.restaurant, label: l10n.petHunger, value: pet.hunger),
              _StatBar(icon: Icons.sentiment_satisfied_alt, label: l10n.mood, value: pet.mood),
              _StatBar(icon: Icons.bolt, label: l10n.energy, value: pet.energy),
              _StatBar(icon: Icons.cleaning_services, label: l10n.petClean, value: pet.cleanliness),
            ],
          ),
        ),
        const SizedBox(height: 12),
        // 互动记录
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.history, size: 16, color: Colors.grey.shade500),
                  const SizedBox(width: 6),
                  Text(l10n.taCareLog, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                ],
              ),
              const SizedBox(height: 8),
              if (_activities.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Center(
                    child: Text(
                      l10n.taCareEmpty,
                      style: TextStyle(fontSize: 13, color: Colors.grey.shade500),
                    ),
                  ),
                )
              else
                for (final act in _activities.take(8)) _ActivityRow(act: act),
            ],
          ),
        ),
      ],
    );
  }
}

class _NoPetView extends StatelessWidget {
  final String name;
  const _NoPetView({required this.name});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        const SizedBox(height: 80),
        const Center(child: Icon(Icons.pets, size: 72, color: Colors.grey)),
        const SizedBox(height: 16),
        Center(
          child: Text(l10n.taNoPet(name),
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
        ),
        const SizedBox(height: 8),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Text(
              l10n.taNoPetHint,
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: Colors.grey.shade500),
            ),
          ),
        ),
      ],
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorView({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(l10n.loadFailed, style: TextStyle(color: Colors.grey.shade600)),
          const SizedBox(height: 4),
          Text(message, style: const TextStyle(fontSize: 11, color: Colors.grey)),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onRetry, child: Text(l10n.retry)),
        ],
      ),
    );
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
          SizedBox(width: 30, child: Text('$value', textAlign: TextAlign.right, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}

class _ActivityRow extends StatelessWidget {
  final Map<String, dynamic> act;
  const _ActivityRow({required this.act});

  @override
  Widget build(BuildContext context) {
    final content = act['content'] as String? ?? '';
    final createdAt = act['created_at'] as String? ?? '';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('· ', style: TextStyle(fontSize: 13, color: Colors.grey)),
          Expanded(child: Text(content, style: const TextStyle(fontSize: 13))),
          const SizedBox(width: 8),
          Text(
            _shortTime(createdAt),
            style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
          ),
        ],
      ),
    );
  }

  String _shortTime(String iso) {
    if (iso.length < 19) return '';
    try {
      final bj = formatBeijingTime(iso);
      return bj.length >= 16 ? bj.substring(5, 16) : bj;
    } catch (_) {
      return '';
    }
  }
}
