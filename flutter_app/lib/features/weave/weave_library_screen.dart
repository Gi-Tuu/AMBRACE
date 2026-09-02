import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../models/character.dart';
import '../../models/weave_card.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/floating_sheet.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/weave_detail_sheet.dart';
import 'weave_canvas_screen.dart';
import "package:ai_companion/theme/tokens.dart";

/// 织库：全景记忆卡片列表（Phase A）
class WeaveLibraryScreen extends StatefulWidget {
  /// 进入时优先选中的角色（从某角色详情页进入时传入）。
  final int? initialCharacterId;
  const WeaveLibraryScreen({super.key, this.initialCharacterId});

  @override
  State<WeaveLibraryScreen> createState() => _WeaveLibraryScreenState();
}

class _WeaveLibraryScreenState extends State<WeaveLibraryScreen> {
  final ApiClient _api = ApiClient();
  List<AICharacter> _characters = [];
  int? _selectedCharId;
  /// 织库双域（2026-08-12）：shared=全·织库（共同记忆）/ private=私·织库（AI 生活）
  String _domain = 'shared';
  List<WeaveCard> _cards = [];
  bool _loading = true;
  bool _generating = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _selectedCharId = widget.initialCharacterId;
    _loadCharacters();
    _loadCards();
  }

  Future<void> _loadCharacters() async {
    try {
      final chars = await _api.getCharacters();
      if (mounted) setState(() => _characters = chars);
    } catch (_) {}
  }

  Future<void> _loadCards() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await _api.getWeaveCards(characterId: _selectedCharId, domain: _domain);
      if (!mounted) return;
      setState(() {
        _cards = r.cards;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = AppLocalizations.of(context)!.weaveCardsLoadFail;
        _loading = false;
      });
    }
  }

  Future<void> _generate() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _generating = true);
    try {
      final r = await _api.generateWeaveCards(characterId: _selectedCharId, domain: _domain);
      if (!mounted) return;
      final created = r['created'] as int? ?? 0;
      final msg = created > 0 ? l10n.weaveDone(created) : l10n.weaveNoNewMemory;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      await _loadCards();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.weaveGenerateFail)));
      }
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  Future<void> _openDetail(WeaveCard card) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final detail = await _api.getWeaveCardDetail(card.id);
      if (!mounted) return;
      // Aurora P8：详情底部弹层 → FloatingSheet（内容/操作保留）
      await showFloatingSheet<void>(
        context: context,
        child: WeaveDetailSheet(
          card: detail,
          onDelete: () async {
            await _deleteCard(detail.id);
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

  bool _deduping = false;

  /// 提取后端错误原因（优先 detail，其次响应正文，最后异常消息）
  String _errMsg(Object e, AppLocalizations l10n) {
    if (e is DioException) {
      final data = e.response?.data;
      if (data is Map && data['detail'] != null) return '${data['detail']}';
      if (data is String && data.trim().isNotEmpty) return data.trim();
      final msg = e.message;
      if (msg != null && msg.trim().isNotEmpty) return msg.trim();
      return l10n.weaveNetworkFail(e.type.name);
    }
    return e.toString();
  }

  /// 查重：调后端返回重复组预览，弹小窗展示（含执行去重按钮）
  Future<void> _checkDup() async {
    if (_cards.isEmpty) return;
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await _api.dedupWeaveCardsCheck(domain: _domain);
      if (!mounted) return;
      final groups = (r['groups'] as List? ?? []).cast<Map<String, dynamic>>();
      if (groups.isEmpty) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.weaveNoDuplicates)));
        return;
      }
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (_) => _DedupSheet(
          groups: groups,
          onDedup: () async {
            await _dedup();
            if (mounted) Navigator.of(context).pop();
          },
        ),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.weaveDedupCheckFail(_errMsg(e, l10n)))),
        );
      }
    }
  }

  /// 去重：确认后执行合并删除并刷新
  Future<void> _dedup() async {
    if (_deduping) return;
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        final dlgL10n = AppLocalizations.of(ctx)!;
        return AlertDialog(
          title: Text(dlgL10n.weaveDedup),
          content: Text(dlgL10n.weaveDedupConfirm),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(dlgL10n.cancel),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(dlgL10n.weaveExecuteDedup),
            ),
          ],
        );
      },
    );
    if (ok != true || !mounted) return;
    setState(() => _deduping = true);
    try {
      final r = await _api.dedupWeaveCards(domain: _domain);
      if (!mounted) return;
      final groups = r['groups'] as int? ?? 0;
      final removed = r['removed'] as int? ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(groups > 0 ? l10n.weaveDedupMerged(groups, removed) : l10n.weaveNoDuplicates)),
      );
      await _loadCards();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.weaveDedupFail(_errMsg(e, l10n)))),
        );
      }
    } finally {
      if (mounted) setState(() => _deduping = false);
    }
  }

  Future<void> _deleteCard(int id) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        final dlgL10n = AppLocalizations.of(ctx)!;
        return AlertDialog(
          title: Text(dlgL10n.weaveDeleteCard),
          content: Text(dlgL10n.weaveDeleteCardConfirm),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(dlgL10n.cancel),
            ),
            TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(dlgL10n.delete),
            ),
          ],
        );
      },
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteWeaveCard(id);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.deleted)));
      await _loadCards();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(l10n.deleteFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        // Aurora P8 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
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
        centerTitle: true,
        title: Text(
          l10n.weaveLibraryTitle,
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
        ),
        actions: [
          IconButton(
            tooltip: l10n.weaveOrganizeGenerate,
            onPressed: _generating ? null : _generate,
            icon: _generating
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_awesome, color: AppColors.accent),
          ),
          IconButton(
            tooltip: l10n.weaveCanvas,
            icon: const Icon(Icons.hub_outlined, color: AppColors.accent),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => WeaveCanvasScreen(initialCharacterId: _selectedCharId, domain: _domain),
                ),
              );
            },
          ),
        ],
      ),
      body: Column(
        children: [
          _buildDomainSwitch(),
          _buildRoleChips(),
          _buildTools(),
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildDomainSwitch() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
      child: SegmentedButton<String>(
        segments: [
          ButtonSegment(value: 'shared', label: Text(l10n.weaveAllDomain), icon: const Icon(Icons.all_inclusive, size: 16)),
          ButtonSegment(value: 'private', label: Text(l10n.weavePrivateDomain), icon: const Icon(Icons.person_outline, size: 16)),
        ],
        selected: {_domain},
        showSelectedIcon: false,
        style: const ButtonStyle(
          visualDensity: VisualDensity.compact,
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
        onSelectionChanged: (sel) {
          setState(() => _domain = sel.first);
          _loadCards();
        },
      ),
    );
  }

  Widget _buildTools() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      child: Row(
        children: [
          OutlinedButton.icon(
            onPressed: _cards.isEmpty || _deduping ? null : _checkDup,
            icon: const Icon(Icons.fact_check_outlined, size: 16),
            label: Text(l10n.weaveCheckDup),
            style: OutlinedButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              foregroundColor: AppColors.accent,
              side: const BorderSide(color: AppColors.accent, width: 1),
            ),
          ),
          const SizedBox(width: 8),
          OutlinedButton.icon(
            onPressed: _cards.isEmpty || _deduping ? null : _dedup,
            icon: _deduping
                ? const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_fix_high, size: 16),
            label: Text(l10n.weaveDedup),
            style: OutlinedButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              foregroundColor: AppColors.accent,
              side: const BorderSide(color: AppColors.accent, width: 1),
            ),
          ),
          const Spacer(),
          Text(
            l10n.weaveCardCount(_cards.length),
            style: const TextStyle(fontSize: 11.5, color: AppColors.textTertiary),
          ),
        ],
      ),
    );
  }

  Widget _buildRoleChips() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: SizedBox(
        height: 34,
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: [
            _chip(l10n.emotionAll, _selectedCharId == null, () {
              setState(() => _selectedCharId = null);
              _loadCards();
            }),
            for (final ch in _characters)
              _chip(ch.name, _selectedCharId == ch.id, () {
                setState(() => _selectedCharId = ch.id);
                _loadCards();
              }),
          ],
        ),
      ),
    );
  }

  Widget _chip(String label, bool selected, VoidCallback onTap) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(17),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? AppColors.accent : AppColors.bgLight,
            borderRadius: BorderRadius.circular(17),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13,
              color: selected ? Colors.white : AppColors.textStrong,
              fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
            ),
          ),
        ),
      ),
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
            Text(_error!, style: const TextStyle(fontSize: 14, color: AppColors.textSecondary)),
            TextButton(onPressed: _loadCards, child: Text(l10n.retry)),
          ],
        ),
      );
    }
    if (_cards.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.blur_circular, size: 56, color: AppColors.textTertiary),
            const SizedBox(height: 12),
            Text(
              l10n.weaveNoMemoryCards,
              style: const TextStyle(fontSize: 14, color: AppColors.textSecondary),
            ),
            const SizedBox(height: 6),
            Text(
              l10n.weaveTapTopRightGenerate,
              style: const TextStyle(fontSize: 12, color: AppColors.textTertiary),
            ),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _loadCards,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        itemCount: _cards.length,
        itemBuilder: (_, i) => _CardTile(
          card: _cards[i],
          onTap: () => _openDetail(_cards[i]),
        ),
      ),
    );
  }
}

