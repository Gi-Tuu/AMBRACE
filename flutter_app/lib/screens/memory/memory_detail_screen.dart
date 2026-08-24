import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/memory.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";
import "../../utils/beijing_time.dart";
import "../../utils/memory_decay.dart";
import "../../widgets/memory_decay_chart.dart";

class MemoryDetailScreen extends StatefulWidget {
  final Memory memory;

  const MemoryDetailScreen({super.key, required this.memory});

  @override
  State<MemoryDetailScreen> createState() => _MemoryDetailScreenState();
}

class _MemoryDetailScreenState extends State<MemoryDetailScreen> {
  static const int _decayHorizonDays = 30;
  final _api = ApiClient();
  late Memory _memory;
  bool _changed = false;
  List<MemoryNode> _children = const [];
  bool _loadingChildren = true;
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
    _loadChildren();
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
          strengthDays: _memory.strengthDays,
          lastReinforceAt: _memory.lastReinforceAt,
          nextReviewAt: _memory.nextReviewAt,
          reviewCount: _memory.reviewCount,
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
          strengthDays: _memory.strengthDays,
          lastReinforceAt: _memory.lastReinforceAt,
          nextReviewAt: _memory.nextReviewAt,
          reviewCount: _memory.reviewCount,
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


  Future<void> _loadChildren() async {
    try {
      final kids = await _api.getMemoryChildren(_memory.id);
      if (mounted) setState(() { _children = kids; _loadingChildren = false; });
    } catch (_) {
      if (mounted) setState(() => _loadingChildren = false);
    }
  }

