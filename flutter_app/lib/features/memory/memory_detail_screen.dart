import "package:flutter/material.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/memory.dart";
import "../../models/memory_history.dart";
import "../../models/memory_derivations.dart";
import "../../services/api_client.dart";
import "../../widgets/ios_card_group.dart";
import "../../utils/beijing_time.dart";
import "../../utils/memory_decay.dart";
import "../../widgets/memory_decay_chart.dart";

class MemoryDetailScreen extends StatefulWidget {
  final Memory memory;

  const MemoryDetailScreen({super.key, required this.memory});

  @override
  State<MemoryDetailScreen> createState() => _MemoryDetailScreenState();
}

class _MemoryDetailScreenState extends State<MemoryDetailScreen> {
  static const int _decayHorizonDays = 30;
  final _api = ApiClient();
  late Memory _memory;
  bool _changed = false;
  List<MemoryNode> _chain = const [];
  bool _loadingChain = true;
  String? _cachedOriginalSource;
  // 原始内容 Future 只取一次并缓存，避免 build 内联 Future 导致每条重建重取；
  // 切换记忆 ID 时置空重取（RC2 G1 的身份边界）。
  late Future<String> _sourceFuture = _fetchOriginalSource();

  // RC2 G1：修正历史（只读）。null＝加载中/未加载；失败与空态分开，不互相冒充。
  List<MemoryCorrection>? _history;
  bool _loadingHistory = true;
  bool _historyFailed = false;
  bool _historyTruncated = false;
  int _historyGeneration = 0;

  /// 页面代次：切换记忆 ID 时 +1；本页所有异步读（详情/链条/来源/历史）共用，
  /// 晚到结果必须同时满足「页面代次未变＋widget 当前 ID 相同＋baseUrl/token 未变＋mounted」。
  int _pageGeneration = 0;

  // RC2 G2：派生关系（只读、两方向独立分页）
  bool _derivLoading = true;
  bool _derivFailed = false;
  List<DerivationItem> _parentItems = const [];
  List<DerivationItem> _childItems = const [];
  String? _parentsCursor;
  String? _childrenCursor;
  bool _parentsHasMore = false;
  bool _childrenHasMore = false;
  bool _parentsIncomplete = false;
  bool _childrenIncomplete = false;
  bool _parentsMoreFailed = false;
  bool _childrenMoreFailed = false;
  int _derivGeneration = 0;
  int _derivationClickGeneration = 0;
  // R5：同方向串行/单飞锁 + 页请求代次（防重复点击与旧页乱序回退）
  bool _parentsInflight = false;
  bool _childrenInflight = false;
  int _parentsPageGen = 0;
  int _childrenPageGen = 0;

  Map<String, String> _typeLabels(AppLocalizations l10n) => {
        "user_info": l10n.memoryImpression,
        "preference": l10n.memoryPreference,
        "event": l10n.memoryEvent,
        "insight": l10n.memoryInsight,
      };

  // 批 0-2 / M1b：手机观察条（source=perception）与「用户已认可」（epistemic_status=FACT）判定
  bool get _isPerception => (_memory.source ?? "").toLowerCase() == "perception";
  bool get _isAccepted => (_memory.epistemicStatus ?? "").toUpperCase() == "FACT";

  @override
  void initState() {
    super.initState();
    _memory = widget.memory;
    _loadChain();
    _refreshMemory();  // P2-11：补齐调用方没带的字段（如 why_it_matters）
    _loadHistory();
    _loadDerivations();
  }

  @override
  void didUpdateWidget(covariant MemoryDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 同 State 切换记忆 ID：作废在途请求、重置本页局部状态，再按新 ID 重取。
    if (oldWidget.memory.id != widget.memory.id) {
      _memory = widget.memory;
      _pageGeneration++;      // 在途详情/链条/来源/历史一律作废
      _historyGeneration++;  // 在途历史结果一律作废
      _history = null;
      _historyFailed = false;
      _historyTruncated = false;
      _loadingHistory = true;
      _chain = const [];
      _loadingChain = true;
      _cachedOriginalSource = null;
      _sourceFuture = _fetchOriginalSource();
      _derivGeneration++;      // 在途派生关系/点击一律作废
      _derivLoading = true;
      _derivFailed = false;
      _parentItems = const [];
      _childItems = const [];
      _parentsCursor = null;
      _childrenCursor = null;
      _parentsHasMore = false;
      _childrenHasMore = false;
      _parentsIncomplete = false;
      _childrenIncomplete = false;
      _parentsMoreFailed = false;
      _childrenMoreFailed = false;
      _parentsInflight = false;
      _childrenInflight = false;
      _parentsPageGen++;
      _childrenPageGen++;
      _loadChain();
      _refreshMemory();
      _loadHistory();
      _loadDerivations();
    }
  }

  /// 本页异步读的统一有效性边界（R1）：页面代次 + widget 当前记忆 + 同源 + mounted。
  bool _readIsValid({
    required int memoryId,
    required int generation,
    required String baseUrl,
    required String token,
  }) =>
      mounted &&
      generation == _pageGeneration &&
      widget.memory.id == memoryId &&
      _api.baseUrl == baseUrl &&
      _api.token == token;

