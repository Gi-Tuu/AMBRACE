import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
// 上面两条 ignore 的理由：SSE 事件流走 dart:html EventSource——M6 契约本就是
// 为浏览器 cookie-ticket 设计的；package:web 迁移随后续批次处理。
import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'events_transport.dart';
import 'reader_state.dart';

/// Agent 运行面板（2026-11-22 批 7）：列表＋新建运行＋取消＋SSE 事件流。
///
/// 事件流 = `POST /{id}/stream-ticket`（HttpOnly cookie）→ 浏览器
/// `EventSource` 直连 `GET /{id}/events`（同源 cookie 自动随发；Last-Event-ID
/// 续传由服务端 offset 语义承担）。dart:html 仅在本文件使用（Web 目标）。
class AgentRunsPane extends StatefulWidget {
  const AgentRunsPane({
    super.key,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<AgentRunsPane> createState() => _AgentRunsPaneState();
}

class _AgentRunsPaneState extends State<AgentRunsPane> {
  late final AgentApi _api = AgentApi(widget.client);
  late final ReaderLoader<AgentRunsSnapshot> _loader =
      ReaderLoader<AgentRunsSnapshot>();
  bool _creating = false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _loader.dispose();
    super.dispose();
  }

  void _reload() {
    _loader.load((ct) => _api.runs(cancelToken: ct),
        isEmpty: (s) => s.runs.isEmpty);
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _createSheet() async {
    final ctrl = TextEditingController();
    final ok = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (sheetCtx) => SafeArea(
        child: Padding(
          padding: EdgeInsets.only(
              left: 16,
              right: 16,
              top: 12,
              bottom: MediaQuery.of(sheetCtx).viewInsets.bottom + 12),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(widget.rs.t('runsCreate'),
                style: Theme.of(sheetCtx).textTheme.titleMedium),
            const SizedBox(height: 10),
            TextField(
              controller: ctrl,
              maxLines: 3,
              autofocus: true,
              decoration: InputDecoration(
                  labelText: widget.rs.t('runsTaskHint'),
                  border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10))),
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: () => Navigator.pop(sheetCtx, true),
                icon: const Icon(Icons.play_arrow, size: 18),
                label: Text(widget.rs.t('runsCreate')),
              ),
            ),
          ]),
        ),
      ),
    );
    ctrl.dispose();
    if (ok != true) return;
    final task = ctrl.text;
    setState(() => _creating = true);
    try {
      await _api.createRun(task: task);
      _reload();
    } catch (e) {
      _toast('${widget.rs.t('runsCreateFail')}：$e');
    } finally {
      if (mounted) setState(() => _creating = false);
    }
  }

  Future<void> _cancelRun(String runId) async {
    try {
      await _api.cancelRun(runId);
      _reload();
    } catch (e) {
      _toast('${widget.rs.t('runsCancelFail')}：$e');
    }
  }

  /// SSE 事件流（浏览器 EventSource；HttpOnly ticket cookie 同源自动随发）。
  Future<void> _eventsSheet(String runId) async {
    final lines = <String>[];
    RunEventStream? stream;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheetCtx) => StatefulBuilder(
        builder: (ctx, setSheet) {
          void append(String line) {
            setSheet(() {
              lines.add(line);
              if (lines.length > 200) lines.removeRange(0, lines.length - 200);
            });
          }

          if (stream == null) {
            append(widget.rs.t('runsEventsConn'));
            () async {
              try {
                await _api.streamTicket(runId);
                stream = createRunEventStream();
                stream!.start(runId, (line) {
                  append(line);
                  if (line.contains('"type":"run_terminal"')) {
                    append(widget.rs.t('runsEventsClosed'));
                    stream?.close();
                  }
                });
              } catch (e) {
                append('${widget.rs.t('runsEventsClosed')}：$e');
              }
            }();
          }
          return SafeArea(
            child: SizedBox(
              height: MediaQuery.of(ctx).size.height * 0.7,
              child: Column(children: [
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.stream, size: 18),
                  title: Text(widget.rs.t('runsEventsTitle')),
                  trailing: IconButton(
                    icon: const Icon(Icons.close, size: 18),
                    onPressed: () {
                      stream?.close();
                      Navigator.pop(sheetCtx);
                    },
                  ),
                ),
                const Divider(height: 1),
                Expanded(
                  child: lines.isEmpty
                      ? const SizedBox.shrink()
                      : ListView(
                          padding: const EdgeInsets.all(10),
                          children: [
                            for (final l in lines.reversed)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 4),
                                child: SelectableText(l,
                                    style: Theme.of(ctx)
                                        .textTheme
                                        .bodySmall
                                        ?.copyWith(fontFamily: 'monospace')),
                              ),
                          ],
                        ),
                ),
              ]),
            ),
          );
        },
      ),
    );
    stream?.close();
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return ReaderAsyncView<AgentRunsSnapshot>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (snapshot) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Row(children: [
            Expanded(
              child: Text(rs.t('agentRuns'),
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(
              tooltip: rs.t('runsCreate'),
              onPressed: _creating ? null : _createSheet,
              icon: const Icon(Icons.add_circle_outline),
            ),
            IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          ]),
          Text(
            '${rs.t('agentActive')}: ${snapshot.active} / ${snapshot.limit}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          for (final run in snapshot.runs)
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                leading: Icon(run.status == 'running'
                    ? Icons.play_circle_outline
                    : Icons.circle_outlined),
                title: Text(run.runId),
                subtitle: Text('${rs.t('agentEvents')}: ${run.events}'),
                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                  IconButton(
                    tooltip: rs.t('runsEventsTitle'),
                    icon: const Icon(Icons.stream, size: 20),
                    onPressed: () => _eventsSheet(run.runId),
                  ),
                  if (run.status == 'running' || run.status == 'starting')
                    IconButton(
                      tooltip: rs.t('runsCancel'),
                      icon: const Icon(Icons.stop_circle_outlined, size: 20),
                      onPressed: () => _cancelRun(run.runId),
                    ),
                  Text(run.status, style: theme.textTheme.labelSmall),
                ]),
              ),
            ),
        ],
      ),
    );
  }
}
