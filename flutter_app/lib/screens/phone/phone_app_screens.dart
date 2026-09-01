import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:intl/intl.dart';
import 'package:image_picker/image_picker.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';
import '../../services/api/phone_desktop_api.dart';
import '../../utils/beijing_time.dart';
import '../life/life_browsing_screen.dart';

/// 小手机应用页集合：相册 / 应用市场 / 日历 / 浏览器 / 主题 / 设置（2026-08-11）

/// Aurora P3 统一玻璃 AppBar（手机内页模式：半透明底 + 0.5px 描边，无 BackdropFilter）
AppBar _phoneGlassAppBar(
  BuildContext context, {
  Widget? title,
  List<Widget> actions = const [],
  PreferredSizeWidget? bottom,
}) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  return AppBar(
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
    title: title,
    actions: actions,
    bottom: bottom,
  );
}

// ── 相册：AI 生成图片 + 用户上传（iOS 图库风格：网格 → 全屏预览，保存/删除） ──
class AlbumScreen extends StatefulWidget {
  const AlbumScreen({super.key});
  @override
  State<AlbumScreen> createState() => _AlbumScreenState();
}

class _AlbumScreenState extends State<AlbumScreen> {
  List<String> _aiPhotos = [];
  List<String> _userPhotos = [];
  bool _loading = true;
  int _tab = 0; // 0 = AI 生成，1 = 我的上传

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await ApiClient().getPhonePhotos();
      if (!mounted) return;
      setState(() {
        _aiPhotos = (data['ai_photos'] as List? ?? []).cast<String>();
        _userPhotos = (data['user_photos'] as List? ?? []).cast<String>();
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _upload() async {
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null || !mounted) return;
    try {
      await ApiClient().uploadPhonePhoto(picked.path);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.uploadedToAlbum)));
        _load();
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.uploadFail)));
      }
    }
  }

  String _fileName(String url) => url.split('/').last;

  Future<void> _deletePhoto(String url) async {
    final source = _tab == 0 ? 'ai' : 'user';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(AppLocalizations.of(ctx)!.deletePhoto),
        content: Text(AppLocalizations.of(ctx)!.deletePhotoConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(AppLocalizations.of(ctx)!.cancel)),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(AppLocalizations.of(ctx)!.delete, style: const TextStyle(color: Colors.red)),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    try {
      await ApiClient().deletePhonePhoto(source, _fileName(url));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.deleted)));
        _load();
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.deleteFail)));
      }
    }
  }

  Future<void> _savePhoto(String url) async {
    try {
      await ApiClient().savePhonePhoto(_fileName(url));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.savedToAlbum)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.saveFail)));
      }
    }
  }

  void _openPreview(List<String> photos, int index) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _PhotoPreviewPage(
          photos: photos,
          initialIndex: index,
          isAiTab: _tab == 0,
          onSave: _savePhoto,
          onDelete: _deletePhoto,
        ),
      ),
    ).then((_) {
      if (mounted) _load(); // 返回时刷新（可能已删除/保存）
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final photos = _tab == 0 ? _aiPhotos : _userPhotos;
    return DefaultTabController(length: 2, child: Scaffold(
      appBar: _phoneGlassAppBar(
        context,
        title: Text(l10n.albumTitle),
        actions: [
          TextButton.icon(
            onPressed: _upload,
            icon: const Icon(Icons.add_photo_alternate_outlined, size: 18),
            label: Text(l10n.upload),
          ),
        ],
        bottom: TabBar(
          onTap: (v) => setState(() => _tab = v),
          tabs: [
            Tab(text: l10n.aiGenerated),
            Tab(text: l10n.myUploads),
          ],
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : photos.isEmpty
              ? _EmptyAlbum(isAiTab: _tab == 0)
              : GridView.builder(
                  padding: const EdgeInsets.all(10),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 3,
                    mainAxisSpacing: 8,
                    crossAxisSpacing: 8,
                  ),
                  itemCount: photos.length,
                  itemBuilder: (_, i) {
                    final url = photos[i];
                    return GestureDetector(
                      onTap: () => _openPreview(photos, i),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(12),
                        child: Image.network(
                          ApiClient().resolveUrl(url),
                          fit: BoxFit.cover,
                          frameBuilder: (_, child, frame, wasSyncLoaded) =>
                              frame == null
                                  ? ColoredBox(
                                      color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                      child: child,
                                    )
                                  : child,
                          errorBuilder: (_, __, ___) => ColoredBox(
                            color: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
                            child: const Icon(Icons.broken_image, color: IosCardColors.subtitle),
                          ),
                        ),
                      ),
                    );
                  },
                ),
    ));
  }
}

