// F7-c-6（2026-09-01）自 features/settings/api_config_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
class LlmUsageSheet extends StatefulWidget {
  const LlmUsageSheet({super.key});

  @override
  State<LlmUsageSheet> createState() => LlmUsageSheetState();
}

class LlmUsageSheetState extends State<LlmUsageSheet> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  bool _failed = false;
  bool _showByUser = false; // #68 P6 主账号「按账号」展开

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().getLlmUsage();
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
        _failed = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  Future<void> _setLimit() async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController(text: '${_data?['total_limit'] ?? 0}');
    final val = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.setQuotaTotal),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
            hintText: l10n.quotaHint,
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: BorderSide.none,
            ),
            filled: true,
            fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text.trim()),
            child: Text(l10n.save),
          ),
        ],
      ),
    );
    ctrl.dispose();
    if (val == null || val.isEmpty || !mounted) return;
    final limit = int.tryParse(val) ?? -1;
    if (limit < 0) return;
    try {
      await ApiClient().updateLlmUsageLimit(limit);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(limit == 0 ? l10n.quotaCleared : l10n.quotaUpdated)));
      _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.saveFailed)));
      }
    }
  }

  String _fmt(int n, AppLocalizations l10n) {
    if (n >= 100000000) return l10n.unitYi((n / 100000000).toStringAsFixed(1));
    if (n >= 10000) return l10n.unitWan((n / 10000).toStringAsFixed(1));
    return n.toString();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Text(l10n.llmUsageStats,
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(28),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_failed)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Text(l10n.loadFailedCheckServer,
                    textAlign: TextAlign.center,
                    style: TextStyle(color: IosCardColors.subtitle)),
              )
            else
              _buildContent(context, scheme),
          ],
        ),
      ),
    );
  }

  Widget _buildContent(BuildContext context, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final total = (_data?['used_total'] as num? ?? 0).toInt();
    final limit = (_data?['total_limit'] as num? ?? 0).toInt();
    final remaining = (_data?['remaining'] as num?)?.toInt();
    final today = (_data?['today'] as num? ?? 0).toInt();
    final week = (_data?['week'] as num? ?? 0).toInt();
    final month = (_data?['month'] as num? ?? 0).toInt();
    final byModel =
        (_data?['by_model'] as List? ?? []).cast<Map<String, dynamic>>();
    final byUser =
        (_data?['by_user'] as List? ?? []).cast<Map<String, dynamic>>();
    final canEdit = _data?['can_edit_limit'] == true;
    final cardColor = scheme.surfaceContainerHighest.withValues(alpha: 0.5);

    Widget statCard(String label, int value) => Expanded(
          child: Container(
            margin: const EdgeInsets.only(right: 8),
            padding: const EdgeInsets.symmetric(vertical: 12),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              children: [
                Text(_fmt(value, l10n),
                    style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: scheme.onSurface)),
                const SizedBox(height: 2),
                Text(label,
                    style: const TextStyle(
                        fontSize: 11, color: IosCardColors.subtitle)),
              ],
            ),
          ),
        );

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: cardColor,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Text(l10n.usedTotal,
                      style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                  const Spacer(),
                  if (canEdit)
                    InkWell(
                      onTap: _setLimit,
                      child: Row(
                        children: [
                          Icon(Icons.edit_outlined,
                              size: 14, color: scheme.primary),
                          const SizedBox(width: 2),
                          Text(l10n.setQuota,
                              style: TextStyle(fontSize: 12, color: scheme.primary)),
                        ],
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                limit > 0
                    ? '${_fmt(total, l10n)} / ${_fmt(limit, l10n)} tokens'
                    : l10n.totalTokensNoQuota(_fmt(total, l10n)),
                style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: scheme.onSurface),
              ),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: limit > 0 ? (total / limit).clamp(0.0, 1.0) : null,
                  minHeight: 8,
                  backgroundColor: scheme.surfaceContainerHighest,
                ),
              ),
              if (remaining != null) ...[
                const SizedBox(height: 6),
                Text(
                  l10n.remainingTokens(_fmt(remaining, l10n)),
                  style: TextStyle(
                    fontSize: 12,
                    color: remaining <= 0 ? scheme.error : IosCardColors.subtitle,
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            statCard(l10n.today, today),
            statCard(l10n.last7Days, week),
            statCard(l10n.thisMonth, month),
          ],
        ),
        if (byModel.isNotEmpty) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.byModelUsage,
                    style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                const SizedBox(height: 4),
                for (final m in byModel.take(6))
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            m['model'] as String? ?? l10n.unknown,
                            style: TextStyle(fontSize: 13, color: scheme.onSurface),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        Text(
                          _fmt((m['total'] as num? ?? 0).toInt(), l10n),
                          style: const TextStyle(
                              fontSize: 12, color: IosCardColors.subtitle),
                        ),
                      ],
                    ),
                  ),
                if (byModel.length > 6)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(l10n.etcModels('${byModel.length}'),
                        style: const TextStyle(
                            fontSize: 11, color: IosCardColors.subtitle)),
                  ),
              ],
            ),
          ),
        ],
        // #68 P6：主账号显示组聚合 + 「按账号」展开明细（子账号不返回 by_user → 不渲染）
        if (byUser.isNotEmpty) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: cardColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                InkWell(
                  onTap: () => setState(() => _showByUser = !_showByUser),
                  child: Row(
                    children: [
                      Text(l10n.byUserUsage,
                          style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                      const Spacer(),
                      Text(
                        _showByUser ? l10n.collapseByAccount : l10n.expandByAccount,
                        style: TextStyle(fontSize: 12, color: scheme.primary),
                      ),
                      Icon(
                        _showByUser ? Icons.expand_less : Icons.expand_more,
                        size: 16,
                        color: scheme.primary,
                      ),
                    ],
                  ),
                ),
                if (_showByUser) ...[
                  const SizedBox(height: 6),
                  for (final u in byUser)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              u['nickname'] as String? ?? l10n.unknown,
                              style: TextStyle(fontSize: 13, color: scheme.onSurface),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          Text(
                            _fmt((u['total'] as num? ?? 0).toInt(), l10n),
                            style: const TextStyle(
                                fontSize: 12, color: IosCardColors.subtitle),
                          ),
                        ],
                      ),
                    ),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 8),
        Text(
          l10n.usageNote,
          style: TextStyle(fontSize: 10, color: IosCardColors.subtitle),
        ),
      ],
    );
  }
}
