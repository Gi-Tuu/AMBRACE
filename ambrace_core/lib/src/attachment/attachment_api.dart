import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import '../models/chat_message.dart';
import '../platform/text_file_picker.dart';
import 'upload_progress.dart';

/// 文本附件上传结果（已按契约解析并校验）。
class AttachmentUploadResult {
  const AttachmentUploadResult({
    required this.fileMessage,
    required this.chunks,
  });

  /// 服务器已落库的**用户文件消息**（`content`＝原文文件名，`file_url` 指向 `/uploads/...`）；
  /// 与历史 `GET .../messages` 返回的同 `id` 消息是同一实体，UI 按稳定 id 去重。
  final ChatMessage fileMessage;

  /// AI 回复 chunk（**可能为空**）。空即空——不得虚构逐段到达的 SSE 效果，也不得
  /// 把坏封套当成功。
  final List<ChatMessage> chunks;
}

/// 严格解析 `{file_message, chunks}` 响应。
///
/// 校验：`file_message` 必填字段（id/session_id/sender_type/content/created_at）；
/// 其 `session_id` **必须**等于发起 sid（不等 ⇒ 拒绝落 UI）；chunks 每项为对象且同 sid。
/// 结构/类型不符一律 [FormatException]（调用方归一为 ApiException，**不**当成功）。
///
/// **上传专用文件身份校验（返工 R3）**：这是**新上传成功封套**的解析入口，故额外要求
/// `file_message` 是 **user 文件消息** 且带**可用文件身份**——`content` 为原名、且文件元数据里
/// 有非空 `url`（后端即时回包走 `file_url`；亦兼容既有合法 `extra_meta.file.url` 路径）。
/// 缺文件信息的「成功」不得当成功：否则 PWA 会把它当普通文字消息、并**消耗掉待发卡**，
/// 用户再也无法恢复文件卡。**历史遗留 DTO 不走本入口**，其降级展示不受此约束
/// （不凭空要求每条历史行都具备完整新元数据；不做重传，只允许 GET 核对）。
AttachmentUploadResult attachmentUploadResultFromJson(
  Map<String, dynamic> json, {
  required int requestedSessionId,
}) {
  try {
    final rawFile = json['file_message'];
    if (rawFile is! Map) {
      throw const FormatException('file_message missing or not an object');
    }
    final fileMessage = ChatMessage.fromJson(rawFile.cast<String, dynamic>());
    if (fileMessage.sessionId != requestedSessionId) {
      throw const FormatException('file_message.session_id mismatch');
    }
    if (fileMessage.senderType != 'user') {
      throw const FormatException('file_message.sender_type must be user');
    }
    if (fileMessage.content.trim().isEmpty) {
      throw const FormatException('file_message has no file name');
    }
    final fileUrl = fileMessage.fileMeta?['url'];
    if (fileUrl is! String || fileUrl.isEmpty) {
      throw const FormatException('file_message has no usable file url');
    }
    final rawChunks = json['chunks'];
    if (rawChunks is! List) {
      throw const FormatException('chunks missing or not a list');
    }
    final chunks = <ChatMessage>[];
    for (final c in rawChunks) {
      if (c is! Map) {
        throw const FormatException('chunk is not an object');
      }
      final msg = ChatMessage.fromJson(c.cast<String, dynamic>());
      if (msg.sessionId != requestedSessionId) {
        throw const FormatException('chunk.session_id mismatch');
      }
      chunks.add(msg);
    }
    return AttachmentUploadResult(fileMessage: fileMessage, chunks: chunks);
  } on FormatException {
    rethrow;
  } catch (e) {
    throw FormatException('invalid attachment upload response: $e');
  }
}

/// 共享附件 API（走既有 [AmbApiClient] 认证/验签/错误归一）。
///
/// 契约权威＝`backend/app/api/chat.py::upload_chat_file`：
/// `POST /api/v1/chat/sessions/{session_id}/file`，multipart 字段名 **`file`**，
/// 语言头 **`lang`**，返回普通 JSON `{file_message, chunks}`（**不是** SSE 上传通道）。
class AmbAttachmentApi {
  AmbAttachmentApi(this.client);

  final AmbApiClient client;

  /// 上传单个文本文件。
  ///
  /// * 只接受本地已通过校验的 [PickedTextFile]（`accepted` 为真）；否则直接拒绝，不发请求；
  /// * [onUploadProgress] 经命名键传入适配器 ⇒ 真实 wire 上行进度（非准备期计数）；
  /// * 超时由调用方在 [Options] 之外用适配器语义处理：本方法不覆盖全局 connect/receive/send；
  /// * 支持 [CancelToken]：取消正在等待的请求（**不**保证撤销服务端已开始的写入）。
  Future<AttachmentUploadResult> uploadTextFile({
    required int sessionId,
    required PickedTextFile file,
    String lang = 'zh',
    CancelToken? cancelToken,
    AmbWireUploadProgress? onUploadProgress,
  }) async {
    final bytes = file.bytes;
    if (!file.accepted || bytes == null) {
      throw const ApiException(
        kind: 'unknown',
        message: 'attachment not accepted locally',
      );
    }
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: file.name),
    });
    final r = await client.dio.post(
      '/api/v1/chat/sessions/$sessionId/file',
      data: form,
      options: Options(
        headers: {'lang': lang},
        extra: <String, dynamic>{
          if (onUploadProgress != null)
            kAmbWireUploadProgressExtraKey: onUploadProgress,
        },
      ),
      cancelToken: cancelToken,
    );
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(
        kind: 'unknown',
        message: 'malformed attachment upload response: not an object',
      );
    }
    try {
      return attachmentUploadResultFromJson(
        data.cast<String, dynamic>(),
        requestedSessionId: sessionId,
      );
    } on FormatException catch (e) {
      throw ApiException(
        kind: 'unknown',
        message: 'malformed attachment upload response',
        cause: e,
      );
    }
  }
}