class _EmptyAlbum extends StatelessWidget {
  final bool isAiTab;
  const _EmptyAlbum({required this.isAiTab});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    // Aurora P3：EmptyState 统一渲染
    return EmptyState(
      icon: Icons.photo_library_outlined,
      title: isAiTab ? l10n.noAiImages : l10n.noUploadsHint,
    );
  }
}

/// 全屏预览：黑底左右滑动 + 缩放，底部 保存/删除 操作栏
class _PhotoPreviewPage extends StatefulWidget {
  final List<String> photos;
  final int initialIndex;
  final bool isAiTab;
  final Future<void> Function(String url) onSave;
  final Future<void> Function(String url) onDelete;

  const _PhotoPreviewPage({
    required this.photos,
    required this.initialIndex,
    required this.isAiTab,
    required this.onSave,
    required this.onDelete,
  });

  @override
  State<_PhotoPreviewPage> createState() => _PhotoPreviewPageState();
}

class _PhotoPreviewPageState extends State<_PhotoPreviewPage> {
  late final PageController _pageCtrl = PageController(initialPage: widget.initialIndex);
  late int _index = widget.initialIndex;

  Future<void> _delete() async {
    await widget.onDelete(widget.photos[_index]);
    if (!mounted) return;
    if (widget.photos.length <= 1) {
      Navigator.of(context).pop();
      return;
    }
    widget.photos.removeAt(_index);
    setState(() {
      if (_index >= widget.photos.length) _index = widget.photos.length - 1;
    });
    if (_pageCtrl.hasClients) _pageCtrl.jumpToPage(_index);
  }

