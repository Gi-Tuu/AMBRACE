import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 抖音批准请求全局角标（AI好友 Tab 信封上的未读数）
class DouyinApprovalBadge {
  static final ValueNotifier<int> count = ValueNotifier<int>(0);
  static Future<void> refresh() async {
    try {
      final items = await ApiClient().getDouyinPending();
      count.value = items.length;
    } catch (_) {
      // 失败保持原值
    }
  }
}

/// 抖音批准请求页：AI 待发布的图文/回复草稿，人工确认后进入随机执行队列
class DouyinApprovalsScreen extends StatefulWidget {
  const DouyinApprovalsScreen({super.key});

  @override
  State<DouyinApprovalsScreen> createState() => _DouyinApprovalsScreenState();
}

class _DouyinApprovalsScreenState extends State<DouyinApprovalsScreen> {
  List<Map<String, dynamic>> _items = [];
  List<Map<String, dynamic>> _upcoming = [];
  DateTime _upcomingAt = DateTime.now();
  Timer? _ticker;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  bool _restrict = false;  // 抖音记忆收紧开关

  @override
  void initState() {
    super.initState();
    _load();
    _loadRestrict();
    // 发布倒计时每秒刷新
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _upcoming.isNotEmpty) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  /// 抖音记忆收紧开关（2026-08-15 补挂到小信封）
  Future<void> _loadRestrict() async {
    try {
      final data = await ApiClient().getDouyinProfile();
      if (mounted) setState(() => _restrict = data['memory_restrict'] == 'relationship');
    } catch (_) {
      // 失败保持原值
    }
  }

