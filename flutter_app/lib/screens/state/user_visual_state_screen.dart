import 'package:flutter/material.dart';
import '../../models/user_state.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/spider_chart.dart';
import '../../widgets/ios_card_group.dart';

/// 用户可视化状态：正八边形蛛网图 + 八维滑动调整
/// （用户手动设置自己当前的状态，AI 角色在对话中可感知）
class UserVisualStateScreen extends StatefulWidget {
  const UserVisualStateScreen({super.key});

  @override
  State<UserVisualStateScreen> createState() => _UserVisualStateScreenState();
}

class _UserVisualStateScreenState extends State<UserVisualStateScreen> {
  final _api = ApiClient();
  UserState? _state;
  bool _loading = true;
  bool _saving = false;
  String? _error;

  static const _labels = ['心情', '体温', '性欲', '占有欲', '疲惫感', '敏感度', '舒适感', '怒气值'];
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
    Color(0xFFFF7043),
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
      final st = await _api.getUserStates();
      if (mounted) setState(() { _state = st; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  void _setValue(int i, int v) {
    final st = _state;
    if (st == null) return;
    final vals = st.toValues();
    vals[i] = v;
    setState(() => _state = st.withValues(vals));
  }

  Future<void> _save() async {
    final st = _state;
    if (st == null || _saving) return;
    setState(() => _saving = true);
    try {
      final updated = await _api.updateUserStates(st.toSubmitJson());
      if (mounted) {
        setState(() { _state = updated; _saving = false; });
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('状态已保存，AI 角色会感知到你的状态')),
        );
      }
    } catch (_) {
      if (mounted) {
        setState(() => _saving = false);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('保存失败，请重试')),
        );
      }
    }
  }

  void _resetAll() {
    final st = _state;
    if (st == null) return;
    setState(() => _state = st.withValues(List.filled(8, 50)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('我的可视化状态')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text('加载失败: $_error', textAlign: TextAlign.center),
                  const SizedBox(height: 12),
                  ElevatedButton(onPressed: _load, child: const Text('重试')),
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
                                values: (_state?.toValues() ?? List.filled(8, 50)).map((v) => v.toDouble()).toList(),
                                labels: _labels,
                                colors: _colors,
                              ),
                            ),
                          ),
                        ],
                      ),
                      if (_state != null && _state!.updatedAt.isNotEmpty)
                        Center(child: Text('更新于 ${formatBeijingTime(_state!.updatedAt)}', style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle))),
                      const SizedBox(height: 8),
                      const Padding(
                        padding: EdgeInsets.only(left: 4, bottom: 4),
                        child: Text('拖动滑块调整你当前的状态，保存后 AI 角色在聊天中会感知到（例如心情低落时角色会更温柔地关心你）',
                            style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
                      ),
                      const SizedBox(height: 12),
                      ..._buildSliders(),
                      const SizedBox(height: 16),
                      Row(children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: _resetAll,
                            icon: const Icon(Icons.restart_alt),
                            label: const Text('重置为默认'),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: FilledButton.icon(
                            onPressed: _save,
                            icon: _saving ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.check),
                            label: Text(_saving ? '保存中...' : '保存状态'),
                          ),
                        ),
                      ]),
                    ],
                  ),
                ),
    );
  }

  List<Widget> _buildSliders() {
    final st = _state;
    final values = st?.toValues() ?? List.filled(8, 50);
    return [
      IosCardGroup(
        children: [
          for (var i = 0; i < _labels.length; i++) ...[
            if (i > 0) const IosCardDivider(),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              child: Row(children: [
                Icon(_icons[i], size: 20, color: _colors[i]),
                const SizedBox(width: 10),
                SizedBox(width: 56, child: Text(_labels[i], style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600))),
                Expanded(child: Slider(
                  value: values[i].toDouble(),
                  min: 0,
                  max: 100,
                  divisions: 100,
                  activeColor: _colors[i],
                  onChanged: (v) => _setValue(i, v.round()),
                )),
                SizedBox(width: 36, child: Text(values[i].toString(), textAlign: TextAlign.right, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold))),
              ]),
            ),
          ],
        ],
      ),
    ];
  }
}
