import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../services/shizuku_service.dart';

/// 从已安装应用中选择（2026-08-14 易用性升级）：
/// 显示应用中文名 + 包名（普通用户无需懂包名），支持按名称/包名搜索。
class AppPickerScreen extends StatefulWidget {
  const AppPickerScreen({super.key});
  @override
  State<AppPickerScreen> createState() => _AppPickerScreenState();
}

class _AppPickerScreenState extends State<AppPickerScreen> {
  List<Map<String, dynamic>> _apps = [];
  bool _loading = true;
  String? _error;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await ShizukuService.getAppListDetailed();
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      setState(() {
        _loading = false;
        if (r['ok'] != true) {
          _error = l10n.appPickLoadFailed(r['error'] ?? l10n.appPickUnknownError);
        } else {
          // 提前转成真实 Map 列表并剔除异常元素，避免 ListView 渲染时惰性类型转换崩溃（release 灰块）
          final raw = r['apps'];
          final temp = raw is List ? raw : <dynamic>[];
          final apps = <Map<String, dynamic>>[];
          for (final e in temp) {
            if (e is Map) {
              apps.add(Map<String, dynamic>.from(e));
            }
          }
          _apps = apps;
          if (_apps.isEmpty) _error = l10n.appPickNoApps;
        }
      });
    } catch (e) {
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      setState(() {
        _loading = false;
        _error = l10n.appPickLoadError('$e');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final q = _query.trim().toLowerCase();
    final filtered = q.isEmpty
        ? _apps
        : _apps.where((a) {
            final label = (a['label'] as String? ?? '').toLowerCase();
            final pkg = (a['package'] as String? ?? '').toLowerCase();
            return label.contains(q) || pkg.contains(q);
          }).toList();
    return Scaffold(
      appBar: AppBar(title: Text(l10n.appPickTitle)),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextField(
              onChanged: (v) => setState(() => _query = v),
              decoration: InputDecoration(
                hintText: l10n.appPickSearchHint,
                prefixIcon: const Icon(Icons.search),
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
              ),
            ),
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(_error!, style: const TextStyle(color: Colors.grey)),
                            const SizedBox(height: 12),
                            TextButton(onPressed: _load, child: Text(l10n.retry)),
                          ],
                        ),
                      )
                    : filtered.isEmpty
                        ? Center(child: Text(l10n.appPickNoResult, style: const TextStyle(color: Colors.grey)))
                        : ListView.builder(
                            itemCount: filtered.length,
                            itemBuilder: (_, i) {
                              final a = filtered[i];
                              final pkg = a['package'] as String? ?? '';
                              final label = a['label'] as String? ?? pkg;
                              return ListTile(
                                leading: CircleAvatar(
                                  radius: 18,
                                  backgroundColor:
                                      Theme.of(context).colorScheme.primary.withValues(alpha: 0.12),
                                  child: Text(
                                    label.isNotEmpty ? label.substring(0, 1) : '?',
                                    style: TextStyle(
                                      fontSize: 15,
                                      color: Theme.of(context).colorScheme.primary,
                                    ),
                                  ),
                                ),
                                title: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
                                subtitle: Text(pkg, style: const TextStyle(fontSize: 11)),
                                onTap: () => Navigator.pop(context, pkg),
                              );
                            },
                          ),
          ),
        ],
      ),
    );
  }
}
