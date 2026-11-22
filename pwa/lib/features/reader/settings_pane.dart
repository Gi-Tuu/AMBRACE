import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';
import '../../theme/pwa_skins.dart';

import '../../l10n/reader_strings.dart';
import 'updates_page.dart';
import 'server_config_page.dart';
import 'feature_flags_page.dart';
import 'context_budget_page.dart';

/// 基础设置页：账号 / 外观 / DND / 模型与 API。
///
/// DND 只写服务端（`PUT /auth/dnd`）；后端当前没有 GET，刷新后以本地默认值显示，
/// 不伪造“已从服务端读回”。
///
/// 「模型与 API」（2026-11-22）：列表 / 新增 / 编辑 / 删除 / 设默认 / 子账号共享 /
/// 测试连接 / Token 用量，契约 = `/api/v1/llm-configs` 与 `/api/v1/system/llm-usage`；
/// 共享配置（`is_shared=true`）只读：`api_key` 恒空、禁改禁删（后端强校验）。
class SettingsPane extends StatefulWidget {
  const SettingsPane({
    super.key,
    required this.client,
    required this.myUserId,
    required this.lang,
    required this.themeMode,
    required this.onTheme,
    required this.onLang,
    required this.onLogout,
    required this.rs,
    required this.cs,
    required this.skinId,
    required this.onSkin,
    required this.seedIndex,
    required this.onSeed,
  });

  final AmbApiClient client;
  final int? myUserId;
  final String lang;
  final ThemeMode themeMode;
  final ValueChanged<ThemeMode> onTheme;
  final ValueChanged<String> onLang;
  final VoidCallback onLogout;
  final ReaderStrings rs;
  final CoreStrings cs;

  /// 外观批（2026-11-22）：皮肤预设 id 与主题色索引（-1＝跟随皮肤）。
  /// 会话内存态（Web 不落存储，刷新回默认）——由宿主 ValueNotifier 承载。
  final String skinId;
  final ValueChanged<String> onSkin;
  final int seedIndex;
  final ValueChanged<int> onSeed;

  @override
  State<SettingsPane> createState() => _SettingsPaneState();
}

class _SettingsPaneState extends State<SettingsPane> {
  late final SettingsApi _api = SettingsApi(widget.client);
  late final ApiConfigApi _cfgApi = ApiConfigApi(widget.client);
  Map<String, dynamic>? _profile;
  String _profileError = '';
  DndSettings _dnd = const DndSettings();
  bool _saving = false;

  List<Map<String, dynamic>> _configs = const [];
  Map<String, dynamic>? _usage;
  String _configsError = '';
  bool _configsLoading = false;
  List<Map<String, dynamic>> _taskCatalog = const [];
  final Map<String, Map<String, dynamic>> _taskCfgs = {};

  @override
  void initState() {
    super.initState();
    _loadProfile();
    _loadConfigs();
  }

  Future<void> _loadProfile() async {
    try {
      final p = await _api.profile();
      if (mounted) setState(() => _profile = p);
    } catch (e) {
      if (mounted) setState(() => _profileError = '$e');
    }
  }

  Future<void> _loadConfigs() async {
    setState(() {
      _configsLoading = true;
      _configsError = '';
    });
    try {
      final items = await _cfgApi.list();
      Map<String, dynamic>? usage;
      try {
        usage = await _cfgApi.usage(); // 用量失败不拖垮配置列表
      } catch (_) {}
      List<Map<String, dynamic>> tasks = const [];
      final cfgs = <String, Map<String, dynamic>>{};
      try {
        tasks = await _cfgApi.taskCatalog();
        for (final t in tasks) {
          final task = t['task'] as String? ?? '';
          if (task.isEmpty) continue;
          try {
            cfgs[task] = await _cfgApi.getTaskConfig(task); // 单任务失败不拖垮整页
          } catch (_) {}
        }
      } catch (_) {} // 目录失败不拖垮 LLM 配置列表
      if (mounted) {
        setState(() {
          _configs = items;
          _usage = usage;
          _taskCatalog = tasks;
          _taskCfgs
            ..clear()
            ..addAll(cfgs);
          _configsLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _configsError = '$e';
          _configsLoading = false;
        });
      }
    }
  }

