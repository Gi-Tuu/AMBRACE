// 织库 · 无限画布（Phase B/C，2026-08-12；2.5D 加强 2026-08-23；渲染接口抽象 2026-08-24）
//
// 2026-08-24（织网 3D P0）：把渲染与非渲染逻辑解耦——
// - 布局/旋转/缩放/命中/聚类泡 → WeaveSceneController（weave_scene_controller.dart，纯逻辑）；
// - 2.5D CustomPaint 画布 → WeaveSceneView2D（weave_scene_view.dart，行为与旧版一致，作降级）；
// - 3D 球（flutter_scene）→ WeaveSceneView3D（新增）；
// - 本页只保留：数据加载、维度筛选、详情弹层、loading/error 与「按 weave_3d flag 选视图」。
//
// 2026-08-24（织网 3D 低端机体验修复）：3D 分档降级 full→light→2D，本页新增 onDegradeToLight 回调
// （降到 light 时提示「已切换轻量模式」，不切 2.5D）；持续低于 light 档阈值仍走 onFallbackTo2D 切回 2.5D。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../services/feature_flag_service.dart';
import '../../widgets/weave_detail_sheet.dart';
import '../../utils/sphere_projection.dart';
import "package:ai_companion/theme/tokens.dart";
import 'weave_scene_controller.dart';
import 'weave_scene_view.dart';
import 'weave_view_mode.dart';
import '../../providers/settings_provider.dart';
import 'package:provider/provider.dart';

class WeaveCanvasScreen extends StatefulWidget {
  final int? initialCharacterId;
  /// 织库双域（2026-08-12）：shared=全·织库 / private=私·织库
  final String domain;

  const WeaveCanvasScreen({super.key, this.initialCharacterId, this.domain = 'shared'});

  @override
  State<WeaveCanvasScreen> createState() => _WeaveCanvasScreenState();
}

class _CharacterMeta {
  final int id;
  final String name;

  _CharacterMeta(this.id, this.name);
}

class _WeaveCanvasScreenState extends State<WeaveCanvasScreen> {
  final ApiClient _api = ApiClient();

  List<WeaveSceneNode> _allNodes = [];
  List<WeaveSceneEdge> _edges = [];
  List<_CharacterMeta> _characters = [];
  bool _loading = true;
  String? _error;

  /// 渲染层逻辑控制器（纯逻辑，2D/3D 视图共用）。
  final WeaveSceneController _controller = WeaveSceneController();

  // 维度筛选：时间（all/7d/30d）、角色（null=全部）、心情（null=全部）
  String _timeFilter = 'all';
  int? _charFilter;
  String? _moodFilter;
  /// 私域增强：生活类型筛选（null=全部）
  String? _lifeTypeFilter;

  /// 3D 试图初始化/渲染异常或持续低帧率时回退 2.5D（本地状态，不进后端 flag）。
  bool _force2D = false;

  /// 手动渲染档位模式（全自动/3D 全量/3D 轻量/2.5D）；默认 auto（全自动）。
  /// 用户手选优先；自动降级检测只在 auto 生效。
  WeaveViewMode _viewMode = WeaveViewMode.auto;

  @override
  void initState() {
    super.initState();
    _charFilter = widget.initialCharacterId;
    // 读取持久化的手动档位（App 重启保持；首次默认 auto=全自动）。
    _viewMode = context.read<SettingsProvider>().weaveViewMode;
    // 监听运行时 flag：weave_3d 切换时重选 2D/3D 视图
    FeatureFlagService.instance.addListener(_onFeatureFlagsChanged);
    _loadGraph();
  }

  @override
  void dispose() {
    FeatureFlagService.instance.removeListener(_onFeatureFlagsChanged);
    _controller.dispose();
    super.dispose();
  }

