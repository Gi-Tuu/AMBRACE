import 'package:dio/dio.dart';

/// 非 Web 平台：不提供增量适配器（返回 null ⇒ 调用方保持现状，零行为变化）。
///
/// 手机壳的 SSE 走 `dart:io` + Dio 原生 stream，本来就是真流式；此适配器只为修
/// 「Web 上 dio_web_adapter 先缓冲整个响应才交付」这一条（主审 §2）。
HttpClientAdapter? createIncrementalStreamAdapter() => null;

/// 该平台是否支持增量流（供壳/测试判断，不猜）。
bool get supportsIncrementalStream => false;
