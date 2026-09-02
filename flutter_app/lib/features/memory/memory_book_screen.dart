import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/memory.dart";
import "../../services/api_client.dart";
import "../../theme/aurora_tokens.dart";
import "../../widgets/aurora_card.dart";
import "../../widgets/empty_state.dart";
import "memory_detail_screen.dart";
import "../../utils/beijing_time.dart";
import "package:ai_companion/theme/tokens.dart";
import "package:ai_companion/widgets/app_page_route.dart";

class MemoryBookScreen extends StatefulWidget {
  final int characterId;
  final String characterName;

  const MemoryBookScreen({
    super.key,
    required this.characterId,
    required this.characterName,
  });

  @override
  State<MemoryBookScreen> createState() => _MemoryBookScreenState();
}

class _MemoryBookScreenState extends State<MemoryBookScreen> {
  final _api = ApiClient();
  List<Memory> _allMemories = [];
  int _totalCount = 0;
  final Map<String, Memory?> _pinned = {};
  bool _loading = true;
  String? _error;
  String _selectedType = "all";
  bool _summarizing = false;

  Map<String, String> _typeLabels(AppLocalizations l10n) => {
        "all": l10n.memoryAll,
        "user_info": l10n.memoryImpression,
        "preference": l10n.memoryPreference,
        "event": l10n.memoryEvent,
        "insight": l10n.memoryInsight,
      };

  static const _typeIcons = {
    "all": Icons.auto_stories,
    "user_info": Icons.person_outline,
    "preference": Icons.favorite_outline,
    "event": Icons.event_note,
    "insight": Icons.lightbulb_outline,
  };

  static const _typeColors = {
    "all": Colors.blue,
    "user_info": Colors.purple,
    "preference": Colors.pink,
    "event": Colors.orange,
    "insight": Colors.teal,
  };

  @override
  void initState() {
    super.initState();
    _loadMemories();
  }

  Future<void> _loadMemories() async {
    setState(() { _loading = true; _error = null; });
    try {
      final result = await _api.getMemoriesWithTotal(characterId: widget.characterId);
      if (!mounted) return;
      setState(() {
        _allMemories = result.memories;
        _totalCount = result.total;
        _pinned.clear();
        for (final m in result.memories) {
          if (m.isPinned) _pinned[m.memoryType] = m;
        }
        _loading = false;
      });
      _ensureSummary(_selectedType);
    } catch (e) {
      setState(() { _error = e.toString(); _loading = false; });
    }
  }

  // ?? tab ??????????? 6 ??????????????
  Future<void> _ensureSummary(String type) async {
    if (type == "all" || _summarizing || _pinned[type] != null) return;
    setState(() { _summarizing = true; });
    try {
      final r = await _api.summarizeMemories(widget.characterId, type);
      if (r['generated'] == true && r['memory_id'] != null) {
        final result = await _api.getMemoriesWithTotal(characterId: widget.characterId);
      if (!mounted) return;
        setState(() {
          _allMemories = result.memories;
          _totalCount = result.total;
          _pinned.clear();
          for (final m in result.memories) {
            if (m.isPinned) _pinned[m.memoryType] = m;
          }
        });
      }
    } catch (e) {
      // ?????????????
    } finally {
      if (mounted) setState(() { _summarizing = false; });
    }
  }

