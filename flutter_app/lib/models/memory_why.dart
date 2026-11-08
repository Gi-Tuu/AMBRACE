/// RC2 G5：记忆「为什么记住」来源链的只读 DTO。
///
/// 数据来自 `GET /api/v1/memories/{id}/why`（后端只读端点）。
/// 约定：
/// - 未知字段向前兼容（忽略）；必需字段缺失/形状错误 → 抛 [FormatException]，
///   由调用方呈现「加载失败」，不得降级成「没有来源」。
/// - `has_source=false` 是后端明确回答（老记忆/闸前写入），不是错误；
///   此时 [note] 给出原因，客户端不伪造来源。
class MemoryWhySource {
  final String kind;
  final int? sourceRowId;
  final int? sessionId;
  final String? eventAt;
  final int? messageId;
  final String? messageExcerpt;
  final String? messageCreatedAt;

  const MemoryWhySource({
    required this.kind,
    this.sourceRowId,
    this.sessionId,
    this.eventAt,
    this.messageId,
    this.messageExcerpt,
    this.messageCreatedAt,
  });

  factory MemoryWhySource.fromJson(Map<String, dynamic> json) {
    final kind = json['kind'];
    if (kind is! String || kind.isEmpty) {
      throw const FormatException('memory why source: kind missing or invalid');
    }
    return MemoryWhySource(
      kind: kind,
      sourceRowId: json['source_row_id'] as int?,
      sessionId: json['session_id'] as int?,
      eventAt: json['event_at'] as String?,
      messageId: json['message_id'] as int?,
      messageExcerpt: json['message_excerpt'] as String?,
      messageCreatedAt: json['message_created_at'] as String?,
    );
  }

  static List<MemoryWhySource> listFromJson(Object? raw) {
    if (raw is! List) {
      throw const FormatException('memory why: sources is not a list');
    }
    return raw
        .map((e) => MemoryWhySource.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList(growable: false);
  }
}

/// 一条记忆的来源链快照。
class MemoryWhy {
  final String status;
  final int memoryId;
  final String? sourceEventUid;
  final bool hasSource;
  final List<MemoryWhySource> sources;
  final int receiptCount;
  final String note;

  const MemoryWhy({
    required this.status,
    required this.memoryId,
    required this.hasSource,
    required this.sources,
    required this.receiptCount,
    required this.note,
    this.sourceEventUid,
  });

  factory MemoryWhy.fromJson(Map<String, dynamic> json) {
    final status = json['status'];
    final memoryId = json['memory_id'];
    final hasSource = json['has_source'];
    final receiptCount = json['receipt_count'];
    final note = json['note'];
    if (status is! String) {
      throw const FormatException('memory why: status missing or invalid');
    }
    if (memoryId is! int) {
      throw const FormatException('memory why: memory_id missing or invalid');
    }
    if (hasSource is! bool) {
      throw const FormatException('memory why: has_source missing or invalid');
    }
    if (receiptCount is! int) {
      throw const FormatException('memory why: receipt_count missing or invalid');
    }
    if (note is! String) {
      throw const FormatException('memory why: note missing or invalid');
    }
    return MemoryWhy(
      status: status,
      memoryId: memoryId,
      sourceEventUid: json['source_event_uid'] as String?,
      hasSource: hasSource,
      sources: MemoryWhySource.listFromJson(json['sources']),
      receiptCount: receiptCount,
      note: note,
    );
  }
}
