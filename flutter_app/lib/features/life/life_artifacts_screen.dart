import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ios_card_group.dart';
import "package:ai_companion/theme/tokens.dart";

/// AI 生活产物库（Life Engine v2 Phase 2，2026-08-12）：创作/浏览/学习产物列表
class LifeArtifactsScreen extends StatefulWidget {
  const LifeArtifactsScreen({super.key, required this.characterId, required this.characterName, this.showScaffold = true});

  final int characterId;
  final String characterName;
  final bool showScaffold;

  @override
  State<LifeArtifactsScreen> createState() => _LifeArtifactsScreenState();
}

class _LifeArtifactsScreenState extends State<LifeArtifactsScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _loadError = false; // 加载失败标记

  Map<String, String> _typeLabel(AppLocalizations l10n) => {
    'text': l10n.artifactText,
    'image': l10n.artifactImage,
    'note': l10n.artifactNote,
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = false;
    });
    try {
      final items = await _api.getLifeArtifacts(characterId: widget.characterId);
      if (mounted) {
        setState(() {
          _items = items;
          _loading = false;
        });
      }
    } catch (e) {
      // 不再静默：打印便于定位，UI 给重试
      assert(() {
        debugPrint('LifeArtifacts load failed: $e');
        return true;
      }());
      if (mounted) setState(() { _loading = false; _loadError = true; });
    }
  }

  String _fmt(String iso) {
    try {
      return formatBeijingTime(iso).substring(0, 16);
    } catch (_) {
      return '';
    }
  }

  void _showImage(String rawUrl) {
    final url = _api.resolveUrl(rawUrl); // 补全主机（resolveUrl 幂等）
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => Scaffold(
          backgroundColor: Colors.black,
          body: Stack(
            children: [
              Positioned.fill(
                child: InteractiveViewer(
                  child: Center(child: Image.network(url, fit: BoxFit.contain)),
                ),
              ),
              SafeArea(
                child: Align(
                  alignment: Alignment.topLeft,
                  child: IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final Widget body;
    if (_loading) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_loadError) {
      body = Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.cloud_off_rounded, size: 40, color: IosCardColors.subtitle),
          const SizedBox(height: 8),
          Text(l10n.loadFailedCheckServer,
              style: const TextStyle(color: IosCardColors.subtitle)),
          const SizedBox(height: 8),
          TextButton.icon(
            onPressed: _load,
            icon: const Icon(Icons.refresh_rounded, size: 18),
            label: Text(l10n.retry),
          ),
        ]),
      );
    } else if (_items.isEmpty) {
      body = Center(
        child: Text(l10n.noArtifacts,
            style: const TextStyle(color: IosCardColors.subtitle)),
      );
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView.separated(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(12),
          itemCount: _items.length,
          separatorBuilder: (context, index) => const SizedBox(height: 8),
          itemBuilder: (context, i) {
            final it = _items[i];
            final type = (it['type'] as String? ?? 'text');
            final title = it['title'] as String? ?? '';
            final text = it['content_text'] as String? ?? '';
            // 相对路径（/uploads/...）补全为服务器地址；http(s) 绝对地址原样返回
            final url = _api.resolveUrl(it['content_url'] as String?);
            final time = _fmt(it['created_at'] as String? ?? '');
            final label = _typeLabel(l10n)[type] ?? l10n.lifeTypeLife;
            return _ArtifactCard(
              label: label,
              title: title,
              text: text,
              url: url,
              time: time,
              onTap: url.isNotEmpty ? () => _showImage(url) : null,
            );
          },
        ),
      );
    }
    if (!widget.showScaffold) return body;
    return Scaffold(
      backgroundColor: AppColors.bgLight,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(l10n.artifactsTitle(widget.characterName)),
        centerTitle: true,
      ),
      body: body,
    );
  }
}

class _ArtifactCard extends StatelessWidget {
  const _ArtifactCard({
    required this.label,
    required this.title,
    required this.text,
    required this.url,
    required this.time,
    this.onTap,
  });

  final String label;
  final String title;
  final String text;
  final String url;
  final String time;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: EdgeInsets.zero,
      elevation: 0,
      color: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: scheme.primary.withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(label,
                        style: TextStyle(fontSize: 11, color: scheme.primary)),
                  ),
                  const Spacer(),
                  Text(time, style: const TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                ],
              ),
              if (title.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(title,
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
              ],
              if (url.isNotEmpty) ...[
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: Image.network(
                    url,
                    height: 160,
                    width: double.infinity,
                    fit: BoxFit.cover,
                    errorBuilder: (context, error, stackTrace) => Container(
                      height: 160,
                      color: AppColors.bgLight,
                      child: const Center(child: Icon(Icons.broken_image_outlined)),
                    ),
                  ),
                ),
              ],
              if (text.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(text,
                    style: const TextStyle(fontSize: 14, height: 1.4, color: Colors.black87)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
