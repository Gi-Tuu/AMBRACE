import 'package:dio/dio.dart';

/// 统一 API 异常（F7-c，2026-08-31；方案 §8-3）。
///
/// dio 错误拦截器把每个 [DioException] 归一为挂在 `DioException.error` 上的本类型
/// （分类 kind + 人类可读 message；原始 error 保存在 [cause]）。异常抛出类型不变
/// （仍是 DioException）——存量 `catch (_) {}` 逐字节兼容，纯增量；UI 层需要展示
/// 文案时用 [ApiException.messageOf] 一行取用，无需逐处解析 Dio 错误类型。
class ApiException implements Exception {
  /// unauthorized（401）/ network（断网/DNS）/ timeout / server（5xx）/
  /// client（4xx 其余）/ cancelled / unknown
  final String kind;

  /// HTTP 状态码（网络层失败为 null）
  final int? statusCode;

  /// 展示用文案（中文；调用方也可按 kind 自行本地化）
  final String message;

  /// 原始错误（拦截器归一前的 e.error / e.message），调试用
  final dynamic cause;

  const ApiException({
    required this.kind,
    this.statusCode,
    required this.message,
    this.cause,
  });

  /// 从 DioException 归一分类（拦截器调用）。
  factory ApiException.fromDio(DioException e) {
    final code = e.response?.statusCode;
    switch (e.type) {
      case DioExceptionType.connectionError:
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        final kind =
            (e.type == DioExceptionType.connectionError) ? 'network' : 'timeout';
        return ApiException(
          kind: kind,
          statusCode: code,
          message: kind == 'network' ? '网络连接失败，请检查服务器地址与网络' : '请求超时，请稍后重试',
          cause: e.error ?? e.message,
        );
      case DioExceptionType.badCertificate:
        return ApiException(
          kind: 'network', statusCode: code, message: '证书校验失败，请检查服务器配置', cause: e.error);
      case DioExceptionType.cancel:
        return ApiException(kind: 'cancelled', statusCode: code, message: '请求已取消', cause: e.error);
      case DioExceptionType.badResponse:
        final detail = _detailFromResponse(e.response);
        if (code == 401) {
          return ApiException(
              kind: 'unauthorized', statusCode: code,
              message: detail ?? '登录已失效，请重新登录', cause: e.error);
        }
        if (code != null && code >= 500) {
          return ApiException(
              kind: 'server', statusCode: code,
              message: detail ?? '服务器开小差了（$code），请稍后重试', cause: e.error);
        }
        return ApiException(
            kind: 'client', statusCode: code,
            message: detail ?? '请求被拒绝（$code）', cause: e.error);
      default:
        return ApiException(kind: 'unknown', statusCode: code, message: '请求失败，请稍后重试', cause: e.error ?? e.message);
    }
  }

  /// 从响应体提取后端 detail 字段（FastAPI 惯例 {"detail": ...}；截断防长文案）。
  static String? _detailFromResponse(Response? resp) {
    try {
      final data = resp?.data;
      if (data is Map) {
        final d = data['detail'];
        if (d is String && d.trim().isNotEmpty) {
          final s = d.trim();
          return s.length > 120 ? '${s.substring(0, 120)}…' : s;
        }
      }
    } catch (_) {}
    return null;
  }

  /// 任意异常对象 → 展示文案：DioException 取归一后的 ApiException.message；
  /// 已是 ApiException 直接用；其余原样字符串（截断）。
  static String messageOf(Object? error) {
    if (error is DioException) {
      final inner = error.error;
      if (inner is ApiException) return inner.message;
      return ApiException.fromDio(error).message;
    }
    if (error is ApiException) return error.message;
    if (error == null) return '未知错误';
    final s = '$error';
    return s.length > 200 ? '${s.substring(0, 200)}…' : s;
  }

  @override
  String toString() => 'ApiException($kind${statusCode == null ? '' : '/$statusCode'}): $message';
}
