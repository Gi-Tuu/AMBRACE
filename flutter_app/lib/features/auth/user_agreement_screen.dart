import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 用户协议与免责声明：开源自托管软件的使用边界（重点为作者免责）。
class UserAgreementScreen extends StatelessWidget {
  const UserAgreementScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.agreeTitle)),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _Section(
            title: l10n.agreeSection1Title,
            body: l10n.agreeSection1Body,
          ),
          _Section(
            title: l10n.agreeSection2Title,
            body: l10n.agreeSection2Body,
          ),
          _Section(
            title: l10n.agreeSection3Title,
            body: l10n.agreeSection3Body,
          ),
          _Section(
            title: l10n.agreeSection4Title,
            body: l10n.agreeSection4Body,
          ),
          _Section(
            title: l10n.agreeSection5Title,
            body: l10n.agreeSection5Body,
          ),
          _Section(
            title: l10n.agreeSection6Title,
            body: l10n.agreeSection6Body,
          ),
          _Section(
            title: l10n.agreeSection7Title,
            body: l10n.agreeSection7Body,
          ),
          _Section(
            title: l10n.agreeSection8Title,
            body: l10n.agreeSection8Body,
          ),
          _LicenseButton(
            label: l10n.agreeViewLicense,
            hint: l10n.agreeViewLicenseHint,
          ),
        ],
      ),
    );
  }
}

/// 「查看开源许可」入口：走系统 LicensePage，与 Flutter 插件默认许可并列展示。
/// 不自己再造一个文本页——showLicensePage 里的内容全部来自 main.dart 注册的
/// LicenseRegistry（项目自有 AGPL/NOTICE）+ Flutter 框架自带的插件许可。
class _LicenseButton extends StatelessWidget {
  const _LicenseButton({required this.label, required this.hint});

  final String label;
  final String hint;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextButton.icon(
            onPressed: () => showLicensePage(
              context: context,
              applicationName: label,
              applicationVersion: 'AGPL-3.0-only',
              applicationLegalese: hint,
            ),
            icon: const Icon(Icons.description_outlined),
            label: Text(label),
          ),
          Text(
            hint,
            style: TextStyle(
              fontSize: 12,
              color: Theme.of(context)
                  .colorScheme
                  .onSurface
                  .withValues(alpha: 0.6),
            ),
          ),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.body});

  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          const SizedBox(height: 6),
          Text(
              body,
              style: TextStyle(
                fontSize: 14,
                height: 1.5,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withValues(alpha: 0.87),
              )),
        ],
      ),
    );
  }
}