  @override
  void dispose() {
    _pageCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final count = widget.photos.length;
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          PageView.builder(
            controller: _pageCtrl,
            itemCount: count,
            onPageChanged: (v) => setState(() => _index = v),
            itemBuilder: (_, i) => InteractiveViewer(
              minScale: 1,
              maxScale: 4,
              child: Center(
                child: Image.network(
                  ApiClient().resolveUrl(widget.photos[i]),
                  fit: BoxFit.contain,
                  errorBuilder: (_, __, ___) =>
                      const Icon(Icons.broken_image, color: Colors.white54, size: 56),
                ),
              ),
            ),
          ),
          // 顶部：关闭 + 页码
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(
              padding: EdgeInsets.only(top: MediaQuery.of(context).padding.top + 8, bottom: 12),
              decoration: const BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.black54, Colors.transparent],
                ),
              ),
              child: Row(
                children: [
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close, color: Colors.white, size: 26),
                  ),
                  const Spacer(),
                  Text(
                    '${_index + 1} / $count',
                    style: const TextStyle(color: Colors.white, fontSize: 14),
                  ),
                  const SizedBox(width: 16),
                ],
              ),
            ),
          ),
          // 底部操作栏：保存（仅 AI 生成）/ 删除
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 32, 16, 12),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    if (widget.isAiTab) ...[
                      _PreviewAction(
                        icon: Icons.download_rounded,
                        label: l10n.save,
                        onTap: () => widget.onSave(widget.photos[_index]),
                      ),
                      const SizedBox(width: 40),
                    ],
                    _PreviewAction(
                      icon: Icons.delete_outline_rounded,
                      label: l10n.delete,
                      onTap: _delete,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _PreviewAction extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  const _PreviewAction({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(22),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 9),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.18),
          borderRadius: BorderRadius.circular(22),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: Colors.white, size: 18),
            const SizedBox(width: 6),
            Text(label, style: const TextStyle(color: Colors.white, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}

// ── 应用市场：误删的应用可恢复 ──
class MarketScreen extends StatelessWidget {
  const MarketScreen({
    super.key,
    required this.catalog,
    required this.installedKeys,
    required this.onRestore,
  });

  final List<Map<String, dynamic>> catalog; // [{key,label,deletable,plugin?}]
  final Set<String> installedKeys; // 桌面当前可见应用 key
  final Future<void> Function(String key) onRestore;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.marketTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(l10n.marketHint,
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)),
          const SizedBox(height: 12),
          // Aurora P3：ListTile → AuroraCard 行（46px 图标容器 + 安装态/下载按钮）
          for (final app in catalog)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: AuroraCard(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    Container(
                      width: 46,
                      height: 46,
                      decoration: BoxDecoration(
                        color: scheme.primaryContainer,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(_appIcon(app['key'] as String), color: scheme.primary),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(app['label'] as String? ?? app['key'] as String,
                              maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600)),
                          const SizedBox(height: 2),
                          Text(_appSubtitle(app, l10n),
                              maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                  fontSize: 12, color: scheme.onSurfaceVariant)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (installedKeys.contains(app['key']))
                      Text(l10n.installed,
                          style: TextStyle(color: scheme.onSurfaceVariant))
                    else
                      FilledButton(
                        onPressed: () async {
                          await onRestore(app['key'] as String);
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text(l10n.restoredToDesktop)),
                            );
                          }
                        },
                        child: Text(l10n.download),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

// ── 日历：月视图 + 备注（AI 可查看/写备注） ──
class CalendarScreen extends StatefulWidget {
  const CalendarScreen({super.key, required this.characterId});
  final int characterId;
  @override
  State<CalendarScreen> createState() => _CalendarScreenState();
}

class _CalendarScreenState extends State<CalendarScreen> {
  late DateTime _month;
  List<Map<String, dynamic>> _notes = [];
  List<Map<String, dynamic>> _schedules = [];

  @override
  void initState() {
    super.initState();
    _month = DateTime(DateTime.now().year, DateTime.now().month);
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait<List<Map<String, dynamic>>>([
        ApiClient().getCalendarNotes(
          widget.characterId,
          month: DateFormat('yyyy-MM').format(_month),
        ),
        ApiClient().getLifeSchedules(widget.characterId, limit: 60),
      ]);
      if (!mounted) return;
      setState(() {
        _notes = results[0];
        _schedules = results[1];
      });
    } catch (_) {}
  }

  List<Map<String, dynamic>> _notesOf(String date) =>
      _notes.where((n) => n['date'] == date).toList();

  /// 某天（YYYY-MM-DD，北京时间）的 AI 日程
  List<Map<String, dynamic>> _schedulesOf(String date) => _schedules
      .where((s) => formatInTz(s['start_time'] as String? ?? '').startsWith(date))
      .toList();

  /// 备注显示：尾部带记录者署名（-xxx），避免记忆混乱（2026-08-14）
  String _noteTitle(Map<String, dynamic> n) {
    final text = (n['text'] as String? ?? '').trim();
    final author = (n['author'] as String? ?? '').trim();
    return author.isEmpty ? text : '$text  -  $author';
  }

  /// 日程开始时间显示（P0-10 修复：空串/短串不越界）
  String _schedTimeText(String raw) {
    final t = formatInTz(raw);
    return t.length >= 16 ? t.substring(11, 16) : t;
  }

  String _schedStatus(String status) {
    final l10n = AppLocalizations.of(context)!;
    return switch (status) {
      'active' => l10n.inProgress,
      'completed' => l10n.completed,
      'cancelled' => l10n.cancelled,
      'overdue' => l10n.notCompleted,
      _ => l10n.todo,
    };
  }

  Future<void> _openDay(DateTime day) async {
    final l10n = AppLocalizations.of(context)!;
    final date = DateFormat('yyyy-MM-dd').format(day);
    final dayNotes = _notesOf(date);
    final daySchedules = _schedulesOf(date);
    final ctrl = TextEditingController();
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => Padding(
        padding: EdgeInsets.only(
          left: 16, right: 16, top: 16,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 16,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(l10n.dateNotes(date), style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            const SizedBox(height: 8),
            if (daySchedules.isNotEmpty) ...[
              Text(l10n.aiSchedule, style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: Theme.of(ctx).colorScheme.primary)),
              const SizedBox(height: 4),
              ...daySchedules.map((s) => ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.event, size: 18),
                    title: Text(s['title'] as String? ?? '', style: const TextStyle(fontSize: 14)),
                    subtitle: Text('${_schedTimeText(s['start_time'] as String? ?? '')} · ${s['source'] == 'fixed_routine' ? l10n.routine : s['source'] == 'goal_derived' ? l10n.goal : l10n.arrangement}'),
                    trailing: Text(_schedStatus(s['status'] as String? ?? 'scheduled'),
                        style: TextStyle(fontSize: 12, color: Theme.of(ctx).colorScheme.primary)),
                  )),
              const SizedBox(height: 8),
            ],
            if (dayNotes.isNotEmpty)
              ...dayNotes.map((n) => ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(_noteTitle(n)),
                    trailing: IconButton(
                      icon: const Icon(Icons.delete_outline, size: 18, color: Colors.red),
                      onPressed: () async {
                        await ApiClient().deleteCalendarNote(n['id'] as int);
                        if (ctx.mounted) Navigator.pop(ctx);
                        _load();
                      },
                    ),
                  )),
            TextField(
              controller: ctrl,
              maxLines: 2,
              maxLength: 200,
              decoration: InputDecoration(
                hintText: l10n.noteHint,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
              ),
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () async {
                  final text = ctrl.text.trim();
                  if (text.isEmpty) return;
                  await ApiClient().addCalendarNote(widget.characterId, date, text);
                  if (ctx.mounted) Navigator.pop(ctx);
                  _load();
                },
                child: Text(l10n.saveNote),
              ),
            ),
          ],
        ),
      ),
    );
    ctrl.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final now = DateTime.now();
    final first = DateTime(_month.year, _month.month, 1);
    final lead = first.weekday - 1; // 周一开头
    final daysInMonth = DateTime(_month.year, _month.month + 1, 0).day;
    return Scaffold(
      appBar: _phoneGlassAppBar(
        context,
        title: Text(l10n.calendarTitle(_month.year, _month.month)),
        actions: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: () => setState(() {
              _month = DateTime(_month.year, _month.month - 1);
              _load();
            }),
          ),
          IconButton(
            icon: const Icon(Icons.chevron_right),
            onPressed: () => setState(() {
              _month = DateTime(_month.year, _month.month + 1);
              _load();
            }),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [l10n.weekday1, l10n.weekday2, l10n.weekday3, l10n.weekday4, l10n.weekday5, l10n.weekday6, l10n.weekday7]
                  .map((w) => SizedBox(width: 40, child: Text(w, textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle))))
                  .toList(),
            ),
          ),
          Expanded(
            child: GridView.builder(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: 7,
                mainAxisSpacing: 4,
                crossAxisSpacing: 4,
              ),
              itemCount: lead + daysInMonth,
              itemBuilder: (_, i) {
                if (i < lead) return const SizedBox.shrink();
                final day = i - lead + 1;
                final dt = DateTime(_month.year, _month.month, day);
                final date = DateFormat('yyyy-MM-dd').format(dt);
                final hasNotes = _notesOf(date).isNotEmpty;
                final isToday = dt.year == now.year && dt.month == now.month && dt.day == now.day;
                return InkWell(
                  onTap: () => _openDay(dt),
                  borderRadius: BorderRadius.circular(8),
                  child: Container(
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: hasNotes
                          ? Theme.of(context).colorScheme.primaryContainer.withValues(alpha: 0.7)
                          : isToday
                              ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.18)
                              : null,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text('$day', style: TextStyle(
                          fontSize: 14,
                          fontWeight: isToday ? FontWeight.bold : null,
                          color: isToday ? Theme.of(context).colorScheme.primary : null,
                        )),
                        if (hasNotes)
                          Container(
                            width: 5, height: 5,
                            decoration: const BoxDecoration(
                              color: Colors.orange, shape: BoxShape.circle),
                          ),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(8),
            child: Text(l10n.calendarHint,
                style: TextStyle(
                    fontSize: 11,
                    color: Theme.of(context).colorScheme.onSurfaceVariant)),
          ),
        ],
      ),
    );
  }
}