  Future<void> _taskSheet(Map<String, dynamic> task,
      [Map<String, dynamic>? cfg]) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _TaskFormSheet(
          api: _cfgApi, rs: widget.rs, task: task, existing: cfg),
    );
    if (saved == true) await _loadConfigs();
  }

  Future<void> _saveDnd(DndSettings next) async {
    setState(() {
      _dnd = next;
      _saving = true;
    });
    try {
      await _api.updateDnd(next);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(widget.rs.t('settingsSaved'))));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('${widget.rs.t('settingsSaveFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _pickTime(bool start) async {
    final initial = TimeOfDay(
        hour: start ? _dnd.startHour : _dnd.endHour,
        minute: start ? _dnd.startMinute : _dnd.endMinute);
    final picked = await showTimePicker(context: context, initialTime: initial);
    if (picked == null) return;
    await _saveDnd(start
        ? _dnd.copyWith(startHour: picked.hour, startMinute: picked.minute)
        : _dnd.copyWith(endHour: picked.hour, endMinute: picked.minute));
  }

  String _name() {
    final p = _profile;
    if (p == null) return widget.myUserId == null ? '—' : '#${widget.myUserId}';
    final nick = (p['nickname'] as String? ?? '').trim();
    final user = (p['username'] as String? ?? '').trim();
    return nick.isNotEmpty ? nick : (user.isNotEmpty ? user : '#${p['id']}');
  }

  String _hm(int h, int m) =>
      '${h.toString().padLeft(2, '0')}:${m.toString().padLeft(2, '0')}';

  String _num(Object? v) {
    final n = v is num ? v.toInt() : int.tryParse('$v') ?? 0;
    final s = n.toString();
    final b = StringBuffer();
    for (var i = 0; i < s.length; i++) {
      final rem = s.length - i;
      b.write(s[i]);
      if (rem > 1 && rem % 3 == 1) b.write(',');
    }
    return b.toString();
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(msg)));
  }

  // ── 模型与 API ────────────────────────────────────────────────────

  Future<void> _editSheet([Map<String, dynamic>? existing]) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _CfgFormSheet(
          api: _cfgApi, rs: widget.rs, existing: existing),
    );
    if (saved == true) await _loadConfigs();
  }

  Future<void> _cfgActions(Map<String, dynamic> cfg) async {
    final rs = widget.rs;
    final shared = cfg['is_shared'] == true;
    // 测试结果三态变量外提：StatefulBuilder 重建会重置 builder 内局部变量，
    // 不外提的话「测试连接」的结果永远不会显示出来。
    Map<String, dynamic>? testResult;
    String? testError;
    bool testing = false;
    await showModalBottomSheet<void>(
      context: context,
      builder: (sheetCtx) => StatefulBuilder(builder: (ctx, setSheet) {
        Future<void> run(Future<void> Function() action) async {
          Navigator.pop(sheetCtx);
          await action();
        }

        return SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            ListTile(
              leading: const Icon(Icons.key),
              title: Text(
                  ((cfg['name'] as String? ?? '').trim().isNotEmpty)
                      ? cfg['name'] as String
                      : '#${cfg['id']}'),
              subtitle: shared ? Text(rs.t('cfgReadonly')) : null,
            ),
            const Divider(height: 1),
            if (shared)
              ListTile(
                enabled: false,
                leading: const Icon(Icons.lock_outline),
                title: Text(rs.t('cfgReadonly')),
              )
            else ...[
              ListTile(
                enabled: cfg['is_default'] != true,
                leading: const Icon(Icons.star_outline),
                title: Text(rs.t('cfgSetDefault')),
                onTap: cfg['is_default'] == true
                    ? null
                    : () => run(() async {
                          try {
                            await _cfgApi.setDefault(cfg['id'] as int);
                            await _loadConfigs();
                          } catch (e) {
                            _toast('${rs.t('cfgSaveFailed')}：$e');
                          }
                        }),
              ),
              SwitchListTile(
                secondary: const Icon(Icons.group_outlined),
                title: Text(rs.t('cfgShared')),
                value: cfg['shared_with_subs'] == true,
                onChanged: (v) => run(() async {
                  try {
                    await _cfgApi.setShare(cfg['id'] as int, v);
                    await _loadConfigs();
                  } catch (e) {
                    _toast('${rs.t('cfgSaveFailed')}：$e');
                  }
                }),
              ),
              StatefulBuilder(builder: (ctx2, setSheet) {
                Future<void> doTest() async {
                  setSheet(() {
                    testing = true;
                    testError = null;
                    testResult = null;
                  });
                  try {
                    final r = await _cfgApi.test(cfg['id'] as int);
                    setSheet(() => testResult = r);
                  } catch (e) {
                    setSheet(() => testError = '$e');
                  } finally {
                    setSheet(() => testing = false);
                  }
                }

                return Column(mainAxisSize: MainAxisSize.min, children: [
                  ListTile(
                    enabled: !testing,
                    leading: const Icon(Icons.network_check),
                    title: Text(rs.t('cfgTest')),
                    trailing: testing
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child:
                                CircularProgressIndicator(strokeWidth: 2))
                        : null,
                    onTap: doTest,
                  ),
                  if (testResult != null)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: Text(
                          testResult!['ok'] == true
                              ? '${rs.t('cfgTestOk')} · ${rs.t('cfgTestLatency')} '
                                  '${testResult!['latency_ms']}ms · '
                                  '${testResult!['model'] ?? ''} '
                                  '${testResult!['api_key_tail'] ?? ''}'
                              : rs.t('cfgTestFail'),
                          style: Theme.of(ctx2).textTheme.bodySmall,
                        ),
                      ),
                    ),
                  if (testError != null)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: Text('${rs.t('cfgTestFail')}：$testError',
                            style: Theme.of(ctx2).textTheme.bodySmall),
                      ),
                    ),
                ]);
              }),
              ListTile(
                leading: const Icon(Icons.edit_outlined),
                title: Text(rs.t('cfgEdit')),
                onTap: () {
                  Navigator.pop(sheetCtx);
                  _editSheet(cfg);
                },
              ),
              ListTile(
                leading: const Icon(Icons.delete_outline),
                title: Text(rs.t('cfgDelete')),
                onTap: () async {
                  final sheetNav = Navigator.of(sheetCtx);
                  final ok = await showDialog<bool>(
                    context: sheetCtx,
                    builder: (dctx) => AlertDialog(
                      content: Text(rs.t('cfgDeleteConfirm')),
                      actions: [
                        TextButton(
                            onPressed: () => Navigator.pop(dctx, false),
                            child: Text(MaterialLocalizations.of(dctx)
                                .cancelButtonLabel)),
                        TextButton(
                            onPressed: () => Navigator.pop(dctx, true),
                            child: Text(rs.t('cfgDelete'))),
                      ],
                    ),
                  );
                  if (ok != true) return;
                  sheetNav.pop();
                  try {
                    await _cfgApi.delete(cfg['id'] as int);
                    await _loadConfigs();
                  } catch (e) {
                    _toast('${rs.t('cfgSaveFailed')}：$e');
                  }
                },
              ),
            ],
          ]),
        );
      }),
    );
  }

  Widget _badge(String text, ThemeData theme, {bool warn = false}) {
    final scheme = theme.colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: warn ? scheme.errorContainer : scheme.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(text,
          style: theme.textTheme.labelSmall?.copyWith(
              color: warn ? scheme.onErrorContainer : scheme.onSecondaryContainer)),
    );
  }

  Widget _cfgTile(Map<String, dynamic> cfg, ThemeData theme) {
    final rs = widget.rs;
    final name =
        ((cfg['name'] as String? ?? '').trim().isNotEmpty)
            ? cfg['name'] as String
            : '#${cfg['id']}';
    final subtitle = [
      cfg['model'],
      cfg['provider'],
      cfg['base_url'],
    ].whereType<String>().where((s) => s.trim().isNotEmpty).join(' · ');
    final badges = <Widget>[
      if (cfg['is_default'] == true) _badge(rs.t('cfgBadgeDefault'), theme),
      if (cfg['is_shared'] == true) _badge(rs.t('cfgBadgeShared'), theme),
      if (cfg['enabled'] == false) _badge(rs.t('cfgBadgeOff'), theme, warn: true),
    ];
    return ListTile(
      leading: Icon(
          cfg['is_shared'] == true ? Icons.group_outlined : Icons.vpn_key_outlined),
      title: Row(children: [
        Expanded(child: Text(name, overflow: TextOverflow.ellipsis)),
        ...badges,
      ]),
      subtitle: subtitle.isEmpty ? null : Text(subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      onTap: () => _cfgActions(cfg),
    );
  }

  Widget _usageTile(ThemeData theme) {
    final rs = widget.rs;
    final u = _usage!;
    Widget cell(String label, Object? v) => Expanded(
          child: Column(children: [
            Text(_num(v), style: theme.textTheme.titleSmall),
            Text(label, style: theme.textTheme.labelSmall),
          ]),
        );
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.insights, size: 18, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(width: 6),
          Text(rs.t('cfgUsage'), style: theme.textTheme.titleSmall),
          const Spacer(),
          if (u['total_limit'] is num && (u['total_limit'] as num) > 0)
            Text('${rs.t('cfgUsageRemaining')} ${_num(u['remaining'])}',
                style: theme.textTheme.labelSmall),
        ]),
        const SizedBox(height: 8),
        Row(children: [
          cell(rs.t('cfgUsageToday'), u['today']),
          cell(rs.t('cfgUsageWeek'), u['week']),
          cell(rs.t('cfgUsageMonth'), u['month']),
          cell(rs.t('cfgUsageTotal'), u['used_total']),
        ]),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return ListView(padding: const EdgeInsets.all(16), children: [
      _section(rs.t('settingsAccount'), theme),
      Card(
        child: Column(children: [
          ListTile(
            leading: const Icon(Icons.person_outline),
            title: Text(_name()),
            subtitle: Text(_profileError.isNotEmpty
                ? _profileError
                : '${rs.t('settingsUser')} #${widget.myUserId ?? '—'} · ${widget.client.dio.options.baseUrl}'),
          ),
          const Divider(height: 1),
          ListTile(
            leading: const Icon(Icons.logout),
            title: Text(rs.t('settingsLogout')),
            onTap: widget.onLogout,
          ),
        ]),
      ),
      const SizedBox(height: 18),
      _section(rs.t('settingsAppearance'), theme),
      Card(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(rs.t('appearanceMode'),
                      style: theme.textTheme.labelSmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant)),
                  const SizedBox(height: 8),
                  SegmentedButton<ThemeMode>(
                    segments: [
                      ButtonSegment(
                          value: ThemeMode.system,
                          icon: const Icon(Icons.brightness_auto, size: 16),
                          label: Text(rs.t('appearanceFollowSystem'))),
                      ButtonSegment(
                          value: ThemeMode.light,
                          icon: const Icon(Icons.light_mode_outlined, size: 16),
                          label: Text(rs.t('appearanceLight'))),
                      ButtonSegment(
                          value: ThemeMode.dark,
                          icon: const Icon(Icons.dark_mode_outlined, size: 16),
                          label: Text(rs.t('appearanceDark'))),
                    ],
                    selected: {widget.themeMode},
                    onSelectionChanged: (sel) =>
                        widget.onTheme(sel.first),
                  ),
                ]),
          ),
          const Divider(height: 1),
          ListTile(
            leading: const Icon(Icons.translate),
            title: Text(rs.t('settingsLanguage')),
            trailing: Text(widget.lang == 'zh' ? '中文' : 'English'),
            onTap: () => widget.onLang(widget.lang == 'zh' ? 'en' : 'zh'),
          ),
        ]),
      ),
      const SizedBox(height: 18),
      _section(rs.t('appearanceSkins'), theme),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final preset in pwaSkinPresets)
                ChoiceChip(
                  label: Text(widget.lang == 'zh'
                      ? preset.nameZh
                      : preset.nameEn),
                  selected: widget.skinId == preset.id,
                  onSelected: (_) => widget.onSkin(preset.id),
                ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 18),
      _section(rs.t('appearanceSeed'), theme),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              ChoiceChip(
                avatar: const Icon(Icons.palette_outlined, size: 16),
                label: Text(rs.t('appearanceSeedAuto')),
                selected: widget.seedIndex < 0,
                onSelected: (_) => widget.onSeed(-1),
              ),
              for (var i = 0; i < pwaSeedColors.length; i++)
                ChoiceChip(
                  avatar: CircleAvatar(
                      radius: 8, backgroundColor: pwaSeedColors[i].color),
                  label: Text(widget.lang == 'zh'
                      ? pwaSeedColors[i].nameZh
                      : pwaSeedColors[i].nameEn),
                  selected: widget.seedIndex == i,
                  onSelected: (_) => widget.onSeed(i),
                ),
            ],
          ),
        ),
      ),
      Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 0),
        child: Text(rs.t('appearanceNote'), style: theme.textTheme.bodySmall),
      ),
      const SizedBox(height: 18),
      _section(rs.t('settingsDnd'), theme),
      Card(
        child: Column(children: [
          SwitchListTile(
            secondary: const Icon(Icons.nightlight_outlined),
            title: Text(rs.t('settingsDndEnabled')),
            value: _dnd.enabled,
            onChanged: _saving
                ? null
                : (v) => _saveDnd(_dnd.copyWith(enabled: v)),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.notifications_none),
            title: Text(rs.t('settingsNotifications')),
            value: _dnd.notificationsEnabled,
            onChanged: _saving
                ? null
                : (v) => _saveDnd(_dnd.copyWith(notificationsEnabled: v)),
          ),
          ListTile(
            leading: const Icon(Icons.schedule),
            title: Text(rs.t('settingsDndStart')),
            trailing: Text(_hm(_dnd.startHour, _dnd.startMinute)),
            onTap: _saving ? null : () => _pickTime(true),
          ),
          ListTile(
            leading: const Icon(Icons.schedule_outlined),
            title: Text(rs.t('settingsDndEnd')),
            trailing: Text(_hm(_dnd.endHour, _dnd.endMinute)),
            onTap: _saving ? null : () => _pickTime(false),
          ),
          if (_saving)
            const Padding(
              padding: EdgeInsets.only(bottom: 8),
              child: LinearProgressIndicator(),
            ),
        ]),
      ),
      const SizedBox(height: 18),
      _section(rs.t('settingsApiModels'), theme),
      Card(
        child: _configsLoading
            ? const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()))
            : _configsError.isNotEmpty
                ? ListTile(
                    leading: const Icon(Icons.error_outline),
                    title: Text(rs.t('cfgLoadFailed')),
                    subtitle: Text(_configsError),
                  )
                : Column(children: [
                    if (_usage != null) _usageTile(theme),
                    if (_configs.isEmpty)
                      ListTile(title: Text(rs.t('cfgEmpty')))
                    else
                      ..._configs.map((c) => _cfgTile(c, theme)),
                    ListTile(
                      leading: const Icon(Icons.add),
                      title: Text(rs.t('cfgAdd')),
                      onTap: () => _editSheet(),
                    ),
                  ]),
      ),
      const SizedBox(height: 18),
      _section(rs.t('updatesTitle'), theme),
      Card(
        child: Column(children: [
          ListTile(
            leading: const Icon(Icons.campaign_outlined),
            title: Text(rs.t('updatesTitle')),
            onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => UpdatesPage(client: widget.client, rs: rs))),
          ),
          const Divider(height: 1),
          ListTile(
            leading: const Icon(Icons.tune),
            title: Text(rs.t('flagsTitle')),
            onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => FeatureFlagsPage(client: widget.client, rs: rs))),
          ),
          const Divider(height: 1),
          ListTile(
            leading: const Icon(Icons.speed),
            title: Text(rs.t('budgetTitle')),
            onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => ContextBudgetPage(client: widget.client, rs: rs))),
          ),
          const Divider(height: 1),
          ListTile(
            leading: const Icon(Icons.dns_outlined),
            title: Text(rs.t('srvCfgTitle')),
            onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => ServerConfigPage(client: widget.client, rs: rs))),
          ),
        ]),
      ),
      const SizedBox(height: 18),
      _section(rs.t('taskCfgTitle'), theme),
      Card(
        child: _taskCatalog.isEmpty
            ? Column(children: [
                ListTile(title: Text(rs.t('cfgEmpty'))),
                ListTile(
                  leading: const Icon(Icons.add),
                  title: Text(rs.t('cfgAdd')),
                  onTap: () => _loadConfigs(),
                ),
              ])
            : Column(
                children: _taskCatalog.map((t) {
                  final task = t['task'] as String? ?? '';
                  final cfg = _taskCfgs[task];
                  final bound = cfg?['configured'] == true;
                  final model = (cfg?['model'] as String? ?? '').trim();
                  final desc = (t['desc'] as String? ?? '').trim();
                  return ListTile(
                    leading: Icon(
                        bound ? Icons.check_circle_outline : Icons.circle_outlined),
                    title: Text(t['name'] as String? ?? task),
                    subtitle: Text(
                        '${desc.isEmpty ? task : desc}${bound && model.isNotEmpty ? ' · $model' : ''}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis),
                    trailing: Text(
                        bound ? rs.t('cfgBadgeDefault') : rs.t('taskCfgNotConfigured'),
                        style: theme.textTheme.labelSmall),
                    onTap: () => _taskSheet(t, cfg),
                  );
                }).toList(),
              ),
      ),
      const SizedBox(height: 24),
      Text(
        rs.t('settingsDndNote'),
        style: theme.textTheme.bodySmall,
      ),
    ]);
  }

  Widget _section(String title, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
        child: Text(title,
            style: theme.textTheme.titleSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant)),
      );
}

