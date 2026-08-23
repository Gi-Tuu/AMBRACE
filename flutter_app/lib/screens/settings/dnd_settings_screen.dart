import "dart:async";

import "package:flutter/material.dart";
import "package:provider/provider.dart";
import "../../services/dnd_settings.dart";
import "../../services/background_polling_service.dart";
import "../../services/notification_service.dart";
import "../../providers/settings_provider.dart";
import "../../widgets/ios_card_group.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";

class DndSettingsScreen extends StatefulWidget {
  const DndSettingsScreen({super.key});

  @override
  State<DndSettingsScreen> createState() => _DndSettingsScreenState();
}

class _DndSettingsScreenState extends State<DndSettingsScreen> {
  bool _notificationsEnabled = true;
  bool _enabled = false;
  TimeOfDay _startTime = const TimeOfDay(hour: 22, minute: 0);
  TimeOfDay _endTime = const TimeOfDay(hour: 8, minute: 0);

  @override
  void initState() {
    super.initState();
    NotificationService().setActiveScreen(ActiveScreen.other);
    _load();
  }

  Future<void> _load() async {
    final settings = await DndSettings.get();
      if (!mounted) return;
    setState(() {
      _notificationsEnabled = settings["notificationsEnabled"] as bool? ?? true;
      _enabled = settings["enabled"] as bool;
      _startTime = TimeOfDay(hour: settings["startHour"] as int, minute: settings["startMinute"] as int);
      _endTime = TimeOfDay(hour: settings["endHour"] as int, minute: settings["endMinute"] as int);
    });
  }

  Future<void> _save() async {
    await DndSettings.set({
      "enabled": _enabled,
      "notificationsEnabled": _notificationsEnabled,
      "startHour": _startTime.hour,
      "startMinute": _startTime.minute,
      "endHour": _endTime.hour,
      "endMinute": _endTime.minute,
    });
    unawaited(DndSettings.syncToServer()); // 同步到服务端：后端状态触发等主动行为在免打扰时段不打扰
    if (mounted) {
      final l10n = AppLocalizations.of(context)!;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.dndSaved)));
    }
  }

  Future<void> _pickTime(String label, TimeOfDay current, Function(TimeOfDay) onPicked) async {
    final picked = await showTimePicker(context: context, initialTime: current);
    if (picked != null) {
      onPicked(picked);
      _save();
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.dndSettings)),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          IosCardGroup(
            title: l10n.notificationSection,
            children: [
              SwitchListTile(
                title: Text(l10n.messageNotifications),
                subtitle: Text(_notificationsEnabled ? l10n.msgNotifOnSubtitle : l10n.msgNotifOffSubtitle,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
                value: _notificationsEnabled,
                onChanged: (val) {
                  setState(() => _notificationsEnabled = val);
                  _save();
                },
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.backgroundKeepalive,
            children: [
              SwitchListTile(
                title: Text(l10n.backgroundKeepalive),
                subtitle: Text(l10n.backgroundKeepaliveHint,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
                value: context.watch<SettingsProvider>().backgroundKeepalive,
                onChanged: (val) async {
                  final sp = context.read<SettingsProvider>();
                  await sp.setBackgroundKeepalive(val);
                  if (val) {
                    try { await BackgroundPollingService.start(); } catch (_) {}
                  } else {
                    try { await BackgroundPollingService.stop(); } catch (_) {}
                  }
                },
              ),
            ],
          ),
          IosCardGroup(
            title: l10n.dnd,
            children: [
              SwitchListTile(
                title: Text(l10n.enableDnd),
                subtitle: Text(_enabled ? l10n.dndOnSubtitle : l10n.dndOffSubtitle,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
                value: _enabled,
                onChanged: (val) {
                  setState(() => _enabled = val);
                  _save();
                },
              ),
              const IosCardDivider(),
              ListTile(
                leading: Icon(Icons.bedtime, color: Theme.of(context).colorScheme.primary),
                title: Text(l10n.dndStartLabel),
                subtitle: Text(_startTime.format(context)),
                onTap: _enabled ? () => _pickTime(l10n.dndStartAction, _startTime, (t) => setState(() => _startTime = t)) : null,
              ),
              const IosCardDivider(),
              ListTile(
                leading: Icon(Icons.wb_sunny, color: Theme.of(context).colorScheme.primary),
                title: Text(l10n.dndEndLabel),
                subtitle: Text(_endTime.format(context)),
                onTap: _enabled ? () => _pickTime(l10n.dndEndAction, _endTime, (t) => setState(() => _endTime = t)) : null,
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 20, top: 8, bottom: 16),
            child: Text(
              l10n.dndNote,
              style: const TextStyle(fontSize: 13, color: IosCardColors.subtitle),
            ),
          ),
        ],
      ),
    );
  }
}
