// 只读工作台（M1a-2）· 动态阅读面板：
// 列表 GET /api/v1/moments（skip/limit；**无 character_id 过滤——接口本身没有，不伪造**），
// 详情抽屉评论 GET /{id}/comments（树形，时间升序）。
// 纪律：
// - 后端 total＝本页条数（非全量总数）；且 **DB 先按 skip/limit 取页、再在 Python 层按角色
//   活性过滤** ⇒ 空/短可见页之后仍可能有内容，**不能把空/短页当末尾**；
// - 续页用**原始窗口游标**推进（服务端已消费的行数），按稳定 id 去重只影响展示、不作 offset；
//   无可靠穷尽元数据时保留**用户可控的「继续查找」**入口并说明已加载范围，
//   不后台自动扫、不假称已读全、不伪造接口没有的过滤；
// - 作者区分：sender_type=='ai' → 角色（characterName 原文）；=='user' → 用户昵称原文；
// - 时间显示按 author_tz_offset：UTC naive 加偏移小时数格式化，不做系统时区换算；
// - 图片动态：列表/详情加载受控 image_url，同时保留 image_desc 文本；图片加载失败显示占位；点赞数只读，不调用点赞写接口。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';
import '../../widgets/reader_async_view.dart';
import 'reader_state.dart';

/// UTC naive 时间串按作者时区偏移格式化（解析失败回退原文，不猜不换算错）。
String formatAuthorTime(String utcNaive, int tzOffset) {
  final dt = DateTime.tryParse(utcNaive.endsWith('Z') ? utcNaive : '${utcNaive}Z');
  if (dt == null) return utcNaive;
  final local = dt.toUtc().add(Duration(hours: tzOffset));
  String two(int v) => v.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} '
      '${two(local.hour)}:${two(local.minute)}';
}

