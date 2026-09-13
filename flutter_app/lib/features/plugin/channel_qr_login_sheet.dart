// 渠道扫码登录下放手机（2026-09-12，交接：AMBRACE_扫码绑定下放手机_交接_Codex致Zcode）。
// WechatQrLoginSheet：微信 ClawBot 取码 → 渲染二维码 → 长轮询状态机（wait/scaned/
//   need_verifycode/expired/confirmed/binded_redirect）→ 选角色绑定。
// DouyinBindSheet：抖音 bind/qr 会话（服务器有头 Edge 截屏回传二维码图）→ 轮询状态。
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../../models/character.dart';
import '../../services/api_client.dart';
import '../../l10n/app_localizations.dart';

/// 微信扫码登录结果（pop 返回给调用方刷新绑定列表）。
class WechatQrLoginResult {
  const WechatQrLoginResult({required this.characterName, required this.gatewayRestartPending});
  final String characterName;
  final bool gatewayRestartPending;
}

/// 微信扫码登录弹层（仅主账号入口）。
///
/// 状态机对齐腾讯官方 login-qr.ts（2026-09-12 核准）：二维码 5 分钟过期、最多自动刷新
/// 3 次；need_verifycode 弹配对码输入（微信上显示的数字）；binded_redirect 引导走
/// 「查看可添加的 bot」；confirmed 后选角色 → POST /bind。
class WechatQrLoginSheet extends StatefulWidget {
  const WechatQrLoginSheet({super.key});

  @override
  State<WechatQrLoginSheet> createState() => _WechatQrLoginSheetState();
}

enum _WechatPhase { loading, scanning, picking, binding, done, failed }

class _WechatQrLoginSheetState extends State<WechatQrLoginSheet> {
  _WechatPhase _phase = _WechatPhase.loading;
  String _statusText = '';
  String _qrData = ''; // 待渲染成二维码的 URL（qrcode_img_content）
  String _qrcode = ''; // 轮询用 token
  String _verifyCode = '';
  DateTime _sessionStart = DateTime.now();
  int _refreshes = 0;
  bool _wrongVerify = false;
  int _consecutiveErrors = 0; // 连续轮询失败计数（≥3 给可见提示，防静默卡住；红点1 连带自查）

  // confirmed 载荷
  String _botToken = '', _baseurl = '', _ilinkUserId = '', _ilinkBotId = '';
  List<AICharacter> _chars = [];
  int _pickedCid = -1;
  bool _gatewayRestartPending = false;
  String _boundName = '';

  static const _sessionTtl = Duration(minutes: 5); // iLink 二维码有效期（官方 ACTIVE_LOGIN_TTL_MS）
  static const _maxRefreshes = 3; // 对齐官方 MAX_QR_REFRESH_COUNT

  @override
  void initState() {
    super.initState();
    _startSession();
  }

