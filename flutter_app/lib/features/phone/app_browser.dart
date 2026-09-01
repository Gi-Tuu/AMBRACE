// F7-c-4a（2026-08-31）自 screens/phone/phone_app_screens.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/aurora_card.dart';
import '../../widgets/empty_state.dart';
import '../../widgets/ios_card_group.dart';
import '../../services/api/phone_desktop_api.dart';
import '../../screens/life/life_browsing_screen.dart';
import 'app_settings.dart' show fmtTime;

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
                                                  fmtTime(
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
