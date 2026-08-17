import 'package:flutter/material.dart';
import '../../services/phone_perception_service.dart';

/// 从屏幕可视化点选操作目标（2026-08-14 易用性）：
/// 当前屏幕可点击/可输入节点（含无文本图标节点 [图标]）→ 返回 {label, x, y}；
/// 若当前屏幕（本 app 编辑页）无可选，会展示「最近打开的应用」里采集到的目标。
class NodePickerScreen extends StatefulWidget {
  const NodePickerScreen({super.key});
  @override
  State<NodePickerScreen> createState() => _NodePickerScreenState();
}

class _NodePickerScreenState extends State<NodePickerScreen> {
  List<Map<dynamic, dynamic>> _liveNodes = [];
  List<Map<dynamic, dynamic>> _externalNodes = [];
  String _externalPkg = '';
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
    final r = await PhonePerceptionService.getNodeTree();
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (r['serviceEnabled'] != true) {
        final sysEnabled = r['systemEnabled'] == true;
        _error = sysEnabled
            ? '读屏服务未连接：App 更新后需在系统设置里重新开启「读屏（无障碍）」'
            : '未开启读屏（无障碍），无法读取当前屏幕';
      } else {
        _liveNodes = (r['nodes'] as List? ?? []).cast<Map<dynamic, dynamic>>();
        _externalNodes =
            (r['externalNodes'] as List? ?? []).cast<Map<dynamic, dynamic>>();
        _externalPkg = r['externalPackage'] as String? ?? '';
        if (_liveNodes.isEmpty && _externalNodes.isEmpty) {
          _error = '当前屏幕暂无可操作节点';
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('选择操作目标')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 6),
                      Text('可先打开目标应用再回来点选，或直接手输目标文本',
                          style: TextStyle(fontSize: 12, color: Colors.grey.shade400)),
                      const SizedBox(height: 12),
                      FilledButton.tonal(
                        onPressed: () async {
                          await PhonePerceptionService.openAccessibilitySettings();
                        },
                        child: const Text('去开启读屏'),
                      ),
                      TextButton(onPressed: _load, child: const Text('重试')),
                    ],
                  ),
                )
              : ListView(
                  children: [
                    if (_liveNodes.isNotEmpty) ...[
                      _sectionHeader('当前屏幕', Icons.smartphone),
                      for (final n in _liveNodes) _nodeTile(n, isExternal: false),
                    ],
                    if (_externalNodes.isNotEmpty) ...[
                      _sectionHeader(
                        _externalPkg.isEmpty ? '最近打开的应用' : '最近打开的应用（$_externalPkg）',
                        Icons.history,
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
                        child: Text(
                          '来自最近浏览的页面，执行时会按文字在当前屏幕重新匹配',
                          style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
                        ),
                      ),
                      for (final n in _externalNodes) _nodeTile(n, isExternal: true),
                    ],
                    const SizedBox(height: 24),
                  ],
                ),
    );
  }

  Widget _sectionHeader(String title, IconData icon) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
      child: Row(
        children: [
          Icon(icon, size: 16, color: Colors.grey.shade500),
          const SizedBox(width: 6),
          Text(title, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _nodeTile(Map<dynamic, dynamic> n, {required bool isExternal}) {
    final label = n['text'] as String? ?? '';
    final x = n['x'] as num? ?? 0;
    final y = n['y'] as num? ?? 0;
    final isIcon = label == '[图标]';
    final isEdit = n['editable'] == true;
    return ListTile(
      dense: true,
      leading: Icon(
        isEdit
            ? Icons.keyboard_outlined
            : isIcon
                ? Icons.image_outlined
                : Icons.touch_app_outlined,
        color: Theme.of(context).colorScheme.primary,
      ),
      title: Text(
        isIcon ? '图标（无文字）' : label,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(
        '${isEdit ? '输入框' : isIcon ? '图标按钮' : '可点击'} · (${x.toInt()}, ${y.toInt()})',
        style: const TextStyle(fontSize: 11),
      ),
      onTap: () => Navigator.pop(context, {
        'label': label,
        'x': x.toInt(),
        'y': y.toInt(),
      }),
    );
  }
}
