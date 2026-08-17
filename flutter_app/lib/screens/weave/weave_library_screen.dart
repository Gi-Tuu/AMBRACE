import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../models/character.dart';
import '../../models/weave_card.dart';
import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/weave_detail_sheet.dart';
import 'weave_canvas_screen.dart';

/// 织库：全景记忆卡片列表（Phase A）
class WeaveLibraryScreen extends StatefulWidget {
  const WeaveLibraryScreen({super.key});

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
        _error = '加载失败，请重试';
        _loading = false;
      });
    }
  }

  Future<void> _generate() async {
    setState(() => _generating = true);
    try {
      final r = await _api.generateWeaveCards(characterId: _selectedCharId, domain: _domain);
      if (!mounted) return;
      final created = r['created'] as int? ?? 0;
      final msg = created > 0 ? '已织好 $created 张卡片' : '没有新的可整理记忆';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
      await _loadCards();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('整理失败，请稍后重试')));
      }
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  Future<void> _openDetail(WeaveCard card) async {
    try {
      final detail = await _api.getWeaveCardDetail(card.id);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        backgroundColor: Colors.transparent,
        builder: (_) => WeaveDetailSheet(
          card: detail,
          onDelete: () async {
            await _deleteCard(detail.id);
          },
        ),
      );
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('详情加载失败')));
      }
    }
  }

  bool _deduping = false;

  /// 提取后端错误原因（优先 detail，其次响应正文，最后异常消息）
  String _errMsg(Object e) {
    if (e is DioException) {
      final data = e.response?.data;
      if (data is Map && data['detail'] != null) return '${data['detail']}';
      if (data is String && data.trim().isNotEmpty) return data.trim();
      final msg = e.message;
      if (msg != null && msg.trim().isNotEmpty) return msg.trim();
      return '网络请求失败（${e.type.name}）';
    }
    return e.toString();
  }

  /// 查重：调后端返回重复组预览，弹小窗展示（含执行去重按钮）
  Future<void> _checkDup() async {
    if (_cards.isEmpty) return;
    try {
      final r = await _api.dedupWeaveCardsCheck(domain: _domain);
      if (!mounted) return;
      final groups = (r['groups'] as List? ?? []).cast<Map<String, dynamic>>();
      if (groups.isEmpty) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('未发现重复卡片')));
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
          SnackBar(content: Text('查重失败：${_errMsg(e)}')),
        );
      }
    }
  }

  /// 去重：确认后执行合并删除并刷新
  Future<void> _dedup() async {
    if (_deduping) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('去重'),
        content: const Text('每组重复卡片将保留信息最全的一张，其余删除（参与记忆会合并，原始记忆不受影响）。确定执行吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('执行去重'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _deduping = true);
    try {
      final r = await _api.dedupWeaveCards(domain: _domain);
      if (!mounted) return;
      final groups = r['groups'] as int? ?? 0;
      final removed = r['removed'] as int? ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(groups > 0 ? '已合并 $groups 组，删除 $removed 张重复卡片' : '未发现重复卡片')),
      );
      await _loadCards();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('去重失败：${_errMsg(e)}')),
        );
      }
    } finally {
      if (mounted) setState(() => _deduping = false);
    }
  }

  Future<void> _deleteCard(int id) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除卡片'),
        content: const Text('仅删除织库卡片，不影响原始记忆。确定删除吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteWeaveCard(id);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('已删除')));
      await _loadCards();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('删除失败')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F7),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0.5,
        centerTitle: true,
        title: const Text(
          '织库',
          style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
        ),
        actions: [
          IconButton(
            tooltip: '整理生成',
            onPressed: _generating ? null : _generate,
            icon: _generating
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_awesome, color: Color(0xFF007AFF)),
          ),
          IconButton(
            tooltip: '画布',
            icon: const Icon(Icons.hub_outlined, color: Color(0xFF007AFF)),
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
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 4),
      child: SegmentedButton<String>(
        segments: const [
          ButtonSegment(value: 'shared', label: Text('全·织库'), icon: Icon(Icons.all_inclusive, size: 16)),
          ButtonSegment(value: 'private', label: Text('私·织库'), icon: Icon(Icons.person_outline, size: 16)),
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
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      child: Row(
        children: [
          OutlinedButton.icon(
            onPressed: _cards.isEmpty || _deduping ? null : _checkDup,
            icon: const Icon(Icons.fact_check_outlined, size: 16),
            label: const Text('查重'),
            style: OutlinedButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              foregroundColor: const Color(0xFF007AFF),
              side: const BorderSide(color: Color(0xFF007AFF), width: 1),
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
            label: const Text('去重'),
            style: OutlinedButton.styleFrom(
              visualDensity: VisualDensity.compact,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              foregroundColor: const Color(0xFF007AFF),
              side: const BorderSide(color: Color(0xFF007AFF), width: 1),
            ),
          ),
          const Spacer(),
          Text(
            '${_cards.length} 张卡片',
            style: const TextStyle(fontSize: 11.5, color: Color(0xFFC7C7CC)),
          ),
        ],
      ),
    );
  }

  Widget _buildRoleChips() {
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: SizedBox(
        height: 34,
        child: ListView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: [
            _chip('全部', _selectedCharId == null, () {
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
            color: selected ? const Color(0xFF007AFF) : const Color(0xFFF2F2F7),
            borderRadius: BorderRadius.circular(17),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13,
              color: selected ? Colors.white : const Color(0xFF333333),
              fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(_error!, style: const TextStyle(fontSize: 14, color: Color(0xFF8E8E93))),
            TextButton(onPressed: _loadCards, child: const Text('重试')),
          ],
        ),
      );
    }
    if (_cards.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: const [
            Icon(Icons.blur_circular, size: 56, color: Color(0xFFC7C7CC)),
            SizedBox(height: 12),
            Text(
              '还没有织好的记忆卡片',
              style: TextStyle(fontSize: 14, color: Color(0xFF8E8E93)),
            ),
            SizedBox(height: 6),
            Text(
              '点右上角 ✨ 整理生成',
              style: TextStyle(fontSize: 12, color: Color(0xFFC7C7CC)),
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
    final total = groups.fold<int>(0, (s, g) => s + ((g['duplicates'] as List?)?.length ?? 0));
    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.72,
      ),
      decoration: const BoxDecoration(
        color: Color(0xFFF2F2F7),
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
                color: const Color(0xFFD1D1D6),
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            '查重结果：${groups.length} 组重复，将合并 $total 张',
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 4),
          const Text(
            '每组保留信息最全的一张，重复卡片合并后删除（原始记忆不受影响）',
            style: TextStyle(fontSize: 11.5, color: Color(0xFF8E8E93)),
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
                            const Icon(Icons.check_circle, size: 16, color: Color(0xFF34C759)),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                '保留：${_title(g['keeper'] as Map<String, dynamic>)}（${_mem(g['keeper'] as Map<String, dynamic>)} 条记忆）',
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
                              '合并：${_title(d)}（${_mem(d)} 条记忆）',
                              style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93)),
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
                backgroundColor: const Color(0xFF007AFF),
              ),
              child: const Text('执行去重'),
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
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
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
                        color: Color(0xFF1C1C1E),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: const Color(0xFF007AFF).withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(9),
                    ),
                    child: Text(
                      '${card.memoryCount} 条记忆',
                      style: const TextStyle(fontSize: 10.5, color: Color(0xFF007AFF)),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                card.summary,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12.5, height: 1.45, color: Color(0xFF666666)),
              ),
              const SizedBox(height: 10),
              Text(
                formatInTz(card.createdAt),
                style: const TextStyle(fontSize: 11, color: Color(0xFFC7C7CC)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
