import 'dart:async';
import 'dart:convert';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../l10n/reader_strings.dart';

/// 渠道扫码登录（2026-11-22 批 9）：微信 ilink 扫码（官方 login-qr 状态机）＋
/// 抖音 bind/qr 会话（服务器有头 Edge 截屏回传）。
///
/// 二维码是**待渲染的 URL 字符串**（非图片），用 qr_flutter 自行绘制——纯 Dart
/// 绘制，Web 可用。轮询用 Timer 驱动（微信 ~35s 长轮询、抖音 2s 快轮询）。
/// 关页面即取消抖音会话（尽力而为）；微信无取消（等 TTL 过期）。
class ChannelQrPage extends StatefulWidget {
  const ChannelQrPage({super.key, required this.client, required this.rs});

  final AmbApiClient client;
  final ReaderStrings rs;

  @override
  State<ChannelQrPage> createState() => _ChannelQrPageState();
}

enum _Channel { wechat, douyin }

class _ChannelQrPageState extends State<ChannelQrPage> {
  late final ChannelQrApi _api = ChannelQrApi(widget.client);
  _Channel _channel = _Channel.wechat;
  bool _busy = false;
  String _error = '';
  String _statusText = '';
  bool _success = false;
  String? _restartPending;

  // 微信状态机
  Timer? _wxTimer;
  String? _qrcode;
  bool _verifyInput = false;
  final TextEditingController _verifyCtrl = TextEditingController();

  // 抖音会话
  Timer? _dyTimer;
  String? _dySession;
  String? _dyImageB64;

  // 绑定用角色（confirmed 后拉取）
  List<({int id, String name})> _characters = const [];
  int? _bindCharId;
  Map<String, dynamic>? _wxConfirmed;

  @override
  void dispose() {
    _wxTimer?.cancel();
    _dyTimer?.cancel();
    _verifyCtrl.dispose();
    // 抖音会话尽力而为取消（微信无取消，等 TTL）
    if (_dySession != null) {
      _api.cancelDouyinQr(_dySession!);
    }
    super.dispose();
  }

