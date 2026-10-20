/// 真实 wire 上行进度契约（跨端共享）。
///
/// 为什么不用 dio 的 `options.onSendProgress`：那套计数在「**收集请求体**」阶段就可能
/// 被推到 100%，与「字节是否已真正上行到服务器」无关；本包的自定义 XHR 适配器因此
/// **不**消费 `onSendProgress`，真实上行字节只经
/// [kAmbWireUploadProgressExtraKey] 命名的回调上报，数据源为浏览器
/// `xhr.upload.onprogress`（multipart 实际已发送 / 总字节）。
///
/// 语义约定：
/// * [AmbUploadProgress.total] `<= 0` 表示长度未知（不定进度，UI 展示不确定态），
///   不得用文件大小伪算 multipart 百分比；
/// * [AmbUploadProgress.done] `true` 只表示「上传阶段结束」，**不等于**服务器已处理完成，
///   更不代表收到完整可解析响应；
/// * 回调可能一次都不触发（极短上传/bodyless 请求），调用方不得依赖事件次数。
library;

/// 上行进度事件（不可变）。
class AmbUploadProgress {
  const AmbUploadProgress({
    required this.loaded,
    required this.total,
    required this.done,
  });

  /// 已发送字节（来自 `xhr.upload.onprogress` 的 `loaded`）。
  final int loaded;

  /// 总字节；`<= 0` 表示未知（`lengthComputable == false`）。
  final int total;

  /// 上传阶段是否已结束（`xhr.upload` 完成事件）；`true` 时 [loaded]/[total] 为尽力记录值。
  final bool done;

  @override
  String toString() =>
      'AmbUploadProgress(loaded: $loaded, total: $total, done: $done)';
}

/// wire 上行进度回调签名。
typedef AmbWireUploadProgress = void Function(AmbUploadProgress progress);

/// `options.extra` 中约定本键携带 [AmbWireUploadProgress] 回调。
///
/// 绝不写 `onSendProgress`：那是准备期计数，与真实上行无关。
const String kAmbWireUploadProgressExtraKey = '_ambrace_wire_upload_progress';
