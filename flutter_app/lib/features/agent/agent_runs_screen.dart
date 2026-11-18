// M6 SSE 增量渲染页：Agent 运行监控（真实页面验收路径见 docs/m6-ui-acceptance.md）。
import 'dart:convert';

import 'package:flutter/material.dart';

import '../../l10n/app_localizations.dart';
import '../../services/agent_run_sse_service.dart';
import '../../services/api_client.dart';
import '../../services/secure_token_store.dart';

class AgentRunsScreen extends StatefulWidget {
  const AgentRunsScreen({super.key});

  @override
  State<AgentRunsScreen> createState() => _AgentRunsScreenState();
}

class _AgentRunsScreenState extends State<AgentRunsScreen> {
  final _idCtrl = TextEditingController();
  final List<Map<String, dynamic>> _runs = [];
  final List<AgentRunEvent> _events = [];
  String? _selected;
  String _status = '';
  bool _streaming = false;
  int _reconnects = 0;
  Map<String, dynamic>? _coreStatus;

  @override
  void initState() {
    super.initState();
    _loadRuns();
    _loadCoreStatus();
  }

  Future<void> _loadCoreStatus() async {
    try {
      final r = await ApiClient().dio.get('/api/agent/tools/core-status');
      if (mounted) setState(() => _coreStatus = Map<String, dynamic>.from(r.data as Map));
    } catch (_) {
      if (mounted) setState(() => _coreStatus = {'configured': false});
    }
  }