  void _reset({bool keepChannel = false}) {
    _wxTimer?.cancel();
    _dyTimer?.cancel();
    if (!keepChannel && _dySession != null) {
      _api.cancelDouyinQr(_dySession!);
      _dySession = null;
    }
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = '';
      _statusText = '';
      _success = false;
      _restartPending = null;
      _qrcode = null;
      _verifyInput = false;
      _dyImageB64 = null;
      _wxConfirmed = null;
      _bindCharId = null;
    });
  }

  Future<void> _startWechat() async {
    _reset();
    setState(() => _busy = true);
    try {
      final data = await _api.fetchWechatQrcode();
      if (!mounted) return;
      setState(() {
        _qrcode = data['qrcode_img_content'] as String? ?? '';
        _statusText = widget.rs.t('qrWaitScan');
        _busy = false;
      });
      _pollWechat();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _busy = false;
      });
    }
  }

  Future<void> _pollWechat() async {
    if (_qrcode == null || _qrcode!.isEmpty) return;
    try {
      final data = await _api.pollWechatStatus(_qrcode!,
          verifyCode: _verifyCtrl.text.trim());
      if (!mounted) return;
      final status = data['status'] as String? ?? 'wait';
      switch (status) {
        case 'confirmed':
          setState(() {
            _wxConfirmed = data;
            _statusText = widget.rs.t('qrWechatConfirmed');
          });
          await _loadBindCharacters();
          return; // 停轮询，等选角色绑定
        case 'need_verifycode':
          setState(() {
            _verifyInput = true;
            _statusText = widget.rs.t('qrNeedVerify');
          });
          return; // 等输入后再续轮询
        case 'expired':
        case 'verify_code_blocked':
        case 'scaned_but_redirect':
        case 'binded_redirect':
          setState(() {
            _statusText = '${widget.rs.t('qrStatus')}: $status';
          });
          return; // 终态（引导重开）
        case 'scaned':
          setState(() => _statusText = widget.rs.t('qrScanned'));
          break;
        default:
          setState(() => _statusText = widget.rs.t('qrWaitScan'));
      }
      _wxTimer = Timer(const Duration(seconds: 1), _pollWechat);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  Future<void> _continueWechatPoll() async {
    _wxTimer?.cancel();
    setState(() => _statusText = widget.rs.t('qrWaitScan'));
    _pollWechat();
  }

  Future<void> _loadBindCharacters() async {
    try {
      final t = ChatTransport(widget.client);
      final chars = await t.getCharacters();
      if (!mounted) return;
      setState(() {
        _characters = [for (final c in chars) (id: c.id, name: c.name)];
        if (_characters.isNotEmpty) _bindCharId = _characters.first.id;
      });
    } catch (_) {}
  }

  Future<void> _bindWechat() async {
    final c = _wxConfirmed;
    if (c == null || _bindCharId == null) return;
    setState(() => _busy = true);
    try {
      final r = await _api.bindWechat(
        characterId: _bindCharId!,
        botToken: c['bot_token'] as String? ?? '',
        baseurl: c['baseurl'] as String? ?? '',
        ilinkUserId: c['ilink_user_id'] as String? ?? '',
        ilinkBotId: c['ilink_bot_id'] as String? ?? '',
      );
      if (!mounted) return;
      setState(() {
        _success = true;
        _restartPending =
            r['gateway_restart_pending'] == true ? widget.rs.t('qrRestartPending') : null;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _busy = false;
      });
    }
  }

  Future<void> _startDouyin() async {
    _reset(keepChannel: true);
    setState(() => _busy = true);
    try {
      final data = await _api.startDouyinQr();
      if (!mounted) return;
      _dySession = data['session_id'] as String? ?? '';
      setState(() {
        _dyImageB64 = data['image_png_base64'] as String?;
        _statusText = widget.rs.t('qrWaitScan');
        _busy = false;
      });
      _pollDouyin();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _busy = false;
      });
    }
  }

  Future<void> _pollDouyin() async {
    final sid = _dySession;
    if (sid == null || sid.isEmpty) return;
    try {
      final data = await _api.pollDouyinQr(sid);
      if (!mounted) return;
      final state = data['state'] as String? ?? '';
      final img = data['image_png_base64'] as String?;
      final account = data['account_name'] as String?;
      setState(() {
        if (img != null && img.isNotEmpty) _dyImageB64 = img;
        _statusText = account != null && account.isNotEmpty
            ? '${widget.rs.t('qrDouyinBound')} $account'
            : state;
        if (state == 'confirmed' || state == 'binded') {
          _success = true;
        }
      });
      if (_success) return;
      _dyTimer = Timer(const Duration(seconds: 2), _pollDouyin);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final rs = widget.rs;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(rs.t('qrChannelTitle'))),
      body: ListView(padding: const EdgeInsets.all(12), children: [
        SegmentedButton<_Channel>(
          segments: [
            ButtonSegment(
                value: _Channel.wechat,
                icon: const Icon(Icons.chat_outlined, size: 16),
                label: Text(rs.t('qrChannelWechat'))),
            ButtonSegment(
                value: _Channel.douyin,
                icon: const Icon(Icons.videocam_outlined, size: 16),
                label: Text(rs.t('qrChannelDouyin'))),
          ],
          selected: {_channel},
          onSelectionChanged: (sel) {
            setState(() => _channel = sel.first);
            _reset();
          },
        ),
        const SizedBox(height: 12),
        if (_error.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(_error,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.error)),
          ),
        if (_success) ...[
          Card(
            child: ListTile(
              leading: const Icon(Icons.check_circle, color: Colors.green),
              title: Text(rs.t('qrSuccess')),
              subtitle: _restartPending != null ? Text(_restartPending!) : null,
            ),
          ),
        ] else ...[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(children: [
                if (_busy)
                  const CircularProgressIndicator()
                else ...[
                  if (_channel == _Channel.wechat) ...[
                    if (_qrcode != null && _qrcode!.isNotEmpty)
                      QrImageView(data: _qrcode!, size: 220)
                    else
                      Text(rs.t('qrStartHint')),
                    if (_verifyInput) ...[
                      const SizedBox(height: 10),
                      TextField(
                        controller: _verifyCtrl,
                        keyboardType: TextInputType.number,
                        decoration: InputDecoration(
                            labelText: rs.t('qrVerifyCode'),
                            border: OutlineInputBorder(
                                borderRadius: BorderRadius.circular(8))),
                      ),
                      TextButton(
                          onPressed: _continueWechatPoll,
                          child: Text(rs.t('qrVerifySubmit'))),
                    ],
                  ] else ...[
                    if (_dyImageB64 != null && _dyImageB64!.isNotEmpty)
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.memory(
                          base64Decode(_dyImageB64!),
                          height: 220,
                          fit: BoxFit.contain,
                        ),
                      )
                    else
                      Text(rs.t('qrStartHint')),
                  ],
                  const SizedBox(height: 10),
                  if (_statusText.isNotEmpty) Text(_statusText),
                ],
              ]),
            ),
          ),
          if (_channel == _Channel.wechat &&
              _wxConfirmed != null &&
              _characters.isNotEmpty) ...[
            _section(rs.t('qrBindCharacter'), theme),
            Card(
              child: RadioGroup<int>(
                groupValue: _bindCharId,
                onChanged: (v) => setState(() => _bindCharId = v ?? _bindCharId),
                child: Column(
                  children: [
                    for (final c in _characters)
                      RadioListTile<int>(
                        dense: true,
                        value: c.id,
                        title: Text(c.name),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: _busy ? null : _bindWechat,
                icon: const Icon(Icons.link, size: 18),
                label: Text(rs.t('qrBindConfirm')),
              ),
            ),
          ],
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _busy
                ? null
                : (_channel == _Channel.wechat
                    ? _startWechat
                    : _startDouyin),
            icon: const Icon(Icons.refresh, size: 18),
            label: Text(rs.t('qrRestart')),
          ),
          const SizedBox(height: 8),
          Text(rs.t('qrNote'), style: theme.textTheme.bodySmall),
        ],
      ]),
    );
  }

  Widget _section(String title, ThemeData theme) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 6, 4, 6),
        child: Text(title,
            style: theme.textTheme.titleSmall
                ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
      );
}
