import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 上下文预算页（2026-11-22 批 4）：`GET /system/context-budget` 快照只读
/// （档位/总配额/本轮预算/最近一轮/24h 裁剪/分层体量）＋ 档位切换
/// （`PUT /context-budget/tier` `{tier}`；脏值服务端夹取，下一轮生效）。
class ContextBudgetPage extends StatefulWidget {
  const ContextBudgetPage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<ContextBudgetPage> createState() => _ContextBudgetPageState();
}

class _ContextBudgetPageState extends State<ContextBudgetPage> {
  late final ApiConfigApi _api = ApiConfigApi(widget.client);
  Map<String, dynamic>? _data;
  bool _loading = true;
  bool _saving = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final data = await _api.contextBudget();
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _pickTier(String key) async {
    setState(() => _saving = true);
    try {
      await _api.setContextBudgetTier(key);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(widget.rs.t('cfgSaved'))));
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('${widget.rs.t('cfgSaveFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  String _num(Object? v) {
    final n = v is num ? v.toInt() : int.tryParse('$v') ?? 0;
    final s = n.toString();
    final b = StringBuffer();
    for (var i = 0; i < s.length; i++) {
      final rem = s.length - i;
      b.write(s[i]);
      if (rem > 1 && rem % 3 == 1) b.write(',');
    }
    return b.toString();
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final d = _data;
    final breakdown =
        (((d?['section_breakdown'] as Map?)?['items'] as List?) ?? const [])
            .cast<Map>();
    final lastUsage = (d?['last_usage'] as Map?) ?? const {};

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('budgetTitle'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_error, style: theme.textTheme.bodySmall),
                  TextButton(onPressed: _load, child: Text(rs.t('retry'))),
                ]))
              : ListView(padding: const EdgeInsets.all(12), children: [
                  // ── 档位 ──
                  _section(rs.t('budgetTier'), theme),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: _saving
                          ? const Center(
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : Wrap(
                              spacing: 8,
                              runSpacing: 8,
                              children: [
                                for (final opt
                                    in ((d?['tier_options'] as List?) ??
                                        const [])
                                    .cast<Map>())
                                  ChoiceChip(
                                    label: Text(
                                        '${opt['key']} · ${_num(opt['budget_tokens'])}'),
                                    selected: opt['is_current'] == true,
                                    onSelected: (_) =>
                                        _pickTier(opt['key'] as String),
                                  ),
                              ],
                            ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  // ── 关键读数 ──
                  _section(rs.t('budgetQuota'), theme),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Row(children: [
                        _cell(theme, rs.t('budgetQuota'),
                            _num(d?['total_quota_tokens'])),
                        _cell(theme, rs.t('budgetEffective'),
                            _num(d?['effective_budget_tokens'])),
                        _cell(theme, rs.t('budgetLastTurn'),
                            _num(lastUsage['est_tokens'])),
                        _cell(theme, rs.t('budgetClips'),
                            _num(d?['clip_count_24h'])),
                      ]),
                    ),
                  ),
                  const SizedBox(height: 14),
                  // ── 分层体量 ──
                  _section(rs.t('budgetBreakdown'), theme),
                  Card(
                    child: Column(
                      children: [
                        for (final s in breakdown)
                          ListTile(
                            dense: true,
                            title: Text(s['key'] as String? ?? ''),
                            subtitle: Text(
                                '${rs.t('budgetAvg')} ${_num(s['avg_chars'])} · '
                                'max ${_num(s['max_chars'])} · '
                                'share ${((s['share'] as num? ?? 0) * 100).toStringAsFixed(0)}%',
                                style: theme.textTheme.labelSmall),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 24),
                ]),
    );
  }

  Widget _cell(ThemeData theme, String label, String value) => Expanded(
        child: Column(children: [
          Text(value, style: theme.textTheme.titleSmall),
          Text(label, style: theme.textTheme.labelSmall),
        ]),
      );

  Widget _section(String title, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
        child: Text(title,
            style: theme.textTheme.titleSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      );
}
