import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/api_client.dart';
import 'step_dialog.dart';
import 'workflow_canvas_screen.dart';

/// 工作流可视化编辑页（2026-08-14 方案 A+B+C）：步骤卡片列表 + 节点连线画布双模式
class WorkflowEditScreen extends StatefulWidget {
  final Map<String, dynamic>? item;
  const WorkflowEditScreen({super.key, this.item});

  @override
  State<WorkflowEditScreen> createState() => _WorkflowEditScreenState();
}

class _WorkflowEditScreenState extends State<WorkflowEditScreen> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _descCtrl;
  late List<Map<String, dynamic>> _steps;
  Map<String, dynamic>? _graph;
  bool _saving = false;
  bool get _isEdit => widget.item != null;
  bool get _hasGraph => _graph != null;

  @override
  void initState() {
    super.initState();
    final item = widget.item;
    _nameCtrl = TextEditingController(text: (item?['name'] as String? ?? ''));
    _descCtrl = TextEditingController(text: (item?['description'] as String? ?? ''));
    _steps = (item?['steps'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
    final g = item?['graph'];
    if (g is Map) {
      _graph = Map<String, dynamic>.from(g);
      // 画布模式：用 nodes 填充列表预览（去掉 id）
      final nodes = _graph!['nodes'] as List? ?? const [];
      _steps = nodes
          .map((e) => Map<String, dynamic>.from(e as Map)..remove('id'))
          .toList();
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _descCtrl.dispose();
    super.dispose();
  }

  Future<void> _addStep() async {
    final s = await showStepDialog(context);
    if (s != null && mounted) setState(() => _steps.add(s));
  }

  Future<void> _editStep(int index) async {
    final s = await showStepDialog(context, step: _steps[index]);
    if (s != null && mounted) setState(() => _steps[index] = s);
  }

  Future<void> _openCanvas() async {
    final result = await Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(
        builder: (_) => WorkflowCanvasScreen(
          steps: List.of(_steps),
          graph: _graph,
        ),
      ),
    );
    if (result != null && mounted) {
      final l10n = AppLocalizations.of(context)!;
      setState(() {
        _graph = result;
        final nodes = _graph!['nodes'] as List? ?? const [];
        _steps = nodes
            .map((e) => Map<String, dynamic>.from(e as Map)..remove('id'))
            .toList();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.workflowCanvasSynced)),
      );
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.workflowNameRequired)),
      );
      return;
    }
    if (_hasGraph) {
      final nodes = _graph!['nodes'] as List? ?? const [];
      if (nodes.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.workflowCanvasNoNodes)),
        );
        return;
      }
    } else if (_steps.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.workflowNameAndStepRequired)),
      );
      return;
    }
    setState(() => _saving = true);
    try {
      final api = ApiClient();
      if (_isEdit) {
        await api.updateWorkflow(widget.item!['id'] as int,
            name: name,
            description: _descCtrl.text.trim(),
            steps: _hasGraph ? null : _steps,
            graph: _graph);
      } else {
        await api.createWorkflow(name,
            description: _descCtrl.text.trim(),
            steps: _hasGraph ? null : _steps,
            graph: _graph);
      }
      if (mounted) Navigator.pop(context, true);
    } catch (_) {
      if (mounted) {
        setState(() => _saving = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.saveFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(_isEdit ? l10n.workflowEditTitle : l10n.workflowNewTitle),
        actions: [
          IconButton(
            tooltip: l10n.workflowCanvasTitle,
            icon: const Icon(Icons.account_tree_outlined),
            onPressed: _openCanvas,
          ),
          TextButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l10n.save),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Column(
              children: [
                TextField(
                  controller: _nameCtrl,
                  decoration: InputDecoration(labelText: l10n.workflowNameLabel),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _descCtrl,
                  decoration: InputDecoration(labelText: l10n.workflowDescLabel),
                ),
              ],
            ),
          ),
          if (_hasGraph)
            Container(
              margin: const EdgeInsets.symmetric(horizontal: 16),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: Colors.blue.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                l10n.workflowCanvasModeHint,
                style: const TextStyle(fontSize: 12, color: Colors.blueGrey),
              ),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Row(
              children: [
                Text(_hasGraph ? l10n.workflowCanvasPreview : l10n.workflowStepsLabel,
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                const Spacer(),
                if (!_hasGraph)
                  TextButton.icon(
                    onPressed: _addStep,
                    icon: const Icon(Icons.add, size: 18),
                    label: Text(l10n.workflowAddStep),
                  ),
              ],
            ),
          ),
          Expanded(
            child: _steps.isEmpty && !_hasGraph
                ? Center(child: Text(l10n.workflowNoStepsHint, style: const TextStyle(color: Colors.grey)))
                : _hasGraph
                    ? ListView.builder(
                        itemCount: _steps.length,
                        itemBuilder: (_, i) {
                          final s = _steps[i];
                          final action = s['action'] as String? ?? 'click';
                          return Card(
                            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                            child: ListTile(
                              dense: true,
                              leading: Icon(stepIconOf(action), size: 18,
                                  color: Theme.of(context).colorScheme.primary),
                              title: Text(stepLabelOf(l10n, action)),
                              subtitle: Text(stepSummary(l10n, s)),
                            ),
                          );
                        },
                      )
                    : ReorderableListView.builder(
                        itemCount: _steps.length,
                        onReorderItem: (oldIndex, newIndex) {
                          setState(() {
                            // onReorderItem 已自动调整 newIndex，无需手动 --
                            final s = _steps.removeAt(oldIndex);
                            _steps.insert(newIndex, s);
                          });
                        },
                        itemBuilder: (_, i) {
                          final s = _steps[i];
                          final action = s['action'] as String? ?? 'click';
                          return Card(
                            key: ValueKey(i),
                            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                            child: ListTile(
                              leading: Icon(stepIconOf(action), color: Theme.of(context).colorScheme.primary),
                              title: Text(stepLabelOf(l10n, action)),
                              subtitle: Text(stepSummary(l10n, s)),
                              trailing: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  IconButton(
                                    icon: const Icon(Icons.delete_outline, color: Colors.red),
                                    onPressed: () => setState(() => _steps.removeAt(i)),
                                  ),
                                  const Icon(Icons.drag_handle, color: Colors.grey),
                                ],
                              ),
                              onTap: () => _editStep(i),
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
