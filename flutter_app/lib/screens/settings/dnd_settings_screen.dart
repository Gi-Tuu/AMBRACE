import "dart:async";

import "package:flutter/material.dart";
import "../../services/dnd_settings.dart";
import "../../services/notification_service.dart";
import "../../widgets/ios_card_group.dart";

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
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("已保存免打扰设置")));
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
    return Scaffold(
      appBar: AppBar(title: const Text("免打扰设置")),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          IosCardGroup(
            title: '通知',
            children: [
              SwitchListTile(
                title: const Text("消息通知"),
                subtitle: Text(_notificationsEnabled ? "AI好友新消息将弹横幅与系统通知" : "关闭后横幅与系统通知都不弹（红点仍更新）",
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
            title: '免打扰',
            children: [
              SwitchListTile(
                title: const Text("启用免打扰"),
                subtitle: Text(_enabled ? "在设定时段内不推送通知" : "通知将正常推送",
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
                title: const Text("开始时间"),
                subtitle: Text(_startTime.format(context)),
                onTap: _enabled ? () => _pickTime("开始", _startTime, (t) => setState(() => _startTime = t)) : null,
              ),
              const IosCardDivider(),
              ListTile(
                leading: Icon(Icons.wb_sunny, color: Theme.of(context).colorScheme.primary),
                title: const Text("结束时间"),
                subtitle: Text(_endTime.format(context)),
                onTap: _enabled ? () => _pickTime("结束", _endTime, (t) => setState(() => _endTime = t)) : null,
              ),
            ],
          ),
          const Padding(
            padding: EdgeInsets.only(left: 20, top: 8, bottom: 16),
            child: Text(
              "免打扰时段内，AI好友将不会推送新消息通知。\n例如: 22:00 ~ 08:00 适合夜间休息时段。",
              style: TextStyle(fontSize: 13, color: IosCardColors.subtitle),
            ),
          ),
        ],
      ),
    );
  }
}