  Future<void> _startSession() async {
    setState(() {
      _phase = _WechatPhase.loading;
      _statusText = AppLocalizations.of(context)!.channelQrFetching;
      _verifyCode = '';
      _wrongVerify = false;
    });
    try {
      final resp = await ApiClient().fetchWechatLoginQrcode();
      if (!mounted) return;
      if (resp['ok'] != true) {
        setState(() {
          _phase = _WechatPhase.failed;
          _statusText = resp['message'] as String? ??
              AppLocalizations.of(context)!.channelQrLoadFailed;
        });
        return;
      }
      setState(() {
        _qrData = resp['qrcode_img_content'] as String? ?? '';
        _qrcode = resp['qrcode'] as String? ?? '';
        _sessionStart = DateTime.now();
        _phase = _WechatPhase.scanning;
      });
      _pollLoop();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _WechatPhase.failed;
        _statusText = AppLocalizations.of(context)!.channelQrLoadFailed;
      });
    }
  }

  Future<void> _refreshQr() async {
    _statusText = AppLocalizations.of(context)!.channelQrRefreshing;
    if (mounted) setState(() {});
    try {
      final resp = await ApiClient().fetchWechatLoginQrcode();
      if (!mounted) return;
      if (resp['ok'] == true) {
        setState(() {
          _qrData = resp['qrcode_img_content'] as String? ?? '';
          _qrcode = resp['qrcode'] as String? ?? '';
          _sessionStart = DateTime.now();
          _verifyCode = '';
          _wrongVerify = false;
        });
        return;
      }
    } catch (_) {}
    if (mounted) {
      setState(() {
        _phase = _WechatPhase.failed;
        _statusText = AppLocalizations.of(context)!.channelQrLoadFailed;
      });
    }
  }

  Future<void> _pollLoop() async {
    final l10n = AppLocalizations.of(context)!;
    while (mounted && _phase == _WechatPhase.scanning) {
      // 会话 TTL：超限前自动刷新，超限后终止
      if (DateTime.now().difference(_sessionStart) > _sessionTtl) {
        if (_refreshes < _maxRefreshes) {
          _refreshes++;
          await _refreshQr();
          continue;
        }
        setState(() {
          _phase = _WechatPhase.failed;
          _statusText = l10n.channelQrExpiredFinal;
        });
        return;
      }
      Map<String, dynamic> resp;
      try {
        resp = await ApiClient().pollWechatLoginStatus(_qrcode, verifyCode: _verifyCode);
      } on DioException catch (_) {
        // 网络抖动：官方口径视为等待，继续轮询
        await Future.delayed(const Duration(seconds: 2));
        continue;
      } catch (_) {
        await Future.delayed(const Duration(seconds: 2));
        continue;
      }
      if (!mounted || _phase != _WechatPhase.scanning) return;
      // 连续失败守卫：ok=false 是真故障（HTTP/协议错误，后端已不再吞成 wait），
      // 连续 3 次进入 failed 给用户可见提示与重试入口，避免无限静默轮询。
      if (resp['ok'] != true) {
        _consecutiveErrors++;
        if (_consecutiveErrors >= 3) {
          setState(() {
            _phase = _WechatPhase.failed;
            _statusText = resp['message'] as String? ?? l10n.channelQrLoadFailed;
          });
          return;
        }
        await Future.delayed(const Duration(seconds: 2));
        continue;
      }
      _consecutiveErrors = 0;
      final status = resp['status'] as String? ?? (resp['ok'] == true ? 'wait' : '');
      switch (status) {
        case 'scaned':
        case 'scaned_but_redirect':
          setState(() => _statusText = l10n.channelQrScanned);
          break;
        case 'need_verifycode':
          await _promptVerifyCode();
          continue; // 立即带码重轮询（官方口径，不等待）
        case 'verify_code_blocked':
          setState(() {
            _verifyCode = '';
            _wrongVerify = false;
            _statusText = l10n.channelQrVerifyBlocked;
          });
          break;
        case 'expired':
          if (_refreshes < _maxRefreshes) {
            _refreshes++;
            await _refreshQr();
            continue;
          }
          setState(() {
            _phase = _WechatPhase.failed;
            _statusText = l10n.channelQrExpiredFinal;
          });
          return;
        case 'binded_redirect':
          setState(() {
            _phase = _WechatPhase.failed;
            _statusText = l10n.channelQrBindedRedirect;
          });
          return;
        case 'confirmed':
          await _onConfirmed(resp);
          return;
        case 'wait':
        default:
          // wait 与未知状态：保持当前提示继续等（首扫前为「等待扫码」）
          if (_statusText != l10n.channelQrScanned && _statusText != l10n.channelQrVerifyBlocked) {
            setState(() => _statusText = l10n.channelQrWaiting);
          }
          break;
      }
      await Future.delayed(const Duration(seconds: 1));
    }
  }

  /// need_verifycode：弹配对码输入（微信上显示的数字）。取消输入 = 继续等待。
  Future<void> _promptVerifyCode() async {
    final l10n = AppLocalizations.of(context)!;
    final ctrl = TextEditingController(text: _verifyCode);
    final code = await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (c) => AlertDialog(
        title: Text(l10n.channelQrNeedVerifyTitle),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(_wrongVerify ? l10n.channelQrVerifyWrong : l10n.channelQrNeedVerifyHint,
                style: const TextStyle(fontSize: 13)),
            const SizedBox(height: 10),
            TextField(
              controller: ctrl,
              autofocus: true,
              keyboardType: TextInputType.number,
              maxLength: 8,
              decoration: InputDecoration(counterText: '', isDense: true, border: const OutlineInputBorder()),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, ''),
            child: Text(l10n.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, ctrl.text.trim()),
            child: Text(l10n.confirm),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (code == null || code.isEmpty) return; // 取消：保持等待
    setState(() {
      _verifyCode = code;
      _wrongVerify = true; // 若再次 need_verifycode，提示改为「不匹配」
      _statusText = AppLocalizations.of(context)!.channelQrScanned;
    });
  }

  Future<void> _onConfirmed(Map<String, dynamic> resp) async {
    _botToken = resp['bot_token'] as String? ?? '';
    _baseurl = resp['baseurl'] as String? ?? '';
    _ilinkUserId = resp['ilink_user_id']?.toString() ?? '';
    _ilinkBotId = resp['ilink_bot_id']?.toString() ?? '';
    if (_botToken.isEmpty) {
      setState(() {
        _phase = _WechatPhase.failed;
        _statusText = AppLocalizations.of(context)!.channelQrLoadFailed;
      });
      return;
    }
    setState(() => _statusText = AppLocalizations.of(context)!.channelQrScanned);
    try {
      final chars = await ApiClient().getCharacters();
      if (!mounted) return;
      setState(() {
        _chars = chars.where((c) => c.isActive).toList();
        _pickedCid = _chars.isNotEmpty ? _chars.first.id : -1;
        _phase = _WechatPhase.picking;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _phase = _WechatPhase.failed;
        _statusText = AppLocalizations.of(context)!.channelQrLoadFailed;
      });
    }
  }

  Future<void> _bind() async {
    final l10n = AppLocalizations.of(context)!;
    if (_pickedCid <= 0) return;
    String name = '';
    for (final c in _chars) {
      if (c.id == _pickedCid) {
        name = c.name;
        break;
      }
    }
    setState(() => _phase = _WechatPhase.binding);
    try {
      final resp = await ApiClient().bindWechatLogin(
        characterId: _pickedCid,
        botToken: _botToken,
        baseurl: _baseurl,
        ilinkUserId: _ilinkUserId,
        ilinkBotId: _ilinkBotId,
      );
      if (!mounted) return;
      if (resp['ok'] == true) {
        setState(() {
          _boundName = name;
          _gatewayRestartPending = resp['gateway_restart_pending'] == true;
          _phase = _WechatPhase.done;
        });
      } else {
        setState(() {
          _phase = _WechatPhase.failed;
          _statusText = resp['detail'] as String? ?? l10n.channelQrLoadFailed;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _WechatPhase.failed;
        _statusText = '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(child: Text(l10n.channelQrLoginTitle,
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold))),
              IconButton(icon: const Icon(Icons.close, size: 20), onPressed: () => Navigator.pop(context)),
            ]),
            const SizedBox(height: 4),
            Text(l10n.channelQrScanHintWechat, style: const TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 14),
            _buildBody(context, l10n),
          ],
        ),
      ),
    );
  }

  Widget _buildBody(BuildContext context, AppLocalizations l10n) {
    switch (_phase) {
      case _WechatPhase.loading:
        return const Padding(
          padding: EdgeInsets.symmetric(vertical: 48),
          child: Center(child: CircularProgressIndicator()),
        );
      case _WechatPhase.scanning:
        return Column(children: [
          if (_qrData.isNotEmpty)
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Colors.grey.shade300),
              ),
              child: QrImageView(data: _qrData, size: 220, backgroundColor: Colors.white),
            ),
          const SizedBox(height: 14),
          Text(_statusText, textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 10),
          const SizedBox(
            height: 14, width: 14,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ]);
      case _WechatPhase.picking:
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l10n.channelQrPickChar, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 260),
            child: RadioGroup<int>(
              groupValue: _pickedCid,
              onChanged: (v) => setState(() => _pickedCid = v ?? -1),
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: _chars.length,
                itemBuilder: (_, i) {
                  final c = _chars[i];
                  return RadioListTile<int>(
                    value: c.id,
                    title: Text(c.name, style: const TextStyle(fontSize: 14)),
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                  );
                },
              ),
            ),
          ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: _pickedCid > 0 ? _bind : null,
            child: Text(l10n.confirm),
          ),
        ]);
      case _WechatPhase.binding:
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 40),
          child: Column(children: [
            const CircularProgressIndicator(),
            const SizedBox(height: 12),
            Text(l10n.channelQrBinding, style: const TextStyle(fontSize: 13)),
          ]),
        );
      case _WechatPhase.done:
        return Column(children: [
          const Icon(Icons.check_circle_outline, color: Colors.green, size: 44),
          const SizedBox(height: 10),
          Text(l10n.channelQrBindSuccess(_boundName),
              textAlign: TextAlign.center, style: const TextStyle(fontSize: 14)),
          if (_gatewayRestartPending) ...[
            const SizedBox(height: 8),
            Text(l10n.channelQrGatewayPending,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: Colors.orange)),
          ],
          const SizedBox(height: 14),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(l10n.done)),
        ]);
      case _WechatPhase.failed:
        return Column(children: [
          const Icon(Icons.error_outline, color: Colors.orange, size: 44),
          const SizedBox(height: 10),
          Text(_statusText, textAlign: TextAlign.center, style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 14),
          Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            OutlinedButton(onPressed: () => Navigator.pop(context), child: Text(l10n.cancel)),
            const SizedBox(width: 10),
            FilledButton(onPressed: _startSession, child: Text(l10n.retry)),
          ]),
        ]);
    }
  }
}