class MomentsPane extends StatefulWidget {
  const MomentsPane({
    super.key,
    required this.client,
    required this.editor,
    required this.myUserId,
    required this.imagePicker,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final EditorApi editor;
  final int? myUserId;
  final ImageFilePicker imagePicker;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<MomentsPane> createState() => _MomentsPaneState();
}

class _MomentsPaneState extends State<MomentsPane> {
  static const int _limit = 20;

  late final ReaderApi _api = ReaderApi(widget.client);
  late final ReaderLoader<List<ReaderMoment>> _loader =
      ReaderLoader<List<ReaderMoment>>();

  /// 服务端**原始窗口游标**（已消费的 DB 行数）：`skip` 的来源。
  /// 不是 `_items.length`——可见条数已被过滤/去重，与窗口脱钩，不能当 offset。
  /// 每个成功页推进一个 `_limit`（DB 已消费整窗；若 DB 已到末尾，多推进也无害）。
  int _serverSkip = 0;

  bool _loadingMore = false;
  String? _moreError;
  CancelToken? _moreCt;
  Object? _moreBusyOwner;
  int _gen = 0;

  List<ReaderMoment> get _items => _loader.data ?? const [];

  /// 空/短**可见**页都不代表末尾（后端 `total` 只是本页条数，且 DB 分页后按角色活性过滤）
  /// ⇒ **首屏与每次续页必须用同一空态语义**：永不进入 empty 终态，继续入口一律由页脚提供。
  /// （首审 P2：续页曾用默认判据，连续两窗为空即被封死。）
  bool _neverEmpty(List<ReaderMoment> _) => false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _gen++; // 作废在飞续页
    _cancelMore();
    _loader.dispose();
    super.dispose();
  }

  void _cancelMore() {
    final c = _moreCt;
    _moreCt = null;
    if (c != null && !c.isCancelled) c.cancel('superseded');
  }

  /// 刷新：作废在飞续页 + 清游标。这里**不做 empty 终态**——首可见空页可能只是
  /// 被过滤掉的 DB 页，后页仍可能有内容，入口交给 dataBuilder 的「继续查找」。
  Future<void> _reload() async {
    final gen = ++_gen;
    _cancelMore();
    _moreBusyOwner = null;
    _loadingMore = false;
    _moreError = null;
    final ok = await _loader.load(
      (ct) => _api.listMoments(skip: 0, limit: _limit, cancelToken: ct)
          .then((p) => p.items),
      isEmpty: _neverEmpty,
    );
    if (!ok || !mounted || gen != _gen) return;
    setState(() => _serverSkip = _limit); // 首窗 0..limit-1 已被服务端消费
  }

  /// 续页：独立 CancelToken + 代次归属 + busy 归属；推进原始窗口，重复页去重不回退。
  Future<void> _loadMore() async {
    if (_loadingMore) return; // 同一窗不重入
    final gen = _gen;
    final owner = Object();
    final ct = CancelToken();
    _moreCt = ct;
    _moreBusyOwner = owner;
    setState(() {
      _loadingMore = true;
      _moreError = null;
    });
    try {
      final page = await _api.listMoments(
        skip: _serverSkip,
        limit: _limit,
        cancelToken: ct,
      );
      if (!mounted || gen != _gen) return; // 旧刷新：整页丢弃（连游标一起）
      final merged = <ReaderMoment>[..._items];
      final seen = merged.map((e) => e.id).toSet();
      for (final m in page.items) {
        if (seen.add(m.id)) merged.add(m); // 去重只影响展示
      }
      _loader.commitData(merged, isEmpty: _neverEmpty);
      setState(() => _serverSkip = _serverSkip + _limit); // 原始窗口推进
    } on Object catch (e) {
      if (!mounted || gen != _gen || isReaderCancel(e)) return;
      setState(() => _moreError = localizedError(e, widget.cs));
    } finally {
      if (identical(_moreCt, ct)) _moreCt = null;
      if (identical(_moreBusyOwner, owner)) {
        _moreBusyOwner = null;
        if (mounted) setState(() => _loadingMore = false); // 旧 finally 不得清新 busy
      }
    }
  }

  /// 「继续查找后续窗口」页脚：接口没有可靠穷尽元数据（`total` 只是本页条数），
  /// 所以这里**不判断末尾**——只如实说明已加载数量，由用户决定是否再查一窗。
  Widget _moreFooter(ReaderStrings rs) {
    final theme = Theme.of(context);
    if (_loadingMore) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    return Padding(
      padding: const EdgeInsets.all(12),
      child: Column(children: [
        Text('${rs.t('loaded')} ${_items.length} · ${rs.t('moreMayExist')}',
            style: theme.textTheme.bodySmall),
        if (_moreError != null) ...[
          const SizedBox(height: 6),
          Text(_moreError!, style: TextStyle(color: theme.colorScheme.error)),
        ],
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: _loadMore,
          child: Text(_moreError != null ? rs.t('retry') : rs.t('loadMore')),
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return ReaderAsyncView<List<ReaderMoment>>(
      loader: _loader,
      rs: rs,
      cs: widget.cs,
      onRetry: _reload,
      dataBuilder: (items) => ListView(padding: const EdgeInsets.all(8), children: [
        Align(
          alignment: Alignment.centerRight,
          child: Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: FilledButton.icon(
              onPressed: _publish,
              icon: const Icon(Icons.add, size: 18),
              label: Text(rs.t('publishMoment')),
            ),
          ),
        ),
        // 空/短可见页**不是**终点（后端 DB 分页后再按角色活性过滤）：显示空态文案，
        // 但保留「继续查找」入口，不把用户堵在死页上
        if (items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 24),
            child: Center(child: Text(rs.t('empty'))),
          ),
        for (final m in items)
          Card(
            child: InkWell(
              onTap: () => _openDetail(m),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(
                      child: Text(m.characterName,
                          style: theme.textTheme.titleSmall,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis),
                    ),
                    Text(formatAuthorTime(m.createdAt, m.authorTzOffset),
                        style: theme.textTheme.bodySmall),
                  ]),
                  const SizedBox(height: 6),
                  Text(m.content, maxLines: 4, overflow: TextOverflow.ellipsis),
                  if (m.imageUrl != null && m.imageUrl!.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: GestureDetector(
                        onTap: () => _previewImage(_imageUrl(m.imageUrl!)),
                        child: Image.network(
                          _imageUrl(m.imageUrl!),
                          height: 180,
                          width: double.infinity,
                          fit: BoxFit.cover,
                          errorBuilder: (_, __, ___) => Container(
                            height: 120,
                            alignment: Alignment.center,
                            color: theme.colorScheme.surfaceContainerHighest,
                            child: Text(rs.t('imageUnavailable'),
                                style: theme.textTheme.bodySmall),
                          ),
                        ),
                      ),
                    ),
                  ],
                  if (m.imageDesc != null) ...[
                    const SizedBox(height: 6),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(8),
                      color: theme.colorScheme.surfaceContainerHighest,
                      child: Text('${rs.t('imageDesc')}：${m.imageDesc}',
                          style: theme.textTheme.bodySmall),
                    ),
                  ],
                  const SizedBox(height: 6),
                  Row(children: [
                    TextButton.icon(
                      onPressed: () => _toggleLike(m),
                      icon: Icon(m.likedByMe ? Icons.favorite : Icons.favorite_border,
                          size: 18),
                      label: Text('${rs.t('likes')} ${m.likesCount}'),
                    ),
                    const SizedBox(width: 8),
                    Text('${rs.t('comments')} ${m.comments.length}'),
                    if (_mine(m)) ...[
                      const Spacer(),
                      IconButton(
                        tooltip: rs.t('delete'),
                        onPressed: () => _deleteMoment(m),
                        icon: const Icon(Icons.delete_outline, size: 18),
                      ),
                    ],
                  ]),
                ]),
              ),
            ),
          ),
        _moreFooter(rs),
      ]),
    );
  }

  Future<void> _toggleLike(ReaderMoment m) async {
    try {
      await widget.editor.likeMoment(m.id);
      if (mounted) _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

    void _previewImage(String url) {
    showDialog<void>(
      context: context,
      barrierColor: Colors.black87,
      builder: (_) => Dialog.fullscreen(
        backgroundColor: Colors.black,
        child: Stack(children: [
          Center(
            child: InteractiveViewer(
              maxScale: 4,
              child: Image.network(url,
                  errorBuilder: (_, __, ___) => Text(
                      widget.rs.t('imageUnavailable'),
                      style: const TextStyle(color: Colors.white70))),
            ),
          ),
          Positioned(
            top: 12,
            right: 12,
            child: IconButton(
              icon: const Icon(Icons.close, color: Colors.white),
              onPressed: () => Navigator.pop(context),
            ),
          ),
        ]),
      ),
    );
  }

String _imageUrl(String url) {
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    final base = widget.client.dio.options.baseUrl;
    return url.startsWith('/') ? '$base$url' : '$base/$url';
  }

  Future<void> _publish() async {
    final ctrl = TextEditingController();
    PickedImageFile? pickedImage;
    final draft = await showDialog<({String text, PickedImageFile? image})>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: Text(widget.rs.t('publishMoment')),
          content: SizedBox(
            width: 420,
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              TextField(
                controller: ctrl,
                maxLines: 5,
                autofocus: true,
                decoration: InputDecoration(hintText: widget.rs.t('momentHint')),
              ),
              const SizedBox(height: 8),
              Row(children: [
                OutlinedButton.icon(
                  onPressed: () async {
                    final p = await widget.imagePicker.pick();
                    if (p == null) return;
                    if (!ctx.mounted) return;
                    if (p.reject != null || !p.accepted) {
                      ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(content: Text(widget.rs.t('imageRejected'))));
                      return;
                    }
                    setDlg(() => pickedImage = p);
                  },
                  icon: const Icon(Icons.image_outlined, size: 18),
                  label: Text(widget.rs.t('addImage')),
                ),
                if (pickedImage != null) ...[
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(pickedImage!.name,
                        maxLines: 1, overflow: TextOverflow.ellipsis,
                        style: Theme.of(ctx).textTheme.bodySmall),
                  ),
                  IconButton(
                    onPressed: () => setDlg(() => pickedImage = null),
                    icon: const Icon(Icons.close, size: 16),
                    tooltip: widget.rs.t('cancel'),
                  ),
                ],
              ]),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx), child: Text(widget.rs.t('cancel'))),
            FilledButton(
              onPressed: () {
                final v = ctrl.text.trim();
                if (v.isEmpty && pickedImage == null) return;
                Navigator.pop(ctx, (text: v, image: pickedImage));
              },
              child: Text(widget.rs.t('publishMoment')),
            ),
          ],
        ),
      ),
    );
    if (draft == null) return;
    try {
      await widget.editor.publishUserMoment(draft.text,
          imageBytes: draft.image?.bytes, imageName: draft.image?.name);
      if (!mounted) return;
      _reload();
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(widget.rs.t('momentPublished'))));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('publishFailed')}：$e')));
    }
  }

  bool _mine(ReaderMoment m) =>
      widget.myUserId != null &&
      m.senderType == 'user' &&
      m.userId == widget.myUserId;

  Future<void> _deleteMoment(ReaderMoment m) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('delete')),
        content: Text(widget.rs.t('deleteMomentBody')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.editor.deleteMoment(m.id);
      if (mounted) _reload();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  void _openDetail(ReaderMoment m) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.9,
        child: _MomentDetailSheet(
          moment: m, api: _api, editor: widget.editor,
          myUserId: widget.myUserId, rs: widget.rs, cs: widget.cs,
        ),
      ),
    );
  }
}

