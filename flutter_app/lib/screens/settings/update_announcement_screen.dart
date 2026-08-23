import 'package:flutter/material.dart';
import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 更新公告页：拉取 /api/v1/system/updates，按天折叠展示（最新一天默认展开）
class UpdateAnnouncementScreen extends StatefulWidget {
  const UpdateAnnouncementScreen({super.key});

  @override
  State<UpdateAnnouncementScreen> createState() => _UpdateAnnouncementScreenState();
}

class _UpdateAnnouncementScreenState extends State<UpdateAnnouncementScreen> {
  List<Map<String, dynamic>> _days = [];
  bool _loading = true;

  /// 用户手动折叠/展开的索引覆盖：滚动重建时保持用户选择（默认最新一天展开）
  final Map<int, bool> _expandedOverride = {};
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final r = await ApiClient().dio.get('/api/v1/system/updates');
      final days = (r.data as Map<String, dynamic>)['days'] as List? ?? [];
      if (mounted) {
        setState(() {
          _days = days.cast<Map<String, dynamic>>();
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) {
        final l10n = AppLocalizations.of(context)!;
        setState(() { _error = l10n.loadFailedCheckServer; _loading = false; });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.updateAnnouncement)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: IosCardColors.subtitle)),
                      const SizedBox(height: 12),
                      OutlinedButton(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : _days.isEmpty
                  ? Center(child: Text(l10n.noUpdates, style: const TextStyle(color: IosCardColors.subtitle)))
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      itemCount: _days.length,
                      itemBuilder: (context, i) {
                        final day = _days[i];
                        final items = (day['items'] as List? ?? []).cast<Map<String, dynamic>>();
                        final scheme = Theme.of(context).colorScheme;
                        return Padding(
                          padding: const EdgeInsets.only(left: 12, right: 12, bottom: 10),
                          child: IosCardGroup(
                            children: [
                              Material(
                                color: Colors.transparent,
                                child: ExpansionTile(
                                  initiallyExpanded: _expandedOverride[i] ?? (i == 0), // 最新一天默认展开
                                  onExpansionChanged: (v) {
                                    setState(() => _expandedOverride[i] = v);
                                  },
                                  leading: Icon(Icons.history, size: 20, color: scheme.primary),
                                  title: Text(
                                    '${day['date']}${day['title'] != null && day['title']!.toString().isNotEmpty ? ' · ${day['title']}' : ''}',
                                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                                  ),
                                  children: items.isEmpty
                                      ? [ListTile(title: Text(l10n.updateNoDetail, style: const TextStyle(color: IosCardColors.subtitle, fontSize: 13)))]
                                      : [
                                          for (final item in items)
                                            Padding(
                                              padding: const EdgeInsets.fromLTRB(16, 4, 16, 10),
                                              child: Column(
                                                crossAxisAlignment: CrossAxisAlignment.start,
                                                children: [
                                                  Text(
                                                    item['content']?.toString() ?? '',
                                                    style: const TextStyle(fontSize: 14, height: 1.5),
                                                  ),
                                                  if ((item['reason']?.toString() ?? '').isNotEmpty)
                                                    Padding(
                                                      padding: const EdgeInsets.only(top: 2),
                                                      child: Text(
                                                        l10n.updateReason(item['reason']),
                                                        style: TextStyle(fontSize: 12, color: IosCardColors.subtitle),
                                                      ),
                                                    ),
                                                ],
                                              ),
                                            ),
                                        ],
                                  ),
                                ),
                            ],
                          ),
                        );
                      },
                    ),
    );
  }
}
