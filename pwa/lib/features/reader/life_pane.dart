import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import 'life_home_visual_page.dart';
import 'open_url.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// AI 生活（对齐 App LifeHomeScreen 三 Tab）：生活时间线 + 兴趣与目标 + 产物库。
/// 全部只读、按当前角色；右上角 home 打开小家状态页。
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

/// 兴趣与目标合并加载的结果（一次刷新同时取两份列表）。
class _LifeFocus {
  const _LifeFocus({required this.interests, required this.goals});

  final List<ReaderLifeInterest> interests;
  final List<ReaderLifeGoal> goals;

  bool get isEmpty => interests.isEmpty && goals.isEmpty;
}

class _LifePaneState extends State<LifePane> {
  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderLifeEvent>> _timeline =
      ReaderLoader<List<ReaderLifeEvent>>();
  late final ReaderLoader<_LifeFocus> _focus = ReaderLoader<_LifeFocus>();
  late final ReaderLoader<List<ReaderLifeArtifact>> _artifacts =
      ReaderLoader<List<ReaderLifeArtifact>>();
  late final ReaderLoader<List<ReaderLifeBrowsing>> _browsing =
      ReaderLoader<List<ReaderLifeBrowsing>>();

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _timeline.dispose();
    _focus.dispose();
    _artifacts.dispose();
    _browsing.dispose();
    super.dispose();
  }

  void _openHome() {
    Navigator.push(
        context,
        MaterialPageRoute(
            builder: (_) => LifeHomeVisualPage(
                client: widget.client,
                character: widget.character,
                rs: widget.rs,
                cs: widget.cs)));
  }

  void _reload() {
    _timeline.load(
      (ct) => _api.getLifeTimeline(widget.character.id, cancelToken: ct),
      isEmpty: (items) => items.isEmpty,
    );
    _focus.load(
      (ct) async {
        final results = await Future.wait([
          _api.getLifeInterests(widget.character.id, cancelToken: ct),
          _api.getLifeGoals(widget.character.id, cancelToken: ct),
        ]);
        return _LifeFocus(
          interests: results[0] as List<ReaderLifeInterest>,
          goals: results[1] as List<ReaderLifeGoal>,
        );
      },
      isEmpty: (f) => f.isEmpty,
    );
    _artifacts.load(
      (ct) => _api.getLifeArtifacts(widget.character.id, cancelToken: ct),
      isEmpty: (items) => items.isEmpty,
    );
    _browsing.load(
      (ct) => _api.getLifeBrowsing(widget.character.id, cancelToken: ct),
      isEmpty: (items) => items.isEmpty,
    );
  }

  String _time(String raw) {
    if (raw.isEmpty) return '';
    final dt = DateTime.tryParse(raw.endsWith('Z') ? raw : '${raw}Z');
    if (dt == null) return raw;
    final local = dt.toUtc().add(const Duration(hours: 8));
    String two(int v) => v.toString().padLeft(2, '0');
    return '${local.year}-${two(local.month)}-${two(local.day)} '
        '${two(local.hour)}:${two(local.minute)}';
  }

  /// 相对路径（/uploads/...）补全为服务器地址；http(s) 原样返回。
  String? _resolveUrl(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
    var base = widget.client.baseUrl;
    if (base.endsWith('/')) base = base.substring(0, base.length - 1);
    return raw.startsWith('/') ? '$base$raw' : '$base/$raw';
  }

  // ── 时间线 ─────────────────────────────────────────────

  Widget _timelineView() {
    return ReaderAsyncView<List<ReaderLifeEvent>>(
      loader: _timeline,
      rs: widget.rs,
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
                  label: Text(_timelineLabel(e.subType)),
                  visualDensity: VisualDensity.compact,
                ),
            ]),
            const SizedBox(height: 4),
            Text(e.content,
                style: const TextStyle(height: 1.7),
                maxLines: 3,
                overflow: TextOverflow.ellipsis),
          ],
        ],
      ),
    );
  }

  String _timelineLabel(String subType) {
    final rs = widget.rs;
    switch (subType) {
      case 'life_event':
        return rs.t('lifeSubLifeEvent');
      case 'reflection':
        return rs.t('lifeSubReflection');
      case 'note':
        return rs.t('lifeSubNote');
      case 'interest':
        return rs.t('lifeSubInterest');
      case 'goal':
        return rs.t('lifeSubGoal');
      default:
        return subType;
    }
  }

  // ── 兴趣与目标 ─────────────────────────────────────────

  /// 与 App 同口径：≥60 热爱（橙）/ ≥30 兴趣（绿）/ 其余（灰）。
  Color _levelColor(int level) {
    if (level >= 60) return const Color(0xFFF57C00);
    if (level >= 30) return const Color(0xFF2E7D32);
    return const Color(0xFF9E9E9E);
  }

  String _goalTypeLabel(String type) {
    final rs = widget.rs;
    switch (type) {
      case 'relationship':
        return rs.t('goalTypeRelationship');
      case 'creative':
        return rs.t('goalTypeCreative');
      case 'growth':
        return rs.t('goalTypeGrowth');
      case 'explore':
        return rs.t('goalTypeExplore');
      case 'skill':
        return rs.t('goalTypeSkill');
      default:
        return type;
    }
  }

  String _goalStatusLabel(String status) {
    final rs = widget.rs;
    switch (status) {
      case 'completed':
        return rs.t('goalStatusCompleted');
      case 'failed':
        return rs.t('goalStatusFailed');
      default:
        return rs.t('goalStatusActive');
    }
  }

  Widget _focusView() {
    final rs = widget.rs;
    return ReaderAsyncView<_LifeFocus>(
      loader: _focus,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (f) {
        final textTheme = Theme.of(context).textTheme;
        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(children: [
              Text(rs.t('lifeInterests'), style: textTheme.titleSmall),
              const SizedBox(width: 6),
              Text('${f.interests.length}',
                  style: textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.outline)),
            ]),
            const SizedBox(height: 8),
            if (f.interests.isEmpty)
              Text(rs.t('lifeNoInterests'),
                  style: textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.outline))
            else
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final i in f.interests)
                    Chip(
                      label: Text('${i.name} · ${i.level}'),
                      side: BorderSide(color: _levelColor(i.level)),
                      backgroundColor: _levelColor(i.level).withValues(alpha: 0.08),
                      visualDensity: VisualDensity.compact,
                    ),
                ],
              ),
            const SizedBox(height: 20),
            Row(children: [
              Text(rs.t('lifeGoals'), style: textTheme.titleSmall),
              const SizedBox(width: 6),
              Text('${f.goals.length}',
                  style: textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.outline)),
            ]),
            const SizedBox(height: 8),
            if (f.goals.isEmpty)
              Text(rs.t('lifeNoGoals'),
                  style: textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.outline))
            else
              for (final g in f.goals) ...[
                Card(
                  margin: const EdgeInsets.only(bottom: 10),
                  elevation: 0,
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(children: [
                          Expanded(
                            child: Text(
                              g.title.isEmpty ? rs.t('artifactUntitled') : g.title,
                              style: textTheme.titleSmall,
                            ),
                          ),
                          Chip(
                            label: Text(_goalTypeLabel(g.type)),
                            visualDensity: VisualDensity.compact,
                          ),
                          const SizedBox(width: 6),
                          Text(_goalStatusLabel(g.status),
                              style: textTheme.bodySmall),
                        ]),
                        if (g.description.isNotEmpty) ...[
                          const SizedBox(height: 6),
                          Text(g.description, style: const TextStyle(height: 1.6)),
                        ],
                        if (g.progressTotal > 0) ...[
                          const SizedBox(height: 10),
                          LinearProgressIndicator(
                            value: (g.progress / g.progressTotal).clamp(0.0, 1.0),
                          ),
                          const SizedBox(height: 4),
                          Text('${rs.t('lifeGoalProgress')} ${g.progress}/${g.progressTotal}',
                              style: textTheme.bodySmall),
                        ],
                      ],
                    ),
                  ),
                ),
              ],
          ],
        );
      },
    );
  }

  // ── 产物库 ─────────────────────────────────────────────

  String _artifactTypeLabel(String type) {
    final rs = widget.rs;
    switch (type) {
      case 'image':
        return rs.t('artifactImage');
      case 'note':
        return rs.t('artifactNote');
      default:
        return rs.t('artifactText');
    }
  }

  void _showImage(String url) {
    showDialog<void>(
      context: context,
      builder: (ctx) => Dialog(
        backgroundColor: Colors.black,
        insetPadding: const EdgeInsets.all(12),
        child: Stack(children: [
          Positioned.fill(
            child: InteractiveViewer(
              child: Center(child: Image.network(url, fit: BoxFit.contain)),
            ),
          ),
          Positioned(
            top: 4,
            left: 4,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white),
              onPressed: () => Navigator.pop(ctx),
            ),
          ),
        ]),
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
      dataBuilder: (items) {
        final textTheme = Theme.of(context).textTheme;
        return ListView.separated(
          padding: const EdgeInsets.all(16),
          itemCount: items.length,
          separatorBuilder: (_, __) => const SizedBox(height: 10),
          itemBuilder: (ctx, index) {
            final a = items[index];
            final url = _resolveUrl(a.contentUrl);
            return Card(
              margin: EdgeInsets.zero,
              elevation: 0,
              clipBehavior: Clip.antiAlias,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              child: InkWell(
                onTap: url == null ? null : () => _showImage(url),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(children: [
                        Chip(
                          label: Text(_artifactTypeLabel(a.type)),
                          visualDensity: VisualDensity.compact,
                        ),
                        const Spacer(),
                        Text(_time(a.createdAt), style: textTheme.bodySmall),
                      ]),
                      if (a.title.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text(a.title, style: textTheme.titleSmall),
                      ],
                      if (url != null) ...[
                        const SizedBox(height: 8),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(10),
                          child: Image.network(
                            url,
                            height: 170,
                            width: double.infinity,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              height: 170,
                              color: Theme.of(ctx).colorScheme.surface,
                              child: const Center(
                                  child: Icon(Icons.broken_image_outlined)),
                            ),
                          ),
                        ),
                      ],
                      if (a.contentText.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(a.contentText, style: const TextStyle(height: 1.6)),
                      ],
                    ],
                  ),
                ),
              ),
            );
          },
        );
      },
    );
  }

  // ── 浏览记录 ───────────────────────────────────────────

  String _duration(int sec) {
    if (sec <= 0) return '';
    if (sec < 60) return '$sec ${widget.rs.t('unitSec')}';
    return '${(sec / 60).round()} ${widget.rs.t('unitMin')}';
  }

  Widget _browsingView() {
    final rs = widget.rs;
    return ReaderAsyncView<List<ReaderLifeBrowsing>>(
      loader: _browsing,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) {
        final textTheme = Theme.of(context).textTheme;
        return ListView.separated(
          padding: const EdgeInsets.all(16),
          itemCount: items.length + 1,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (ctx, index) {
            if (index == 0) {
              return Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(rs.t('lifeBrowsingHint'),
                    style: textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.outline)),
              );
            }
            final b = items[index - 1];
            final title = b.title.isEmpty ? b.url : b.title;
            final dur = _duration(b.durationSec);
            return Card(
              margin: EdgeInsets.zero,
              elevation: 0,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              child: ListTile(
                leading: const Icon(Icons.public, size: 20),
                title: Text(title,
                    style: textTheme.titleSmall,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis),
                subtitle: Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Row(children: [
                    Chip(
                      label: Text(b.activityType == 'learn'
                          ? rs.t('activityLearn')
                          : rs.t('activityBrowse')),
                      visualDensity: VisualDensity.compact,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text('${_time(b.createdAt)}${dur.isEmpty ? '' : ' · $dur'}',
                          style: textTheme.bodySmall),
                    ),
                  ]),
                ),
                trailing: const Icon(Icons.open_in_new, size: 16),
                onTap: b.url.isEmpty ? null : () => openExternalUrl(b.url),
              ),
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 4,
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
        TabBar(
          isScrollable: true,
          tabs: [
            Tab(text: widget.rs.t('lifeTimeline')),
            Tab(text: widget.rs.t('lifeFocus')),
            Tab(text: widget.rs.t('lifeArtifacts')),
            Tab(text: widget.rs.t('lifeBrowsing')),
          ],
        ),
        Expanded(
          child: TabBarView(children: [
            _timelineView(),
            _focusView(),
            _artifactsView(),
            _browsingView(),
          ]),
        ),
      ]),
    );
  }
}