// ── 浏览器：搜索记录 + 历史（7 天保留，AI 上下文可见） ──
class BrowserScreen extends StatefulWidget {
  const BrowserScreen({super.key, required this.characterId, this.characterName = ''});
  final int characterId;
  final String characterName;
  @override
  State<BrowserScreen> createState() => _BrowserScreenState();
}

class _BrowserScreenState extends State<BrowserScreen> {
  final _queryCtrl = TextEditingController();
  List<Map<String, dynamic>> _history = [];
  Map<String, dynamic>? _result;
  bool _loading = true;
  bool _searching = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _queryCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final items = await ApiClient().getBrowserHistory(widget.characterId);
      if (!mounted) return;
      setState(() {
        _history = items;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _search() async {
    final q = _queryCtrl.text.trim();
    if (q.isEmpty || _searching) return;
    _queryCtrl.clear();
    setState(() {
      _searching = true;
      _result = null;
    });
    try {
      await ApiClient().addBrowserHistory(widget.characterId, q);
      final res = await ApiClient().searchWeb(q);
      if (!mounted) return;
      setState(() {
        _result = res;
        _searching = false;
      });
      _load();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _result = {'ok': false, 'message': AppLocalizations.of(context)!.searchFailDetail(e)};
        _searching = false;
      });
    }
  }

