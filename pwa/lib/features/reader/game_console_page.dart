import 'dart:convert';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 游戏控制台（2026-11-22 批 13）：新建对局→对局视图（状态 JSON）→行动表单→认输/中止。
/// 状态以 JSON 明细渲染（Web 版以功能对齐为口径，逐游戏视觉壳属 Flutter 版）。
class GameConsolePage extends StatefulWidget {
  const GameConsolePage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<GameConsolePage> createState() => _GameConsolePageState();
}

class _GameConsolePageState extends State<GameConsolePage> {
  late final GameApi _api = GameApi(widget.client);

  List<Map<String, dynamic>> _catalog = const [];
  bool _loading = true;
  String _error = '';

  int? _gameSid;
  Map<String, dynamic>? _state;
  bool _stateLoading = false;
  String _gameError = '';
  bool _acting = false;
  String _selectedGameType = '';
  bool _userAsPlayer = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      _catalog = await _api.catalog();
      if (!mounted) return;
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _createGame() async {
    if (_selectedGameType.isEmpty) return;
    setState(() => _acting = true);
    try {
      final body = {
        'game_type': _selectedGameType,
        'player_ids': <int>[],
        'user_as_player': _userAsPlayer,
      };
      final result = await _api.createSession(body);
      if (!mounted) return;
      final sid = result['session_id'] is num
          ? (result['session_id'] as num).toInt()
          : int.tryParse('${result['session_id'] ?? ''}');
      if (sid != null) {
        setState(() {
          _gameSid = sid;
          _acting = false;
        });
        await _refreshState();
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _gameError = '$e';
        _acting = false;
      });
    }
  }

  Future<void> _refreshState() async {
    final sid = _gameSid;
    if (sid == null) return;
    setState(() => _stateLoading = true);
    try {
      final st = await _api.gameState(sid);
      if (!mounted) return;
      setState(() {
        _state = st;
        _stateLoading = false;
        _gameError = '';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _gameError = '$e';
        _stateLoading = false;
      });
    }
  }

  void _showActionDialog() {
    final seatCtrl = TextEditingController();
    final actionCtrl = TextEditingController();
    final payloadCtrl = TextEditingController();
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(widget.rs.t('gameActionSubmit')),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            controller: seatCtrl,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
                labelText: widget.rs.t('gameActionSeat'),
                border:
                    OutlineInputBorder(borderRadius: BorderRadius.circular(8))),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: actionCtrl,
            decoration: InputDecoration(
                labelText: widget.rs.t('gameActionName'),
                border:
                    OutlineInputBorder(borderRadius: BorderRadius.circular(8))),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: payloadCtrl,
            maxLines: 2,
            decoration: InputDecoration(
                labelText: widget.rs.t('gameActionPayload'),
                hintText: '{"key": "value"}',
                border:
                    OutlineInputBorder(borderRadius: BorderRadius.circular(8))),
          ),
        ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
          FilledButton(
            onPressed: () async {
              final sid = _gameSid;
              final seat = int.tryParse(seatCtrl.text.trim()) ?? -1;
              final action = actionCtrl.text.trim();
              Map<String, dynamic>? payload;
              try {
                if (payloadCtrl.text.trim().isNotEmpty) {
                  payload =
                      jsonDecode(payloadCtrl.text.trim()) as Map<String, dynamic>;
                }
              } catch (_) {}
              Navigator.pop(ctx);
              if (sid == null || action.isEmpty) return;
              setState(() => _acting = true);
              try {
                await _api.gameAction(sid,
                    seat: seat, action: action, payload: payload);
                await _refreshState();
              } catch (e) {
                if (mounted) setState(() => _gameError = '$e');
              } finally {
                if (mounted) setState(() => _acting = false);
              }
            },
            child: Text(widget.rs.t('gameActionSubmit')),
          ),
        ],
      ),
    );
  }

  void _showCreateDialog() {
    if (_catalog.isEmpty) return;
    showDialog<void>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlg) => AlertDialog(
          title: Text(widget.rs.t('gameCreate')),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            DropdownButtonFormField<String>(
              initialValue: _selectedGameType.isNotEmpty ? _selectedGameType : null,
              items: [
                for (final g in _catalog)
                  DropdownMenuItem(
                      value: g['key'] as String? ?? '',
                      child: Text(g['name'] as String? ?? g['key'].toString())),
              ],
              onChanged: (v) => setDlg(() => _selectedGameType = v ?? ''),
              decoration: InputDecoration(
                  labelText: widget.rs.t('gameSelectType'),
                  border:
                      OutlineInputBorder(borderRadius: BorderRadius.circular(8))),
            ),
            const SizedBox(height: 8),
            SwitchListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              title: Text(widget.rs.t('gameUserAsPlayer')),
              value: _userAsPlayer,
              onChanged: (v) => setDlg(() => _userAsPlayer = v),
            ),
          ]),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx),
                child: Text(MaterialLocalizations.of(ctx).cancelButtonLabel)),
            FilledButton(
              onPressed: () {
                Navigator.pop(ctx);
                _createGame();
              },
              child: Text(widget.rs.t('gameCreate')),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _controls(String action) async {
    final sid = _gameSid;
    if (sid == null || _acting) return;
    setState(() => _acting = true);
    try {
      if (action == 'surrender') {
        await _api.gameSurrender(sid);
      } else if (action == 'abort') {
        await _api.gameAbort(sid);
      }
      await _refreshState();
    } catch (e) {
      if (mounted) setState(() => _gameError = '$e');
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);

    if (_loading) {
      return Scaffold(
          appBar: AppBar(title: Text(rs.t('gameConsoleTitle'))),
          body: const Center(child: CircularProgressIndicator()));
    }

    return Scaffold(
      appBar: AppBar(title: Text(rs.t('gameConsoleTitle'))),
      body: _error.isNotEmpty && _gameSid == null
          ? Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text(_error, style: theme.textTheme.bodySmall),
              TextButton(onPressed: _load, child: Text(rs.t('retry'))),
            ]))
          : ListView(padding: const EdgeInsets.all(12), children: [
              if (_gameSid == null) ...[
                Card(
                  child: ListTile(
                    leading: const Icon(Icons.sports_esports),
                    title: Text(rs.t('gameCreate')),
                    subtitle: _selectedGameType.isNotEmpty
                        ? Text(_selectedGameType)
                        : null,
                    onTap: _showCreateDialog,
                  ),
                ),
              ] else ...[
                Row(children: [
                  Expanded(
                    child: Text('${rs.t('gameSession')} #$_gameSid',
                        style: theme.textTheme.titleMedium),
                  ),
                  IconButton(
                    tooltip: rs.t('refresh'),
                    onPressed: _refreshState,
                    icon: const Icon(Icons.refresh, size: 20),
                  ),
                ]),
                if (_stateLoading)
                  const Padding(
                    padding: EdgeInsets.all(20),
                    child: Center(child: CircularProgressIndicator()),
                  )
                else if (_state != null) ...[
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(rs.t('gameState'),
                                style: theme.textTheme.titleSmall),
                            const SizedBox(height: 6),
                            SelectableText(
                                const JsonEncoder.withIndent('  ')
                                    .convert(_state),
                                style: theme.textTheme.bodySmall
                                    ?.copyWith(fontFamily: 'monospace')),
                          ]),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Row(children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _acting ? null : () => _controls('surrender'),
                        icon: const Icon(Icons.flag_outlined, size: 16),
                        label: Text(rs.t('gameSurrender')),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _acting ? null : () => _controls('abort'),
                        icon: const Icon(Icons.cancel_outlined, size: 16),
                        label: Text(rs.t('gameAbort')),
                      ),
                    ),
                  ]),
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      onPressed: _acting ? null : _showActionDialog,
                      icon: const Icon(Icons.touch_app, size: 16),
                      label: Text(rs.t('gameActionSubmit')),
                    ),
                  ),
                ],
                if (_gameError.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(_gameError,
                        style: theme.textTheme.bodySmall
                            ?.copyWith(color: theme.colorScheme.error)),
                  ),
                const SizedBox(height: 12),
                TextButton(
                    onPressed: () => setState(() {
                          _gameSid = null;
                          _state = null;
                        }),
                    child: Text(rs.t('gameBackToList'))),
              ],
              const SizedBox(height: 14),
              Text(rs.t('gameCatalog'), style: theme.textTheme.titleSmall),
              Card(
                child: Column(
                  children: [
                    for (final g in _catalog)
                      ListTile(
                        dense: true,
                        leading: const Icon(Icons.sports_esports_outlined,
                            size: 20),
                        title: Text(g['name'] as String? ?? ''),
                        subtitle: Text(g['key'] as String? ?? ''),
                      ),
                  ],
                ),
              ),
            ]),
    );
  }

}
