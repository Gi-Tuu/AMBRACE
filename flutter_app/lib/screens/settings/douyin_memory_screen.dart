import 'package:flutter/material.dart';

import '../../services/api_client.dart';
import '../../widgets/ios_card_group.dart';

/// 抖音记忆收紧开关（2026-08-12）：公开平台记忆注入隐私保护
class DouyinMemoryScreen extends StatefulWidget {
  const DouyinMemoryScreen({super.key});

  @override
  State<DouyinMemoryScreen> createState() => _DouyinMemoryScreenState();
}

class _DouyinMemoryScreenState extends State<DouyinMemoryScreen> {
  final ApiClient _api = ApiClient();
  bool _loading = true;
  bool _restrict = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getDouyinProfile();
      if (mounted) {
        setState(() {
          _restrict = data['memory_restrict'] == 'relationship';
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save(bool v) async {
    setState(() => _restrict = v);
    try {
      await _api.updateDouyinProfile(v ? 'relationship' : 'off');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(v ? '已开启：排除关系类私密记忆' : '已关闭：按现状筛选记忆')),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() => _restrict = !v);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('保存失败：$e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('抖音记忆收紧')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 16),
              children: [
                IosCardGroup(title: '公开记忆注入', children: [
                  SwitchListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                    title: const Text('收紧私密记忆',
                        style: TextStyle(fontSize: 15, fontWeight: FontWeight.w500)),
                    subtitle: const Text('开启后，抖音图文创作与评论回复不再注入「关系类」记忆（表白/金钱等无姓名但私密的内容）',
                        style: TextStyle(fontSize: 11, color: IosCardColors.subtitle)),
                    value: _restrict,
                    onChanged: _save,
                    activeColor: scheme.primary,
                  ),
                ]),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  child: Text(
                    '说明：无论开关状态，抖音都永不注入「身份画像」与含用户姓名的记忆。开启「收紧」后，关系类记忆（如表白、亲密互动、金钱往来）也会被排除，适合对外更谨慎的场景。',
                    style: TextStyle(fontSize: 12, color: scheme.onSurface.withValues(alpha: 0.55)),
                  ),
                ),
              ],
            ),
    );
  }
}