  Widget _buildResult(Map<String, dynamic> r) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final ok = r['ok'] == true;
    if (!ok) {
      // Aurora P3：失败分支 → EmptyState
      return EmptyState(
        icon: Icons.cloud_off_rounded,
        title: r['message'] as String? ?? l10n.searchFail,
      );
    }
    final results = (r['results'] as List? ?? []).cast<Map<String, dynamic>>();
    if (results.isEmpty) {
      return EmptyState(
        icon: Icons.search_off_rounded,
        title: l10n.noResultsHint,
      );
    }
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
          child: Row(children: [
            Icon(Icons.travel_explore, size: 18, color: scheme.onSurfaceVariant),
            const SizedBox(width: 6),
            Expanded(
              child: Text(r['query'] as String? ?? '',
                  style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 15,
                      color: scheme.onSurface)),
            ),
            if ((r['engine'] as String? ?? '').isNotEmpty)
              Text(l10n.sourceFrom(r['engine'] as String? ?? ''),
                  style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
          ]),
        ),
        Expanded(
          child: GridView.builder(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 16),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 2,
              mainAxisSpacing: 10,
              crossAxisSpacing: 10,
              childAspectRatio: 0.92,
            ),
            itemCount: results.length,
            itemBuilder: (_, i) => _searchResultCard(context, results[i]),
          ),
        ),
      ],
    );
  }

  /// 搜索结果固定大小卡片：标题+概要上卡，点击卡片打开真实链接。
  Widget _searchResultCard(BuildContext ctx, Map<String, dynamic> result) {
    final title = result['title'] as String? ?? '';
    final snippet = result['snippet'] as String? ?? '';
    final url = result['url'] as String? ?? '';
    String host = url;
    try {
      host = Uri.parse(url).host;
    } catch (_) {}
    return Material(
      color: Theme.of(ctx).colorScheme.surfaceContainerHighest.withValues(alpha: 0.55),
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () async {
          try {
            await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
          } catch (_) {}
        },
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, maxLines: 2, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, height: 1.3)),
              const Spacer(),
              if (snippet.isNotEmpty)
                Text(snippet, maxLines: 3, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 11.5, color: IosCardColors.subtitle, height: 1.4)),
              const SizedBox(height: 6),
              Row(children: [
                const Icon(Icons.open_in_new, size: 13, color: IosCardColors.subtitle),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(host, maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 10.5, color: IosCardColors.subtitle)),
                ),
              ]),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(
        context,
        title: Text(l10n.browserTitle),
        actions: [
          // AI 生活浏览记录（2026-08-14：入口从小手机/角色生活迁入浏览器）
          IconButton(
            icon: const Icon(Icons.auto_stories_outlined),
            tooltip: l10n.aiBrowseHistory,
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => LifeBrowsingScreen(
                  characterId: widget.characterId,
                  characterName: widget.characterName.isEmpty ? 'AI' : widget.characterName,
                ),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            // Aurora P3：搜索框主题化（圆角 24 + surfaceContainerHighest 填充）
            child: TextField(
              controller: _queryCtrl,
              decoration: InputDecoration(
                hintText: l10n.searchPlaceholder,
                prefixIcon: const Icon(Icons.search, size: 20),
                filled: true,
                fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.6),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                suffixIcon: IconButton(icon: const Icon(Icons.send, size: 20), onPressed: _search),
              ),
              onSubmitted: (_) => _search(),
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(left: 16, bottom: 4),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text(l10n.searchHistory,
                  style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant)),
            ),
          ),
          Expanded(
            child: _searching
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const CircularProgressIndicator(),
                        const SizedBox(height: 12),
                        Text(l10n.searching,
                            style: TextStyle(
                                color: scheme.onSurfaceVariant, fontSize: 13)),
                      ],
                    ),
                  )
                : _result != null
                    ? _buildResult(_result!)
                    : _loading
                        ? const Center(child: CircularProgressIndicator())
                        : _history.isEmpty
                            // Aurora P3：空历史 → EmptyState
                            ? EmptyState(
                                icon: Icons.public_rounded,
                                title: l10n.searchHint,
                              )
                            : ListView.builder(
                                padding: const EdgeInsets.symmetric(horizontal: 12),
                                itemCount: _history.length,
                                itemBuilder: (_, i) {
                                  final h = _history[i];
                                  // Aurora P3：历史行 ListTile → AuroraCard 行
                                  return Padding(
                                    padding: const EdgeInsets.only(bottom: 8),
                                    child: AuroraCard(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 12, vertical: 8),
                                      child: Row(
                                        children: [
                                          Icon(Icons.history,
                                              size: 18, color: scheme.onSurfaceVariant),
                                          const SizedBox(width: 10),
                                          Expanded(
                                            child: Column(
                                              crossAxisAlignment:
                                                  CrossAxisAlignment.start,
                                              children: [
                                                Text(h['query'] as String? ?? '',
                                                    maxLines: 1,
                                                    overflow: TextOverflow.ellipsis,
                                                    style: TextStyle(
                                                        fontSize: 14,
                                                        color: scheme.onSurface)),
                                                Text(
                                                  _fmtTime(
                                                      h['created_at'] as String? ?? ''),
                                                  style: TextStyle(
                                                      fontSize: 11,
                                                      color:
                                                          scheme.onSurfaceVariant),
                                                ),
                                              ],
                                            ),
                                          ),
                                          IconButton(
                                            icon: Icon(Icons.close,
                                                size: 16,
                                                color: scheme.onSurfaceVariant),
                                            onPressed: () async {
                                              await ApiClient()
                                                  .deleteBrowserHistory(
                                                      h['id'] as int);
                                              _load();
                                            },
                                          ),
                                        ],
                                      ),
                                    ),
                                  );
                                },
                              ),
          ),
        ],
      ),
    );
  }
}

