import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'life_home_page.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// AI 生活：时间线 + 产物库（均只读，按当前角色）。
class LifePane extends StatefulWidget {
  const LifePane({
    super.key,
    required this.character,
    required this.client,
    required this.rs,
    required this.cs,
  });

  final Character character;
  final AmbApiClient client;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<LifePane> createState() => _LifePaneState();
}

class _LifePaneState extends State<LifePane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderLifeEvent>> _timeline =
      ReaderLoader<List<ReaderLifeEvent>>();
  late final ReaderLoader<List<ReaderLifeArtifact>> _artifacts =
      ReaderLoader<List<ReaderLifeArtifact>>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _timeline.dispose();
    _artifacts.dispose();
    super.dispose();
  }

  void _openHome() {
    Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => LifeHomePage(
                client: widget.client,
                characterId: widget.character.id,
                rs: widget.rs)));
  }

  void _reload() {
    _timeline.load(
      (ct) => _api.getLifeTimeline(widget.character.id, cancelToken: ct),
      isEmpty: (items) => items.isEmpty,
    );
    _artifacts.load(
      (ct) => _api.getLifeArtifacts(widget.character.id, cancelToken: ct),
      isEmpty: (items) => items.isEmpty,
    );
  }

  String _time(String raw) {
    final dt = DateTime.tryParse(raw.endsWith('Z') ? raw : '${raw}Z');
    if (dt == null) return raw;
    final local = dt.toUtc().add(const Duration(hours: 8));
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}-${two(local.month)}-${two(local.day)} '
        '${two(local.hour)}:${two(local.minute)}';
  }

  Widget _timelineView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<ReaderLifeEvent>>(
      loader: _timeline,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final e in items) ...[
            const Divider(),
            Row(children: [
              Expanded(
                child: Text(_time(e.createdAt),
                    style: Theme.of(context).textTheme.bodySmall),
              ),
              if (e.subType.isNotEmpty)
                Chip(
                  label: Text(e.subType),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
            const SizedBox(height: 4),
            Text(e.content, style: const TextStyle(height: 1.7)),
          ],
        ],
      ),
    );
  }

  Widget _artifactsView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<ReaderLifeArtifact>>(
      loader: _artifacts,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final a in items) ...[
            const Divider(),
            Row(children: [
              Expanded(
                child: Text(
                  a.title.isEmpty ? rs.t('artifactUntitled') : a.title,
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ),
              if (a.type.isNotEmpty)
                Chip(
                  label: Text(a.type),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
            const SizedBox(height: 4),
            Text(a.contentText, style: const TextStyle(height: 1.7)),
            if (a.contentUrl != null && a.contentUrl!.isNotEmpty)
              Text(a.contentUrl!,
                  style: Theme.of(context).textTheme.bodySmall,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis),
          ],
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 8, 0),
          child: Row(children: [
            Expanded(
              child: Text('${widget.rs.t('life')} · ${widget.character.name}',
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            IconButton(
              tooltip: widget.rs.t('lifeHomeTitle'),
              icon: const Icon(Icons.home_outlined, size: 20),
              onPressed: _openHome,
            ),
            IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
          ]),
        ),
        TabBar(tabs: [
          Tab(text: widget.rs.t('lifeTimeline')),
          Tab(text: widget.rs.t('lifeArtifacts')),
        ]),
        Expanded(
          child: TabBarView(children: [_timelineView(), _artifactsView()]),
        ),
      ]),
    );
  }
}
