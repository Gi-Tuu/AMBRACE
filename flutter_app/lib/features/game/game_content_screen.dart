import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../services/api_client.dart';
import '../../services/api_exception.dart';

/// 游戏内容编辑页（#62 Phase 3）。
///
/// 内容优先级由后端决定：用户自定义 > 插件内容包 > 内置常量；本页按后端返回的
/// `source` 字段标注每条内容来源（`user` / `plugin` / `builtin`）。
/// 编辑区「每行一条」，保存走 PUT 整段覆盖该 key，「恢复默认」走 DELETE 回落
/// 插件内容包 / 内置常量；保存与恢复后都会重新拉取一次生效内容。
class GameContentScreen extends StatefulWidget {
  const GameContentScreen({super.key});

  @override
  State<GameContentScreen> createState() => _GameContentScreenState();
}

class _GameContentScreenState extends State<GameContentScreen> {
  final ApiClient _api = ApiClient();

  /// 行内字段分隔符（词对元素 / 对象字段）。
  static const String _fieldSep = ' / ';

  /// 二级分隔符（对象内的数组字段，如海龟汤 keywords）。
  static const String _itemSep = ',';

  /// 行内字段切分（兼容全角斜杠与竖线）。
  static final RegExp _fieldSepRe = RegExp(r'\s*[/／|｜]\s*');

  /// 二级字段切分（兼容中英文逗号）。
  static final RegExp _itemSepRe = RegExp(r'\s*[,，]\s*');

