/// 渠道扫码登录（2026-11-22 批 9）：微信 ilink 扫码登录 ＋ 抖音 bind/qr 会话。
///
/// 契约以 `backend/app/api/plugins.py` 与 App 封装
/// `flutter_app/lib/services/api/plugins_api.dart` 为准：
///
/// 微信（腾讯官方 login-qr 状态机）：
/// - `GET  /api/v1/plugins/wechat_ilink/qrcode` → `{qrcode_img_content: <URL 字符串>}`
///   （**待渲染成二维码的 URL**，非图片；客户端自行绘制）；
/// - `GET  /api/v1/plugins/wechat_ilink/qrcode/{qrcode}?verify_code=` → 长轮询
///   （~35s 服务端长轮询，客户端 receiveTimeout 必须 ≥50s）；
///   status 全集：wait/scaned/confirmed/expired/scaned_but_redirect/
///   need_verifycode/verify_code_blocked/binded_redirect；confirmed 含
///   bot_token/baseurl/ilink_user_id/ilink_bot_id（原样回传 [bindWechat]）；
/// - `POST /api/v1/plugins/wechat_ilink/bind` → 绑定（响应含
///   gateway_registered/gateway_restart_pending——网关需重启一次才拉新 bot 消息）。
///
/// 抖音（服务器有头 Edge 截屏回传）：
/// - `POST /api/v1/plugins/douyin_mcp/bind/qr/start` → `{session_id,image_png_base64?}`；
/// - `GET  /api/v1/plugins/douyin_mcp/bind/qr/status?session_id=` →
///   `{state,image_png_base64?,account_name?,message?}`；
/// - `POST /api/v1/plugins/douyin_mcp/bind/qr/cancel` `{session_id}`（尽力而为，
///   失败靠会话 TTL 兜底）。
///
/// 纪律：仅主账号入口（后端强校验）；所有方法经同一个 [AmbApiClient]。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

class ChannelQrApi {
  ChannelQrApi(this.client);

  final AmbApiClient client;

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    throw ApiException(
        kind: 'unknown', message: 'malformed $what', cause: data.runtimeType);
  }

  Future<Map<String, dynamic>> fetchWechatQrcode(
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/plugins/wechat_ilink/qrcode',
        options: Options(receiveTimeout: const Duration(seconds: 20)),
        cancelToken: cancelToken);
    return _obj(r.data, 'wechat qrcode');
  }

  Future<Map<String, dynamic>> pollWechatStatus(String qrcode,
      {String verifyCode = '', CancelToken? cancelToken}) async {
    final r = await client.dio.get(
      '/api/v1/plugins/wechat_ilink/qrcode/${Uri.encodeComponent(qrcode)}',
      queryParameters: {if (verifyCode.isNotEmpty) 'verify_code': verifyCode},
      options: Options(receiveTimeout: const Duration(seconds: 50)),
      cancelToken: cancelToken,
    );
    return _obj(r.data, 'wechat status');
  }

  Future<Map<String, dynamic>> bindWechat({
    required int characterId,
    required String botToken,
    required String baseurl,
    required String ilinkUserId,
    required String ilinkBotId,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.post('/api/v1/plugins/wechat_ilink/bind',
        data: {
          'character_id': characterId,
          'bot_token': botToken,
          'baseurl': baseurl,
          'ilink_user_id': ilinkUserId,
          'ilink_bot_id': ilinkBotId,
        },
        cancelToken: cancelToken);
    return _obj(r.data, 'wechat bind');
  }

  Future<Map<String, dynamic>> startDouyinQr({CancelToken? cancelToken}) async {
    final r = await client.dio.post(
        '/api/v1/plugins/douyin_mcp/bind/qr/start',
        options: Options(receiveTimeout: const Duration(seconds: 60)),
        cancelToken: cancelToken);
    return _obj(r.data, 'douyin qr start');
  }

  Future<Map<String, dynamic>> pollDouyinQr(String sessionId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get(
        '/api/v1/plugins/douyin_mcp/bind/qr/status',
        queryParameters: {'session_id': sessionId},
        options: Options(receiveTimeout: const Duration(seconds: 20)),
        cancelToken: cancelToken);
    return _obj(r.data, 'douyin qr status');
  }

  /// 尽力而为取消：失败静默（会话 TTL 自动过期兜底）。
  Future<void> cancelDouyinQr(String sessionId, {CancelToken? cancelToken}) async {
    try {
      await client.dio.post('/api/v1/plugins/douyin_mcp/bind/qr/cancel',
          data: {'session_id': sessionId}, cancelToken: cancelToken);
    } catch (_) {}
  }
}
