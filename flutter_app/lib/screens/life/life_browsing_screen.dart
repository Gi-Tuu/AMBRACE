import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../services/api_client.dart';
import '../../utils/beijing_time.dart';
import '../../widgets/ios_card_group.dart';

/// AI 真实浏览记录（Phase B，2026-08-14）：browse/learn 活动的真实网页记录（URL/标题/时长）
class LifeBrowsingScreen extends StatefulWidget {
  const LifeBrowsingScreen({super.key, required this.characterId, required this.characterName});

  final int characterId;
  final String characterName;

  @override
  State<LifeBrowsingScreen> createState() => _LifeBrowsingScreenState();
}

class _LifeBrowsingScreenState extends State<LifeBrowsingScreen> {
  final ApiClient _api = ApiClient();
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = await _api.getLifeBrowsing(characterId: widget.characterId);
      if (mounted) {
        setState(() {
          _items = items;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  String _fmt(String? iso) {
    try {
      return formatBeijingTime(iso ?? '').substring(0, 16);
    } catch (_) {
      return '';
    }
  }

  String _dur(int sec) {
    final l10n = AppLocalizations.of(context)!;
    if (sec <= 0) return '';
    if (sec < 60) return l10n.durationSec(sec);
    return l10n.durationMin((sec / 60).toStringAsFixed(0));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F7),
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        title: Text(l10n.browsingTitle(widget.characterName)),
        centerTitle: true,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _items.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.travel_explore_outlined, size: 48, color: Colors.grey),
                      const SizedBox(height: 8),
                      Text(l10n.noBrowsingRecords, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 6),
                      Text(
                        l10n.browsingHint,
                        style: TextStyle(fontSize: 12, color: scheme.onSurface.withValues(alpha: 0.55)),
                      ),
                    ],
                  ),
                )
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(12),
                    children: [
                      IosCardGroup(children: [
                        for (var i = 0; i < _items.length; i++) ...[
                          if (i > 0) const IosCardDivider(indent: 56),
                          _row(_items[i], scheme),
                        ],
                      ]),
                    ],
                  ),
                ),
    );
  }

  Widget _row(Map<String, dynamic> m, ColorScheme scheme) {
    final l10n = AppLocalizations.of(context)!;
    final title = m['title'] as String? ?? '';
    final url = m['url'] as String? ?? '';
    final dur = _dur((m['duration_sec'] as num? ?? 0).toInt());
    final t = _fmt(m['created_at'] as String?);
    final act = m['activity_type'] == 'learn' ? l10n.activityLearn : l10n.activityBrowse;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      leading: Container(
        width: 38,
        height: 38,
        decoration: BoxDecoration(
          color: scheme.primary.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(Icons.public, size: 20, color: scheme.primary),
      ),
      title: Text(title.isEmpty ? url : title,
          maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 14)),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: scheme.primary.withValues(alpha: 0.08),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(act, style: TextStyle(fontSize: 10, color: scheme.primary)),
            ),
            const SizedBox(width: 8),
            Text('$t${dur.isEmpty ? '' : ' · $dur'}',
                style: TextStyle(fontSize: 11, color: scheme.onSurface.withValues(alpha: 0.45))),
          ],
        ),
      ),
      onTap: url.isEmpty
          ? null
          : () async {
              final ok = await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
              if (!ok && mounted) {
                ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.openFailed(url))));
              }
            },
    );
  }
}