  @override
  void dispose() {
    _idCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadRuns() async {
    try {
      final r = await ApiClient().dio.get('/api/agent/tools/runs');
      final items = (r.data is Map && r.data['runs'] is List)
          ? List<Map<String, dynamic>>.from(
              (r.data['runs'] as List).map((e) => Map<String, dynamic>.from(e)))
          : <Map<String, dynamic>>[];
      if (mounted) {
        setState(() {
          _runs
            ..clear()
            ..addAll(items);
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _status = tr(e));
      }
    }
  }

  String tr(Object e) => '\$e';

  Future<void> _streamRun(String runId) async {
    final token = await SecureTokenStore.instance.readToken();
    final base = ApiClient().dio.options.baseUrl;
    if (base.isEmpty || token.isEmpty) {
      setState(() => _status = 'missing server/token');
      return;
    }
    setState(() {
      _selected = runId;
      _events.clear();
      _reconnects = 0;
      _streaming = true;
      _status = 'connecting';
    });
    final client = AgentRunSseClient(baseUrl: base, token: token);
    try {
      await client.stream(
        runId,
        onEvent: (e) {
          if (!mounted) return;
          setState(() {
            _events.add(e);
            _status = 'streaming (offset ${e.offset})';
          });
        },
        onEof: () {
          if (!mounted) return;
          setState(() {
            _status = 'eof';
            _streaming = false;
          });
        },
        onProtocolError: (code, data) {
          if (!mounted) return;
          setState(() {
            _status = '$code ${jsonEncode(data)}';
            _streaming = false;
          });
        },
      );
    } catch (e) {
      _reconnects++;
      if (mounted) {
        setState(() {
          _status = 'reconnect failed ($_reconnects): $e';
          _streaming = false;
        });
      }
    }
  }

  String _coreStatusLabel(AppLocalizations l10n) {
    final s = _coreStatus ?? {};
    if (s['configured'] != true) return l10n.coreStatusUnconfigured;
    final ok = s['online'] == true;
    if (!ok) return l10n.coreStatusOffline;
    return l10n.coreStatusOnline((s['tools'] as num?)?.toInt() ?? 0,
        (s['expected'] as num?)?.toInt() ?? 0);
  }

  Future<void> _createRun() async {
    final l10n = AppLocalizations.of(context)!;
    final taskCtrl = TextEditingController();
    final deadlineCtrl = TextEditingController(text: '30');
    final created = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.agentRunsCreate),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: taskCtrl,
              decoration: InputDecoration(hintText: l10n.agentRunsTaskHint),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: deadlineCtrl,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(labelText: l10n.agentRunsDeadline),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text(l10n.cancel)),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, 'ok'),
            child: Text(l10n.agentRunsCreateConfirm),
          ),
        ],
      ),
    );
    if (created != 'ok') return;
    try {
      final r = await ApiClient().dio.post('/api/agent/tools/runs', data: {
        'task': taskCtrl.text.trim(),
        'deadline_s': double.tryParse(deadlineCtrl.text.trim()) ?? 30,
      });
      final rid = r.data is Map ? '${r.data['run_id'] ?? ''}' : '';
      await _loadRuns();
      if (rid.isNotEmpty) _streamRun(rid);
    } catch (e) {
      setState(() => _status = '$e');
    }
  }

  Future<void> _cancelRun(String runId) async {
    try {
      await ApiClient().dio.post('/api/agent/tools/runs/$runId/cancel');
      await _loadRuns();
    } catch (e) {
      setState(() => _status = '$e');
    }
  }

  Future<void> _showAudit(String runId) async {
    try {
      final r = await ApiClient()
          .dio
          .get('/api/agent/tools/audit', queryParameters: {'run_id': runId});
      final items = (r.data is Map && r.data['items'] is List)
          ? (r.data['items'] as List)
          : const [];
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(AppLocalizations.of(context)!.agentRunsAudit),
          content: SizedBox(
            width: 520,
            height: 360,
            child: ListView(
              children: [
                for (final it in items)
                  ListTile(
                    dense: true,
                    title: Text('${it['tool']} · ${it['status']}'),
                    subtitle: Text('${it['steps']}'.length > 160
                        ? '${it['steps']}'.substring(0, 160)
                        : '${it['steps']}'),
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(AppLocalizations.of(context)!.cancel)),
          ],
        ),
      );
    } catch (e) {
      setState(() => _status = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.agentRunsTitle),
        actions: [
          if (_coreStatus != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Center(
                child: Text(
                  _coreStatusLabel(l10n),
                  style: const TextStyle(fontSize: 11),
                ),
              ),
            ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: l10n.agentRunsRefresh,
            onPressed: () { _loadRuns(); _loadCoreStatus(); },
          ),
          IconButton(
            icon: const Icon(Icons.add),
            tooltip: l10n.agentRunsCreate,
            onPressed: _createRun,
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _idCtrl,
                    decoration: InputDecoration(hintText: l10n.agentRunsIdHint),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: () {
                    final id = _idCtrl.text.trim();
                    if (id.isNotEmpty) _streamRun(id);
                  },
                  child: Text(l10n.agentRunsConnect),
                ),
                IconButton(
                  onPressed: _loadRuns,
                  icon: const Icon(Icons.refresh),
                  tooltip: l10n.agentRunsRefresh,
                ),
              ],
            ),
          ),
          if (_status.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(_status, style: const TextStyle(fontSize: 12)),
              ),
            ),
          Expanded(
            child: _selected == null
                ? ListView(
                    children: [
                      if (_runs.isEmpty)
                        ListTile(title: Text(l10n.agentRunsNoRuns)),
                      for (final r in _runs)
                        ListTile(
                          title: Text('${r['run_id']} · ${r['status']}'),
                          subtitle: Text('events: ${r['events']}'),
                          trailing: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              IconButton(
                                icon: const Icon(Icons.play_arrow),
                                tooltip: l10n.agentRunsConnect,
                                onPressed: () =>
                                    _streamRun('${r['run_id']}'),
                              ),
                              IconButton(
                                icon: const Icon(Icons.stop),
                                tooltip: l10n.agentRunsCancel,
                                onPressed: () =>
                                    _cancelRun('${r['run_id']}'),
                              ),
                            ],
                          ),
                          onTap: () => _streamRun('${r['run_id']}'),
                        ),
                    ],
                  )
                : Column(
                    children: [
                      ListTile(
                        title: Text(_selected!),
                        subtitle: Text(l10n.agentRunsStatus('$_streaming')),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            TextButton(
                              onPressed: _streaming
                                  ? null
                                  : () => _cancelRun(_selected!),
                              child: Text(l10n.agentRunsCancel),
                            ),
                            TextButton(
                              onPressed: () => _showAudit(_selected!),
                              child: Text(l10n.agentRunsAudit),
                            ),
                          ],
                        ),
                      ),
                      const Divider(height: 1),
                      Expanded(
                        child: ListView.builder(
                          itemCount: _events.length,
                          itemBuilder: (ctx, i) {
                            final e = _events[i];
                            return ListTile(
                              dense: true,
                              leading: Text('${e.offset ?? ''}'),
                              title: Text(e.type),
                              subtitle: Text(jsonEncode(e.data).length > 180
                                  ? jsonEncode(e.data).substring(0, 180)
                                  : jsonEncode(e.data)),
                            );
                          },
                        ),
                      ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}