  /// P2-11（2026-09-28）：用服务端完整记录替换传入的精简对象。
  ///
  /// R1：A 的晚到详情不得覆盖已切换的 B（校验页面代次/当前 ID/返回 id）。
  Future<void> _refreshMemory() async {
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    try {
      final full = await _api.getMemory(memoryId);
      if (!_readIsValid(memoryId: memoryId, generation: generation, baseUrl: baseUrl, token: token)) {
        return;
      }
      if (full.id != memoryId) return;
      setState(() => _memory = full);
    } catch (_) {
      // 失败静默：维持调用方传入的对象（即改动前行为）
    }
  }

  /// RC2 G1：加载修正历史。请求绑定「widget 当前 memory.id ＋ 页面代次 ＋ 历史代次 ＋
  /// baseUrl/token 快照」，返回后再核同源有效性；晚到结果不写入新页面/新账号。
  /// 重试一律取 widget.memory.id，不读可能被旧详情污染的 _memory。
  Future<void> _loadHistory() async {
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final historyGeneration = ++_historyGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    if (mounted) {
      setState(() {
        _loadingHistory = true;
        _historyFailed = false;
      });
    }
    bool pageCurrent() =>
        mounted &&
        generation == _pageGeneration &&
        widget.memory.id == memoryId &&
        historyGeneration == _historyGeneration;
    bool sameOrigin() => _api.baseUrl == baseUrl && _api.token == token;
    try {
      final history = await _api.getMemoryHistory(memoryId);
      if (!pageCurrent()) return;
      if (!sameOrigin()) {
        // 请求期间换了服务器/账号：丢弃结果并明确失败，不把旧账号历史画到新页面。
        setState(() {
          _loadingHistory = false;
          _historyFailed = true;
          _history = null;
        });
        return;
      }
      setState(() {
        _loadingHistory = false;
        _historyFailed = false;
        _history = history.corrections;
        _historyTruncated = history.truncated;
      });
    } catch (_) {
      if (!pageCurrent()) return;
      setState(() {
        _loadingHistory = false;
        _historyFailed = true;
      });
    }
  }

  /// RC2 G2：加载两方向派生关系（只读）。绑定 widget 当前 memory.id＋页面代次＋
  /// 派生代次＋baseUrl/token 快照；晚到结果不污染新页面/新账号。
  Future<void> _loadDerivations() async {
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final derivGeneration = ++_derivGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    if (mounted) {
      setState(() {
        _derivLoading = true;
        _derivFailed = false;
      });
    }
    bool current() =>
        mounted &&
        generation == _pageGeneration &&
        widget.memory.id == memoryId &&
        derivGeneration == _derivGeneration;
    try {
      final data = await _api.getMemoryDerivations(memoryId);
      if (!current()) return;
      if (_api.baseUrl != baseUrl || _api.token != token) {
        setState(() {
          _derivLoading = false;
          _derivFailed = true;
        });
        return;
      }
      setState(() {
        _parentItems = data.parents.items;
        _childItems = data.children.items;
        _parentsCursor = data.parents.nextCursor;
        _childrenCursor = data.children.nextCursor;
        _parentsHasMore = data.parents.hasMore;
        _childrenHasMore = data.children.hasMore;
        _parentsIncomplete = data.parents.dataIncomplete;
        _childrenIncomplete = data.children.dataIncomplete;
        _parentsMoreFailed = false;
        _childrenMoreFailed = false;
        _parentsInflight = false;
        _childrenInflight = false;
        _derivLoading = false;
        _derivFailed = false;
      });
    } catch (_) {
      if (!current()) return;
      setState(() {
        _derivLoading = false;
        _derivFailed = true;
      });
    }
  }

