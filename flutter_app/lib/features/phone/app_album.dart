// F7-c-4a（2026-08-31）自 screens/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:image_picker/image_picker.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';
import '../../services/api/phone_desktop_api.dart';

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