// ── 主题：壁纸选择 / 上传（字体等未来） ──
class ThemeScreen extends StatefulWidget {
  const ThemeScreen({
    super.key,
    required this.current,
    required this.onChanged,
  });
  final String? current; // null/空=默认；http=上传图；key=预置
  final Future<void> Function(String? wallpaper) onChanged;
  @override
  State<ThemeScreen> createState() => _ThemeScreenState();
}

class _ThemeScreenState extends State<ThemeScreen> {
  static const _presets = <Map<String, Object>>[
    {'key': '', 'c1': 0xFF1C1C3A, 'c2': 0xFF6C4E7E},
    {'key': 'aurora', 'c1': 0xFF0F3443, 'c2': 0xFF34E89E},
    {'key': 'sunset', 'c1': 0xFFC33764, 'c2': 0xFF1D2671},
    {'key': 'ocean', 'c1': 0xFF2193B0, 'c2': 0xFF6DD5ED},
    {'key': 'cherry', 'c1': 0xFFF7B3C6, 'c2': 0xFF6A5ACD},
    {'key': 'coffee', 'c1': 0xFF3E2723, 'c2': 0xFFB8860B},
  ];

  String? _selected;
  bool _saving = false;

  /// 预设壁纸名国际化（_presets 保持 const）
  String _presetLabel(Object? key) {
    final l10n = AppLocalizations.of(context)!;
    return switch (key) {
      null || '' => l10n.themeStarryNight,
      'aurora' => l10n.themeAurora,
      'sunset' => l10n.themeSunset,
      'ocean' => l10n.themeOcean,
      'cherry' => l10n.themeCherry,
      'coffee' => l10n.themeCoffee,
      _ => '',
    };
  }

