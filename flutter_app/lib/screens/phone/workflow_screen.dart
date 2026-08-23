import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../services/phone_perception_service.dart';
import 'workflow_edit_screen.dart';

/// 手机操作工作流（2026-08-14 P1）：用户自建动作序列，AI 触发执行
class WorkflowScreen extends StatefulWidget {
  const WorkflowScreen({super.key});
  @override
  State<WorkflowScreen> createState() => _WorkflowScreenState();
}

class _WorkflowScreenState extends State<WorkflowScreen> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().listWorkflows();
      if (!mounted) return;
      setState(() {
        _items = (data['items'] as List? ?? []).cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _edit({Map<String, dynamic>? item}) async {
    await Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => WorkflowEditScreen(item: item),
    ));
    _load();
  }

  /// 48c：从 workflow 型插件模板导入（列出已安装 workflow 型插件的 templates → 选中导入）
  Future<void> _showImportDialog() async {
    final l10n = AppLocalizations.of(context)!;
    final templates = <Map<String, dynamic>>[];
    try {
      final plugins = await ApiClient().getPlugins();
      for (final p in plugins) {
        if ((p['type'] as String? ?? '') != 'workflow') continue;
        final wf = ((p['config'] as Map<String, dynamic>?)?['workflow'])
            as Map<String, dynamic>?;
        final list = (wf?['templates'] as List?) ?? const [];
        for (final t in list) {
          if (t is! Map<String, dynamic>) continue;
          templates.add({
            'plugin_name': p['name'],
            'id': t['id'],
            'displayName': t['displayName'] ?? t['id'],
            'description': t['description'] ?? '',
          });
        }
      }
    } catch (_) {}
    if (!mounted) return;
    if (templates.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.wfImportNoTemplates)),
      );
      return;
    }
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.wfImportTemplates),
        content: SizedBox(
          width: double.maxFinite,
          child: ListView.builder(
            shrinkWrap: true,
            itemCount: templates.length,
            itemBuilder: (_, i) {
              final t = templates[i];
              final desc = t['description'] as String? ?? '';
              return ListTile(
                dense: true,
                leading: const Icon(Icons.account_tree_outlined, size: 20),
                title: Text(t['displayName'] as String? ?? ''),
                subtitle: Text(
                  (desc.isEmpty ? '' : '$desc · ') + (t['plugin_name'] as String? ?? ''),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () async {
                  Navigator.pop(ctx);
                  await _confirmImport(t);
                },
              );
            },
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: Text(l10n.cancel),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmImport(Map<String, dynamic> t) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.wfImportConfirm(t['displayName'] as String? ?? '')),
        content: Text('${t['plugin_name']} · ${t['id']}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await ApiClient().importWorkflowTemplate(
          t['plugin_name'] as String, t['id'] as String);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.wfImportSuccess)),
      );
      _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('${l10n.wfImportTemplates}: $e')),
      );
    }
  }

  Future<void> _run(Map<String, dynamic> item) async {
    final graph = item['graph'] as Map<String, dynamic>?;
    final steps = (item['steps'] as List? ?? []).cast<Map>();
    final wfName = item['name'] as String? ?? '';
    final bool isGraph;
    final int stepCount;
    if (graph != null) {
      final nodes = (graph['nodes'] as List? ?? []).cast<Map>();
      if (nodes.isEmpty) return;
      isGraph = true;
      stepCount = nodes.length;
    } else {
      if (steps.isEmpty) return;
      isGraph = false;
      stepCount = steps.length;
    }
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('执行「$wfName」'),
        content: Text('共 $stepCount 步：点击“执行”后按顺序操作手机（敏感步骤需你确认）'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('执行')),
        ],
      ),
    );
    if (confirm != true || !mounted) return;
    final List<Map<String, dynamic>> results;
    if (isGraph) {
      final nodes = (graph!['nodes'] as List? ?? []).cast<Map>();
      final edges = (graph['edges'] as List? ?? []).cast<Map>();
      results = await PhonePerceptionService.executeWorkflowGraph(nodes, edges: edges);
    } else {
      results = await PhonePerceptionService.executeActionSequence(steps);
    }
    if (!mounted) return;
    final allOk = results.every((r) => r['ok'] == true);
    final summary = results
        .map((r) => '第${r['step']}步${r['ok'] == true ? '✓' : '✗'} ${r['message'] ?? ''}')
        .join('；');
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(allOk ? '工作流执行完成：$summary' : '工作流中断：$summary', maxLines: 3),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: const Text('手机操作工作流'),
        actions: [
          IconButton(
            tooltip: l10n.wfImportTemplates,
            icon: const Icon(Icons.file_download_outlined),
            onPressed: _showImportDialog,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _edit(),
        child: const Icon(Icons.add),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _items.isEmpty
              ? ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    const Text('还没有工作流。点右下角 + 新建：把常用手机操作编排成序列，之后对 AI 说“帮我执行 XX”即可。',
                        style: TextStyle(color: Colors.grey)),
                  ],
                )
              : ListView.builder(
                  itemCount: _items.length,
                  itemBuilder: (_, i) {
                    final item = _items[i];
                    final g = item['graph'] as Map<String, dynamic>?;
                    final steps = g != null
                        ? ((g['nodes'] as List?) ?? const []).length
                        : (item['steps'] as List? ?? []).length;
                    final desc = item['description'] as String? ?? '';
                    return ListTile(
                      title: Text(item['name'] as String? ?? ''),
                      subtitle: Text('${desc.isEmpty ? '' : '$desc · '}$steps 步'),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          IconButton(
                            icon: const Icon(Icons.play_arrow, color: Colors.green),
                            tooltip: '执行',
                            onPressed: () => _run(item),
                          ),
                          IconButton(
                            icon: const Icon(Icons.edit_outlined),
                            onPressed: () => _edit(item: item),
                          ),
                          IconButton(
                            icon: const Icon(Icons.delete_outline, color: Colors.red),
                            onPressed: () async {
                              await ApiClient().deleteWorkflow(item['id'] as int);
                              _load();
                            },
                          ),
                        ],
                      ),
                    );
                  },
                ),
    );
  }
}