  Future<void> _editContent() async {
    final l10n = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: _memory.content);
    final newContent = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.memoryEditContent),
        content: TextField(
          controller: controller,
          maxLines: 6,
          decoration: InputDecoration(hintText: l10n.memoryEditContentHint),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () {
              if (controller.text.trim().isEmpty) return;
              Navigator.pop(ctx, controller.text.trim());
            },
            child: Text(l10n.memorySaveEdit),
          ),
        ],
      ),
    );
    if (newContent == null || newContent.isEmpty) return;
    try {
      await _api.updateMemoryContent(_memory.id, newContent);
      if (mounted) {
        setState(() {
          _memory = Memory(
            id: _memory.id, memoryType: _memory.memoryType, subType: _memory.subType,
            source: _memory.source, sourceId: _memory.sourceId, sourceLabel: _memory.sourceLabel,
            sourceIcon: _memory.sourceIcon, title: _memory.title, content: newContent,
            importance: _memory.importance, importancePct: _memory.importancePct,
            createdAt: _memory.createdAt, updatedAt: _memory.updatedAt, deleteAt: null,
            isPinned: _memory.isPinned, isLocked: _memory.isLocked, whyItMatters: _memory.whyItMatters,
            chainId: _memory.chainId, parentId: _memory.parentId, nodeType: _memory.nodeType,
            version: _memory.version + 1,
            strengthDays: _memory.strengthDays, lastReinforceAt: _memory.lastReinforceAt,
            nextReviewAt: _memory.nextReviewAt, reviewCount: _memory.reviewCount,
          );
          _changed = true;
        });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.memoryUpdatedOk)));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.updateFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _deleteCascade() async {
    final l10n = AppLocalizations.of(context)!;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.memoryDeleteCascadeTitle),
        content: Text(l10n.memoryDeleteCascadeConfirm),
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
      await _api.deleteMemoryCascade(_memory.id);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailedErr(e.toString()))));
      }
    }
  }

  Widget _buildChainCard(AppLocalizations l10n) {
    return IosCardGroup(children: [
      ExpansionTile(
        leading: Icon(Icons.account_tree_outlined, color: Theme.of(context).colorScheme.primary),
        title: Text(l10n.memoryChainTitle, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
        subtitle: Text(l10n.memoryChainChildren, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [
          if (_loadingChildren)
            const Padding(padding: EdgeInsets.all(16), child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))))
          else if (_children.isEmpty)
            Padding(padding: const EdgeInsets.all(16), child: Text(l10n.memoryChainEmpty, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)))
          else
            ..._children.map((c) => _buildChainNode(c)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
              TextButton.icon(icon: const Icon(Icons.edit_outlined, size: 18), label: Text(l10n.memoryEditContent), onPressed: _editContent),
              const SizedBox(width: 8),
              TextButton.icon(
                icon: const Icon(Icons.delete_forever_outlined, size: 18, color: Colors.red),
                label: Text(l10n.memoryDeleteCascadeTitle, style: const TextStyle(color: Colors.red)),
                onPressed: _deleteCascade,
              ),
            ]),
          ),
        ],
      ),
    ]);
  }

  Widget _buildChainNode(MemoryNode node) {
    return Padding(
      padding: const EdgeInsets.only(left: 20, right: 16, top: 4, bottom: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Padding(padding: EdgeInsets.only(top: 4, right: 8), child: Icon(Icons.subdirectory_arrow_right, size: 18, color: IosCardColors.subtitle)),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (node.title != null && node.title!.isNotEmpty)
            Text(node.title!, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
          Text(node.content, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle, height: 1.4)),
        ])),
      ]),
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

            // 记忆链条（树状图）
            _buildChainCard(l10n),
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
            _buildDecayCard(l10n),
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

  Widget _buildDecayCard(AppLocalizations l10n) {
    final nowUtc = DateTime.now().toUtc();
    final lastReinforce = _parseUtc(_memory.lastReinforceAt);
    final createdAt = _parseUtc(_memory.createdAt);
    final nextReview = _parseUtc(_memory.nextReviewAt);
    final strength = _memory.strengthDays ?? 7.0;
    final elapsed = memoryElapsedDays(nowUtc, lastReinforce, createdAt);
    final current = memoryRetentionPct(elapsed, strength).round();
    final nextOffset = nextReviewOffsetDays(nowUtc, nextReview, _decayHorizonDays);
    final isLock = _memory.isLocked;
    final nextText = (_memory.nextReviewAt == null || _memory.nextReviewAt!.isEmpty)
        ? l10n.memoryNextReviewNone
        : formatBeijingTime(_memory.nextReviewAt!);
    return IosCardGroup(children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.show_chart, size: 18, color: IosCardColors.subtitle),
              const SizedBox(width: 8),
              Text(l10n.memoryDecayTitle, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 12),
            SizedBox(
              height: 150,
              width: double.infinity,
              child: MemoryDecayChart(
                strengthDays: strength,
                elapsedDays: elapsed,
                isLocked: isLock,
                horizonDays: _decayHorizonDays,
                nextReviewDay: nextOffset,
                accentColor: Theme.of(context).colorScheme.primary,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              l10n.memoryDecayHorizon(_decayHorizonDays),
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
            ),
            const SizedBox(height: 10),
            _decayInfoRow(Icons.trending_down, l10n.memoryCurrentRetention, '$current%'),
            _decayInfoRow(Icons.timer_outlined, l10n.memoryStrengthDays, strength.toStringAsFixed(1)),
            _decayInfoRow(Icons.event_available, l10n.memoryNextReview, nextText),
            _decayInfoRow(Icons.replay, l10n.memoryReviewCount, '${_memory.reviewCount ?? 0}'),
            if (isLock) ...[
              const SizedBox(height: 4),
              Row(children: [
                const Icon(Icons.lock, size: 14, color: IosCardColors.subtitle),
                const SizedBox(width: 6),
                Text(l10n.lockedNoDecay, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
              ]),
            ],
          ],
        ),
      ),
    ]);
  }

  DateTime? _parseUtc(String? s) {
    if (s == null || s.isEmpty) return null;
    final d = DateTime.tryParse(s);
    if (d == null) return null;
    return DateTime.utc(d.year, d.month, d.day, d.hour, d.minute, d.second, d.millisecond, d.microsecond);
  }

  Widget _decayInfoRow(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 15, color: IosCardColors.subtitle),
          const SizedBox(width: 8),
          Text("$label: ", style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          Text(value, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500)),
        ],
      ),
    );
  }
}
