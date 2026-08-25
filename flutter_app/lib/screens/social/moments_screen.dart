import "dart:io";
import "package:flutter/material.dart";
import "package:image_picker/image_picker.dart";
import "package:ai_companion/l10n/app_localizations.dart";
import "../../models/moment.dart";
import "../../models/moment_comment.dart";
import "../../services/api_client.dart";
import "../../utils/beijing_time.dart";
import "../../widgets/moment_card.dart";
import "../../widgets/moment_bars.dart";
import '../../widgets/shimmer.dart';
import '../home/home_screen.dart';

class MomentsScreen extends StatefulWidget {
  const MomentsScreen({super.key});
  @override
  State<MomentsScreen> createState() => _MomentsScreenState();
}

class _MomentsScreenState extends State<MomentsScreen>
    with AutomaticKeepAliveClientMixin {
  @override
  bool get wantKeepAlive => true;
  final _api = ApiClient();
  List<Moment> _moments = [];
  bool _loading = true;
  final Map<int, List<MomentComment>> _comments = {};
  int? _replyingToMoment;
  int? _replyingToComment;
  final _commentCtrl = TextEditingController();
  final _publishCtrl = TextEditingController();
  bool _showPublishInput = false;
  File? _pendingImage;
  bool _uploadingMoment = false;
  bool _archiveMode = false;
  List<Map<String, dynamic>> _archiveDays = [];
  Set<String> _expandedDays = {};

  @override
  void initState() {
    super.initState();
    // 进入朋友圈页即上报已读，重置"回复我的"红点
    _api.markMomentsRead().catchError((_) {});
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      if (_archiveMode) {
        var data = await _api.getMomentsArchive();
        var days = (data["days"] as List).cast<Map<String, dynamic>>();
        // 默认展开今天和昨天
        var today = DateTime.now();
        var yesterday = today.subtract(const Duration(days: 1));
        var todayStr = "${today.year.toString().padLeft(4, "0")}-${today.month.toString().padLeft(2, "0")}-${today.day.toString().padLeft(2, "0")}";
        var yesterdayStr = "${yesterday.year.toString().padLeft(4, "0")}-${yesterday.month.toString().padLeft(2, "0")}-${yesterday.day.toString().padLeft(2, "0")}";
        Set<String> expanded = {todayStr, yesterdayStr};
        // 加载展开日期的评论
        _comments.clear();
        for (var day in days) {
          if (expanded.contains(day["date"] as String)) {
            for (var m in (day["moments"] as List)) {
              try { _comments[m["id"]] = await _api.getComments(m["id"] as int); } catch (_) {}
            }
          }
        }
        if (mounted) setState(() { _archiveDays = days; _expandedDays = expanded; _loading = false; });
      } else {
        var moments = await _api.getMoments();
        // 评论已随列表批量返回，消除 N+1
        _comments.clear();
        for (var m in moments) {
          _comments[m.id] = m.comments;
        }
        if (mounted) setState(() { _moments = moments; _loading = false; });
      }
    } catch (_) { if (mounted) setState(() => _loading = false); }
  }

  Future<void> _toggleViewMode() async {
    _archiveMode = !_archiveMode;
    await _load();
  }

  Future<void> _toggleLikeArchive(String dayKey, int idx, Map<String, dynamic> momentMap) async {
    var momentId = momentMap["id"] as int;
    try {
      var result = await _api.likeMoment(momentId);
      var liked = result["liked"] as bool? ?? !(momentMap["liked_by_me"] as bool? ?? false);
      var count = result["likes_count"] as int? ?? (momentMap["likes_count"] as int? ?? 0);
      if (mounted) {
        setState(() {
          var dayIdx = _archiveDays.indexWhere((d) => d["date"] == dayKey);
          if (dayIdx >= 0) {
            var newMoments = List<Map<String, dynamic>>.from(_archiveDays[dayIdx]["moments"]);
            newMoments[idx] = Map<String, dynamic>.from(newMoments[idx]);
            newMoments[idx]["likes_count"] = count;
            newMoments[idx]["liked_by_me"] = liked;
            _archiveDays[dayIdx]["moments"] = newMoments;
          }
        });
      }
    } catch (_) {}
  }

  Future<void> _toggleLike(Moment moment) async {
    var idx = _moments.indexOf(moment);
    if (idx < 0) return;
    try {
      var result = await _api.likeMoment(moment.id);
      var liked = result["liked"] as bool? ?? !moment.likedByMe;
      var count = result["likes_count"] as int? ?? moment.likesCount;
      if (mounted) setState(() { _moments[idx] = moment.copyWith(likesCount: count, likedByMe: liked); });
    } catch (_) {}
  }

  void _startReply(int momentId, {int? commentId}) {
    setState(() { _replyingToMoment = momentId; _replyingToComment = commentId; _showPublishInput = false; });
  }

  Future<void> _sendComment(int momentId) async {
    var text = _commentCtrl.text.trim();
    if (text.isEmpty) return;
    try {
      await _api.postComment(momentId, text, parentId: _replyingToComment);
      _commentCtrl.clear();
      _comments[momentId] = await _api.getComments(momentId);
      if (mounted) setState(() { _replyingToMoment = null; _replyingToComment = null; });
    } catch (_) {}
  }

  Future<void> _publishUserMoment() async {
    final l10n = AppLocalizations.of(context)!;
    var text = _publishCtrl.text.trim();
    if (text.isEmpty && _pendingImage == null) return;
    if (_uploadingMoment) return;
    setState(() => _uploadingMoment = true);
    try {
      if (_pendingImage != null) {
        await _api.publishUserMoment(text, image: _pendingImage);
      } else {
        await _api.publishUserMoment(text);
      }
      _publishCtrl.clear();
      _pendingImage = null;
      _showPublishInput = false;
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.publishFailed)));
      }
    } finally {
      if (mounted) setState(() => _uploadingMoment = false);
    }
  }

  // "+" 键：向上展开更多功能面板（未来：图片/文件等，目前占位）
  void _showMoreActions() {
    final l10n = AppLocalizations.of(context)!;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Text(l10n.moreFunctions, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  _pickImageItem(ctx, Icons.image_outlined, l10n.image),
                  _futureItem(ctx, Icons.insert_drive_file_outlined, l10n.file),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _pickImageItem(BuildContext ctx, IconData icon, String label) {
    final l10n = AppLocalizations.of(context)!;
    return Expanded(
      child: InkWell(
        onTap: () async {
          Navigator.pop(ctx);
          final picked = await ImagePicker().pickImage(
            source: ImageSource.gallery,
            maxWidth: 1920,
            maxHeight: 1920,
            imageQuality: 85,
          );
          if (picked == null) return;
          if (mounted) setState(() => _pendingImage = File(picked.path));
        },
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Column(
            children: [
              Icon(icon, size: 28, color: Colors.grey.shade500),
              const SizedBox(height: 6),
              Text(label, style: const TextStyle(fontSize: 12)),
              Text(l10n.pickOne, style: TextStyle(fontSize: 10, color: Colors.grey.shade400)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _futureItem(BuildContext ctx, IconData icon, String label) {
    final l10n = AppLocalizations.of(context)!;
    return Expanded(
      child: InkWell(
        onTap: () {
          Navigator.pop(ctx);
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(l10n.comingSoonTemplate(label))),
          );
        },
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Column(
            children: [
              Icon(icon, size: 28, color: Colors.grey.shade500),
              const SizedBox(height: 6),
              Text(label, style: const TextStyle(fontSize: 12)),
              Text(l10n.comingSoon, style: TextStyle(fontSize: 10, color: Colors.grey.shade400)),
            ],
          ),
        ),
      ),
    );
  }

  String _formatDateShort(String isoDate, {int tzOffset = 8}) {
    final l10n = AppLocalizations.of(context)!;
    try {
      // 时间按动态作者所在地区（时区）显示
      final shifted = formatInTz(isoDate, offset: tzOffset);
      var d = DateTime.parse(shifted);
      return l10n.dateMonthDay(d.month, d.day);
    } catch (_) { return isoDate.length >= 10 ? isoDate.substring(5, 10) : isoDate; }
  }

  String _formatDayHeader(String dayStr) {
    final l10n = AppLocalizations.of(context)!;
    try {
      var d = DateTime.parse(dayStr);
      var now = DateTime.now();
      if (d.year == now.year && d.month == now.month && d.day == now.day) return l10n.today;
      var yesterday = now.subtract(const Duration(days: 1));
      if (d.year == yesterday.year && d.month == yesterday.month && d.day == yesterday.day) return l10n.yesterday;
      return l10n.dateFull(d.year, d.month, d.day);
    } catch (_) { return dayStr; }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(
        leading: IconButton(icon: const Icon(Icons.menu), onPressed: () => AppDrawerController.toggle(), tooltip: l10n.menu),
        title: Text(l10n.moments),
        actions: [
          IconButton(
            icon: Icon(_archiveMode ? Icons.view_stream : Icons.calendar_view_day),
            tooltip: _archiveMode ? l10n.listMode : l10n.dateArchive,
            onPressed: _toggleViewMode,
          ),
          IconButton(
            icon: const Icon(Icons.add_circle_outline),
            tooltip: l10n.publishMoment,
            onPressed: () => setState(() => _showPublishInput = !_showPublishInput),
          ),
        ],
      ),
      body: Column(
        children: [
          if (_showPublishInput)
            MomentPublishBar(
              controller: _publishCtrl,
              pendingImage: _pendingImage,
              uploading: _uploadingMoment,
              onRemoveImage: () => setState(() => _pendingImage = null),
              onShowMore: _showMoreActions,
              onPublish: _publishUserMoment,
            ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _load,
              child: _loading
                  ? const MomentsSkeleton()
                  : _archiveMode ? _buildArchiveView() : _buildFlatView(),
            ),
          ),
          if (_replyingToMoment != null)
            MomentCommentBar(
              controller: _commentCtrl,
              replyingToComment: _replyingToComment != null,
              onSend: () => _sendComment(_replyingToMoment!),
              onClose: () => setState(() { _replyingToMoment = null; _replyingToComment = null; _commentCtrl.clear(); }),
              onShowMore: _showMoreActions,
            ),
        ],
      ),
    );
  }

  Widget _buildFlatView() {
    final l10n = AppLocalizations.of(context)!;
    if (_moments.isEmpty) {
      return ListView(children: [const SizedBox(height: 200), Center(child: Text(l10n.noMoments, style: const TextStyle(color: Colors.grey, fontSize: 16)))]);
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      itemCount: _moments.length,
      itemBuilder: (context, index) {
        var m = _moments[index];
        return MomentCard(
          moment: m,
          comments: _comments[m.id] ?? const [],
          onLike: () => _toggleLike(m),
          onReply: () => _startReply(m.id),
          onReplyTo: (c) => _startReply(c.momentId, commentId: c.id),
          onDelete: () => _confirmDeleteMoment(m.id),
        );
      },
    );
  }

  Widget _buildArchiveView() {
    final l10n = AppLocalizations.of(context)!;
    if (_archiveDays.isEmpty) {
      return ListView(children: [const SizedBox(height: 200), Center(child: Text(l10n.noMoments, style: const TextStyle(color: Colors.grey, fontSize: 16)))]);
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      itemCount: _archiveDays.length,
      itemBuilder: (context, index) {
        var day = _archiveDays[index];
        var dateStr = day["date"] as String;
        var moments = (day["moments"] as List).cast<Map<String, dynamic>>();
        var isExpanded = _expandedDays.contains(dateStr);

        return Card(
          margin: const EdgeInsets.only(bottom: 8),
          child: Column(
            children: [
              InkWell(
                onTap: () async {
                  if (_expandedDays.contains(dateStr)) {
                    setState(() => _expandedDays.remove(dateStr));
                  } else {
                    setState(() => _expandedDays.add(dateStr));
                    // 加载该日期的评论
                    for (var m in moments) {
                      if (!_comments.containsKey(m["id"] as int)) {
                        try { _comments[m["id"]] = await _api.getComments(m["id"] as int); } catch (_) {}
                      }
                    }
                    if (mounted) setState(() {});
                  }
                },
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  child: Row(
                    children: [
                      Icon(isExpanded ? Icons.expand_less : Icons.expand_more, size: 20, color: Colors.grey.shade600),
                      const SizedBox(width: 8),
                      Text(_formatDayHeader(dateStr), style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                      const Spacer(),
                      Text(l10n.momentsCount(moments.length), style: TextStyle(fontSize: 12, color: Colors.grey.shade500)),
                    ],
                  ),
                ),
              ),
              if (isExpanded)
                ...moments.asMap().entries.map((entry) {
                  var idx = entry.key;
                  var m = entry.value;
                  return _buildArchiveMomentCard(dateStr, idx, m);
                }),
            ],
          ),
        );
      },
    );
  }

  Widget _buildArchiveMomentCard(String dayKey, int idx, Map<String, dynamic> m) {
    final l10n = AppLocalizations.of(context)!;
    var displayName = m["sender_type"] == "user" ? l10n.me : (m["character_name"] as String? ?? l10n.unknown);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            MomentAvatar(avatarUrl: m["avatar_url"] as String?, name: displayName, radius: 16),
            const SizedBox(width: 8),
            Text(displayName, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
            if (m["sender_type"] == "user")
              Container(
                margin: const EdgeInsets.only(left: 4),
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(color: Colors.green.shade100, borderRadius: BorderRadius.circular(4)),
                child: Text(l10n.me, style: TextStyle(fontSize: 10, color: Colors.green.shade700)),
              ),
            IconButton(
              icon: Icon(Icons.delete_outline, size: 16, color: Colors.grey.shade400),
              tooltip: l10n.deleteMoment,
              visualDensity: VisualDensity.compact,
              onPressed: () => _confirmDeleteMoment(m["id"] as int, dayKey: dayKey, idx: idx, archive: true),
            ),
            Text(_formatDateShort(m["created_at"] as String? ?? "", tzOffset: (m["author_tz_offset"] as num?)?.toInt() ?? 8), style: TextStyle(fontSize: 11, color: Colors.grey.shade500)),
          ]),
          const SizedBox(height: 8),
          Text(m["content"] as String? ?? "", style: const TextStyle(fontSize: 14, height: 1.4)),
          if (m["image_url"] != null && (m["image_url"] as String).isNotEmpty) ...[
            const SizedBox(height: 8),
            MomentImageView(imageUrl: m["image_url"] as String),
          ],
          const SizedBox(height: 6),
          Row(children: [
            IconButton(
              icon: Icon((m["liked_by_me"] as bool? ?? false) ? Icons.favorite : Icons.favorite_border, color: (m["liked_by_me"] as bool? ?? false) ? Colors.red : Colors.grey, size: 18),
              tooltip: l10n.like,
              onPressed: () => _toggleLikeArchive(dayKey, idx, m),
              visualDensity: VisualDensity.compact,
            ),
            if ((m["likes_count"] as int? ?? 0) > 0) Text((m["likes_count"] as int?).toString(), style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
            const SizedBox(width: 4),
            IconButton(
              icon: const Icon(Icons.chat_bubble_outline, size: 18),
              onPressed: () => _startReply(m["id"] as int),
              visualDensity: VisualDensity.compact,
            ),
            if (_comments[m["id"] as int] != null && _comments[m["id"] as int]!.isNotEmpty)
              Text(_comments[m["id"] as int]!.length.toString(), style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
          ]),
          if (_comments.containsKey(m["id"] as int) && _comments[m["id"] as int]!.isNotEmpty)
            MomentCommentsSection(
              comments: _comments[m["id"]]!,
              onReply: () => _startReply(m["id"] as int),
              onReplyTo: (c) => _startReply(c.momentId, commentId: c.id),
            ),
          const Divider(height: 1),
        ],
      ),
    );
  }

  Future<void> _confirmDeleteMoment(int momentId, {String? dayKey, int? idx, bool archive = false}) async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.deleteMoment),
        content: Text(l10n.deleteMomentConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: Text(l10n.delete),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await _api.deleteMoment(momentId);
      if (mounted) {
        if (archive && dayKey != null && idx != null) {
          setState(() {
            var dayIdx = _archiveDays.indexWhere((d) => d["date"] == dayKey);
            if (dayIdx >= 0) {
              var moments = List<Map<String, dynamic>>.from(_archiveDays[dayIdx]["moments"]);
              if (idx < moments.length) moments.removeAt(idx);
              _archiveDays[dayIdx]["moments"] = moments;
            }
          });
        } else {
          setState(() {
            _moments.removeWhere((m) => m.id == momentId);
            _comments.remove(momentId);
          });
        }
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.deleteFailed)));
      }
    }
  }
}