  void _onFeatureFlagsChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _loadGraph() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await _api.getWeaveGraph(domain: widget.domain);
      if (!mounted) return;
      final nodesJson = (data['nodes'] as List?) ?? const [];
      final edgesJson = (data['edges'] as List?) ?? const [];
      final charsJson = (data['characters'] as List?) ?? const [];
      final pts = fibonacciSphere(nodesJson.length);
      final nodes = <WeaveSceneNode>[];
      for (var i = 0; i < nodesJson.length; i++) {
        final j = nodesJson[i] as Map<String, dynamic>;
        final cid = j['character_id'] as int? ?? 0;
        final cids =
            (j['character_ids'] as List?)?.map((e) => e as int).toList();
        nodes.add(
          WeaveSceneNode(
            id: j['id'] as int,
            characterId: cid,
            characterIds: (cids != null && cids.isNotEmpty) ? cids : [cid],
            characterName: j['character_name'] as String? ?? '',
            title: j['title'] as String? ?? '',
            summary: j['summary'] as String? ?? '',
            importance: (j['importance'] as num?)?.toDouble() ?? 0,
            mood: j['mood'] as String? ?? '',
            createdAt: DateTime.tryParse(j['created_at']?.toString() ?? ''),
            lat: pts[i].lat,
            lon: pts[i].lon,
            lifeType: j['life_type'] as String? ?? '',
            hotTags: (j['hot_tags'] as List?)?.map((e) => e.toString()).toList() ?? const [],
          ),
        );
      }
      final edges = <WeaveSceneEdge>[
        for (final e in edgesJson)
          WeaveSceneEdge(
            source: (e as Map<String, dynamic>)['source'] as int,
            target: e['target'] as int,
            strength: (e['strength'] as num?)?.toDouble() ?? 0,
          ),
      ];
      final characters = <_CharacterMeta>[
        for (final c in charsJson)
          _CharacterMeta(
            (c as Map<String, dynamic>)['id'] as int,
            c['name'] as String? ?? AppLocalizations.of(context)!.memorySourceCharacter,
          ),
      ];
      if (!mounted) return;
      setState(() {
        _allNodes = nodes;
        _edges = edges;
        _characters = characters;
        _loading = false;
      });
      _controller.setGraph(nodes: _filteredNodes(), edges: _edges);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.weaveLoadFail;
        _loading = false;
      });
    }
  }

  /// 当前筛选后的节点（时间/角色/心情维度）
  List<WeaveSceneNode> _filteredNodes() {
    final now = DateTime.now().toUtc();
    return _allNodes.where((n) {
      if (_charFilter != null && !n.characterIds.contains(_charFilter)) {
        return false;
      }
      if (_moodFilter != null && n.mood.trim() != _moodFilter) {
        return false;
      }
      if (_timeFilter != 'all' && n.createdAt != null) {
        final days = now.difference(n.createdAt!).inDays;
        final limit = _timeFilter == '7d' ? 7 : 30;
        if (days > limit) return false;
      }
      if (_lifeTypeFilter != null && n.lifeType != _lifeTypeFilter) {
        return false;
      }
      return true;
    }).toList();
  }

  void _applyFilters() {
    _controller.setNodes(_filteredNodes());
  }

  List<String> _distinctMoods() {
    final set = <String>{};
    for (final n in _allNodes) {
      final m = n.mood.trim();
      if (m.isNotEmpty && m != '不详') set.add(m);
    }
    final list = set.toList()..sort();
    return list;
  }

  Future<void> _openNode(int nodeId) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final detail = await _api.getWeaveCardDetail(nodeId);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (_) => WeaveDetailSheet(
          card: detail,
          onDelete: () async {
            try {
              await _api.deleteWeaveCard(nodeId);
              if (mounted) await _loadGraph();
            } catch (_) {
              if (mounted) {
                ScaffoldMessenger.of(context)
                    .showSnackBar(SnackBar(content: Text(l10n.deleteFail)));
              }
            }
          },
        ),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.weaveDetailLoadFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        centerTitle: true,
        title: Text(
          l10n.weaveCanvasTitle,
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
        ),
        actions: [
          IconButton(
            tooltip: l10n.refresh,
            icon: const Icon(Icons.refresh, color: AppColors.accent),
            onPressed: _loadGraph,
          ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    final l10n = AppLocalizations.of(context)!;
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              _error!,
              style: const TextStyle(fontSize: 14, color: AppColors.textSecondary),
            ),
            TextButton(onPressed: _loadGraph, child: Text(l10n.retry)),
          ],
        ),
      );
    }
    if (_allNodes.isEmpty) {
      return Center(
        child: Text(
          l10n.weaveNoCards,
          style: const TextStyle(fontSize: 14, color: AppColors.textSecondary),
        ),
      );
    }
    return Column(
      children: [
        _buildModeBar(),
        _buildFilterBar(),
        Expanded(child: _buildCanvas()),
      ],
    );
  }

  /// 按 weave_3d 运行时 flag 选择 2D（降级）/3D 视图；已在 3D 且被自动降级时强切 2D。
  Widget _buildCanvas() {
    // 手动档位优先：auto 按 weave_3d flag（且排除自动降级 2D 的 force2D）；full3d/light3d 强制 3D；
    // twoD 强制 2.5D（见 shouldUse3DView）。mode 变化用 ValueKey 重建 3D 视图（重新暖机/重开检测）。
    final use3D = shouldUse3DView(
      _viewMode,
      weave3dFlag: FeatureFlagService.instance.isEnabled('weave_3d'),
      force2D: _force2D,
    );
    if (use3D) {
      return WeaveSceneView3D(
        key: ValueKey('weave3d:${_viewMode.name}'),
        controller: _controller,
        mode: _viewMode,
        onCardTap: _openNode,
        onFallbackTo2D: _onFallbackTo2D,
        onDegradeToLight: _onDegradeToLight,
      );
    }
    return WeaveSceneView2D(
      key: const ValueKey('weave2d'),
      controller: _controller,
      onCardTap: _openNode,
    );
  }

  /// 3D 视图回退 2.5D：切到 2D 视图并提示（一次性降级，避免频繁抖动）。
  /// SnackBar 带上具体 [reason]（渲染异常/持续低帧率/节点数超限），方便真机反馈定位问题根因。
  void _onFallbackTo2D(WeaveFallbackReason reason) {
    if (!mounted) return;
    setState(() => _force2D = true);
    final l10n = AppLocalizations.of(context)!;
    final message = switch (reason) {
      WeaveFallbackReason.renderError => l10n.weaveFallback2DRenderError,
      WeaveFallbackReason.lowFps => l10n.weaveFallback2DLowFps,
      WeaveFallbackReason.nodesExceed => l10n.weaveFallback2DNodeLimit,
    };
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  /// 3D full 档持续低帧率降到 light 档（3D 简化渲染）：提示「已切换轻量模式」但不切 2.5D 视图。
  void _onDegradeToLight() {
    if (!mounted) return;
    final l10n = AppLocalizations.of(context)!;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(l10n.weaveSwitchedToLight)));
  }

  bool get _isPrivate => widget.domain == 'private';

  /// 切换手动档位：立即 setState 重建视图（即时生效），并持久化用户选择（App 重启保持）。
  /// 手动模式与自动降级检测互斥；切回 auto 时 3D 视图经 ValueKey 重建，重新启用降级检测并从头暖机。
  void _setViewMode(WeaveViewMode mode) {
    if (mode == _viewMode) return;
    setState(() {
      _viewMode = mode;
      // 用户显式切换档位：清空自动降级到 2D 的状态，切回 auto 时重新启用监测并从头暖机。
      _force2D = false;
    });
    context.read<SettingsProvider>().setWeaveViewMode(mode);
  }

  /// 顶部手动档位选择器（SegmentedButton 四选一：全自动/3D 全量/3D 轻量/2.5D）。
  /// 放在维度筛选条上方一行；横向可滚动，小屏不溢出。切换即时生效（重建视图）。
  Widget _buildModeBar() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 10),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: SegmentedButton<WeaveViewMode>(
          showSelectedIcon: false,
          style: const ButtonStyle(
            visualDensity: VisualDensity.compact,
            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          ),
          segments: [
            ButtonSegment(value: WeaveViewMode.auto, label: Text(l10n.weaveModeAuto)),
            ButtonSegment(value: WeaveViewMode.full3d, label: Text(l10n.weaveModeFull3D)),
            ButtonSegment(value: WeaveViewMode.light3d, label: Text(l10n.weaveModeLight3D)),
            ButtonSegment(value: WeaveViewMode.twoD, label: Text(l10n.weaveMode2D)),
          ],
          selected: {_viewMode},
          onSelectionChanged: (s) {
            if (s.isNotEmpty) _setViewMode(s.first);
          },
        ),
      ),
    );
  }

  Widget _buildFilterBar() {
    final l10n = AppLocalizations.of(context)!;
    final moods = _distinctMoods();
    final showCharDivider = _characters.isNotEmpty;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        child: Row(
          children: [
            _chip(l10n.emotionAll, _timeFilter == 'all',
                () => setState(() => _timeFilter = 'all')),
            _chip(l10n.weaveNear7Days, _timeFilter == '7d',
                () => setState(() => _timeFilter = '7d')),
            _chip(l10n.weaveNear30Days, _timeFilter == '30d',
                () => setState(() => _timeFilter = '30d')),
            _separator(),
            _chip(l10n.weaveAllCharacters, _charFilter == null,
                () => setState(() => _charFilter = null)),
            for (final c in _characters)
              _chip(c.name, _charFilter == c.id,
                  () => setState(() => _charFilter = c.id)),
            if (showCharDivider) _separator(),
            _chip(l10n.weaveAllMoods, _moodFilter == null,
                () => setState(() => _moodFilter = null)),
            for (final m in moods)
              _chip(m, _moodFilter == m, () => setState(() => _moodFilter = m)),
            if (_isPrivate) ...[
              _separator(),
              _chip(l10n.weaveAllTypes, _lifeTypeFilter == null,
                  () => setState(() => _lifeTypeFilter = null)),
              _chip(l10n.lifeTypeLife, _lifeTypeFilter == 'life_event',
                  () => setState(() => _lifeTypeFilter = 'life_event')),
              _chip(l10n.lifeTypeReflection, _lifeTypeFilter == 'reflection',
                  () => setState(() => _lifeTypeFilter = 'reflection')),
              _chip(l10n.artifactNote, _lifeTypeFilter == 'note',
                  () => setState(() => _lifeTypeFilter = 'note')),
            ],
          ],
        ),
      ),
    );
  }

  Widget _separator() {
    return Container(
      width: 1,
      height: 16,
      margin: const EdgeInsets.symmetric(horizontal: 8),
      color: const Color(0xFFE5E5EA),
    );
  }

  Widget _chip(String label, bool selected, VoidCallback onTap) {
    return Padding(
      padding: const EdgeInsets.only(right: 6),
      child: ChoiceChip(
        label: Text(label,
            style: TextStyle(
                fontSize: 11.5,
                color: selected ? Colors.white : AppColors.textGray)),
        selected: selected,
        onSelected: (_) {
          onTap();
          _applyFilters();
        },
        showCheckmark: false,
        visualDensity: VisualDensity.compact,
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
        backgroundColor: AppColors.bgLight,
        selectedColor: AppColors.accent,
        side: BorderSide.none,
      ),
    );
  }
}
