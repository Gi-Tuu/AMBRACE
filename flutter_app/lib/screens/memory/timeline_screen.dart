import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/timeline.dart";
import "../../services/api_client.dart";
import "package:ai_companion/theme/tokens.dart";

/// 时光页 · 共同回忆：认识天数 + 关键节点时间轴（角色维度）
class TimelineScreen extends StatefulWidget {
  final int characterId;
  final String characterName;
  const TimelineScreen({super.key, required this.characterId, required this.characterName});
  @override
  State<TimelineScreen> createState() => _TimelineScreenState();
}

class _TimelineScreenState extends State<TimelineScreen> {
  final _api = ApiClient();
  TimelineData? _data;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getTimeline(widget.characterId);
      if (mounted) {
        setState(() {
          _data = data;
          _loading = false;
        });
      }
      // 大事记未生成且已有回忆 → 静默生成一次（幂等），完成后刷新
      if (!data.hasMilestones && data.items.isNotEmpty) {
        try {
          await _api.createMilestones(widget.characterId);
          final fresh = await _api.getTimeline(widget.characterId);
          if (mounted && fresh.hasMilestones) {
            setState(() => _data = fresh);
          }
        } catch (_) {}
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  IconData _iconFor(String type) {
    switch (type) {
      case "milestone":
        return Icons.workspace_premium;
      case "first_chat":
        return Icons.forum_outlined;
      case "blessing":
        return Icons.celebration_outlined;
      case "pet":
        return Icons.pets_outlined;
      default:
        return Icons.star_outline;
    }
  }

  Color _colorFor(String type) {
    switch (type) {
      case "milestone":
        return Colors.amber.shade700;
      case "first_chat":
        return Colors.blue;
      case "blessing":
        return Colors.orange;
      case "pet":
        return Colors.green;
      default:
        return Colors.purple;
    }
  }

  String _fmtDate(String date) {
    final l10n = AppLocalizations.of(context)!;
    try {
      final parts = date.split("-");
      return l10n.dateMonthDay(parts[1], parts[2]);
    } catch (_) {
      return date;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.timelineTitle(widget.characterName))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _data == null
              ? Center(child: Text(l10n.timelineLoadFailed))
              : _buildBody(_data!),
    );
  }

  Widget _buildBody(TimelineData data) {
    final l10n = AppLocalizations.of(context)!;
    final items = data.items;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          _daysCard(data),
          if (items.isEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 60),
              child: Center(child: Text(l10n.noMilestones)),
            )
          else
            ..._timelineList(items),
        ],
      ),
    );
  }

  Widget _daysCard(TimelineData data) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.only(bottom: 20),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        gradient: LinearGradient(
          colors: [theme.colorScheme.primaryContainer, theme.colorScheme.secondaryContainer],
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            l10n.daysKnown(data.characterName, data.daysKnown),
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: theme.colorScheme.onPrimaryContainer),
          ),
          const SizedBox(height: 6),
          Text(
            l10n.journeyDesc,
            style: TextStyle(fontSize: 13, color: theme.colorScheme.onPrimaryContainer.withValues(alpha: 0.7)),
          ),
        ],
      ),
    );
  }

  List<Widget> _timelineList(List<TimelineItem> items) {
    final out = <Widget>[];
    String? lastDate;
    for (var i = 0; i < items.length; i++) {
      final item = items[i];
      final dateStr = _fmtDate(item.date);
      final showDate = dateStr != lastDate;
      lastDate = dateStr;
      final isLast = i == items.length - 1;
      out.add(_TimelineEntry(
        item: item,
        dateLabel: showDate ? dateStr : null,
        icon: _iconFor(item.type),
        color: _colorFor(item.type),
        isLast: isLast,
      ));
    }
    return out;
  }
}

class _TimelineEntry extends StatelessWidget {
  final TimelineItem item;
  final String? dateLabel;
  final IconData icon;
  final Color color;
  final bool isLast;
  const _TimelineEntry({
    required this.item,
    required this.dateLabel,
    required this.icon,
    required this.color,
    required this.isLast,
  });

  @override
  Widget build(BuildContext context) {
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 44,
            child: Column(
              children: [
                if (dateLabel != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 4),
                    child: Text(dateLabel!, style: const TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                  ),
                Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(color: color.withValues(alpha: 0.15), shape: BoxShape.circle),
                  child: Icon(icon, size: 17, color: color),
                ),
                if (!isLast)
                  Expanded(
                    child: Container(width: 2, color: Theme.of(context).dividerColor),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(bottom: 22),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(item.title, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: Theme.of(context).colorScheme.onSurface)),
                  if (item.desc.isNotEmpty && item.desc != item.title)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(item.desc, style: const TextStyle(fontSize: 13, color: AppColors.textMuted)),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
