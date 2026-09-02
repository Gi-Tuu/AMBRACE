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
          Text(body, style: const TextStyle(fontSize: 14, height: 1.5, color: Colors.black87)),
        ],
      ),
    );
  }
}