  List<Map<String, dynamic>> _catalog = [];
  List<Map<String, dynamic>> _items = [];
  final Map<String, TextEditingController> _controllers = {};
  String? _gameType;
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadCatalog();
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    _controllers.clear();
    super.dispose();
  }

  // ── 数据加载 ──

  Future<void> _loadCatalog() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final catalog = await _api.getGameCatalog();
      if (!mounted) return;
      setState(() {
        _catalog = catalog;
        _gameType ??=
            catalog.isEmpty ? null : (catalog.first['game_type'] as String?);
      });
      await _loadContent();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = ApiException.messageOf(e);
      });
    }
  }

  Future<void> _loadContent() async {
    final type = _gameType;
    if (type == null) {
      setState(() => _loading = false);
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await _api.getGameContent(type);
      if (!mounted) return;
      _syncControllers(items);
      setState(() {
        _items = items;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = ApiException.messageOf(e);
      });
    }
  }

  /// 每次拉取后重建编辑框（key -> controller），保留当前正在编辑的内容不重建：
  /// 保存/恢复成功后源数据即与输入框一致，这里整体重建即可。
  void _syncControllers(List<Map<String, dynamic>> items) {
    for (final c in _controllers.values) {
      c.dispose();
    }
    _controllers.clear();
    for (final item in items) {
      final key = (item['key'] as String?) ?? '';
      if (key.isEmpty || _controllers.containsKey(key)) continue;
      final values = (item['values'] as List?) ?? const [];
      _controllers[key] =
          TextEditingController(text: values.map(_encodeValue).join('\n'));
    }
  }

  // ── 行文本 <-> values 结构互转 ──

  /// values 元素 -> 单行文本（对象字段用 ` / ` 连接，内部数组用 `,` 连接）。
  String _encodeValue(dynamic v, {bool inner = false}) {
    if (v is String) return v;
    if (v is List) {
      final sep = inner ? _itemSep : _fieldSep;
      return v.map((e) => _encodeValue(e, inner: true)).join(sep);
    }
    if (v is Map) {
      return v.values.map((e) => _encodeValue(e, inner: true)).join(_fieldSep);
    }
    return '$v';
  }

  /// 单行文本 -> values 元素（按 [template] 的结构还原；格式不符抛 [_FormatError]）。
  dynamic _decodeLine(String line, dynamic template, {bool inner = false}) {
    if (template is List) {
      final parts = line.split(inner ? _itemSepRe : _fieldSepRe);
      if (parts.length != template.length) throw const _FormatError();
      return [
        for (var i = 0; i < template.length; i++)
          _decodeLine(parts[i], template[i], inner: true),
      ];
    }
    if (template is Map) {
      final keys = template.keys.toList();
      final parts = line.split(_fieldSepRe);
      if (parts.length != keys.length) throw const _FormatError();
      return <String, dynamic>{
        for (var i = 0; i < keys.length; i++)
          keys[i]: _decodeLine(parts[i], template[keys[i]], inner: true),
      };
    }
    final text = line.trim();
    if (text.isEmpty) throw const _FormatError();
    return text;
  }

  // ── 操作 ──

  Future<void> _save(Map<String, dynamic> item, AppLocalizations l10n) async {
    final type = _gameType;
    final key = (item['key'] as String?) ?? '';
    if (type == null || key.isEmpty) return;
    final raw = _controllers[key]?.text ?? '';
    final values = (item['values'] as List?) ?? const [];
    final template = values.isEmpty ? null : values.first;

    final lines = raw
        .split('\n')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    if (lines.isEmpty) {
      _snack(l10n.gameContentMinOne);
      return;
    }
    final parsed = <dynamic>[];
    for (var i = 0; i < lines.length; i++) {
      try {
        parsed.add(_decodeLine(lines[i], template));
      } on _FormatError {
        _snack(l10n.gameContentParseFailed(i + 1));
        return;
      }
    }

    setState(() => _saving = true);
    try {
      await _api.putGameContent(gameType: type, key: key, values: parsed);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      _snack(l10n.gameContentSaveFailed(ApiException.messageOf(e)));
      return;
    }
    if (!mounted) return;
    await _loadContent(); // 保存后重拉一次，回显后端真实生效内容
    if (!mounted) return;
    setState(() => _saving = false);
    _snack(l10n.gameContentSaved);
  }

  Future<void> _restore(Map<String, dynamic> item, AppLocalizations l10n) async {
    final type = _gameType;
    final key = (item['key'] as String?) ?? '';
    if (type == null || key.isEmpty) return;
    final ok = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: Text(l10n.gameContentRestore),
            content: Text(l10n.gameContentRestoreConfirm),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(ctx).pop(false),
                child: Text(l10n.cancel),
              ),
              TextButton(
                onPressed: () => Navigator.of(ctx).pop(true),
                child: Text(l10n.confirm),
              ),
            ],
          ),
        ) ??
        false;
    if (!ok) return;
    setState(() => _saving = true);
    try {
      await _api.deleteGameContent(gameType: type, key: key);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      _snack(l10n.gameContentRestoreFailed(ApiException.messageOf(e)));
      return;
    }
    if (!mounted) return;
    await _loadContent();
    if (!mounted) return;
    setState(() => _saving = false);
    _snack(l10n.gameContentRestored);
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  void _pickGame(String? type) {
    setState(() => _gameType = type);
    _loadContent();
  }

  // ── UI ──

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.gameContentTitle)),
      body: Column(
        children: [
          if (_catalog.isNotEmpty) _gameSelector(l10n),
          Expanded(child: _body(l10n)),
        ],
      ),
    );
  }

  Widget _gameSelector(AppLocalizations l10n) {
    return SizedBox(
      height: 52,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        children: [
          for (final g in _catalog)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text((g['name'] as String?) ??
                    (g['game_type'] as String?) ??
                    ''),
                selected: _gameType == g['game_type'],
                onSelected: (_) => _pickGame(g['game_type'] as String?),
              ),
            ),
        ],
      ),
    );
  }

  Widget _body(AppLocalizations l10n) {
    if (_loading && _items.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null && _items.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(l10n.gameContentLoadFailed(_error!),
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.grey)),
            const SizedBox(height: 12),
            FilledButton.tonal(
              onPressed: _loadContent,
              child: Text(l10n.retry),
            ),
          ],
        ),
      );
    }
    if (_items.isEmpty) {
      return Center(child: Text(l10n.gameContentEmpty));
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      itemCount: _items.length,
      itemBuilder: (context, i) => _itemCard(_items[i], l10n),
    );
  }

  Widget _itemCard(Map<String, dynamic> item, AppLocalizations l10n) {
    final key = (item['key'] as String?) ?? '';
    final source = (item['source'] as String?) ?? 'builtin';
    final count = (item['count'] as num?)?.toInt() ?? 0;
    final scheme = Theme.of(context).colorScheme;
    final isUser = source == 'user';
    final chipBg = isUser
        ? scheme.primaryContainer
        : (source == 'plugin'
            ? scheme.secondaryContainer
            : scheme.surfaceContainerHighest);
    final chipFg = isUser
        ? scheme.onPrimaryContainer
        : (source == 'plugin'
            ? scheme.onSecondaryContainer
            : scheme.onSurfaceVariant);
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: scheme.outlineVariant),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(key,
                      style: const TextStyle(fontWeight: FontWeight.w700)),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: chipBg,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    '${l10n.gameContentSource}：${_sourceLabel(source, l10n)}',
                    style: TextStyle(fontSize: 11, color: chipFg),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text('${l10n.gameContentCount(count)} · ${l10n.gameContentApplyHint}',
                style: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant)),
            const SizedBox(height: 10),
            TextField(
              controller: _controllers[key],
              minLines: 3,
              maxLines: 12,
              keyboardType: TextInputType.multiline,
              decoration: InputDecoration(
                isDense: true,
                border: const OutlineInputBorder(),
                hintText: _hintFor(key, l10n),
                hintStyle: const TextStyle(fontSize: 12),
              ),
              style: const TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                FilledButton.tonal(
                  onPressed: _saving ? null : () => _save(item, l10n),
                  child: Text(l10n.save),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed:
                      isUser && !_saving ? () => _restore(item, l10n) : null,
                  child: Text(l10n.gameContentRestore),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _sourceLabel(String source, AppLocalizations l10n) {
    switch (source) {
      case 'user':
        return l10n.gameContentSourceUser;
      case 'plugin':
        return l10n.gameContentSourcePlugin;
      default:
        return l10n.gameContentSourceBuiltin;
    }
  }

  String _hintFor(String key, AppLocalizations l10n) {
    switch (key) {
      case 'word_pairs':
        return l10n.gameContentHintPair;
      case 'puzzles':
        return l10n.gameContentHintPuzzle;
      default:
        return l10n.gameContentHintLine;
    }
  }
}

/// 行格式错误（字段数量与原始结构不匹配等）。
class _FormatError implements Exception {
  const _FormatError();
}