  Future<void> _saveRestrict(bool v) async {
    setState(() => _restrict = v);
    try {
      await ApiClient().updateDouyinProfile(v ? 'relationship' : 'off');
    } catch (e) {
      if (mounted) {
        setState(() => _restrict = !v);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('记忆收紧设置失败：$e')),
        );
      }
    }
  }

  /// 记忆收紧开关卡片（列表顶部）
  Widget _buildRestrictCard() {
    return IosCardGroup(
      title: '记忆',
      children: [
        SwitchListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          title: const Text('抖音记忆收紧', style: TextStyle(fontSize: 15)),
          subtitle: const Text('公开平台记忆注入时排除关系类私密记忆', style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          value: _restrict,
          onChanged: _saveRestrict,
        ),
      ],
    );
  }

  void _openCreator() {
    final ctrl = TextEditingController();
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('AI 创作', style: TextStyle(fontSize: 16)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('写点灵感或提示词（可留空，AI 会以自己的想法创作）',
                style: TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              maxLines: 3,
              minLines: 2,
              decoration: InputDecoration(
                hintText: '例如：发一条表达你最近想法的图文…',
                isDense: true,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(ctx).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton.tonal(
            onPressed: () {
              final hint = ctrl.text.trim();
              Navigator.pop(ctx);
              _createDraft('image_post', hint);
            },
            child: const Text('生成图文', style: TextStyle(fontSize: 13)),
          ),
          FilledButton.tonal(
            onPressed: () {
              final hint = ctrl.text.trim();
              Navigator.pop(ctx);
              _createDraft('reply_comment', hint);
            },
            child: const Text('生成回复', style: TextStyle(fontSize: 13)),
          ),
        ],
      ),
    );
  }

  Future<void> _createDraft(String kind, String hint) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().aiDouyinDraft(kind, hint);
      _toast((r['message'] as String?) ?? '已生成草稿');
      await _load();
    } catch (e) {
      _toast('生成失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        ApiClient().getDouyinPending(),
        ApiClient().getDouyinUpcoming(),
      ]);
      final items = (results[0] as List).cast<Map<String, dynamic>>();
      final upcoming = (results[1] as List).cast<Map<String, dynamic>>();
      if (!mounted) return;
      setState(() {
        _items = items;
        _upcoming = upcoming;
        _upcomingAt = DateTime.now();
        _loading = false;
      });
      DouyinApprovalBadge.count.value = items.length;
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _confirm(int id) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().confirmDouyinTask(id);
      _toast((r['message'] as String?) ?? '已确认');
      await _load();
    } catch (e) {
      _toast('确认失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reject(int id) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await ApiClient().rejectDouyinTask(id);
      _toast('已拒绝');
      await _load();
    } catch (e) {
      _toast('拒绝失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pickImage(int taskId) async {
    if (_busy) return;
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().uploadDouyinImage(taskId, picked.path);
      _toast((r['message'] as String?) ?? '图片已上传');
      await _load();
    } catch (e) {
      _toast('上传失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('抖音批准请求'),
        actions: [
          IconButton(
            tooltip: 'AI 创作',
            icon: const Icon(Icons.edit_note),
            onPressed: _busy ? null : _openCreator,
          ),
          IconButton(
            tooltip: '刷新',
            icon: const Icon(Icons.refresh),
            onPressed: _busy ? null : _load,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: IosCardColors.subtitle, fontSize: 12)),
                      const SizedBox(height: 8),
                      OutlinedButton(onPressed: _load, child: const Text('重试')),
                    ],
                  ),
                )
              : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.all(12),
                      children: [
                        _buildRestrictCard(),
                        const SizedBox(height: 8),
                        if (_upcoming.isNotEmpty) ...[
                          _buildUpcomingSection(),
                          const SizedBox(height: 8),
                        ],
                        if (_items.isEmpty)
                          Padding(
                            padding: const EdgeInsets.only(top: 120),
                            child: Center(
                              child: Text(
                                _upcoming.isEmpty ? '暂无待批准的抖音内容' : '暂无待批准的草稿',
                                style: const TextStyle(color: IosCardColors.subtitle),
                              ),
                            ),
                          )
                        else
                          for (final it in _items) _buildItem(it),
                      ],
                    ),
                  ),
    );
  }

  /// 发布倒计时区块：已确认待发布任务 + 剩余时间（每秒本地 tick 刷新）
  Widget _buildUpcomingSection() {
    return IosCardGroup(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.hourglass_bottom, size: 16,
                      color: Theme.of(context).colorScheme.primary),
                  const SizedBox(width: 6),
                  Text('发布倒计时（${_upcoming.length}）',
                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold)),
                ],
              ),
              const SizedBox(height: 4),
              Text('已确认，将在随机时间发布/回复，避开深夜静默',
                  style: TextStyle(fontSize: 11, color: Theme.of(context).colorScheme.onSurfaceVariant)),
              const SizedBox(height: 6),
              for (final u in _upcoming) _buildUpcomingItem(u),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildUpcomingItem(Map<String, dynamic> u) {
    final kind = u['kind'] as String? ?? '';
    final kindLabel = kind == 'image_post' ? '图文' : '回复';
    final content = kind == 'reply_comment'
        ? '回复 ${u['commenter']}：${u['content'] ?? ''}'
        : '${u['content'] ?? ''}';
    final status = u['status'] as String? ?? '';
    final running = status == 'running';
    final eta = _etaOf(u);
    final remaining = running
        ? '正在发布…'
        : (eta <= 0 ? '即将发布' : _formatCountdown(eta));
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            decoration: BoxDecoration(
              color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(kindLabel,
                style: TextStyle(fontSize: 10,
                    color: Theme.of(context).colorScheme.primary,
                    fontWeight: FontWeight.w600)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(content,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13, height: 1.3)),
                const SizedBox(height: 2),
                Text(remaining,
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: running
                          ? Theme.of(context).colorScheme.tertiary
                          : (eta <= 0
                              ? Theme.of(context).colorScheme.error
                              : Theme.of(context).colorScheme.primary),
                    )),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// 剩余秒数（拉取时的 eta_seconds 减去本地已流逝时间）
  int _etaOf(Map<String, dynamic> u) {
    final base = (u['eta_seconds'] as num?)?.toInt() ?? 0;
    final elapsed = DateTime.now().difference(_upcomingAt).inSeconds;
    return base - elapsed;
  }

  String _formatCountdown(int seconds) {
    if (seconds <= 0) return '即将发布';
    final h = seconds ~/ 3600;
    final m = (seconds % 3600) ~/ 60;
    final s = seconds % 60;
    if (h > 0) return '$h 小时 $m 分';
    if (m > 0) return '$m 分 $s 秒';
    return '$s 秒';
  }

  Widget _buildItem(Map<String, dynamic> p) {
    final kind = p['kind'] as String? ?? '';
    final kindLabel = kind == 'image_post' ? '图文发布' : '回复评论';
    final content = kind == 'reply_comment'
        ? '回复 ${p['commenter']}：${p['content'] ?? ''}'
        : '${p['content'] ?? ''}';
    final images = (p['images'] as List? ?? []).cast<String>();
    return IosCardGroup(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: Colors.amber.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(kindLabel,
                        style: const TextStyle(fontSize: 11, color: Colors.orange, fontWeight: FontWeight.w600)),
                  ),
                if (kind == 'reply_comment') ...[
                  const SizedBox(width: 6),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: (p['is_fan'] as bool? ?? false)
                          ? Colors.blue.withValues(alpha: 0.12)
                          : Colors.grey.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text((p['is_fan'] as bool? ?? false) ? '粉丝' : '非粉丝',
                        style: TextStyle(fontSize: 11,
                            color: (p['is_fan'] as bool? ?? false) ? Colors.blue : Colors.grey)),
                  ),
                ],
                const Spacer(),
                Text('#${p['id']}', style: const TextStyle(fontSize: 10, color: IosCardColors.subtitle)),
              ],
            ),
            const SizedBox(height: 6),
            Text(content, style: const TextStyle(fontSize: 14, height: 1.4)),
            if (kind == 'image_post') ...[
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        images.isEmpty ? '未配图（发布时抖音自动生成配图）' : '图片 ${images.length} 张',
                        style: TextStyle(
                            fontSize: 11,
                            color: images.isEmpty ? Colors.redAccent : IosCardColors.subtitle),
                      ),
                    ),
                    OutlinedButton.icon(
                      onPressed: _busy ? null : () => _pickImage(p['id'] as int),
                      style: OutlinedButton.styleFrom(
                          visualDensity: VisualDensity.compact,
                          padding: const EdgeInsets.symmetric(horizontal: 8)),
                      icon: const Icon(Icons.add_photo_alternate_outlined, size: 14),
                      label: const Text('选择图片', style: TextStyle(fontSize: 11)),
                    ),
                  ],
                ),
              ),
            ] else if (images.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('图片 ${images.length} 张', style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
              ),
            ],
            const SizedBox(height: 8),
            Row(
              children: [
                FilledButton.tonal(
                  onPressed: _busy ? null : () => _confirm(p['id'] as int),
                  style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 16)),
                  child: const Text('确认（随机时间发布）', style: TextStyle(fontSize: 12)),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: _busy ? null : () => _reject(p['id'] as int),
                  style: OutlinedButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 12)),
                  child: const Text('拒绝', style: TextStyle(fontSize: 12)),
                ),
              ],
            ),
          ],
        ),
        ),
      ],
    );
  }
}