  @override
  void initState() {
    super.initState();
    _selected = widget.current;
  }

  Future<void> _apply(String? value) async {
    setState(() {
      _saving = true;
      _selected = value;
    });
    try {
      await widget.onChanged(value);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.wallpaperChanged)));
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.saveFail)));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _upload() async {
    final picked = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1920);
    if (picked == null || !mounted) return;
    try {
      final url = await ApiClient().uploadPhonePhoto(picked.path);
      await _apply(url);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.uploadFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.themeTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(l10n.wallpaper,
              style: TextStyle(
                  fontWeight: FontWeight.bold, color: scheme.onSurface)),
          const SizedBox(height: 8),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 10,
            crossAxisSpacing: 10,
            children: [
              for (final p in _presets)
                InkWell(
                  onTap: _saving ? null : () => _apply(p['key'] as String),
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                        colors: [
                          Color(p['c1'] as int),
                          Color(p['c2'] as int),
                        ],
                      ),
                      borderRadius: BorderRadius.circular(12),
                      border: _selected == p['key']
                          ? Border.all(color: Theme.of(context).colorScheme.primary, width: 3)
                          : null,
                    ),
                    child: Center(
                      child: Text(_presetLabel(p['key']),
                          style: const TextStyle(color: Colors.white, fontSize: 11)),
                    ),
                  ),
                ),
              InkWell(
                onTap: _saving ? null : _upload,
                borderRadius: BorderRadius.circular(12),
                child: Container(
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(12),
                    border: (_selected != null && (_selected!.startsWith('http')))
                        ? Border.all(color: Theme.of(context).colorScheme.primary, width: 3)
                        : null,
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.wallpaper, size: 26, color: Colors.grey),
                      const SizedBox(height: 4),
                      Text(l10n.uploadWallpaper, style: const TextStyle(fontSize: 11, color: Colors.grey)),
                    ],
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          Text(l10n.fontIconFuture,
              style: TextStyle(
                  fontWeight: FontWeight.bold, color: scheme.onSurface)),
          const SizedBox(height: 6),
          Text(l10n.fontIconHint,
              style: TextStyle(
                  fontSize: 12, color: scheme.onSurfaceVariant)),
        ],
      ),
    );
  }
}

// ── 备忘录：AI 与用户共同维护的便签 ──
class MemoScreen extends StatefulWidget {
  const MemoScreen({super.key, required this.characterId});
  final int characterId;
  @override
  State<MemoScreen> createState() => _MemoScreenState();
}

