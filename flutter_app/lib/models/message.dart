
import 'dart:convert';

class ChatMessage {
  final int id;
  final int sessionId;
  final String senderType;
  final String content;
  final String? imageUrl;
  final String createdAt;
  final bool isLocal;
  final Map<String, dynamic> extraMeta;

  ChatMessage({
    required this.id,
    required this.sessionId,
    required this.senderType,
    required this.content,
    required this.createdAt,
    this.imageUrl,
    this.isLocal = false,
    this.extraMeta = const {},
  });

  /// 流式增量更新用：保留 id/时间，替换 content/元数据（ChatMessage 全不可变）。
  ChatMessage copyWith({
    String? content,
    bool? isLocal,
    Map<String, dynamic>? extraMeta,
  }) {
    return ChatMessage(
      id: id,
      sessionId: sessionId,
      senderType: senderType,
      content: content ?? this.content,
      createdAt: createdAt,
      imageUrl: imageUrl,
      isLocal: isLocal ?? this.isLocal,
      extraMeta: extraMeta ?? this.extraMeta,
    );
  }

  factory ChatMessage.fromJson(Map<String, dynamic> json) {
    var raw = (json['created_at'] as String? ?? '').replaceAll(' ', 'T');
    // 服务器时间存 UTC（无时区标记），统一补 Z 按 UTC 解析，
    // 避免与本地生成的带 Z 时间（toIso8601String）混排时排序错乱
    if (raw.isNotEmpty && !raw.endsWith('Z') && !raw.contains('+')) {
      raw = '${raw}Z';
    }
    Map<String, dynamic> meta = <String, dynamic>{};
    try {
      final rawMeta = json['extra_meta'];
      if (rawMeta is String && rawMeta.isNotEmpty) {
        meta = (jsonDecode(rawMeta) as Map<String, dynamic>?) ?? <String, dynamic>{};
      } else if (rawMeta is Map<String, dynamic>) {
        meta = rawMeta;
      }
    } catch (_) {}
    // 图片消息兼容：image_message 返回体可能带 file_url/voice_url
    // 注意：Map 字面量必须显式 <String, dynamic>，否则与 const {}（Map<dynamic, dynamic>）
    // 合并时整体推断为 Map<dynamic, dynamic>，导致 getter 的 as Map<String, dynamic> 崩溃
    // （曾在语音发送后使 ListView itemBuilder 抛异常，release 模式渲染成大灰块）
    if (json['file_url'] != null) {
      final existing = meta['file'];
      final file = <String, dynamic>{
        'url': json['file_url'],
        'name': json['content'] ?? '文件',
        'size': '',
        'type': 'file',
        'summary': '',
      };
      if (existing is Map) file.addAll(Map<String, dynamic>.from(existing));
      meta['file'] = file;
    }
    if (json['voice_url'] != null) {
      final existing = meta['voice'];
      final voice = <String, dynamic>{
        'url': json['voice_url'],
        'duration': json['duration'] ?? 0,
        'transcript': json['transcript'] ?? '',
      };
      if (existing is Map) voice.addAll(Map<String, dynamic>.from(existing));
      meta['voice'] = voice;
    }
    // AI 语音回复：chunk 返回体带 tts_url（extra_meta 里是 tts.url，读取历史消息时解析）
    if (json['tts_url'] != null) {
      final existing = meta['tts'];
      final tts = <String, dynamic>{'url': json['tts_url']};
      if (existing is Map) tts.addAll(Map<String, dynamic>.from(existing));
      meta['tts'] = tts;
    }
    return ChatMessage(
      id: json['id'] as int,
      sessionId: json['session_id'] as int,
      senderType: json['sender_type'] as String,
      content: json['content'] as String,
      imageUrl: json['image_url'] as String?,
      createdAt: raw,
      extraMeta: meta,
    );
  }

  Map<String, dynamic>? get quoteMeta => _asStringMap(extraMeta['quote']);
  Map<String, dynamic>? get fileMeta => _asStringMap(extraMeta['file']);
  Map<String, dynamic>? get voiceMeta => _asStringMap(extraMeta['voice']);
  Map<String, dynamic>? get ttsMeta => _asStringMap(extraMeta['tts']);
  /// 思考过程（AI 气泡顶部折叠展示；仅角色开启「思考过程」开关时产生）
  String? get reasoning => extraMeta['reasoning'] is String ? extraMeta['reasoning'] as String : null;

  /// 状态更新（2026-08-14：AI 输出【状态更新：】剥离后附在最后一个气泡，小字显示）
  String? get statusUpdate =>
      extraMeta['status_update'] is String ? extraMeta['status_update'] as String : null;
  /// 调用能力列表（识图/生图/语音回复/扩展等）
  List<String>? get tools {
    final v = extraMeta['tools'];
    if (v is List) return v.map((e) => e.toString()).toList();
    return null;
  }

  /// MCP 工具结果列表（A1，#59 流式路径 MCP 工具循环；extra_meta.tool_results）。
  /// 每项形如 {tool, ok, summary, error}，前端在气泡观察区可折叠展示。
  List<Map<String, dynamic>>? get toolResults {
    final v = extraMeta['tool_results'];
    if (v is List) {
      return v.map((e) => _asStringMap(e) ?? const <String, dynamic>{}).toList();
    }
    return null;
  }

  /// 防御性转换：容忍 Map(键类型非 String) 等变体，避免类型转换崩溃
  static Map<String, dynamic>? _asStringMap(dynamic v) {
    if (v is Map<String, dynamic>) return v;
    if (v is Map) return Map<String, dynamic>.from(v);
    return null;
  }

  bool get isUser => senderType == 'user';
  bool get isAI => senderType == 'ai';
}
