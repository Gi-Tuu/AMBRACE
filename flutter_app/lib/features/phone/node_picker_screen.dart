import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
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
    final l10n = AppLocalizations.of(context)!;
    setState(() {
      _loading = false;
      if (r['serviceEnabled'] != true) {
        final sysEnabled = r['systemEnabled'] == true;
        _error = sysEnabled
            ? l10n.nodePickReaderServiceError
            : l10n.nodePickReaderDisabled;
      } else {
        _liveNodes = (r['nodes'] as List? ?? []).cast<Map<dynamic, dynamic>>();
        _externalNodes =
            (r['externalNodes'] as List? ?? []).cast<Map<dynamic, dynamic>>();
        _externalPkg = r['externalPackage'] as String? ?? '';
        if (_liveNodes.isEmpty && _externalNodes.isEmpty) {
          _error = l10n.chatNoNodes;
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.nodePickTitle)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 6),
                      Text(l10n.nodePickOpenAppHint,
                          style: TextStyle(fontSize: 12, color: Colors.grey.shade400)),
                      const SizedBox(height: 12),
                      FilledButton.tonal(
                        onPressed: () async {
                          await PhonePerceptionService.openAccessibilitySettings();
                        },
                        child: Text(l10n.nodePickEnableScreenReader),
                      ),
                      TextButton(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : ListView(
                  children: [
                    if (_liveNodes.isNotEmpty) ...[
                      _sectionHeader(l10n.nodePickCurrentScreen, Icons.smartphone),
                      for (final n in _liveNodes) _nodeTile(n, isExternal: false),
                    ],
                    if (_externalNodes.isNotEmpty) ...[
                      _sectionHeader(
                        _externalPkg.isEmpty ? l10n.nodePickRecentApps : l10n.nodePickRecentAppsPkg(_externalPkg),
                        Icons.history,
                      ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
                        child: Text(
                          l10n.nodePickExternalHint,
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
    final l10n = AppLocalizations.of(context)!;
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
        isIcon ? l10n.nodePickIconNoText : label,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(
        '${isEdit ? l10n.nodeInput : isIcon ? l10n.nodePickIconButton : l10n.nodeClickable} · (${x.toInt()}, ${y.toInt()})',
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
