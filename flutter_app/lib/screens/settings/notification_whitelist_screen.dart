import "package:flutter/material.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";
import "../../services/phone_perception_service.dart";
import "../../widgets/ios_card_group.dart";

/// 通知白名单页：列出最近感知到的通知来源 app，勾选 = 只感知这些 app
/// 未勾选任何 app = 全部允许（默认）
class NotificationWhitelistScreen extends StatefulWidget {
  const NotificationWhitelistScreen({super.key});

  @override
  State<NotificationWhitelistScreen> createState() => _NotificationWhitelistScreenState();
}

class _NotificationWhitelistScreenState extends State<NotificationWhitelistScreen> {
  bool _loading = true;
  final Map<String, String> _apps = {}; // package -> app 名
  Set<String> _whitelist = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final notifs = await PhonePerceptionService.getNotifications();
    final wl = await PhonePerceptionService.getNotificationWhitelist();
    if (!mounted) return;
    setState(() {
      _apps.clear();
      for (final n in notifs) {
        final pkg = (n["package"] ?? "").toString();
        if (pkg.isEmpty || pkg == "com.aicompanion.ai_companion") continue;
        final app = (n["app"] ?? pkg).toString();
        _apps[pkg] = app;
      }
      _whitelist = wl;
      _loading = false;
    });
  }

  Future<void> _toggle(String pkg, bool v) async {
    setState(() {
      if (v) {
        _whitelist = {..._whitelist, pkg};
      } else {
        _whitelist = {..._whitelist}..remove(pkg);
      }
    });
    await PhonePerceptionService.setNotificationWhitelist(_whitelist);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final entries = _apps.entries.toList();
    return Scaffold(
      appBar: AppBar(title: Text(l10n.notifyWhitelist)),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: 8),
              children: [
                IosCardGroup(
                  children: [
                    Padding(
                      padding: const EdgeInsets.all(14),
                      child: Text(
                        l10n.notifyWhitelistHint,
                        style: TextStyle(
                            fontSize: 12,
                            color: Theme.of(context).colorScheme.onSurfaceVariant,
                            height: 1.6),
                      ),
                    ),
                  ],
                ),
                if (_apps.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      l10n.notifyWhitelistEmpty,
                      textAlign: TextAlign.center,
                      style: TextStyle(color: IosCardColors.subtitle),
                    ),
                  )
                else
                  IosCardGroup(
                    children: [
                      for (var i = 0; i < entries.length; i++) ...[
                        if (i > 0) const IosCardDivider(),
                        SwitchListTile(
                          secondary: const Icon(Icons.apps_outlined),
                          title: Text(entries[i].value, maxLines: 1, overflow: TextOverflow.ellipsis),
                          subtitle: Text(entries[i].key, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 11)),
                          value: _whitelist.contains(entries[i].key),
                          onChanged: (v) => _toggle(entries[i].key, v),
                        ),
                      ],
                    ],
                  ),
              ],
            ),
    );
  }
}