  // 重新生成当前类型的置顶摘要（force 跳过 6 小时节流）
  Future<void> _regenerateSummary(String type) async {
    if (_summarizing) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() { _summarizing = true; });
    try {
      final r = await _api.summarizeMemories(widget.characterId, type, force: true);
      await _loadMemories();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(r['generated'] == true ? l10n.summaryRegenerated : l10n.summaryGenFailed),
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.summaryRegenFailed), duration: const Duration(seconds: 2)),
        );
      }
    } finally {
      if (mounted) setState(() { _summarizing = false; });
    }
  }

  List<Memory> get _filteredMemories {
    final source = _allMemories.where((m) => !m.isPinned);
    if (_selectedType == "all") return source.toList();
    return source.where((m) => m.memoryType == _selectedType).toList();
  }

  String _sourceLabel(Memory mem, AppLocalizations l10n) {
    if (mem.sourceLabel != null && mem.sourceLabel!.isNotEmpty) {
      return mem.sourceLabel!;
    }
    switch (mem.source) {
      case "chat": return l10n.sourceChat;
      case "moment": return l10n.sourceMoment;
      case "diary": return l10n.sourceDiary;
      case "bio": return l10n.sourceBio;
      case "status": return l10n.sourceStatus;
      case "extracted": return l10n.sourceExtracted;
      case "relationship": return l10n.sourceRelationship;
      default: return mem.source ?? l10n.unknown;
    }
  }

  IconData _sourceIcon(Memory mem) {
    if (mem.sourceIcon != null) {
      switch (mem.sourceIcon) {
        case "chat": return Icons.chat;
        case "moment": return Icons.public;
        case "diary": return Icons.book;
        case "bio": return Icons.auto_awesome;
        case "status": return Icons.update;
        case "extracted": return Icons.psychology;
        case "relationship": return Icons.favorite;
        case "profile": return Icons.person;
        default: return Icons.help_outline;
      }
    }
    switch (mem.source) {
      case "chat": return Icons.chat;
      case "moment": return Icons.public;
      case "diary": return Icons.book;
      case "bio": return Icons.auto_awesome;
      case "status": return Icons.update;
      case "extracted": return Icons.psychology;
      case "relationship": return Icons.favorite;
      default: return Icons.help_outline;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P8 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
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
        title: Text(l10n.memoryBookTitle(widget.characterName)),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadMemories,
            tooltip: l10n.refresh,
          ),
        ],
      ),
      body: Column(
        children: [
          // Type filter chips
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: _typeLabels(l10n).entries.map((entry) {
                  final isSelected = _selectedType == entry.key;
                  final typeColor = _typeColors[entry.key] ?? Colors.blueGrey;
                  final scheme = Theme.of(context).colorScheme;
                  return Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: GestureDetector(
                      onTap: () {
                        setState(() => _selectedType = entry.key);
                        _ensureSummary(entry.key);
                      },
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 150),
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        decoration: BoxDecoration(
                          color: isSelected
                              ? typeColor.withValues(alpha: 0.15)
                              : scheme.surface,
                          borderRadius: BorderRadius.circular(16),
                          border: Border.all(
                            color: isSelected
                                ? typeColor.withValues(alpha: 0.5)
                                : Theme.of(context).dividerColor,
                          ),
                        ),
                        child: Row(mainAxisSize: MainAxisSize.min, children: [
                          Icon(
                            _typeIcons[entry.key] ?? Icons.memory,
                            size: 15,
                            color: isSelected ? typeColor : scheme.onSurfaceVariant,
                          ),
                          const SizedBox(width: 5),
                          Text(
                            entry.value,
                            style: TextStyle(
                              fontSize: 13,
                              fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
                              color: isSelected ? typeColor : scheme.onSurface,
                            ),
                          ),
                        ]),
                      ),
                    ),
                  );
                }).toList(),
              ),
            ),
          ),
          // Count bar
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Row(
              children: [
                Text(
                  l10n.memoryCount(_filteredMemories.length),
                  style: const TextStyle(fontSize: 13, color: AppColors.textSecondary),
                ),
                const Spacer(),
                Text(
                  l10n.totalCount(_totalCount),
                  style: const TextStyle(fontSize: 13, color: AppColors.separator),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          // Memory list
          Expanded(
            child: _buildBody(),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    final l10n = AppLocalizations.of(context)!;
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      // Aurora P8：错误态 EmptyState + 重试
      return Center(
        child: EmptyState(
          icon: Icons.cloud_off_rounded,
          title: l10n.loadFailed,
          subtitle: _error,
          action: ElevatedButton(onPressed: _loadMemories, child: Text(l10n.retry)),
        ),
      );
    }

    final filtered = _filteredMemories;
    if (filtered.isEmpty) {
      // Aurora P8：空态 EmptyState 统一渲染
      return Center(
        child: EmptyState(
          icon: Icons.auto_stories,
          title: _selectedType == "all" ? l10n.noMemories : l10n.noMemoriesInCategory,
        ),
      );
    }

    final summaries = _summaryItems();
    return RefreshIndicator(
      onRefresh: _loadMemories,
      child: ListView.builder(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        itemCount: summaries.length + filtered.length,
        itemBuilder: (context, index) {
          if (index < summaries.length) {
            return _SummaryCard(
              memory: summaries[index],
              typeLabel: _typeLabels(l10n)[summaries[index].memoryType] ?? summaries[index].memoryType,
              typeColor: _typeColors[summaries[index].memoryType] ?? Colors.blueGrey,
              onRefresh: () => _regenerateSummary(summaries[index].memoryType),
            );
          }
          final mem = filtered[index - summaries.length];
          return _MemoryCard(
            memory: mem,
            typeLabel: _typeLabels(l10n)[mem.memoryType] ?? mem.memoryType,
            typeIcon: _typeIcons[mem.memoryType] ?? Icons.memory,
            typeColor: _typeColors[mem.memoryType] ?? Colors.blueGrey,
            sourceLabel: _sourceLabel(mem, l10n),
            sourceIcon: _sourceIcon(mem),
            onTap: () async {
              final changed = await Navigator.push<bool>(
                context,
                AppPageRoute(
                  builder: (_) => MemoryDetailScreen(memory: mem),
                ),
              );
              if (changed == true) _loadMemories();
            },
          );
        },
      ),
    );
  }

  // ?? tab ????????"??"????????
  List<Memory> _summaryItems() {
    final l10n = AppLocalizations.of(context)!;
    if (_selectedType == "all") {
      return _typeLabels(l10n).keys
          .where((t) => t != "all" && _pinned[t] != null)
          .map((t) => _pinned[t]!)
          .toList();
    }
    final m = _pinned[_selectedType];
    return m == null ? const [] : [m];
  }
}

class _SummaryCard extends StatelessWidget {
  final Memory memory;
  final String typeLabel;
  final Color typeColor;
  final VoidCallback? onRefresh;

  const _SummaryCard({
    required this.memory,
    required this.typeLabel,
    required this.typeColor,
    this.onRefresh,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return SizedBox(
      width: double.infinity,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: AuroraCard(
          highlight: true,
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.push_pin, size: 16, color: typeColor),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(l10n.pinnedSummary(typeLabel),
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: typeColor)),
                  ),
                  if (onRefresh != null)
                    InkWell(
                      onTap: onRefresh,
                      borderRadius: BorderRadius.circular(12),
                      child: const Padding(
                        padding: EdgeInsets.all(4),
                        child: Icon(Icons.refresh, size: 18, color: Colors.grey),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                memory.content,
                style: TextStyle(
                    fontSize: 14,
                    color: Theme.of(context).colorScheme.onSurface,
                    height: 1.4),
              ),
              if (memory.updatedAt != null && memory.updatedAt!.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    l10n.updatedAt(formatBeijingTime(memory.updatedAt!)),
                    style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}


class _MemoryCard extends StatelessWidget {
  final Memory memory;
  final String typeLabel;
  final IconData typeIcon;
  final Color typeColor;
  final String sourceLabel;
  final IconData sourceIcon;
  final VoidCallback onTap;

  const _MemoryCard({
    required this.memory,
    required this.typeLabel,
    required this.typeIcon,
    required this.typeColor,
    required this.sourceLabel,
    required this.sourceIcon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: AuroraCard(
        onTap: onTap,
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(typeIcon, size: 18, color: typeColor),
                const SizedBox(width: 6),
                Text(typeLabel,
                    style: TextStyle(
                        fontSize: 12,
                        color: typeColor,
                        fontWeight: FontWeight.w500)),
                const SizedBox(width: 8),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: Theme.of(context)
                        .colorScheme
                        .surfaceContainerHighest
                        .withValues(alpha: 0.6),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(sourceIcon,
                          size: 11,
                          color:
                              Theme.of(context).colorScheme.onSurfaceVariant),
                      const SizedBox(width: 3),
                      Text(sourceLabel,
                          style: TextStyle(
                              fontSize: 10,
                              color: Theme.of(context)
                                  .colorScheme
                                  .onSurfaceVariant)),
                    ],
                  ),
                ),
                if (memory.speakerType != null) ...[
                  const SizedBox(width: 6),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: (memory.speakerType == 'user'
                          ? Colors.blue.withValues(alpha: 0.12)
                          : Colors.orange.withValues(alpha: 0.12)),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(
                      memory.speakerType == 'user'
                          ? l10n.memorySourceUser
                          : l10n.memorySourceCharacter,
                      style: TextStyle(
                        fontSize: 10,
                        color: memory.speakerType == 'user'
                            ? Colors.blue.shade700
                            : Colors.orange.shade800,
                      ),
                    ),
                  ),
                ],
                if (memory.isLocked) ...[
                  const Icon(Icons.lock, size: 14, color: Colors.blueGrey),
                  const SizedBox(width: 4),
                ],
                const Spacer(),
                // Importance stars
                ...List.generate(5, (i) {
                  return Icon(
                    i < memory.importance ? Icons.star : Icons.star_border,
                    size: 14,
                    color:
                        i < memory.importance ? Colors.amber : Colors.grey.shade300,
                  );
                }),
              ],
            ),
            if (memory.title != null && memory.title!.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(memory.title!,
                  style: const TextStyle(
                      fontWeight: FontWeight.w600, fontSize: 15)),
            ],
            const SizedBox(height: 4),
            Text(
              memory.content,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontSize: 14,
                  color: Theme.of(context).colorScheme.onSurface,
                  height: 1.4),
            ),
            // 意义记忆（v2.1）：AI 提炼的"为什么重要"小字展示
            if (memory.whyItMatters != null &&
                memory.whyItMatters!.isNotEmpty) ...[
              const SizedBox(height: 6),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.lightbulb_outline,
                      size: 13, color: Colors.amber.shade700),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      l10n.whyMatters(memory.whyItMatters!),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11,
                        fontStyle: FontStyle.italic,
                        color: Colors.grey.shade500,
                      ),
                    ),
                  ),
                ],
              ),
            ],
            if (!memory.isLocked &&
                memory.deleteAt != null &&
                memory.deleteAt!.isNotEmpty) ...[
              const SizedBox(height: 6),
              Row(
                children: [
                  const Icon(Icons.hourglass_bottom,
                      size: 13, color: Colors.red),
                  const SizedBox(width: 4),
                  Text(
                    l10n.deleteCountdown,
                    style: TextStyle(fontSize: 11, color: Colors.red.shade400),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}


