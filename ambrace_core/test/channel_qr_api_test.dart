import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.body);
  final String body;
  final List<RequestOptions> calls = [];

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
    calls.add(o);
    return ResponseBody.fromString(body, 200,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]});
  }

  @override
  void close({bool force = false}) {}
}

AmbApiClient _client(HttpClientAdapter adapter) {
  final c = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  c.dio.httpClientAdapter = adapter;
  return c;
}

void main() {
  test('微信取码→轮询→绑定：三段端点与 body 契约', () async {
    final qr = _Adapter(jsonEncode({'qrcode_img_content': 'https://login.weixin/QR/x1'}));
    final api = ChannelQrApi(_client(qr));
    final q = await api.fetchWechatQrcode();
    expect(q['qrcode_img_content'], contains('login.weixin'));

    final poll = _Adapter(jsonEncode({'status': 'confirmed', 'bot_token': 'T',
        'baseurl': 'https://x', 'ilink_user_id': 'U', 'ilink_bot_id': 'B'}));
    final st = await ChannelQrApi(_client(poll)).pollWechatStatus('QR/x1');
    expect(st['status'], 'confirmed');
    expect(poll.calls.single.path, '/api/v1/plugins/wechat_ilink/qrcode/QR%2Fx1');

    final bind = _Adapter(jsonEncode({'gateway_restart_pending': true}));
    final r = await ChannelQrApi(_client(bind)).bindWechat(
        characterId: 13, botToken: 'T', baseurl: 'https://x',
        ilinkUserId: 'U', ilinkBotId: 'B');
    expect(r['gateway_restart_pending'], true);
    expect(bind.calls.single.method, 'POST');
    expect(bind.calls.single.path, '/api/v1/plugins/wechat_ilink/bind');
    final body = bind.calls.single.data as Map;
    expect(body['character_id'], 13);
    expect(body['ilink_bot_id'], 'B');
  });

  test('抖音 start/poll/cancel：会话 id 与尽力而为取消', () async {
    final start = _Adapter(jsonEncode({'session_id': 'SYN_SESS',
        'image_png_base64': 'SYN_B64'}));
    final api = ChannelQrApi(_client(start));
    final s = await api.startDouyinQr();
    expect(s['session_id'], 'SYN_SESS');
    expect(start.calls.single.path, '/api/v1/plugins/douyin_mcp/bind/qr/start');

    final poll = _Adapter(jsonEncode({'state': 'confirmed',
        'account_name': 'SYN_DOUYIN'}));
    final st = await ChannelQrApi(_client(poll)).pollDouyinQr('SYN_SESS');
    expect(st['account_name'], 'SYN_DOUYIN');
    expect(poll.calls.single.path,
        '/api/v1/plugins/douyin_mcp/bind/qr/status');

    final cancel = _Adapter(jsonEncode({'ok': true}));
    await ChannelQrApi(_client(cancel)).cancelDouyinQr('SYN_SESS');
    expect(cancel.calls.single.path,
        '/api/v1/plugins/douyin_mcp/bind/qr/cancel');
    // 取消失败静默（尽力而为，不抛）——适配器 500 也不炸
    final bad = _BadAdapter();
    await ChannelQrApi(_client(bad)).cancelDouyinQr('SYN_SESS');
  });
}

class _BadAdapter implements HttpClientAdapter {
  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b,
      Future<void>? c) async {
    return ResponseBody.fromString('{"detail":"boom"}', 500,
        headers: {Headers.contentTypeHeader: ['application/json']});
  }

  @override
  void close({bool force = false}) {}
}
