import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "../../models/memory.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";
import "../../utils/beijing_time.dart";

class MemoryDetailScreen extends StatefulWidget {
  final Memory memory;

  const MemoryDetailScreen({super.key, required this.memory});

  @override
  State<MemoryDetailScreen> createState() => _MemoryDetailScreenState();
}

class _MemoryDetailScreenState extends State<MemoryDetailScreen> {
  final _api = ApiClient();
  late Memory _memory;
  bool _changed = false;
  String? _cachedOriginalSource;

  Map<String, String> _typeLabels(AppLocalizations l10n) => {
        "user_info": l10n.memoryImpression,
        "preference": l10n.memoryPreference,
        "event": l10n.memoryEvent,
        "insight": l10n.memoryInsight,
      };

  @override
  void initState() {
    super.initState();
    _memory = widget.memory;
  }

  String _sourceLabel(String? source, AppLocalizations l10n) {
    switch (source) {
      case "chat": return l10n.sourceChat;
      case "moment": return l10n.sourceMoment;
      case "diary": return l10n.sourceDiary;
      case "bio": return l10n.sourceBio;
      case "status": return l10n.sourceStatus;
      case "extracted": return l10n.sourceExtracted;
      case "relationship": return l10n.sourceRelationship;
      default: return source ?? l10n.unknown;
    }
  }

