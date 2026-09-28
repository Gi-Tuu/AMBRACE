import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 上下文注入长度档位页（S2，2026-09-28）：账号级「每轮注入给角色的背景信息量」可调。
///
/// 页面上所有 token 数值一律来自 GET /api/v1/system/context-budget：
/// 服务端读端把装配侧的同一套算式跑一遍，前端再算一次必然漂移，故这里只回显。
class ContextBudgetScreen extends StatefulWidget {
  const ContextBudgetScreen({super.key});

  @override
  State<ContextBudgetScreen> createState() => _ContextBudgetScreenState();
}

class _ContextBudgetScreenState extends State<ContextBudgetScreen> {
  bool _loading = true;
  bool _saving = false;
  String _error = '';
  ContextBudgetInfo? _info;

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
      final info = await ApiClient().getContextBudgetInfo();
      if (!mounted) return;
      setState(() {
        _info = info;
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

  /// 切档后重新 GET 一次：以服务端返回为准（写入端会把脏输入归一/夹紧，回显不能靠本地猜）。
  Future<void> _selectTier(String key) async {
    if (_saving || _info?.tier == key) return;
    final l10n = AppLocalizations.of(context)!;
    setState(() => _saving = true);
    try {
      await ApiClient().setContextBudgetTier(key);
      final info = await ApiClient().getContextBudgetInfo();
      if (!mounted) return;
      setState(() {
        _info = info;
        _saving = false;
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.contextBudgetSaved)));
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.contextBudgetSaveFailed)));
    }
  }

  String _tierName(AppLocalizations l10n, String key) => switch (key) {
        'extended' => l10n.contextBudgetTierExtended,
        'max' => l10n.contextBudgetTierMax,
        _ => l10n.contextBudgetTierStandard,
      };

  /// 档位来源三态：default 不能写成「你设置的」（NULL 只是没配过，等价标准档）。
  String _sourceLabel(AppLocalizations l10n, String source) => switch (source) {
        'user' => l10n.contextBudgetSourceUser,
        'unavailable' => l10n.contextBudgetSourceUnavailable,
        _ => l10n.contextBudgetSourceDefault,
      };

  Widget _row(AppLocalizations l10n, String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 7),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Text(label,
                  style: TextStyle(
                      fontSize: 13, color: Theme.of(context).hintColor)),
            ),
            const SizedBox(width: 8),
            Flexible(child: Text(value, style: const TextStyle(fontSize: 13))),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.contextBudgetTitle)),
      body: _loading
          ? Center(
              key: const Key('contextBudgetLoading'),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const CircularProgressIndicator(),
                  const SizedBox(height: 12),
                  Text(l10n.contextBudgetLoading),
                ],
              ),
            )
          : _error.isNotEmpty
              ? Center(
                  key: const Key('contextBudgetError'),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(l10n.contextBudgetLoadFailed,
                          textAlign: TextAlign.center),
                      const SizedBox(height: 12),
                      ElevatedButton(
                        onPressed: _load,
                        child: Text(l10n.contextBudgetRetry),
                      ),
                    ],
                  ),
                )
              : _buildBody(l10n, _info!),
    );
  }

  Widget _buildBody(AppLocalizations l10n, ContextBudgetInfo info) {
    // 用 SingleChildScrollView：ListView 只构建可视区，读数行/代价提示会在
    // 短屏与 widget test 里被裁掉（内容不长，不需要懒加载）
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          IosCardGroup(
            title: l10n.contextBudgetTierSection,
            children: [
              RadioGroup<String>(
                groupValue: info.tier,
                onChanged: (v) {
                  if (!_saving && v != null) _selectTier(v);
                },
                child: Column(
                  children: [
                    for (final option in info.tierOptions)
                      RadioListTile<String>(
                        value: option.key,
                        title: Text(_tierName(l10n, option.key),
                            style: const TextStyle(fontSize: 15)),
                        subtitle: Row(
                          children: [
                            Text(
                              l10n.contextBudgetTokensValue(
                                  option.budgetTokens),
                              style: TextStyle(
                                  fontSize: 12,
                                  color: Theme.of(context).hintColor),
                            ),
                            if (option.isCurrent) ...[
                              const SizedBox(width: 8),
                              Text(l10n.contextBudgetCurrent,
                                  style: TextStyle(
                                      fontSize: 12,
                                      color: Theme.of(context).primaryColor)),
                            ],
                          ],
                        ),
                        dense: true,
                      ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 2, 16, 10),
                child: Text(
                  _sourceLabel(l10n, info.tierSource),
                  style: TextStyle(
                      fontSize: 12, color: Theme.of(context).hintColor),
                ),
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.contextBudgetReadoutSection,
            children: [
              _row(l10n, l10n.contextBudgetEffective,
                  l10n.contextBudgetTokensValue(info.effectiveBudgetTokens)),
              _row(l10n, l10n.contextBudgetCeiling,
                  l10n.contextBudgetTokensValue(info.tierCeilingTokens)),
              _row(l10n, l10n.contextBudgetReserveReply,
                  l10n.contextBudgetTokensValue(info.reserveReplyTokens)),
              _row(l10n, l10n.contextBudgetReserveTools,
                  l10n.contextBudgetTokensValue(info.reserveToolsTokens)),
              _row(l10n, l10n.contextBudgetFloor,
                  l10n.contextBudgetTokensValue(info.floorTokens)),
              // 无样本时只写「暂无样本」——拿预算数冒充占用会让人以为已经用满了
              _row(
                  l10n,
                  l10n.contextBudgetLastUsage,
                  info.lastUsage.hasSample
                      ? l10n.contextBudgetTokensValue(
                          info.lastUsage.estTokens ?? 0)
                      : l10n.contextBudgetNoSample),
              _row(l10n, l10n.contextBudgetLastClip,
                  info.lastClip?.createdAt ?? l10n.contextBudgetNoClip),
              _row(
                  l10n, l10n.contextBudgetClipCount24h, '${info.clipCount24h}'),
            ],
          ),
          IosCardGroup(
            title: l10n.contextBudgetCostTitle,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 6, 16, 12),
                child: Text(l10n.contextBudgetCostNotice,
                    style: const TextStyle(fontSize: 13, height: 1.5)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