/// 查重结果小窗：列出重复组（保留 + 重复），底部执行去重
class _DedupSheet extends StatelessWidget {
  final List<Map<String, dynamic>> groups;
  final VoidCallback onDedup;

  const _DedupSheet({required this.groups, required this.onDedup});

  String _title(Map<String, dynamic> c) => c['title'] as String? ?? '';
  int _mem(Map<String, dynamic> c) => (c['memory_count'] as int?) ?? 0;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final total = groups.fold<int>(0, (s, g) => s + ((g['duplicates'] as List?)?.length ?? 0));
    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.72,
      ),
      decoration: const BoxDecoration(
        color: AppColors.bgLight,
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.border,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            l10n.weaveDedupResult(groups.length, total),
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          Text(
            l10n.weaveDedupResultDesc,
            style: const TextStyle(fontSize: 11.5, color: AppColors.textSecondary),
          ),
          const SizedBox(height: 10),
          Flexible(
            child: ListView(
              shrinkWrap: true,
              children: [
                for (final g in groups) ...[
                  Container(
                    padding: const EdgeInsets.all(10),
                    margin: const EdgeInsets.only(bottom: 8),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.check_circle, size: 16, color: AppColors.success),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                l10n.weaveKeepTitle(_title(g['keeper'] as Map<String, dynamic>), _mem(g['keeper'] as Map<String, dynamic>)),
                                style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        for (final d in (g['duplicates'] as List? ?? []).cast<Map<String, dynamic>>())
                          Padding(
                            padding: const EdgeInsets.only(left: 22, bottom: 3),
                            child: Text(
                              l10n.weaveMergeTitle(_title(d), _mem(d)),
                              style: const TextStyle(fontSize: 12, color: AppColors.textSecondary),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: onDedup,
              style: FilledButton.styleFrom(
                minimumSize: const Size.fromHeight(44),
                backgroundColor: AppColors.accent,
              ),
              child: Text(l10n.weaveExecuteDedup),
            ),
          ),
        ],
      ),
    );
  }
}

class _CardTile extends StatelessWidget {
  final WeaveCard card;
  final VoidCallback onTap;

  const _CardTile({required this.card, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    // Aurora P8：卡片容器 → AuroraCard（圆角 16 + 投影）
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AuroraCard(
        onTap: onTap,
        padding: const EdgeInsets.all(14),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      card.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        color: AppColors.textPrimary,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppColors.accent.withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(9),
                    ),
                    child: Text(
                      l10n.memoryCount(card.memoryCount),
                      style: const TextStyle(fontSize: 10.5, color: AppColors.accent),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                card.summary,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12.5, height: 1.45, color: AppColors.textGray),
              ),
              const SizedBox(height: 10),
              Text(
                formatInTz(card.createdAt),
                style: const TextStyle(fontSize: 11, color: AppColors.textTertiary),
              ),
            ],
          ),
        ),
    );
  }
}