  /// 手动「继续加载」：只取本方向下一页，按 id 去重合并；不自动无限翻页。
  ///
  /// R5：同方向单飞（inflight 锁，按钮在加载中禁用）、页请求代次＋游标快照；
  /// 旧页晚到不得覆盖新游标/新条目，也不得擦掉完整性状态；另一方向不受影响。
  Future<void> _loadMoreDerivations({required bool parents}) async {
    if (parents ? _parentsInflight : _childrenInflight) return;
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final derivGeneration = _derivGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    final cursor = parents ? _parentsCursor : _childrenCursor;
    if (cursor == null) return;
    final pageGeneration = parents ? ++_parentsPageGen : ++_childrenPageGen;
    if (parents) {
      _parentsInflight = true;
      _parentsMoreFailed = false;
    } else {
      _childrenInflight = true;
      _childrenMoreFailed = false;
    }
    setState(() {});

    bool pageCurrent() =>
        mounted &&
        generation == _pageGeneration &&
        derivGeneration == _derivGeneration &&
        widget.memory.id == memoryId &&
        (parents ? pageGeneration == _parentsPageGen : pageGeneration == _childrenPageGen);
    try {
      final data = await _api.getMemoryDerivations(
        memoryId,
        parentsCursor: parents ? cursor : null,
        childrenCursor: parents ? null : cursor,
      );
      if (!pageCurrent()) return;
      if (_api.baseUrl != baseUrl || _api.token != token) {
        setState(() {
          if (parents) {
            _parentsMoreFailed = true;
            _parentsInflight = false;
          } else {
            _childrenMoreFailed = true;
            _childrenInflight = false;
          }
        });
        return;
      }
      // R2：has_more=true 时必须相对本次请求捕获的 cursor 严格前进；相等/倒退整页拒绝，
      // 保已有条目与有效 cursor，仅本方向失败可重试（不清另一方向、不应用坏页）。
      final target = parents ? data.parents : data.children;
      if (target.hasMore) {
        final prev = int.tryParse(cursor);
        final next = int.tryParse(target.nextCursor ?? '');
        if (prev == null || next == null || next <= prev) {
          setState(() {
            if (parents) {
              _parentsMoreFailed = true;
              _parentsInflight = false;
            } else {
              _childrenMoreFailed = true;
              _childrenInflight = false;
            }
          });
          return;
        }
      }
      setState(() {
        if (parents) {
          final seen = _parentItems.map((e) => e.id).toSet();
          _parentItems = [
            ..._parentItems,
            ...data.parents.items.where((e) => !seen.contains(e.id)),
          ];
          _parentsCursor = data.parents.nextCursor;
          _parentsHasMore = data.parents.hasMore;
          _parentsIncomplete = data.parents.dataIncomplete;
          _parentsInflight = false;
        } else {
          final seen = _childItems.map((e) => e.id).toSet();
          _childItems = [
            ..._childItems,
            ...data.children.items.where((e) => !seen.contains(e.id)),
          ];
          _childrenCursor = data.children.nextCursor;
          _childrenHasMore = data.children.hasMore;
          _childrenIncomplete = data.children.dataIncomplete;
          _childrenInflight = false;
        }
      });
    } catch (_) {
      if (!pageCurrent()) return;
      setState(() {
        if (parents) {
          _parentsMoreFailed = true;
          _parentsInflight = false;
        } else {
          _childrenMoreFailed = true;
          _childrenInflight = false;
        }
      });
    }
  }