/// 新增 / 编辑配置的表单底栏。编辑态可「测试连接」（走 `POST /{id}/test`，
/// 只对已保存配置可用——后端没有无 id 的试探端点）。
class _CfgFormSheet extends StatefulWidget {
  const _CfgFormSheet({required this.api, required this.rs, this.existing});

  final ApiConfigApi api;
  final ReaderStrings rs;
  final Map<String, dynamic>? existing;

  @override
  State<_CfgFormSheet> createState() => _CfgFormSheetState();
}

class _CfgFormSheetState extends State<_CfgFormSheet> {
  late final TextEditingController _name = TextEditingController(
      text: widget.existing?['name'] as String? ?? '');
  late final TextEditingController _baseUrl = TextEditingController(
      text: widget.existing?['base_url'] as String? ?? '');
  late final TextEditingController _model = TextEditingController(
      text: widget.existing?['model'] as String? ?? '');
  late final TextEditingController _provider = TextEditingController(
      text: widget.existing?['provider'] as String? ?? '');
  final TextEditingController _apiKey = TextEditingController();
  late bool _enabled = widget.existing?['enabled'] != false;
  late bool _isDefault = widget.existing?['is_default'] == true;
  late bool _shared = widget.existing?['shared_with_subs'] == true;
  bool _saving = false;
  bool _testing = false;
  String? _testNote;
  bool _testOk = false;

