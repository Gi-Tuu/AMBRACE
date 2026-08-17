import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../config/support_config.dart';
import '../../widgets/ios_card_group.dart';

/// 支持作者：打赏码 + 打赏主页 + 抖音号 + QQ 群（纯静态展示，不接支付逻辑）。
class SupportScreen extends StatelessWidget {
  const SupportScreen({super.key});

  Future<void> _openUrl(BuildContext context, String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null || (uri.scheme != 'http' && uri.scheme != 'https')) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('链接无效')));
      return;
    }
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('打开失败，可复制链接手动打开')));
    }
  }

  Future<void> _copy(BuildContext context, String text) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('已复制：$text')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasAifadian = SupportConfig.aifadianUrl.isNotEmpty;
    return Scaffold(
      appBar: AppBar(title: const Text('支持作者')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Padding(
            padding: EdgeInsets.only(left: 4, bottom: 12),
            child: Text('如果这个应用给你带来了陪伴，欢迎请作者喝杯咖啡 ☕',
                style: TextStyle(fontSize: 14, color: IosCardColors.subtitle)),
          ),
          // 微信赞赏码（随包分发的静态资源）
          Card(
            color: Theme.of(context).colorScheme.surface,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  const Text('微信赞赏',
                      style: TextStyle(fontWeight: FontWeight.w600)),
                  const SizedBox(height: 12),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: Image.asset(
                      'assets/reward_qrcode.png',
                      width: 220,
                      height: 220,
                      fit: BoxFit.contain,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          _OptionCard(
            icon: Icons.favorite,
            color: Colors.pink,
            title: '打赏支持',
            subtitle: hasAifadian ? '打开主页支持作者' : '作者暂未开启打赏渠道',
            trailingLabel: hasAifadian ? '去支持' : '未开启',
            enabled: hasAifadian,
            onTap: hasAifadian ? () => _openUrl(context, SupportConfig.aifadianUrl) : null,
          ),
          const SizedBox(height: 12),
          _OptionCard(
            icon: Icons.play_circle_outline,
            color: Colors.orange,
            title: '关注抖音',
            subtitle: '抖音号：${SupportConfig.douyinId}',
            trailingLabel: '复制',
            enabled: true,
            onTap: () => _copy(context, SupportConfig.douyinId),
          ),
          const SizedBox(height: 12),
          _OptionCard(
            icon: Icons.groups_outlined,
            color: Colors.teal,
            title: '加入 QQ 群',
            subtitle: '群号：${SupportConfig.qqGroup}',
            trailingLabel: '复制',
            enabled: true,
            onTap: () => _copy(context, SupportConfig.qqGroup),
          ),
          const SizedBox(height: 16),
          const Text(
            '打赏与关注纯属自愿，感谢你的支持 ❤️',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 12, color: IosCardColors.subtitle),
          ),
        ],
      ),
    );
  }
}

class _OptionCard extends StatelessWidget {
  const _OptionCard({
    required this.icon,
    required this.color,
    required this.title,
    required this.subtitle,
    required this.trailingLabel,
    required this.enabled,
    this.onTap,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String subtitle;
  final String trailingLabel;
  final bool enabled;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.surface,
      borderRadius: BorderRadius.circular(12),
      child: ListTile(
        leading: Icon(icon, color: enabled ? color : scheme.outlineVariant),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(subtitle),
        trailing: enabled
            ? const Icon(Icons.open_in_new, size: 20)
            : Text(trailingLabel, style: const TextStyle(fontSize: 12, color: IosCardColors.subtitle)),
        enabled: enabled,
        onTap: onTap,
      ),
    );
  }
}