class _MemoScreenState extends State<MemoScreen> {
  final _textCtrl = TextEditingController();
  List<Map<String, dynamic>> _memos = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _textCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final items = await ApiClient().getPhoneMemos(widget.characterId);
      if (!mounted) return;
      setState(() {
        _memos = items;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  /// 备忘录显示：尾部带记录者署名（-xxx），避免记忆混乱（2026-08-14）
  String _memoTitle(Map<String, dynamic> m) {
    final text = (m['text'] as String? ?? '').trim();
    final author = (m['author'] as String? ?? '').trim();
    return author.isEmpty ? text : '$text  -  $author';
  }

  Future<void> _add() async {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    _textCtrl.clear();
    try {
      await ApiClient().addPhoneMemo(widget.characterId, text);
      _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(AppLocalizations.of(context)!.saveFail)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.memoTitle)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            // Aurora P3：输入框主题化（圆角 14 + surfaceContainerHighest 填充）
            child: TextField(
              controller: _textCtrl,
              maxLines: 2,
              maxLength: 300,
              decoration: InputDecoration(
                hintText: l10n.memoHint,
                filled: true,
                fillColor: scheme.surfaceContainerHighest.withValues(alpha: 0.6),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(14),
                  borderSide: BorderSide.none,
                ),
                suffixIcon: IconButton(
                  icon: const Icon(Icons.save_outlined),
                  onPressed: _add,
                ),
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _memos.isEmpty
                    // Aurora P3：空态 EmptyState
                    ? EmptyState(
                        icon: Icons.sticky_note_2_outlined,
                        title: l10n.noMemos,
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        itemCount: _memos.length,
                        itemBuilder: (_, i) {
                          final m = _memos[i];
                          // Aurora P3：ListTile → AuroraCard 行
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: AuroraCard(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 12, vertical: 8),
                              child: Row(
                                children: [
                                  const Icon(Icons.sticky_note_2_outlined,
                                      size: 20, color: Colors.orange),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(_memoTitle(m),
                                            maxLines: 2,
                                            overflow: TextOverflow.ellipsis,
                                            style: TextStyle(
                                                fontSize: 14,
                                                color: scheme.onSurface)),
                                        Text(
                                          _fmtTime(
                                              m['created_at'] as String? ?? ''),
                                          style: TextStyle(
                                              fontSize: 11,
                                              color: scheme.onSurfaceVariant),
                                        ),
                                      ],
                                    ),
                                  ),
                                  IconButton(
                                    icon: Icon(Icons.delete_outline,
                                        size: 18, color: scheme.error),
                                    onPressed: () async {
                                      await ApiClient()
                                          .deletePhoneMemo(m['id'] as int);
                                      _load();
                                    },
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}

// ── 设置：虚拟手机愿景占位 ──
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: _phoneGlassAppBar(context, title: Text(l10n.settingsTitle)),
      // Aurora P3：占位内容包 AuroraCard（文案零改动）
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: AuroraCard(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.phone_android, size: 40, color: scheme.primary),
              const SizedBox(height: 12),
              Text(l10n.virtualPhone,
                  style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: scheme.onSurface)),
              const SizedBox(height: 8),
              Text(
                l10n.virtualPhoneDesc,
                style: TextStyle(
                    fontSize: 13, height: 1.5, color: scheme.onSurfaceVariant),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── 共享小工具 ──
IconData _appIcon(String key) {
  switch (key) {
    case 'chat':
      return Icons.chat_bubble;
    case 'album':
      return Icons.photo_library;
    case 'market':
      return Icons.storefront;
    case 'calendar':
      return Icons.calendar_month;
    case 'browser':
      return Icons.public;
    case 'theme':
      return Icons.palette;
    case 'settings':
      return Icons.settings;
    default:
      return Icons.apps;
  }
}

String _appSubtitle(Map<String, dynamic> app, AppLocalizations l10n) {
  switch (app['key']) {
    case 'browser':
      return l10n.appDescBrowser;
    case 'album':
      return l10n.appDescAlbum;
    case 'market':
      return l10n.appDescMarket;
    case 'calendar':
      return l10n.appDescCalendar;
    case 'theme':
      return l10n.appDescTheme;
    case 'settings':
      return l10n.appDescSettings;
    case 'chat':
      return l10n.appDescChat;
    case 'memo':
      return l10n.appDescMemo;
    default:
      return '';
  }
}

String _fmtTime(String iso) {
  try {
    final dt = DateTime.parse(iso).toLocal();
    final now = DateTime.now();
    final fmt = DateFormat(now.difference(dt).inDays < 1 ? 'HH:mm' : 'MM-dd HH:mm');
    return fmt.format(dt);
  } catch (_) {
    return '';
  }
}
