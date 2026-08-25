import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../models/character_state.dart';
import '../../widgets/spider_chart.dart';
import '../../services/api_client.dart';
import '../memory/emotion_memory_screen.dart';
import 'state_history_screen.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ios_card_group.dart';
import "package:ai_companion/theme/tokens.dart";

/// 角色可视化状态：正八边形蛛网图 + 八维数值列表
class VisualStateScreen extends StatefulWidget {
  final int characterId;
  final String characterName;

  const VisualStateScreen({super.key, required this.characterId, required this.characterName});

  @override
  State<VisualStateScreen> createState() => _VisualStateScreenState();
}

class _VisualStateScreenState extends State<VisualStateScreen> {
  final _api = ApiClient();
  CharacterState? _state;
  bool _loading = true;
  String? _error;

  List<String> _labels(AppLocalizations l10n) => [l10n.mood, l10n.stateTemp, l10n.stateDesire, l10n.statePossessiveness, l10n.stateFatigue, l10n.stateSensitivity, l10n.stateComfort, l10n.stateAnger];
  static const _icons = [
    Icons.mood,
    Icons.device_thermostat,
    Icons.favorite,
    Icons.security,
    Icons.battery_alert,
    Icons.graphic_eq,
    Icons.wb_sunny,
    Icons.psychology,
  ];
  static const _colors = [
    Color(0xFFEF5350),
    AppColors.compareOrange,
    Color(0xFFEC407A),
    Color(0xFFAB47BC),
    Color(0xFF7E57C2),
    Color(0xFF42A5F5),
    Color(0xFF26A69A),
    Color(0xFF66BB6A),
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final st = await _api.getCharacterStates(widget.characterId);
      if (mounted) setState(() { _state = st; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  List<int> _values() {
    final s = _state;
    return [
      s?.mood ?? 50, s?.bodyTemp ?? 50, s?.desire ?? 50, s?.possessiveness ?? 50,
      s?.fatigue ?? 50, s?.sensitivity ?? 50, s?.comfort ?? 50, s?.anger ?? 50,
    ];
  }

  // 漂移趋势方向（与后端 DRIFT_RULES 一致）：疲惫↑ / 怒气↓ / 其他向 50 收敛
  IconData? _trendIcon(int i, int v) {
    if (i == 4) return v >= 100 ? null : Icons.arrow_upward; // fatigue
    if (i == 7) return v <= 0 ? null : Icons.arrow_downward; // anger
    if (v > 50) return Icons.arrow_downward;
    if (v < 50) return Icons.arrow_upward;
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.visualStateTitle(widget.characterName))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(l10n.loadFailedErr(_error ?? ''), textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  ElevatedButton(onPressed: _load, child: Text(l10n.retry)),
                ]))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      IosCardGroup(
                        children: [
                          Padding(
                            padding: const EdgeInsets.all(12),
                            child: AspectRatio(
                              aspectRatio: 1,
                              child: SpiderChart(
                                values: _values().map((v) => v.toDouble()).toList(),
                                labels: _labels(l10n),
                                colors: _colors,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      ..._buildValueTiles(),
                      const SizedBox(height: 8),
                      // 状态情绪记忆 + 状态趋势入口
                      IosCardGroup(
                        children: [
                          ListTile(
                            leading: Icon(Icons.psychology_alt, color: Theme.of(context).colorScheme.tertiary),
                            title: Text(l10n.stateEmotionMemory),
                            subtitle: Text(l10n.stateEmotionMemoryHint),
                            trailing: const Icon(Icons.chevron_right, color: IosCardColors.chevron),
                            onTap: () {
                              Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => EmotionMemoryScreen(
                                    characterId: widget.characterId,
                                    characterName: widget.characterName,
                                  ),
                                ),
                              );
                            },
                          ),
                          const IosCardDivider(),
                          ListTile(
                            leading: Icon(Icons.show_chart, color: Theme.of(context).colorScheme.primary),
                            title: Text(l10n.stateTrend),
                            subtitle: Text(l10n.stateTrendHint),
                            trailing: const Icon(Icons.chevron_right, color: IosCardColors.chevron),
                            onTap: () {
                              Navigator.push(
                                context,
                                MaterialPageRoute(
                                  builder: (_) => StateHistoryScreen(
                                    characterId: widget.characterId,
                                    characterName: widget.characterName,
                                  ),
                                ),
                              );
                            },
                          ),
                        ],
                      ),
                      if (_state != null && _state!.updatedAt.isNotEmpty)
                        Center(child: Text(l10n.stateUpdatedHint(formatBeijingTime(_state!.updatedAt)), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle))),
                    ],
                  ),
                ),
    );
  }

  List<Widget> _buildValueTiles() {
    final l10n = AppLocalizations.of(context)!;
    final values = _values();
    return [
      IosCardGroup(
        children: [
          for (var i = 0; i < _labels(l10n).length; i++) ...[
            if (i > 0) const IosCardDivider(),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Row(children: [
                Icon(_icons[i], size: 20, color: _colors[i]),
                const SizedBox(width: 10),
                SizedBox(width: 56, child: Text(_labels(l10n)[i], style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600))),
                Expanded(child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: values[i] / 100,
                    minHeight: 8,
                    backgroundColor: _colors[i].withValues(alpha: 0.15),
                    valueColor: AlwaysStoppedAnimation(_colors[i]),
                  ),
                )),
                const SizedBox(width: 10),
                SizedBox(width: 36, child: Text(values[i].toString(), textAlign: TextAlign.right, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold))),
                SizedBox(width: 18, child: _trendIcon(i, values[i]) == null ? const SizedBox.shrink() : Icon(_trendIcon(i, values[i]), size: 14, color: IosCardColors.subtitle)),
              ]),
            ),
          ],
        ],
      ),
    ];
  }
}
