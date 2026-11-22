import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 虚拟手机（2026-11-22 批 10）：角色的小手机只读视图——桌面（应用目录/壁纸）
/// ＋相册（AI 生成/用户上传）＋日历备注＋备忘录。数据全部来自
/// `GET /api/v1/phone-desktop/*`（layouts/photos/calendar-notes/memos）。
/// 本页只读：桌面布局编辑/上传/新建属交互层，后续批次（App 已有同款端点）。
class PhoneShellPage extends StatefulWidget {
  const PhoneShellPage({
    super.key,
    required this.client,
    required this.characterId,
    required this.characterName,
    required this.rs,
  });

  final AmbApiClient client;
  final int characterId;
  final String characterName;
  final ReaderStrings rs;

  @override
  State<PhoneShellPage> createState() => _PhoneShellPageState();
}

class _PhoneShellPageState extends State<PhoneShellPage> {
  late final ReaderApi _api = ReaderApi(widget.client);
  Map<String, dynamic>? _layouts;
  Map<String, dynamic>? _photos;
  List<Map<String, dynamic>> _notes = const [];
  List<Map<String, dynamic>> _memos = const [];
  bool _loading = true;
  String _error = '';
  int _tab = 0; // 0=桌面 1=相册 2=日历 3=备忘
  bool _acting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final layouts = await _api.getPhoneLayouts(widget.characterId);
      final photos = await _api.getPhonePhotos();
      final notes = await _api.getPhoneCalendarNotes(widget.characterId);
      final memos = await _api.getPhoneMemos(widget.characterId);
      if (!mounted) return;
      setState(() {
        _layouts = layouts;
        _photos = photos;
        _notes = notes;
        _memos = memos;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  IconData _appIcon(String key) => switch (key) {
        'chat' => Icons.chat_outlined,
        'album' => Icons.photo_outlined,
        'market' => Icons.storefront_outlined,
        'calendar' => Icons.calendar_month_outlined,
        'browser' => Icons.public,
        'theme' => Icons.palette_outlined,
        'settings' => Icons.settings_outlined,
        'memo' => Icons.sticky_note_2_outlined,
        'pets' => Icons.pets_outlined,
        _ => Icons.apps,
      };

  Color _appColor(String key) {
    const keys = [
      'chat',
      'album',
      'market',
      'calendar',
      'browser',
      'theme',
      'settings',
      'memo',
      'pets',
    ];
    const palette = [
      Colors.blueGrey,
      Colors.pink,
      Colors.deepPurple,
      Colors.teal,
      Colors.indigo,
      Colors.brown,
      Colors.blue,
      Color(0xFFB8860B),
      Colors.green,
    ];
    final i = keys.indexOf(key);
    return (i >= 0 && i < palette.length) ? palette[i] : Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    final desk = _layouts ?? {};
    final wallpaper = desk['wallpaper'] as String?;
    final catalog = ((desk['catalog'] as List?) ?? const [])
        .cast<Map>()
        .map((e) => e.cast<String, dynamic>())
        .toList();
    final hidden = {
      for (final a in ((desk['apps'] as List?) ?? const [])
          .cast<Map>()
          .map((e) => e.cast<String, dynamic>()))
        if (a['is_hidden'] == true) a['key'] as String,
    };
    final visibleApps = [
      for (final a in catalog)
        if (!hidden.contains(a['key'])) a,
    ];
    final aiPhotos = ((_photos?['ai_photos'] as List?) ?? const []).cast<String>();
    final userPhotos =
        ((_photos?['user_photos'] as List?) ?? const []).cast<String>();
    final allPhotos = [...userPhotos, ...aiPhotos];

    return Scaffold(
      appBar: AppBar(
          title: Text('${rs.t('phoneTitle')} · ${widget.characterName}')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error.isNotEmpty
              ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(_error, style: theme.textTheme.bodySmall),
                  TextButton(onPressed: _load, child: Text(rs.t('retry'))),
                ]))
              : Column(children: [
                  NavigationBar(
                    height: 56,
                    selectedIndex: _tab,
                    onDestinationSelected: (i) => setState(() => _tab = i),
                    destinations: [
                      NavigationDestination(
                          icon: const Icon(Icons.smartphone, size: 20),
                          label: rs.t('phoneTabDesktop')),
                      NavigationDestination(
                          icon: const Icon(Icons.photo_outlined, size: 20),
                          label: rs.t('phoneAppAlbum')),
                      NavigationDestination(
                          icon: const Icon(Icons.calendar_month_outlined, size: 20),
                          label: rs.t('phoneAppCalendar')),
                      NavigationDestination(
                          icon: const Icon(Icons.sticky_note_2_outlined, size: 20),
                          label: rs.t('phoneAppMemo')),
                    ],
                  ),
                  Expanded(child: _body(theme, wallpaper, visibleApps, allPhotos)),
                ]),
    );
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  Future<void> _addMemo() async {
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('phoneAddMemo')),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLines: 3,
          decoration: InputDecoration(
              hintText: widget.rs.t('phoneMemoHint')),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('phoneAddMemo'))),
        ],
      ),
    );
    ctrl.dispose();
    if (ok != true) return;
    final text = ctrl.text.trim();
    if (text.isEmpty) return;
    setState(() => _acting = true);
    try {
      await _api.addPhoneMemo(widget.characterId, text: text);
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  Future<void> _addCalendarNote() async {
    final ctrl = TextEditingController();
    final dateCtrl = TextEditingController(
        text: DateTime.now().toUtc().add(const Duration(hours: 8))
            .toString().substring(0, 10));
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('phoneAddNote')),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: dateCtrl,
            decoration: InputDecoration(
                labelText: widget.rs.t('phoneNoteDate')),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: ctrl,
            autofocus: true,
            maxLines: 3,
            decoration: InputDecoration(
                hintText: widget.rs.t('phoneNoteHint')),
          ),
        ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('phoneAddNote'))),
        ],
      ),
    );
    ctrl.dispose();
    dateCtrl.dispose();
    if (ok != true) return;
    final date = dateCtrl.text.trim();
    final text = ctrl.text.trim();
    if (date.length != 10 || text.isEmpty) return;
    setState(() => _acting = true);
    try {
      await _api.addPhoneCalendarNote(widget.characterId,
          date: date, text: text);
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  Future<void> _deletePhoto(String source, String filename) async {
    final name = filename.split('/').last;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(widget.rs.t('phoneDeleteConfirm')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(widget.rs.t('phoneDeletePhoto'))),
        ],
      ),
    );
    if (ok != true) return;
    setState(() => _acting = true);
    try {
      await _api.deletePhonePhoto(source, name);
      await _load();
    } catch (e) {
      _toast('$e');
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  Widget _body(ThemeData theme, String? wallpaper,
      List<Map<String, dynamic>> visibleApps, List<String> allPhotos) {
    final rs = widget.rs;
    if (_tab == 0) {
      return SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(children: [
          if (wallpaper != null && wallpaper.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Image.network(wallpaper,
                    height: 120, width: double.infinity, fit: BoxFit.cover),
              ),
            ),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 0.9,
            children: [
              for (final a in visibleApps)
                Column(mainAxisSize: MainAxisSize.min, children: [
                  Container(
                    width: 54,
                    height: 54,
                    decoration: BoxDecoration(
                      color: _appColor(a['key'] as String? ?? '')
                          .withValues(alpha: 0.85),
                      borderRadius: BorderRadius.circular(14),
                    ),
                    child: Icon(_appIcon(a['key'] as String? ?? ''),
                        color: Colors.white, size: 28),
                  ),
                  const SizedBox(height: 4),
                  Text(a['label'] as String? ?? '',
                      style: theme.textTheme.labelSmall, maxLines: 1),
                ]),
            ],
          ),
          const SizedBox(height: 14),
          Text(rs.t('phoneReadonlyNote'), style: theme.textTheme.bodySmall),
        ]),
      );
    }
    if (_tab == 1) {
      return allPhotos.isEmpty
          ? Center(child: Text(rs.t('phoneListEmpty')))
          : GridView.builder(
              padding: const EdgeInsets.all(12),
              gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 3, mainAxisSpacing: 6, crossAxisSpacing: 6),
              itemCount: allPhotos.length,
              itemBuilder: (_, i) {
                final url = allPhotos[i];
                return GestureDetector(
                  onLongPress: () => _deletePhoto(
                      url.contains('/phone/') ? 'user' : 'ai',
                      url.split('/').last),
                  child: ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: Image.network(url,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => Container(
                          color: theme.colorScheme.surfaceContainerHighest,
                          child: Icon(Icons.broken_image_outlined,
                              color: theme.colorScheme.onSurfaceVariant))),
                ),
                );
              },
            );
    }
    if (_tab == 2) {
      return Stack(children: [
      ListView(padding: const EdgeInsets.all(12), children: [
      ListTile(
        leading: _acting
            ? const SizedBox(width: 16, height: 16,
                child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.add),
        title: Text(rs.t('phoneAddNote')),
        onTap: _acting ? null : _addCalendarNote,
      ),
      const Divider(height: 1),
      if (_notes.isEmpty) Padding(
        padding: const EdgeInsets.all(12),
        child: Text(rs.t('phoneListEmpty'), style: theme.textTheme.bodySmall),
      ),
      for (final n in _notes)
        Card(
          margin: const EdgeInsets.only(bottom: 6),
          child: ListTile(
            dense: true,
            leading: Text(n['date'] as String? ?? '',
                style: theme.textTheme.labelSmall),
            title: Text(n['text'] as String? ?? ''),
            trailing: Text(n['author'] as String? ?? '',
                style: theme.textTheme.labelSmall),
          ),
        ),
    ]),
    ]);
    }
    return Stack(children: [
    ListView(padding: const EdgeInsets.all(12), children: [
      ListTile(
        leading: _acting
            ? const SizedBox(width: 16, height: 16,
                child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.add),
        title: Text(rs.t('phoneAddMemo')),
        onTap: _acting ? null : _addMemo,
      ),
      const Divider(height: 1),
      if (_memos.isEmpty) Padding(
        padding: const EdgeInsets.all(12),
        child: Text(rs.t('phoneListEmpty'), style: theme.textTheme.bodySmall),
      ),
      for (final m in _memos)
              Card(
                margin: const EdgeInsets.only(bottom: 6),
                child: ListTile(
                  dense: true,
                  leading: Icon(
                      m['author'] == 'user'
                          ? Icons.person_outline
                          : Icons.smart_toy_outlined,
                      size: 18),
                  title: Text(m['text'] as String? ?? ''),
                  trailing: Text(m['author'] as String? ?? '',
                      style: theme.textTheme.labelSmall),
                ),
              ),
    ]),
    ]);
  }
}
