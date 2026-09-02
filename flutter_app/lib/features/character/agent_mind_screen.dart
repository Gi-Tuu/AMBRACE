import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../services/api_client.dart";
import "../../theme/aurora_tokens.dart";
import "../../widgets/aurora_card.dart";
import "../../widgets/empty_state.dart";
import "../../widgets/ios_card_group.dart";

/// AI 内心世界（Phase J/P1，2026-08-16）：最近复盘 + 任务记录 + 工具使用轨迹
/// Aurora P6：玻璃顶栏 + 统计三卡 + 竖向时间线 + AuroraCard 分组。
class AgentMindScreen extends StatefulWidget {
  const AgentMindScreen({super.key, required this.characterId, required this.characterName});

  final int characterId;
  final String characterName;

  @override
  State<AgentMindScreen> createState() => _AgentMindScreenState();
}

class _AgentMindScreenState extends State<AgentMindScreen> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _data;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final data = await ApiClient().getAgentMind(widget.characterId);
      if (!mounted) return;
      setState(() { _data = data; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() { _error = "$e"; _loading = false; });
    }
  }

  String _fmtTime(String? iso) {
    if (iso == null || iso.isEmpty) return "";
    try {
      final t = DateTime.parse(iso).toLocal();
      return "${t.year}-${t.month.toString().padLeft(2, "0")}-${t.day.toString().padLeft(2, "0")} "
          "${t.hour.toString().padLeft(2, "0")}:${t.minute.toString().padLeft(2, "0")}";
    } catch (_) {
      return "";
    }
  }

  /// 记忆召回明细副标题：命中候选数 / 返回条数（P2-4 语义修正）+ 时间 + 耗时。
  /// 旧日志仅有 hit_count（即原返回条数）时回退显示「N 条」。
  String _hitCountLabel(Map item, AppLocalizations l10n) {
    final hit = (item["hit_count"] as num?)?.toInt() ?? 0;
    final returned = (item["returned"] as num?)?.toInt() ?? hit;
    final core = (returned != hit)
        ? l10n.agentMindRetrievalHitReturn(hit, returned)
        : l10n.agentMindRetrievalCount(hit);
    final time = _fmtTime(item["created_at"] as String?);
    final ms = item["latency_ms"] ?? 0;
    final suffix = [
      if (time.isNotEmpty) time,
      '${ms}ms',
    ].join(" · ");
    return suffix.isEmpty ? core : "$core · $suffix";
  }

  /// Aurora P6 段头（时间线分区用，标题视觉保留）
  Widget _sectionHeader(String title) {
    return Padding(
      padding: const EdgeInsets.only(left: 16, bottom: 6),
      child: Text(title,
          style: const TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: IosCardColors.subtitle)),
    );
  }

  /// Aurora P6 分组：AuroraCard 版 IosCardGroup（标题视觉保留）
  Widget _auroraGroup({required String title, required List<Widget> children}) {
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 16, bottom: 6),
            child: Text(title,
                style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: IosCardColors.subtitle)),
          ),
          AuroraCard(
            padding: EdgeInsets.zero,
            child: Material(
              type: MaterialType.transparency,
              child: Column(children: children),
            ),
          ),
        ],
      ),
    );
  }

  /// Aurora P6 竖向时间线条目：左侧主题色圆点 + 渐变短线，右侧 AuroraCard 内容
  Widget _timelineItem(BuildContext context, {required Widget child, required bool last}) {
    final scheme = Theme.of(context).colorScheme;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 24,
            child: Column(
              children: [
                const SizedBox(height: 14),
                Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                      color: scheme.primary, shape: BoxShape.circle),
                ),
                if (!last)
                  Expanded(
                    child: Container(
                      width: 2,
                      margin: const EdgeInsets.symmetric(vertical: 4),
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(1),
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [
                            scheme.primary.withValues(alpha: 0.55),
                            scheme.primary.withValues(alpha: 0.05),
                          ],
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: AuroraCard(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: child,
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Aurora P6 统计数据卡：数字 24px w700 主题色 + 标签
  Widget _statCard(BuildContext context, String label, String value) {
    final scheme = Theme.of(context).colorScheme;
    return Expanded(
      child: AuroraCard(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
        child: Column(
          children: [
            Text(value,
                style: TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w700,
                    color: scheme.primary,
                    height: 1)),
            const SizedBox(height: 6),
            Text(label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style:
                    TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return DefaultTabController(
      length: 5,
      child: Scaffold(
        appBar: AppBar(
          // Aurora P6 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
          backgroundColor: isDark
              ? Colors.black.withValues(alpha: 0.30)
              : Colors.white.withValues(alpha: 0.55),
          elevation: 0,
          scrolledUnderElevation: 0,
          surfaceTintColor: Colors.transparent,
          shape: Border(
            bottom: BorderSide(
              color: isDark
                  ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                  : Colors.black.withValues(alpha: AppGlass.borderAlpha),
              width: 0.5,
            ),
          ),
          title: Text(l10n.agentMind),
          centerTitle: false,
          bottom: TabBar(
            tabs: [
              Tab(text: l10n.agentMindMemorySearch),
              Tab(text: l10n.agentMindRunningNotes),
              Tab(text: l10n.agentMindReflection),
              Tab(text: l10n.agentMindTasks),
              Tab(text: l10n.agentMindToolLogs),
            ],
          ),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                // Aurora P6：错误态 EmptyState + 重试
                ? ListView(
                    physics: const AlwaysScrollableScrollPhysics(),
                    children: [
                      SizedBox(height: MediaQuery.of(context).size.height * 0.22),
                      EmptyState(
                        icon: Icons.cloud_off_rounded,
                        title: l10n.loadFailed,
                        action: OutlinedButton(onPressed: _load, child: Text(l10n.retry)),
                      ),
                    ],
                  )
                : RefreshIndicator(
                    onRefresh: _load,
                    child: TabBarView(
                      children: [
                        ListView(
                          padding: const EdgeInsets.only(top: 8, bottom: 24),
                          children: [_buildMemorySearch(l10n, scheme)],
                        ),
                        ListView(
                          padding: const EdgeInsets.only(top: 8, bottom: 24),
                          children: [_buildRunningNotes(l10n, scheme)],
                        ),
                        ListView(
                          padding: const EdgeInsets.only(top: 8, bottom: 24),
                          children: [_buildReflection(l10n, scheme)],
                        ),
                        ListView(
                          padding: const EdgeInsets.only(top: 8, bottom: 24),
                          children: [_buildTasks(l10n, scheme)],
                        ),
                        ListView(
                          padding: const EdgeInsets.only(top: 8, bottom: 24),
                          children: [_buildToolLogs(l10n, scheme)],
                        ),
                      ],
                    ),
                  ),
      ),
    );
  }

  Widget _buildReflection(AppLocalizations l10n, ColorScheme scheme) {
    final refl = (_data?["reflection"] as Map?)?.cast<String, dynamic>();
    final content = (refl?["content"] as String?)?.trim() ?? "";
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _sectionHeader(l10n.agentMindReflection),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: _timelineItem(
        context,
        last: true,
        child: content.isEmpty
            ? Text(l10n.agentMindEmpty,
                style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle))
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(content,
                      style: TextStyle(
                          fontSize: 14, height: 1.6, color: scheme.onSurface)),
                  if (_fmtTime(refl?["created_at"] as String?).isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(_fmtTime(refl?["created_at"] as String?),
                          style: const TextStyle(
                              fontSize: 11, color: IosCardColors.subtitle)),
                    ),
                ],
              ),
          ),
        ),
      ],
    );
  }

  Widget _buildMemorySearch(AppLocalizations l10n, ColorScheme scheme) {
    final ms = (_data?["memory_search"] as Map?)?.cast<String, dynamic>();
    final total = (ms?["total"] as num?)?.toInt() ?? 0;
    if (total == 0) return const SizedBox.shrink();
    final hit = (ms?["hit"] as num?)?.toInt() ?? 0;
    final miss = (ms?["miss"] as num?)?.toInt() ?? 0;
    final avg = (ms?["avg_latency_ms"] as num?)?.toInt() ?? 0;
    final recent = ((ms?["recent"] as List?) ?? []).cast<Map>().take(25).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Aurora P6：统计三卡（命中率数据来自 agent-mind 接口字段；接口无「任务数」字段不虚构）
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
          child: Row(
            children: [
              _statCard(context, l10n.agentMindStatHit, '$hit'),
              const SizedBox(width: 8),
              _statCard(context, l10n.agentMindStatMiss, '$miss'),
              const SizedBox(width: 8),
              _statCard(context, l10n.agentMindStatAvgLatency, '${avg}ms'),
            ],
          ),
        ),
        _auroraGroup(title: l10n.agentMindMemorySearch, children: [
          if (recent.isEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
              child: Text(l10n.agentMindSearchEmpty,
                  style:
                      const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            )
          else
            // Aurora P6：命中条目 → AuroraCard（查询内容 + 命中标签 + 时间）
            for (int i = 0; i < recent.length; i++)
              Padding(
                padding: const EdgeInsets.only(left: 8, right: 8, bottom: 8),
                child: AuroraCard(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        ((recent[i]["hit_count"] as num?)?.toInt() ?? 0) > 0
                            ? Icons.check_circle_outline
                            : Icons.remove_circle_outline,
                        size: 16,
                        color: ((recent[i]["hit_count"] as num?)?.toInt() ?? 0) > 0
                            ? Colors.green
                            : IosCardColors.subtitle,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text("${recent[i]["query"] ?? ""}",
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                    fontSize: 13, color: scheme.onSurface)),
                            Text(
                              _hitCountLabel(recent[i], l10n),
                              style: const TextStyle(
                                  fontSize: 10, color: IosCardColors.subtitle),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
        ]),
      ],
    );
  }

  Widget _buildRunningNotes(AppLocalizations l10n, ColorScheme scheme) {
    final notes = (_data?["running_notes"] as Map?)?.cast<String, dynamic>();
    final identity = (notes?["identity"] as Map?)?.cast<String, dynamic>();
    final pinned = ((notes?["pinned"] as List?) ?? []).cast<Map>().take(25).toList();
    final hasIdentity = (identity?["content"] as String?)?.trim().isNotEmpty ?? false;
    final empty = !hasIdentity && pinned.isEmpty;
    // Aurora P6：每条笔记独立 AuroraCard
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 16, bottom: 6),
            child: Text(l10n.agentMindRunningNotes,
                style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: IosCardColors.subtitle)),
          ),
          if (empty)
            AuroraCard(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              child: Text(l10n.agentMindNoteEmpty,
                  style: const TextStyle(
                      fontSize: 13, color: IosCardColors.subtitle)),
            )
          else ...[
            if (hasIdentity)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: AuroraCard(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.agentMindIdentity,
                          style: const TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              color: IosCardColors.subtitle)),
                      const SizedBox(height: 4),
                      Text("${identity?["content"] ?? ""}",
                          style: TextStyle(
                              fontSize: 14,
                              height: 1.5,
                              color: scheme.onSurface)),
                      if (_fmtTime(identity?["updated_at"] as String?)
                          .isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(_fmtTime(identity?["updated_at"] as String?),
                              style: const TextStyle(
                                  fontSize: 10, color: IosCardColors.subtitle)),
                        ),
                    ],
                  ),
                ),
              ),
            for (final p in pinned)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: AuroraCard(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text("${p["label"] ?? p["memory_type"] ?? ""}",
                              style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                  color: IosCardColors.subtitle)),
                          const Spacer(),
                          Text(_fmtTime(p["updated_at"] as String?),
                              style: const TextStyle(
                                  fontSize: 10, color: IosCardColors.subtitle)),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text("${p["content"] ?? ""}",
                          style: TextStyle(
                              fontSize: 14,
                              height: 1.5,
                              color: scheme.onSurface)),
                    ],
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }

  Widget _buildTasks(AppLocalizations l10n, ColorScheme scheme) {
    final tasks = ((_data?["tasks"] as List?) ?? []).cast<Map>().take(25).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _sectionHeader(l10n.agentMindTasks),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Column(
            children: [
      if (tasks.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Text(l10n.agentMindEmpty,
              style:
                  const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        )
      else
        // Aurora P6：条目改竖向时间线
        for (int i = 0; i < tasks.length; i++)
          _timelineItem(
            context,
            last: i == tasks.length - 1,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_statusIcon(tasks[i]["status"] as String?),
                    size: 18, color: _statusColor(tasks[i]["status"] as String?)),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("${tasks[i]["goal"] ?? ""}",
                          style:
                              TextStyle(fontSize: 14, color: scheme.onSurface)),
                      const SizedBox(height: 2),
                      Text(
                        "${tasks[i]["trigger"] ?? ""} · ${tasks[i]["status"] ?? ""} · ${_fmtTime(tasks[i]["created_at"] as String?)}",
                        style: const TextStyle(
                            fontSize: 11, color: IosCardColors.subtitle),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildToolLogs(AppLocalizations l10n, ColorScheme scheme) {
    final logs = ((_data?["tool_logs"] as List?) ?? []).cast<Map>().take(25).toList();
    // 统一 status 口径（2026-08-23）：blocked=拦截未执行不计入成功率；partial=部分失败不按整体失败计
    final succeeded = logs.where((l) => _classifyStatus(l["status"] as String?) == "success").length;
    final partial = logs.where((l) => _classifyStatus(l["status"] as String?) == "partial").length;
    final failed = logs.where((l) => _classifyStatus(l["status"] as String?) == "failed").length;
    final blocked = logs.where((l) => _classifyStatus(l["status"] as String?) == "blocked").length;
    final ok = succeeded + partial;
    final attempts = ok + failed;
    final rate = attempts == 0 ? 0 : (ok * 100 / attempts).round();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _sectionHeader(l10n.agentMindToolLogs),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Column(
            children: [
      if (logs.isNotEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          child: Text(l10n.agentMindToolSummary(rate, ok, failed, blocked),
              style:
                  const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        ),
      if (logs.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Text(l10n.agentMindEmpty,
              style:
                  const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        )
      else
        // Aurora P6：条目改竖向时间线
        for (int i = 0; i < logs.length; i++)
          _timelineItem(
            context,
            last: i == logs.length - 1,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_statusIcon(logs[i]["status"] as String?),
                    size: 16, color: _statusColor(logs[i]["status"] as String?)),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text("${logs[i]["trigger"] ?? ""} / ${logs[i]["route"] ?? ""}",
                          style:
                              TextStyle(fontSize: 13, color: scheme.onSurface)),
                      if ((logs[i]["steps"] as String?)?.isNotEmpty ?? false)
                        Padding(
                          padding: const EdgeInsets.only(top: 3),
                          child: Text(
                            "${logs[i]["steps"]}",
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                fontSize: 11, color: IosCardColors.subtitle),
                          ),
                        ),
                      Text(
                        "${_fmtTime(logs[i]["created_at"] as String?)} · ${logs[i]["latency_ms"] ?? 0}ms",
                        style: const TextStyle(
                            fontSize: 10, color: IosCardColors.subtitle),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
            ],
          ),
        ),
      ],
    );
  }

  /// 统一执行状态口径（2026-08-23）：ok/done/success→success；error/failed→failed；
  /// partial→partial；blocked/skipped→blocked（拦截未执行）；其余 unknown。
  String _classifyStatus(String? status) {
    switch (status) {
      case "ok":
      case "done":
      case "success":
      case "succeeded":
        return "success";
      case "error":
      case "failed":
      case "failure":
        return "failed";
      case "partial":
      case "partial_success":
        return "partial";
      case "blocked":
      case "skipped":
      case "intercepted":
        return "blocked";
      default:
        return "unknown";
    }
  }

  IconData _statusIcon(String? status) {
    switch (status) {
      case "done":
      case "ok":
        return Icons.check_circle_outline;
      case "blocked":
        return Icons.block;
      case "failed":
      case "error":
        return Icons.error_outline;
      default:
        return Icons.schedule;
    }
  }

  Color _statusColor(String? status) {
    switch (status) {
      case "done":
      case "ok":
        return Colors.green;
      case "blocked":
        return Colors.orange;
      case "failed":
      case "error":
        return Colors.red;
      default:
        return Colors.blueGrey;
    }
  }
}