  bool get _isEdit => widget.existing != null;

  @override
  void dispose() {
    _name.dispose();
    _baseUrl.dispose();
    _model.dispose();
    _provider.dispose();
    _apiKey.dispose();
    super.dispose();
  }

  Map<String, dynamic> _body() => {
        'name': _name.text.trim(),
        'base_url': _baseUrl.text.trim(),
        'model': _model.text.trim(),
        'provider': _provider.text.trim(),
        'enabled': _enabled,
        'is_default': _isDefault,
        'shared_with_subs': _shared,
        if (_apiKey.text.trim().isNotEmpty) 'api_key': _apiKey.text.trim(),
      };

  Future<void> _save() async {
    if (_name.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(widget.rs.t('cfgNameRequired'))));
      return;
    }
    setState(() => _saving = true);
    try {
      if (_isEdit) {
        await widget.api
            .update(widget.existing!['id'] as int, _body());
      } else {
        await widget.api.create(_body());
      }
      if (!mounted) return;
      Navigator.pop(context, true);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('cfgSaved'))));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('${widget.rs.t('cfgSaveFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _test() async {
    if (!_isEdit || _testing) return;
    setState(() {
      _testing = true;
      _testNote = null;
    });
    try {
      final r = await widget.api.test(widget.existing!['id'] as int);
      if (!mounted) return;
      setState(() {
        _testOk = r['ok'] == true;
        _testNote = _testOk
            ? '${widget.rs.t('cfgTestOk')} · ${widget.rs.t('cfgTestLatency')} '
                '${r['latency_ms']}ms · ${r['model'] ?? ''} ${r['api_key_tail'] ?? ''}'
            : widget.rs.t('cfgTestFail');
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _testOk = false;
        _testNote = '${widget.rs.t('cfgTestFail')}：$e';
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    Widget field(TextEditingController ctrl, String label,
        {bool obscure = false, String? hint}) {
      return Padding(
        padding: const EdgeInsets.only(top: 10),
        child: TextField(
          controller: ctrl,
          obscureText: obscure,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            isDense: true,
            border:
                OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
          ),
        ),
      );
    }

    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 12,
            bottom: MediaQuery.of(context).viewInsets.bottom + 12),
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(_isEdit ? rs.t('cfgEdit') : rs.t('cfgAdd'),
                style: theme.textTheme.titleMedium),
            field(_name, rs.t('cfgName')),
            field(_baseUrl, rs.t('cfgBaseUrl')),
            field(_model, rs.t('cfgModel')),
            field(_provider, rs.t('cfgProvider')),
            field(_apiKey, rs.t('cfgApiKey'),
                obscure: true,
                hint:
                    (_isEdit && (widget.existing?['has_api_key'] == true))
                        ? rs.t('cfgApiKeyKeep')
                        : null),
            SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(rs.t('cfgEnabled')),
                value: _enabled,
                onChanged: (v) => setState(() => _enabled = v)),
            SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(rs.t('cfgIsDefault')),
                value: _isDefault,
                onChanged: (v) => setState(() => _isDefault = v)),
            SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(rs.t('cfgShared')),
                value: _shared,
                onChanged: (v) => setState(() => _shared = v)),
            if (_isEdit)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _testing ? null : _test,
                  icon: _testing
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child:
                              CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.network_check, size: 18),
                  label: Text(rs.t('cfgTest')),
                ),
              ),
            if (_testNote != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Text(_testNote!,
                    style: theme.textTheme.bodySmall?.copyWith(
                        color: _testOk
                            ? theme.colorScheme.primary
                            : theme.colorScheme.error)),
              ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: const Icon(Icons.save_outlined, size: 18),
                label: Text(rs.t('cfgSave')),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

/// 任务级 LLM 配置表单（用户级 upsert）：base_url/model/provider/api_key ＋ enabled ＋
/// payload 连接测试（api_key 留空＝测试时回落服务器级全局配置；保存时**不上送**＝保留现值）。
class _TaskFormSheet extends StatefulWidget {
  const _TaskFormSheet({
    required this.api,
    required this.rs,
    required this.task,
    this.existing,
  });

  final ApiConfigApi api;
  final ReaderStrings rs;
  final Map<String, dynamic> task;
  final Map<String, dynamic>? existing;

  @override
  State<_TaskFormSheet> createState() => _TaskFormSheetState();
}

class _TaskFormSheetState extends State<_TaskFormSheet> {
  late final TextEditingController _baseUrl = TextEditingController(
      text: widget.existing?['base_url'] as String? ?? '');
  late final TextEditingController _model = TextEditingController(
      text: widget.existing?['model'] as String? ?? '');
  late final TextEditingController _provider = TextEditingController(
      text: widget.existing?['provider'] as String? ?? '');
  final TextEditingController _apiKey = TextEditingController();
  late bool _enabled = widget.existing?['enabled'] != false;
  bool _saving = false;
  bool _testing = false;
  String? _testNote;
  bool _testOk = false;

  String get _task => widget.task['task'] as String? ?? '';

  @override
  void dispose() {
    _baseUrl.dispose();
    _model.dispose();
    _provider.dispose();
    _apiKey.dispose();
    super.dispose();
  }

  Map<String, dynamic> _body() => {
        'base_url': _baseUrl.text.trim(),
        'model': _model.text.trim(),
        'provider': _provider.text.trim(),
        'enabled': _enabled,
        if (_apiKey.text.trim().isNotEmpty) 'api_key': _apiKey.text.trim(),
      };

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await widget.api.putTaskConfig(_task, _body());
      if (!mounted) return;
      Navigator.pop(context, true);
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('cfgSaved'))));
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('${widget.rs.t('cfgSaveFailed')}：$e')));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _test() async {
    if (_testing) return;
    setState(() {
      _testing = true;
      _testNote = null;
    });
    try {
      final r = await widget.api.testConnection({
        'base_url': _baseUrl.text.trim(),
        'model': _model.text.trim(),
        'provider': _provider.text.trim(),
        if (_apiKey.text.trim().isNotEmpty) 'api_key': _apiKey.text.trim(),
      });
      if (!mounted) return;
      setState(() {
        _testOk = r['ok'] == true;
        _testNote = _testOk
            ? '${widget.rs.t('cfgTestOk')} · ${widget.rs.t('cfgTestLatency')} '
                '${r['latency_ms']}ms · ${r['model'] ?? ''}'
            : widget.rs.t('cfgTestFail');
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _testOk = false;
        _testNote = '${widget.rs.t('cfgTestFail')}：$e';
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    Widget field(TextEditingController ctrl, String label,
        {bool obscure = false, String? hint}) {
      return Padding(
        padding: const EdgeInsets.only(top: 10),
        child: TextField(
          controller: ctrl,
          obscureText: obscure,
          decoration: InputDecoration(
            labelText: label,
            hintText: hint,
            isDense: true,
            border:
                OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
          ),
        ),
      );
    }

    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 12,
            bottom: MediaQuery.of(context).viewInsets.bottom + 12),
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text('${widget.task['name'] ?? _task} · ${rs.t('taskCfgTitle')}',
                style: theme.textTheme.titleMedium),
            Text(rs.t('taskCfgHint'),
                style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant)),
            field(_baseUrl, rs.t('cfgBaseUrl')),
            field(_model, rs.t('cfgModel')),
            field(_provider, rs.t('cfgProvider')),
            field(_apiKey, rs.t('cfgApiKey'),
                obscure: true,
                hint: (widget.existing?['has_api_key'] == true)
                    ? rs.t('cfgApiKeyKeep')
                    : null),
            SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(rs.t('cfgEnabled')),
                value: _enabled,
                onChanged: (v) => setState(() => _enabled = v)),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _testing ? null : _test,
                icon: _testing
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.network_check, size: 18),
                label: Text(rs.t('cfgTest')),
              ),
            ),
            if (_testNote != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Text(_testNote!,
                    style: theme.textTheme.bodySmall?.copyWith(
                        color: _testOk
                            ? theme.colorScheme.primary
                            : theme.colorScheme.error)),
              ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: const Icon(Icons.save_outlined, size: 18),
                label: Text(rs.t('cfgSave')),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