/// 动态详情抽屉：全文 + 点赞名只读 + 评论树（独立加载态，实时拉 GET /{id}/comments）。
class _MomentDetailSheet extends StatefulWidget {
  const _MomentDetailSheet({
    required this.moment,
    required this.api,
    required this.editor,
    required this.myUserId,
    required this.rs,
    required this.cs,
  });

  final ReaderMoment moment;
  final ReaderApi api;
  final EditorApi editor;
  final int? myUserId;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<_MomentDetailSheet> createState() => _MomentDetailSheetState();
}

class _MomentDetailSheetState extends State<_MomentDetailSheet> {
  late final ReaderLoader<List<ReaderComment>> _comments =
      ReaderLoader<List<ReaderComment>>();
  final TextEditingController _commentCtrl = TextEditingController();
  bool _posting = false;
  ReaderComment? _replyTo;

  String _imageUrl(String url) {
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    final base = widget.api.client.dio.options.baseUrl;
    return url.startsWith('/') ? '$base$url' : '$base/$url';
  }

  @override
  void initState() {
    super.initState();
    _loadComments();
  }

  @override
  void dispose() {
    _commentCtrl.dispose();
    _comments.dispose();
    super.dispose();
  }

  Future<void> _postComment() async {
    final text = _commentCtrl.text.trim();
    if (text.isEmpty || _posting) return;
    setState(() => _posting = true);
    try {
      await widget.editor.postMomentComment(widget.moment.id, text,
          parentId: _replyTo?.id);
      _commentCtrl.clear();
      setState(() => _replyTo = null);
      _loadComments();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _posting = false);
    }
  }

  void _loadComments() {
    _comments.load(
        (CancelToken ct) =>
            widget.api.getMomentComments(widget.moment.id, cancelToken: ct),
        isEmpty: (l) => l.isEmpty);
  }

  bool _mineComment(ReaderComment c) =>
      widget.myUserId != null &&
      c.senderType == 'user' &&
      c.senderId == widget.myUserId;

  void _startReply(ReaderComment c) {
    setState(() => _replyTo = c);
  }

  Future<void> _deleteComment(ReaderComment c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('delete')),
        content: Text(widget.rs.t('deleteCommentBody')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(widget.rs.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await widget.editor.deleteMomentComment(widget.moment.id, c.id);
      if (mounted) _loadComments();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('actionFailed')}：$e')));
    }
  }

  Widget _commentTile(ReaderComment c, {int depth = 0}) {
    return Padding(
      padding: EdgeInsets.only(left: depth * 16.0, top: 6),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('${c.senderName} · ${c.createdAt}',
            style: const TextStyle(fontSize: 12, color: Colors.grey)),
        Text(c.content),
        Wrap(spacing: 2, children: [
          TextButton(
            onPressed: () => _startReply(c),
            child: Text(widget.rs.t('reply'), style: const TextStyle(fontSize: 11)),
          ),
          if (_mineComment(c))
            TextButton(
              onPressed: () => _deleteComment(c),
              child: Text(widget.rs.t('delete'), style: const TextStyle(fontSize: 11)),
            ),
        ]),
        for (final r in c.replies) _commentTile(r, depth: depth + 1),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final m = widget.moment;
    final theme = Theme.of(context);
    return SafeArea(
      child: ListView(padding: const EdgeInsets.all(16), children: [
        Text(m.characterName, style: theme.textTheme.titleMedium),
        Text(formatAuthorTime(m.createdAt, m.authorTzOffset),
            style: theme.textTheme.bodySmall),
        const SizedBox(height: 8),
        if (m.imageUrl != null && m.imageUrl!.isNotEmpty) ...[
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: Image.network(
              _imageUrl(m.imageUrl!),
              height: 220,
              width: double.infinity,
              fit: BoxFit.cover,
              errorBuilder: (_, __, ___) => Container(
                height: 100,
                alignment: Alignment.center,
                color: theme.colorScheme.surfaceContainerHighest,
                child: Text(rs.t('imageUnavailable'),
                    style: theme.textTheme.bodySmall),
              ),
            ),
          ),
        ],
        SelectableText(m.content),
        if (m.imageDesc != null) ...[
          const SizedBox(height: 8),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(8),
            color: theme.colorScheme.surfaceContainerHighest,
            child: Text('${rs.t('imageDesc')}：${m.imageDesc}'),
          ),
        ],
        const SizedBox(height: 8),
        Text('${rs.t('likes')} ${m.likesCount}'
            '${m.likers.isEmpty ? '' : ' · ${m.likers.join('、')}'}'
            '${m.likedByMe ? ' · ${rs.t('likedByMe')}' : ''}'),
        const Divider(height: 24),
        Text(rs.t('comments'), style: theme.textTheme.titleSmall),
        const SizedBox(height: 6),
        if (_replyTo != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(children: [
              Expanded(
                child: Text(
                  '${rs.t('replyingTo')} ${_replyTo!.senderName}',
                  style: TextStyle(fontSize: 11, color: theme.colorScheme.primary),
                ),
              ),
              TextButton(
                onPressed: () => setState(() => _replyTo = null),
                child: Text(rs.t('cancel'), style: const TextStyle(fontSize: 11)),
              ),
            ]),
          ),
        Row(children: [
          Expanded(
            child: TextField(
              controller: _commentCtrl,
              decoration: InputDecoration(hintText: rs.t('commentHint')),
            ),
          ),
          const SizedBox(width: 8),
          FilledButton(
            onPressed: _postComment,
            child: Text(rs.t('postComment')),
          ),
        ]),
        SizedBox(
          height: 300,
          child: ReaderAsyncView<List<ReaderComment>>(
            loader: _comments,
            rs: rs,
            cs: widget.cs,
            onRetry: _loadComments,
            dataBuilder: (comments) => ListView(children: [
              for (final c in comments) _commentTile(c),
            ]),
          ),
        ),
      ]),
    );
  }
}