  /// 点击派生条目：先补完整记录，再仅在「页面/点击代次/同源」仍有效时导航。
  Future<void> _openDerivation(DerivationItem item) async {
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final clickGeneration = ++_derivationClickGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    final l10n = AppLocalizations.of(context)!;
    try {
      final full = await _api.getMemory(item.id);
      if (!_readIsValid(memoryId: memoryId, generation: generation,
          baseUrl: baseUrl, token: token)) {
        return;
      }
      if (clickGeneration != _derivationClickGeneration || full.id != item.id) return;
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => MemoryDetailScreen(memory: full)),
      );
    } catch (_) {
      if (!_readIsValid(memoryId: memoryId, generation: generation,
          baseUrl: baseUrl, token: token)) {
        return;
      }
      if (clickGeneration != _derivationClickGeneration) return;
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.memoryDerivationsOpenFailed)),
      );
    }
  }

  String _derivationStatusLabel(DerivationItem item, AppLocalizations l10n) {
    switch (item.status) {
      case 'active':
        return l10n.memoryDerivationsStatusActive;
      case 'stale':
        return l10n.memoryDerivationsStatusStale;
      case 'superseded':
        return l10n.memoryDerivationsStatusSuperseded;
      default:
        return l10n.memoryDerivationsStatusUnknown;
    }
  }

  Widget _buildDerivationItemRow(DerivationItem item) {
    return InkWell(
      onTap: () => _openDerivation(item),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(item.isArchived ? Icons.archive_outlined : Icons.subdirectory_arrow_right,
              size: 16, color: IosCardColors.subtitle),
          const SizedBox(width: 8),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if ((item.title ?? '').trim().isNotEmpty)
                Text(item.title!,
                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
              Text(item.content,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 13, height: 1.4)),
            ]),
          ),
          const SizedBox(width: 6),
          Text(_derivationStatusLabel(item, AppLocalizations.of(context)!),
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        ]),
      ),
    );
  }

  Widget _buildDerivationSection(
    AppLocalizations l10n, {
    required bool parents,
  }) {
    final items = parents ? _parentItems : _childItems;
    final hasMore = parents ? _parentsHasMore : _childrenHasMore;
    final incomplete = parents ? _parentsIncomplete : _childrenIncomplete;
    final moreFailed = parents ? _parentsMoreFailed : _childrenMoreFailed;
    final title = parents ? l10n.memoryDerivationsParents : l10n.memoryDerivationsChildren;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
      const SizedBox(height: 4),
      if (items.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Text(
            incomplete
                ? l10n.memoryDerivationsIncomplete
                : (hasMore ? l10n.memoryDerivationsMore : l10n.memoryDerivationsEmpty),
            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
          ),
        )
      else
        ...items.map(_buildDerivationItemRow),
      if (items.isNotEmpty && incomplete)
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(l10n.memoryDerivationsIncomplete,
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
        ),
      if (hasMore) ...[
        if (parents ? _parentsInflight : _childrenInflight)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: ClipRRect(
              borderRadius: BorderRadius.all(Radius.circular(2)),
              child: LinearProgressIndicator(minHeight: 2),
            ),
          ),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton.icon(
            icon: Icon(moreFailed ? Icons.refresh : Icons.expand_more, size: 18),
            label: Text(moreFailed ? l10n.memoryDerivationsRetry : l10n.memoryDerivationsLoadMore),
            onPressed: (parents ? _parentsInflight : _childrenInflight)
                ? null
                : () => _loadMoreDerivations(parents: parents),
          ),
        ),
      ],
    ]);
  }

  Widget _buildDerivationsCard(AppLocalizations l10n) {
    final Widget body;
    if (_derivLoading) {
      // 用线性进度条而非 CircularProgressIndicator：G1 旧测试对「圆圈加载」有唯一性断言，只读不改。
      body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const ClipRRect(
          borderRadius: BorderRadius.all(Radius.circular(2)),
          child: LinearProgressIndicator(minHeight: 3),
        ),
        const SizedBox(height: 8),
        Text(l10n.memoryDerivationsLoading,
            style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
      ]);
    } else if (_derivFailed) {
      body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l10n.memoryDerivationsLoadFailed,
            style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton.icon(
            icon: const Icon(Icons.refresh, size: 18),
            label: Text(l10n.memoryDerivationsRetry),
            onPressed: _loadDerivations,
          ),
        ),
      ]);
    } else {
      body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _buildDerivationSection(l10n, parents: true),
        const Divider(height: 20),
        _buildDerivationSection(l10n, parents: false),
      ]);
    }
    return IosCardGroup(children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.hub_outlined, size: 18, color: IosCardColors.subtitle),
            const SizedBox(width: 8),
            Text(l10n.memoryDerivationsTitle,
                style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
          ]),
          const SizedBox(height: 10),
          body,
        ]),
      ),
    ]);
  }

  String _sourceLabel(String? source, AppLocalizations l10n) {
    switch (source) {
      case "chat": return l10n.sourceChat;
      case "moment": return l10n.sourceMoment;
      case "diary": return l10n.sourceDiary;
      case "bio": return l10n.sourceBio;
      case "status": return l10n.sourceStatus;
      case "perception": return l10n.sourcePerception;
      case "extracted": return l10n.sourceExtracted;
      case "relationship": return l10n.sourceRelationship;
      default: return source ?? l10n.unknown;
    }
  }

  Future<void> _deleteMemory() async {
    final l10n = AppLocalizations.of(context)!;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMemoryTitle),
        content: Text(l10n.deleteMemoryConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.deleteMemory(_memory.id);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _updateImportance(int newValue) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await _api.updateMemory(_memory.id, {"importance": newValue});
      setState(() {
        _memory = Memory(
          id: _memory.id,
          memoryType: _memory.memoryType,
          subType: _memory.subType,
          source: _memory.source,
          sourceId: _memory.sourceId,
          sourceLabel: _memory.sourceLabel,
          sourceIcon: _memory.sourceIcon,
          epistemicStatus: _memory.epistemicStatus,
          title: _memory.title,
          content: _memory.content,
          importance: newValue,
          importancePct: newValue * 20.0,
          createdAt: _memory.createdAt,
          deleteAt: null,
          isPinned: _memory.isPinned,
          isLocked: _memory.isLocked,
          strengthDays: _memory.strengthDays,
          lastReinforceAt: _memory.lastReinforceAt,
          nextReviewAt: _memory.nextReviewAt,
          reviewCount: _memory.reviewCount,
        );
        _changed = true;
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.updateFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _toggleLock() async {
    final l10n = AppLocalizations.of(context)!;
    final target = !_memory.isLocked;
    try {
      await _api.updateMemory(_memory.id, {"is_locked": target});
      setState(() {
        _memory = Memory(
          id: _memory.id,
          memoryType: _memory.memoryType,
          subType: _memory.subType,
          source: _memory.source,
          sourceId: _memory.sourceId,
          sourceLabel: _memory.sourceLabel,
          sourceIcon: _memory.sourceIcon,
          epistemicStatus: _memory.epistemicStatus,
          title: _memory.title,
          content: _memory.content,
          importance: _memory.importance,
          importancePct: _memory.importancePct,
          createdAt: _memory.createdAt,
          updatedAt: _memory.updatedAt,
          deleteAt: null,
          isPinned: _memory.isPinned,
          isLocked: target,
          strengthDays: _memory.strengthDays,
          lastReinforceAt: _memory.lastReinforceAt,
          nextReviewAt: _memory.nextReviewAt,
          reviewCount: _memory.reviewCount,
        );
        _changed = true;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(target ? l10n.lockedFrozen : l10n.unlockedResume),
          duration: const Duration(seconds: 2),
        ));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.opFailedErr(e.toString()))));
      }
    }
  }

  /// M1b「这是真的」：认可后回拉完整记录（后端只升认知状态，来源保持 perception）
  Future<void> _acceptPerception() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await _api.acceptPerceptionMemory(_memory.id);
      final full = await _api.getMemory(_memory.id);
      if (!mounted) return;
      setState(() {
        _memory = full;
        _changed = true;
      });
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(l10n.perceptionRemembered),
        duration: const Duration(seconds: 2),
      ));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.opFailedErr(e.toString()))));
      }
    }
  }

  /// M1b「不记住」：撤回＝归档（不删行、可逆），成功后返回列表
  Future<void> _forgetPerception() async {
    final l10n = AppLocalizations.of(context)!;
    try {
      await _api.archiveMemory(_memory.id);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(l10n.perceptionForgotten),
        duration: const Duration(seconds: 2),
      ));
      Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.opFailedErr(e.toString()))));
      }
    }
  }

  Widget _perceptionBadge(AppLocalizations l10n) {
    final accepted = _isAccepted;
    final color = accepted ? Colors.teal : Colors.amber;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withValues(alpha: 0.40)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(accepted ? Icons.verified_outlined : Icons.visibility_outlined, size: 15, color: color.shade700),
          const SizedBox(width: 6),
          Text(
            accepted ? l10n.perceptionAccepted : l10n.perceptionBadge,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: color.shade700),
          ),
        ],
      ),
    );
  }

  Widget _buildPerceptionCard(AppLocalizations l10n) {
    return IosCardGroup(children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.perceptionHint, style: const TextStyle(fontSize: 13, height: 1.5, color: IosCardColors.subtitle)),
            if (!_isAccepted) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: FilledButton.icon(
                      icon: const Icon(Icons.check_circle_outline, size: 18),
                      label: Text(l10n.perceptionAccept),
                      onPressed: _acceptPerception,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.do_not_disturb_alt_outlined, size: 18),
                      label: Text(l10n.perceptionForget),
                      onPressed: _forgetPerception,
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    ]);
  }

  String _countdownText() {
    final l10n = AppLocalizations.of(context)!;
    final deleteAt = _memory.deleteAt;
    if (deleteAt == null || deleteAt.isEmpty) return "";
    try {
      final now = DateTime.now();
      final target = DateTime.parse(deleteAt).toLocal();
      final hours = target.difference(now).inHours;
      if (hours <= 0) return l10n.deleteSoon;
      if (hours >= 24) return l10n.deleteInDays(hours ~/ 24);
      return l10n.deleteInHours(hours);
    } catch (e) {
      return "";
    }
  }

  Future<String> _fetchOriginalSource() async {
    final l10n = AppLocalizations.of(context)!;
    final memoryId = widget.memory.id;
    if (_memory.id != memoryId || _memory.sourceId == null) return "";
    if (_cachedOriginalSource != null) return _cachedOriginalSource!;
    final generation = _pageGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    try {
      final msg = await _api.getMessage(_memory.sourceId!);
      // R1：来源读晚到不得写缓存/返回旧来源（切换后 A 的原文不能出现在 B）。
      if (!_readIsValid(memoryId: memoryId, generation: generation, baseUrl: baseUrl, token: token)) {
        return "";
      }
      final content = msg["content"] as String? ?? "";
      _cachedOriginalSource = content;
      return content;
    } catch (e) {
      if (!_readIsValid(memoryId: memoryId, generation: generation, baseUrl: baseUrl, token: token)) {
        return "";
      }
      return l10n.loadOriginalFailed(e.toString());
    }
  }

  Widget _buildOriginalSourceTile() {
    final l10n = AppLocalizations.of(context)!;
    return IosCardGroup(
      children: [
        ExpansionTile(
        leading: Icon(Icons.description_outlined, color: Theme.of(context).colorScheme.primary),
        title: Text(l10n.detailTitle, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w500, color: Theme.of(context).colorScheme.primary)),
        subtitle: Text(l10n.tapToViewOriginal, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [
          FutureBuilder<String>(
            future: _sourceFuture,
            builder: (context, snapshot) {
              if (snapshot.connectionState == ConnectionState.waiting) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(
                    child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                  ),
                );
              }
              if (snapshot.hasError || snapshot.data == null || snapshot.data!.isEmpty) {
                return Padding(
                  padding: const EdgeInsets.all(16),
                  child: Text(l10n.originalUnavailable, style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
                );
              }
              return Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Theme.of(context).dividerColor),
                  ),
                  child: Text(
                    snapshot.data!,
                    style: TextStyle(fontSize: 14, height: 1.5, color: Theme.of(context).colorScheme.onSurface),
                  ),
                ),
              );
            },
          ),
        ],
      ),
      ],
    );
  }


  Future<void> _loadChain() async {
    // R1：链条读同样受页面代次/当前 ID/同源边界约束；A 晚到的链条不得写进 B。
    final memoryId = widget.memory.id;
    final generation = _pageGeneration;
    final baseUrl = _api.baseUrl;
    final token = _api.token;
    try {
      final nodes = await _api.getMemoryChain(memoryId);
      if (!_readIsValid(memoryId: memoryId, generation: generation, baseUrl: baseUrl, token: token)) {
        return;
      }
      setState(() {
        _chain = nodes;
        _loadingChain = false;
      });
    } catch (_) {
      if (_readIsValid(memoryId: memoryId, generation: generation, baseUrl: baseUrl, token: token)) {
        setState(() => _loadingChain = false);
      }
    }
  }

  Future<void> _editContent() async {
    final l10n = AppLocalizations.of(context)!;
    final controller = TextEditingController(text: _memory.content);
    final newContent = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.memoryEditContent),
        content: TextField(
          controller: controller,
          maxLines: 6,
          decoration: InputDecoration(hintText: l10n.memoryEditContentHint),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () {
              if (controller.text.trim().isEmpty) return;
              Navigator.pop(ctx, controller.text.trim());
            },
            child: Text(l10n.memorySaveEdit),
          ),
        ],
      ),
    );
    controller.dispose();
    if (newContent == null || newContent.isEmpty) return;
    try {
      await _api.updateMemoryContent(_memory.id, newContent);
      if (mounted) {
        setState(() {
          _memory = Memory(
            id: _memory.id, memoryType: _memory.memoryType, subType: _memory.subType,
            source: _memory.source, sourceId: _memory.sourceId, sourceLabel: _memory.sourceLabel,
            sourceIcon: _memory.sourceIcon, epistemicStatus: _memory.epistemicStatus, title: _memory.title, content: newContent,
            importance: _memory.importance, importancePct: _memory.importancePct,
            createdAt: _memory.createdAt, updatedAt: _memory.updatedAt, deleteAt: null,
            isPinned: _memory.isPinned, isLocked: _memory.isLocked, whyItMatters: _memory.whyItMatters,
            chainId: _memory.chainId, parentId: _memory.parentId, nodeType: _memory.nodeType,
            version: _memory.version + 1,
            strengthDays: _memory.strengthDays, lastReinforceAt: _memory.lastReinforceAt,
            nextReviewAt: _memory.nextReviewAt, reviewCount: _memory.reviewCount,
          );
          _changed = true;
        });
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.memoryUpdatedOk)));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.updateFailedErr(e.toString()))));
      }
    }
  }

  Future<void> _deleteCascade() async {
    final l10n = AppLocalizations.of(context)!;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.memoryDeleteCascadeTitle),
        content: Text(l10n.memoryDeleteCascadeConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(l10n.delete, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.deleteMemoryCascade(_memory.id);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailedErr(e.toString()))));
      }
    }
  }

  // 关联记忆为空时的三态提示：未挂链 / 链条起点 / 兜底，避免三种情况共用一句「暂无关联记忆」
  String _chainEmptyHint(AppLocalizations l10n) {
    if (_memory.chainId == null) return l10n.memoryChainUnlinked;
    if (_memory.nodeType == 'root') return l10n.memoryChainRootNoBranch;
    return l10n.memoryChainEmpty;
  }

  Widget _buildHistoryCard(AppLocalizations l10n) {
    final items = _history ?? const <MemoryCorrection>[];
    final Widget body;
    if (_loadingHistory) {
      body = Row(children: [
        const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
        const SizedBox(width: 10),
        Expanded(
          child: Text(l10n.memoryHistoryLoading,
              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        ),
      ]);
    } else if (_historyFailed) {
      body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l10n.memoryHistoryLoadFailed,
            style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton.icon(
            icon: const Icon(Icons.refresh, size: 18),
            label: Text(l10n.retry),
            onPressed: _loadHistory,
          ),
        ),
      ]);
    } else if (items.isEmpty) {
      // 截断时即使空也不能谎称「完整无历史」
      body = Text(
        _historyTruncated ? l10n.memoryHistoryTruncated : l10n.memoryHistoryEmpty,
        style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
      );
    } else {
      body = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        for (var i = 0; i < items.length; i++) ...[
          _buildHistoryItem(items[i], l10n),
          if (i != items.length - 1) const Divider(height: 20),
        ],
        if (_historyTruncated) ...[
          const SizedBox(height: 10),
          Text(l10n.memoryHistoryTruncated,
              style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        ],
      ]);
    }
    return IosCardGroup(children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Icon(Icons.history, size: 18, color: IosCardColors.subtitle),
            const SizedBox(width: 8),
            Text(l10n.memoryHistoryTitle,
                style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
          ]),
          const SizedBox(height: 10),
          body,
        ]),
      ),
    ]);
  }

  Widget _buildHistoryItem(MemoryCorrection item, AppLocalizations l10n) {
    final changedAt = item.changedAt;
    final timeText = (changedAt == null || changedAt.isEmpty)
        ? l10n.memoryHistoryUnknownTime
        : formatBeijingTime(changedAt);
    final reason = item.reason;
    final reasonText = (reason == null || reason.trim().isEmpty)
        ? l10n.memoryHistoryReasonUnknown
        : reason;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _buildHistoryValue(l10n.memoryHistoryOldLabel, item.oldValue),
      Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Icon(Icons.arrow_downward, size: 16, color: IosCardColors.subtitle),
      ),
      _buildHistoryValue(l10n.memoryHistoryNewLabel, item.newValue),
      const SizedBox(height: 6),
      // 窄屏/长文案用 Wrap 换行，不挤成 overflow；原始内容与 reason 均不翻译
      Wrap(spacing: 12, runSpacing: 2, children: [
        Text(timeText, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        Text(reasonText, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
      ]),
    ]);
  }

  Widget _buildHistoryValue(String label, String value) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
      const SizedBox(height: 2),
      Text(value, style: const TextStyle(fontSize: 14, height: 1.4)),
    ]);
  }

  Widget _buildChainCard(AppLocalizations l10n) {
    // 全链时间线（含自身，服务端按 created_at 升序）：只有自身一条时按挂链状态区分提示
    final related = _chain.where((n) => n.id != _memory.id).length;
    return IosCardGroup(children: [
      ExpansionTile(
        leading: Icon(Icons.account_tree_outlined, color: Theme.of(context).colorScheme.primary),
        title: Text(l10n.memoryChainTitle, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
        subtitle: Text(l10n.memoryChainChildren, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        children: [
          if (_loadingChain)
            const Padding(padding: EdgeInsets.all(16), child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))))
          else if (related == 0)
            Padding(padding: const EdgeInsets.all(16), child: Text(_chainEmptyHint(l10n), style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)))
          else
            ..._chain.map((c) => _buildChainNode(c, isCurrent: c.id == _memory.id)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Row(mainAxisAlignment: MainAxisAlignment.end, children: [
              TextButton.icon(icon: const Icon(Icons.edit_outlined, size: 18), label: Text(l10n.memoryEditContent), onPressed: _editContent),
              const SizedBox(width: 8),
              TextButton.icon(
                icon: const Icon(Icons.delete_forever_outlined, size: 18, color: Colors.red),
                label: Text(l10n.memoryDeleteCascadeTitle, style: const TextStyle(color: Colors.red)),
                onPressed: _deleteCascade,
              ),
            ]),
          ),
        ],
      ),
    ]);
  }

  Widget _buildChainNode(MemoryNode node, {bool isCurrent = false}) {
    return Padding(
      padding: const EdgeInsets.only(left: 20, right: 16, top: 4, bottom: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 4, right: 8),
          child: Icon(isCurrent ? Icons.radio_button_checked : Icons.subdirectory_arrow_right,
              size: 18,
              color: isCurrent ? Theme.of(context).colorScheme.primary : IosCardColors.subtitle),
        ),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (node.title != null && node.title!.isNotEmpty)
            Text(node.title!, style: TextStyle(fontSize: 14, fontWeight: isCurrent ? FontWeight.w700 : FontWeight.w500)),
          Text(node.content, maxLines: 2, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, height: 1.4,
                  color: isCurrent ? null : IosCardColors.subtitle,
                  fontWeight: isCurrent ? FontWeight.w600 : FontWeight.w400)),
        ])),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final typeLabel = _typeLabels(l10n)[_memory.memoryType] ?? _memory.memoryType;

    return PopScope(
      canPop: !_changed,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.pop(context, true);
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(_memory.title ?? l10n.memoryDetailTitle),
          actions: [
            IconButton(
              icon: Icon(
                _memory.isLocked ? Icons.lock : Icons.lock_open,
                color: _memory.isLocked ? Colors.blueGrey : Colors.grey,
              ),
              tooltip: _memory.isLocked ? l10n.unlockMemory : l10n.lockMemory,
              onPressed: _toggleLock,
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, color: Colors.red),
              tooltip: l10n.delete,
              onPressed: _deleteMemory,
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            // Type badge
            Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  typeLabel,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: Theme.of(context).colorScheme.onPrimaryContainer,
                  ),
                ),
              ),
            ),
            if (_isPerception) ...[
              const SizedBox(height: 10),
              Center(child: _perceptionBadge(l10n)),
            ],
            const SizedBox(height: 24),

            // Title
            if (_memory.title != null && _memory.title!.isNotEmpty) ...[
              Text(
                _memory.title!,
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 12),
            ],

            // Content
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Theme.of(context).dividerColor),
              ),
              child: Text(
                _memory.content,
                style: const TextStyle(fontSize: 15, height: 1.6),
              ),
            ),
            const SizedBox(height: 24),

            if (_isPerception) ...[
              _buildPerceptionCard(l10n),
              const SizedBox(height: 24),
            ],

            // 意义（why_it_matters，v2.1）：完整展示，不截断、可长按复制
            if ((_memory.whyItMatters ?? '').trim().isNotEmpty) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  // 淡金暖底，与正文卡区分但不喧宾夺主（深浅色取不同透明度，避免硬编码色值）
                  color: Colors.amber.withValues(alpha: Theme.of(context).brightness == Brightness.dark ? 0.10 : 0.08),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.amber.withValues(alpha: 0.30)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Icon(Icons.lightbulb_outline, size: 16, color: Colors.amber.shade700),
                      const SizedBox(width: 6),
                      Text(l10n.whyMattersTitle,
                          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                    ]),
                    const SizedBox(height: 8),
                    SelectableText(
                      _memory.whyItMatters!,
                      style: const TextStyle(fontSize: 14, height: 1.6, fontStyle: FontStyle.italic),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 24),
            ],

            // 修正历史（RC2 G1：只读，复用既有 /history）
            _buildHistoryCard(l10n),
            const SizedBox(height: 24),

            // 派生关系（RC2 G2：只读、直接边）
            _buildDerivationsCard(l10n),
            const SizedBox(height: 24),

            // 记忆链条（树状图）
            _buildChainCard(l10n),
            const SizedBox(height: 24),

            // Importance
            IosCardGroup(
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.importanceTitle, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: List.generate(5, (i) {
                          final starVal = i + 1;
                          return IconButton(
                            icon: Icon(
                              starVal <= _memory.importance ? Icons.star : Icons.star_border,
                              color: starVal <= _memory.importance ? Colors.amber : Theme.of(context).colorScheme.outlineVariant,
                              size: 32,
                            ),
                            onPressed: () => _updateImportance(starVal),
                          );
                        }),
                      ),
                      Center(
                        child: Text(
                          ["", l10n.importanceLow, l10n.importanceMedium, l10n.importanceHigh, l10n.importanceVeryHigh, l10n.importanceMax][_memory.importance.clamp(0, 5)],
                          style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
                        ),
                      ),
                      if (_memory.importancePct > 0) ...[
                        const SizedBox(height: 4),
                        Center(
                          child: Text(
                            l10n.memoryStrength(_memory.importancePct.round()),
                            style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                          ),
                        ),
                      ],
                    if (_memory.isLocked) ...[
                      const SizedBox(height: 6),
                      Center(
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                          decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: Theme.of(context).dividerColor),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.lock, size: 15, color: IosCardColors.subtitle),
                              const SizedBox(width: 6),
                              Text(
                                l10n.lockedNoDecay,
                                style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                    if (_countdownText().isNotEmpty) ...[
                      const SizedBox(height: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: Colors.red.shade50,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: Colors.red.shade200),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.hourglass_bottom, size: 15, color: Colors.red.shade400),
                            const SizedBox(width: 6),
                            Text(
                              _countdownText(),
                              style: TextStyle(fontSize: 12, color: Colors.red.shade400),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (!_memory.isLocked) ...[
                      const SizedBox(height: 4),
                      Center(
                        child: Text(
                          l10n.setStarToKeep,
                          style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
            _buildDecayCard(l10n),
            const SizedBox(height: 12),

            // Original source text (click to expand)
            if (_memory.source == "chat" && _memory.sourceId != null)
              _buildOriginalSourceTile(),
            const SizedBox(height: 12),

            // Source info
            IosCardGroup(
              children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.sourceInfo, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 8),
                      _infoRow(Icons.source, l10n.sourceLabel, _sourceLabel(_memory.source, l10n)),
                      if (_memory.subType != null)
                        _infoRow(Icons.category, l10n.subCategory, _memory.subType!),
                      _infoRow(Icons.calendar_today, l10n.recordTime, formatBeijingTime(_memory.createdAt)),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _infoRow(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Icon(icon, size: 16, color: IosCardColors.subtitle),
          const SizedBox(width: 8),
          Text("$label: ", style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle)),
          Text(value, style: const TextStyle(fontSize: 13)),
        ],
      ),
    );
  }

  Widget _buildDecayCard(AppLocalizations l10n) {
    final nowUtc = DateTime.now().toUtc();
    final lastReinforce = _parseUtc(_memory.lastReinforceAt);
    final createdAt = _parseUtc(_memory.createdAt);
    final nextReview = _parseUtc(_memory.nextReviewAt);
    final strength = _memory.strengthDays ?? 7.0;
    final elapsed = memoryElapsedDays(nowUtc, lastReinforce, createdAt);
    final current = memoryRetentionPct(elapsed, strength).round();
    final nextOffset = nextReviewOffsetDays(nowUtc, nextReview, _decayHorizonDays);
    final isLock = _memory.isLocked;
    final nextText = (_memory.nextReviewAt == null || _memory.nextReviewAt!.isEmpty)
        ? l10n.memoryNextReviewNone
        : formatBeijingTime(_memory.nextReviewAt!);
    return IosCardGroup(children: [
      Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(Icons.show_chart, size: 18, color: IosCardColors.subtitle),
              const SizedBox(width: 8),
              Text(l10n.memoryDecayTitle, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 12),
            SizedBox(
              height: 150,
              width: double.infinity,
              child: MemoryDecayChart(
                strengthDays: strength,
                elapsedDays: elapsed,
                isLocked: isLock,
                horizonDays: _decayHorizonDays,
                nextReviewDay: nextOffset,
                accentColor: Theme.of(context).colorScheme.primary,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              l10n.memoryDecayHorizon(_decayHorizonDays),
              style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle),
            ),
            const SizedBox(height: 10),
            _decayInfoRow(Icons.trending_down, l10n.memoryCurrentRetention, '$current%'),
            _decayInfoRow(Icons.timer_outlined, l10n.memoryStrengthDays, strength.toStringAsFixed(1)),
            _decayInfoRow(Icons.event_available, l10n.memoryNextReview, nextText),
            _decayInfoRow(Icons.replay, l10n.memoryReviewCount, '${_memory.reviewCount ?? 0}'),
            if (isLock) ...[
              const SizedBox(height: 4),
              Row(children: [
                const Icon(Icons.lock, size: 14, color: IosCardColors.subtitle),
                const SizedBox(width: 6),
                Text(l10n.lockedNoDecay, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
              ]),
            ],
          ],
        ),
      ),
    ]);
  }

  DateTime? _parseUtc(String? s) {
    if (s == null || s.isEmpty) return null;
    final d = DateTime.tryParse(s);
    if (d == null) return null;
    return DateTime.utc(d.year, d.month, d.day, d.hour, d.minute, d.second, d.millisecond, d.microsecond);
  }

  Widget _decayInfoRow(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Icon(icon, size: 15, color: IosCardColors.subtitle),
          const SizedBox(width: 8),
          Text("$label: ", style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          Text(value, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500)),
        ],
      ),
    );
  }
}