  Future<void> _deleteMemory() async {
    final l10n = AppLocalizations.of(context)!;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMemoryTitle),
        content: Text(l10n.deleteMemoryConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.deleteMemory(_memory.id);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _updateImportance(int newValue) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await _api.updateMemory(_memory.id, {"importance": newValue});
      setState(() {
        _memory = Memory(
          id: _memory.id,
          memoryType: _memory.memoryType,
          subType: _memory.subType,
          source: _memory.source,
          sourceId: _memory.sourceId,
          title: _memory.title,
          content: _memory.content,
          importance: newValue,
          importancePct: newValue * 20.0,
          createdAt: _memory.createdAt,
          deleteAt: null,
          isPinned: _memory.isPinned,
          isLocked: _memory.isLocked,
        );
        _changed = true;
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.updateFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _toggleLock() async {
    final l10n = AppLocalizations.of(context)!;
    final target = !_memory.isLocked;
    try {
      await _api.updateMemory(_memory.id, {"is_locked": target});
      setState(() {
        _memory = Memory(
          id: _memory.id,
          memoryType: _memory.memoryType,
          subType: _memory.subType,
          source: _memory.source,
          sourceId: _memory.sourceId,
          sourceLabel: _memory.sourceLabel,
          sourceIcon: _memory.sourceIcon,
          title: _memory.title,
          content: _memory.content,
          importance: _memory.importance,
          importancePct: _memory.importancePct,
          createdAt: _memory.createdAt,
          updatedAt: _memory.updatedAt,
          deleteAt: null,
          isPinned: _memory.isPinned,
          isLocked: target,
        );
        _changed = true;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(target ? l10n.lockedFrozen : l10n.unlockedResume),
          duration: const Duration(seconds: 2),
        ));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.opFailedErr(e.toString()))));
      }
    }
  }

  String _countdownText() {
    final l10n = AppLocalizations.of(context)!;
    final deleteAt = _memory.deleteAt;
    if (deleteAt == null || deleteAt.isEmpty) return "";
    try {
      final now = DateTime.now();
      final target = DateTime.parse(deleteAt).toLocal();
      final hours = target.difference(now).inHours;
      if (hours <= 0) return l10n.deleteSoon;
      if (hours >= 24) return l10n.deleteInDays(hours ~/ 24);
      return l10n.deleteInHours(hours);
    } catch (e) {
      return "";
    }
  }

  Future<String> _fetchOriginalSource() async {
    final l10n = AppLocalizations.of(context)!;
    if (_memory.sourceId == null) return "";
    if (_cachedOriginalSource != null) return _cachedOriginalSource!;
    try {
      final msg = await _api.getMessage(_memory.sourceId!);
      _cachedOriginalSource = msg["content"] as String? ?? "";
      return _cachedOriginalSource!;
    } catch (e) {
      return l10n.loadOriginalFailed(e.toString());
    }
  }

  Widget _buildOriginalSourceTile() {
    final l10n = AppLocalizations.of(context)!;
    return IosCardGroup(
      children: [
        ExpansionTile(
        leading: Icon(Icons.description_outlined, color: Theme.of(context).colorScheme.primary),
        title: Text(l10n.detailTitle, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w500, color: Theme.of(context).colorScheme.primary)),
        subtitle: Text(l10n.tapToViewOriginal, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [
          FutureBuilder<String>(
            future: _fetchOriginalSource(),
            builder: (context, snapshot) {
              if (snapshot.connectionState == ConnectionState.waiting) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(
                    child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                  ),
                );
              }
              if (snapshot.hasError || snapshot.data == null || snapshot.data!.isEmpty) {
                return Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(l10n.originalUnavailable, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                );
              }
              return Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Theme.of(context).dividerColor),
                  ),
                  child: Text(
                    snapshot.data!,
                    style: TextStyle(fontSize: 14, height: 1.5, color: Theme.of(context).colorScheme.onSurface),
                  ),
                ),
              );
            },
          ),
        ],
      ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final typeLabel = _typeLabels(l10n)[_memory.memoryType] ?? _memory.memoryType;

    return PopScope(
      canPop: !_changed,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.pop(context, true);
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(_memory.title ?? l10n.memoryDetailTitle),
          actions: [
            IconButton(
              icon: Icon(
                _memory.isLocked ? Icons.lock : Icons.lock_open,
                color: _memory.isLocked ? Colors.blueGrey : Colors.grey,
              ),
              tooltip: _memory.isLocked ? l10n.unlockMemory : l10n.lockMemory,
              onPressed: _toggleLock,
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, color: Colors.red),
              tooltip: l10n.delete,
              onPressed: _deleteMemory,
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            // Type badge
            Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  typeLabel,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: Theme.of(context).colorScheme.onPrimaryContainer,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 24),

            // Title
            if (_memory.title != null && _memory.title!.isNotEmpty) ...[
              Text(
                _memory.title!,
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
            ],

            // Content
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Theme.of(context).dividerColor),
              ),
              child: Text(
                _memory.content,
                style: const TextStyle(fontSize: 15, height: 1.6),
              ),
            ),
            const SizedBox(height: 24),

            // Importance
            IosCardGroup(
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.importanceTitle, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: List.generate(5, (i) {
                          final starVal = i + 1;
                          return IconButton(
                            icon: Icon(
                              starVal <= _memory.importance ? Icons.star : Icons.star_border,
                              color: starVal <= _memory.importance ? Colors.amber : Theme.of(context).colorScheme.outlineVariant,
                              size: 32,
                            ),
                            onPressed: () => _updateImportance(starVal),
                          );
                        }),
                      ),
                      Center(
                        child: Text(
                          ["", l10n.importanceLow, l10n.importanceMedium, l10n.importanceHigh, l10n.importanceVeryHigh, l10n.importanceMax][_memory.importance.clamp(0, 5)],
                          style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
                        ),
                      ),
                      if (_memory.importancePct > 0) ...[
                        const SizedBox(height: 4),
                        Center(
                          child: Text(
                            l10n.memoryStrength(_memory.importancePct.round()),
                            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                          ),
                        ),
                      ],
                    if (_memory.isLocked) ...[
                      const SizedBox(height: 6),
                      Center(
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                          decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: Theme.of(context).dividerColor),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.lock, size: 15, color: IosCardColors.subtitle),
                              const SizedBox(width: 6),
                              Text(
                                l10n.lockedNoDecay,
                                style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                    if (_countdownText().isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: Colors.red.shade50,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: Colors.red.shade200),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.hourglass_bottom, size: 15, color: Colors.red.shade400),
                            const SizedBox(width: 6),
                            Text(
                              _countdownText(),
                              style: TextStyle(fontSize: 12, color: Colors.red.shade400),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (!_memory.isLocked) ...[
                      const SizedBox(height: 4),
                      Center(
                        child: Text(
                          l10n.setStarToKeep,
                          style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
            const SizedBox(height: 12),

            // Original source text (click to expand)
            if (_memory.source == "chat" && _memory.sourceId != null)
              _buildOriginalSourceTile(),
            const SizedBox(height: 12),

            // Source info
            IosCardGroup(
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.sourceInfo, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),
                      _infoRow(Icons.source, l10n.sourceLabel, _sourceLabel(_memory.source, l10n)),
                      if (_memory.subType != null)
                        _infoRow(Icons.category, l10n.subCategory, _memory.subType!),
                      _infoRow(Icons.calendar_today, l10n.recordTime, formatBeijingTime(_memory.createdAt)),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _infoRow(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Icon(icon, size: 16, color: IosCardColors.subtitle),
          const SizedBox(width: 8),
          Text("$label: ", style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
          Text(value, style: const TextStyle(fontSize: 13)),
        ],
      ),
    );
  }


}
