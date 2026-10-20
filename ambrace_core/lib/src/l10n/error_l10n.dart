import 'package:dio/dio.dart';

import '../api/api_exception.dart';
import 'core_l10n.dart';

/// 把任意异常对象映射成**按当前语言**的错误文案。
///
/// 英文界面绝不能回落到 `ApiException.message`（那是中文硬编码）；只有 kind 未知的
/// 非 API 异常才退化为截断的原样字符串。
String localizedError(Object? error, CoreStrings s) {
  if (error is DioException) {
    final inner = error.error;
    if (inner is ApiException) return s.errorForKind(inner.kind);
    return s.errorForKind(ApiException.fromDio(error).kind);
  }
  if (error is ApiException) return s.errorForKind(error.kind);
  if (error == null) return s.t('errorUnknown');
  final raw = '$error';
  return raw.length > 160 ? '${raw.substring(0, 160)}…' : raw;
}