/// 抖音扫码绑定弹层：bind/qr 会话（服务器有头 Edge 截屏回传二维码图）+ 电脑端兜底入口。
class DouyinBindSheet extends StatefulWidget {
  const DouyinBindSheet({super.key});

  @override
  State<DouyinBindSheet> createState() => _DouyinBindSheetState();
}

enum _DouyinPhase { starting, waiting, done, failed }

class _DouyinBindSheetState extends State<DouyinBindSheet> {
  _DouyinPhase _phase = _DouyinPhase.starting;
  String _statusText = '';
  String _sessionId = '';
  Uint8List? _qrImage; // 服务器回传的二维码截图（PNG）
  bool _busyPc = false;

  @override
  void initState() {
    super.initState();
    _startSession();
  }

  Future<void> _startSession() async {
    setState(() {
      _phase = _DouyinPhase.starting;
      _statusText = '';
    });
    try {
      final resp = await ApiClient().startDouyinQrBind();
      if (!mounted) return;
      if (resp['ok'] == true && (resp['session_id'] as String? ?? '').isNotEmpty) {
        setState(() {
          _sessionId = resp['session_id'] as String;
          _phase = _DouyinPhase.waiting;
        });
        _pollLoop();
      } else {
        setState(() {
          _phase = _DouyinPhase.failed;
          _statusText = resp['message'] as String? ?? '$resp';
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _DouyinPhase.failed;
        _statusText = '$e';
      });
    }
  }

  Future<void> _pollLoop() async {
    final l10n = AppLocalizations.of(context)!;
    while (mounted && _phase == _DouyinPhase.waiting) {
      Map<String, dynamic> resp;
      try {
        resp = await ApiClient().pollDouyinQrBind(_sessionId);
      } catch (_) {
        await Future.delayed(const Duration(seconds: 3));
        continue;
      }
      if (!mounted || _phase != _DouyinPhase.waiting) return;
      final state = resp['state'] as String? ?? 'waiting';
      final imgB64 = resp['image_png_base64'] as String?;
      if (imgB64 != null && imgB64.isNotEmpty) {
        try {
          _qrImage = base64Decode(imgB64);
        } catch (_) {}
      }
      switch (state) {
        case 'success':
          setState(() {
            _phase = _DouyinPhase.done;
            _statusText = resp['account_name'] as String? ?? l10n.douyinQrSuccess;
          });
          return;
        case 'expired':
        case 'failed':
          setState(() {
            _phase = _DouyinPhase.failed;
            _statusText = resp['message'] as String? ?? l10n.douyinQrEnded;
          });
          return;
        default: // waiting / scanned
          setState(() => _statusText =
              state == 'scanned' ? l10n.channelQrScanned : l10n.douyinQrWaiting);
          break;
      }
      await Future.delayed(const Duration(seconds: 2));
    }
  }

  /// 兜底：服务器上弹有头 Edge（电脑前直接扫码，旧 /bind 路径）。
  Future<void> _bindOnPc() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(l10n.douyinQrBindOnPc),
        content: Text(l10n.douyinQrBindOnPcHint),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l10n.cancel)),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: Text(l10n.confirm)),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busyPc = true);
    try {
      final resp = await ApiClient().bindDouyinLegacy();
      if (!mounted) return;
      if (resp['ok'] == true) {
        setState(() {
          _phase = _DouyinPhase.done;
          _statusText = l10n.douyinQrSuccess;
        });
      } else {
        setState(() {
          _phase = _DouyinPhase.failed;
          _statusText = resp['message'] as String? ?? '$resp';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _phase = _DouyinPhase.failed;
          _statusText = '$e';
        });
      }
    } finally {
      if (mounted) setState(() => _busyPc = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(child: Text(l10n.douyinQrTitle,
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold))),
              IconButton(
                icon: const Icon(Icons.close, size: 20),
                onPressed: () {
                  // 关弹层即取消会话（服务器 worker 随即关 Edge；失败由 TTL 兜底）
                  if (_sessionId.isNotEmpty) {
                    ApiClient().cancelDouyinQrBind(_sessionId);
                  }
                  Navigator.pop(context);
                },
              ),
            ]),
            const SizedBox(height: 4),
            Text(l10n.channelQrScanHintDouyin, style: const TextStyle(fontSize: 12, color: Colors.grey)),
            const SizedBox(height: 14),
            _buildBody(context, l10n),
          ],
        ),
      ),
    );
  }

  Widget _buildBody(BuildContext context, AppLocalizations l10n) {
    switch (_phase) {
      case _DouyinPhase.starting:
        return const Padding(
          padding: EdgeInsets.symmetric(vertical: 48),
          child: Center(child: CircularProgressIndicator()),
        );
      case _DouyinPhase.waiting:
      case _DouyinPhase.done:
      case _DouyinPhase.failed:
        return Column(children: [
          if (_qrImage != null)
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: Colors.grey.shade300),
              ),
              child: Image.memory(_qrImage!, width: 240, fit: BoxFit.contain),
            )
          else if (_phase == _DouyinPhase.waiting)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            ),
          const SizedBox(height: 12),
          if (_statusText.isNotEmpty)
            Text(_statusText, textAlign: TextAlign.center, style: const TextStyle(fontSize: 13)),
          if (_phase == _DouyinPhase.waiting) ...[
            const SizedBox(height: 8),
            TextButton.icon(
              onPressed: _startSession,
              icon: const Icon(Icons.refresh, size: 16),
              label: Text(l10n.channelQrRefresh, style: const TextStyle(fontSize: 12)),
            ),
          ],
          if (_phase == _DouyinPhase.waiting || _phase == _DouyinPhase.failed) ...[
            const SizedBox(height: 4),
            TextButton.icon(
              onPressed: _busyPc ? null : _bindOnPc,
              icon: const Icon(Icons.computer, size: 16),
              label: Text(l10n.douyinQrBindOnPc, style: const TextStyle(fontSize: 12)),
            ),
          ],
          if (_phase == _DouyinPhase.done || _phase == _DouyinPhase.failed) ...[
            const SizedBox(height: 10),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: Text(l10n.done)),
          ],
        ]);
    }
  }
}
