import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

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
        final l10n = AppLocalizations.of(context)!;
        setState(() => _restrict = !v);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.dyApprovalsRestrictFailed(e))),
        );
      }
    }
  }

  /// 记忆收紧开关卡片（列表顶部）
  Widget _buildRestrictCard() {
    final l10n = AppLocalizations.of(context)!;
    return IosCardGroup(
      title: l10n.dyApprovalsMemorySection,
      children: [
        SwitchListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          title: Text(l10n.dyMemoryTitle, style: const TextStyle(fontSize: 15)),
          subtitle: Text(l10n.dyApprovalsRestrictHint, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
          value: _restrict,
          onChanged: _saveRestrict,
        ),
      ],
    );
  }

  void _openCreator() {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController();
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.dyApprovalsAiCreate, style: const TextStyle(fontSize: 16)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.dyApprovalsPromptHint,
                style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              maxLines: 3,
              minLines: 2,
              decoration: InputDecoration(
                hintText: l10n.dyApprovalsPromptExample,
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
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(l10n.cancel)),
          FilledButton.tonal(
            onPressed: () {
              final hint = ctrl.text.trim();
              Navigator.pop(ctx);
              _createDraft('image_post', hint);
            },
            child: Text(l10n.dyApprovalsGenPost, style: const TextStyle(fontSize: 13)),
          ),
          FilledButton.tonal(
            onPressed: () {
              final hint = ctrl.text.trim();
              Navigator.pop(ctx);
              _createDraft('reply_comment', hint);
            },
            child: Text(l10n.dyApprovalsGenReply, style: const TextStyle(fontSize: 13)),
          ),
        ],
      ),
    );
  }

  Future<void> _createDraft(String kind, String hint) async {
    final l10n = AppLocalizations.of(context)!;
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().aiDouyinDraft(kind, hint);
      _toast((r['message'] as String?) ?? l10n.dyApprovalsDraftCreated);
      await _load();
    } catch (e) {
      _toast(l10n.dyApprovalsGenFailed(e));
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
    final l10n = AppLocalizations.of(context)!;
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().confirmDouyinTask(id);
      _toast((r['message'] as String?) ?? l10n.dyApprovalsConfirmed);
      await _load();
    } catch (e) {
      _toast(l10n.dyApprovalsConfirmFailed(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reject(int id) async {
    final l10n = AppLocalizations.of(context)!;
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await ApiClient().rejectDouyinTask(id);
      _toast(l10n.dyApprovalsRejected);
      await _load();
    } catch (e) {
      _toast(l10n.dyApprovalsRejectFailed(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pickImage(int taskId) async {
    final l10n = AppLocalizations.of(context)!;
    if (_busy) return;
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null) return;
    setState(() => _busy = true);
    try {
      final r = await ApiClient().uploadDouyinImage(taskId, picked.path);
      _toast((r['message'] as String?) ?? l10n.dyApprovalsImageUploaded);
      await _load();
    } catch (e) {
      _toast(l10n.dyApprovalsUploadFailed(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.dyApprovalsTitle),
        actions: [
          IconButton(
            tooltip: l10n.dyApprovalsAiCreate,
            icon: const Icon(Icons.edit_note),
            onPressed: _busy ? null : _openCreator,
          ),
          IconButton(
            tooltip: l10n.refresh,
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
                      OutlinedButton(onPressed: _load, child: Text(l10n.retry)),
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
                                _upcoming.isEmpty ? l10n.dyApprovalsEmpty : l10n.dyApprovalsEmptyDraft,
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
    final l10n = AppLocalizations.of(context)!;
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
                  Text(l10n.dyApprovalsCountdown(_upcoming.length),
                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold)),
                ],
              ),
              const SizedBox(height: 4),
              Text(l10n.dyApprovalsCountdownHint,
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
    final l10n = AppLocalizations.of(context)!;
    final kind = u['kind'] as String? ?? '';
    final kindLabel = kind == 'image_post' ? l10n.dyKindImage : l10n.dyKindReply;
    final content = kind == 'reply_comment'
        ? l10n.dyApprovalsReplyTo(u['commenter'] ?? '', u['content'] ?? '')
        : '${u['content'] ?? ''}';
    final status = u['status'] as String? ?? '';
    final running = status == 'running';
    final eta = _etaOf(u);
    final remaining = running
        ? l10n.dyApprovalsPublishing
        : (eta <= 0 ? l10n.dyApprovalsSoon : _formatCountdown(eta));
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
    final l10n = AppLocalizations.of(context)!;
    if (seconds <= 0) return l10n.dyApprovalsSoon;
    final h = seconds ~/ 3600;
    final m = (seconds % 3600) ~/ 60;
    final s = seconds % 60;
    if (h > 0) return l10n.dyApprovalsHourMin(h, m);
    if (m > 0) return l10n.dyApprovalsMinSec(m, s);
    return l10n.dyApprovalsSec(s);
  }

  Widget _buildItem(Map<String, dynamic> p) {
    final l10n = AppLocalizations.of(context)!;
    final kind = p['kind'] as String? ?? '';
    final kindLabel = kind == 'image_post' ? l10n.dyApprovalsKindPost : l10n.dyApprovalsKindReplyComment;
    final content = kind == 'reply_comment'
        ? l10n.dyApprovalsReplyTo(p['commenter'] ?? '', p['content'] ?? '')
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
                    child: Text((p['is_fan'] as bool? ?? false) ? l10n.dyApprovalsFan : l10n.dyApprovalsNotFan,
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
                        images.isEmpty ? l10n.dyApprovalsNoImage : l10n.dyApprovalsImageCount(images.length),
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
                      label: Text(l10n.dyApprovalsChooseImage, style: const TextStyle(fontSize: 11)),
                    ),
                  ],
                ),
              ),
            ] else if (images.isNotEmpty) ...[
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(l10n.dyApprovalsImageCount(images.length), style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
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
                  child: Text(l10n.dyApprovalsConfirmBtn, style: const TextStyle(fontSize: 12)),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: _busy ? null : () => _reject(p['id'] as int),
                  style: OutlinedButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                      padding: const EdgeInsets.symmetric(horizontal: 12)),
                  child: Text(l10n.dyApprovalsRejectBtn, style: const TextStyle(fontSize: 12)),
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
