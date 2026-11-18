/// RC2 M3/M4：记忆「生命周期」只读 DTO。
///
/// 数据来自 `GET /api/v1/memories/{id}/lifecycle`（后端只读端点，flag
/// `memory_lifecycle_view_v1` 关闭时 404＝功能未开放，由调用方隐藏入口）。
/// 约定（与 [MemoryWhy] 同族）：
/// - 未知字段向前兼容（忽略）；必需字段缺失/形状错误 → 抛 [FormatException]，
///   由调用方呈现「加载失败」，不得降级成其他状态。
/// - 本模型不判 404：404 属 HTTP 层语义（功能关/记忆不存在），由调用方按
///   DioException.statusCode 处理。
class MemoryLifecycleReceipt {
  final String action;
  final String? reason;
  final String? createdAt;

  const MemoryLifecycleReceipt({
    required this.action,
    this.reason,
    this.createdAt,
  });

  factory MemoryLifecycleReceipt.fromJson(Map<String, dynamic> json) {
    final action = json['action'];
    if (action is! String || action.isEmpty) {
      throw const FormatException('memory lifecycle receipt: action missing or invalid');
    }
    return MemoryLifecycleReceipt(
      action: action,
      reason: json['reason'] as String?,
      createdAt: json['created_at'] as String?,
    );
  }

  static List<MemoryLifecycleReceipt> listFromJson(Object? raw) {
    if (raw is! List) {
      throw const FormatException('memory lifecycle: receipts is not a list');
    }
    return raw
        .map((e) => MemoryLifecycleReceipt.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList(growable: false);
  }
}

/// 一条记忆的生命周期快照（M3 读投影）。
class MemoryLifecycle {
  final String status;
  final int memoryId;

  /// 记忆自身的生命周期状态（active/superseded/stale/expired）。
  ///
  /// 后端 2026-11-13 首版响应没有这个字段（顶层 [status] 是信封 "ok"）；
  /// 旧服务器缺省 ⇒ null，UI 从 supersededBy/validTo/isArchived 派生兜底。
  final String? lifecycleStatus;
  final int version;
  final int? supersededBy;
  final String? validTo;
  final String? epistemicStatus;
  final String? source;
  final bool isArchived;
  final String? sourceEventUid;
  final int sourceEventCount;
  final List<MemoryLifecycleReceipt> receipts;
  final int receiptCount;
  final int revisionCount;
  final int derivedCount;

  const MemoryLifecycle({
    required this.status,
    required this.memoryId,
    required this.version,
    required this.isArchived,
    required this.sourceEventCount,
    required this.receipts,
    required this.receiptCount,
    required this.revisionCount,
    required this.derivedCount,
    this.lifecycleStatus,
    this.supersededBy,
    this.validTo,
    this.epistemicStatus,
    this.source,
    this.sourceEventUid,
  });

  factory MemoryLifecycle.fromJson(Map<String, dynamic> json) {
    final status = json['status'];
    final memoryId = json['memory_id'];
    final version = json['version'];
    final isArchived = json['is_archived'];
    final sourceEventCount = json['source_event_count'];
    final receiptCount = json['receipt_count'];
    final revisionCount = json['revision_count'];
    final derivedCount = json['derived_count'];
    if (status is! String) {
      throw const FormatException('memory lifecycle: status missing or invalid');
    }
    if (memoryId is! int) {
      throw const FormatException('memory lifecycle: memory_id missing or invalid');
    }
    if (version is! int) {
      throw const FormatException('memory lifecycle: version missing or invalid');
    }
    if (isArchived is! bool) {
      throw const FormatException('memory lifecycle: is_archived missing or invalid');
    }
    if (sourceEventCount is! int) {
      throw const FormatException('memory lifecycle: source_event_count missing or invalid');
    }
    if (receiptCount is! int) {
      throw const FormatException('memory lifecycle: receipt_count missing or invalid');
    }
    if (revisionCount is! int) {
      throw const FormatException('memory lifecycle: revision_count missing or invalid');
    }
    if (derivedCount is! int) {
      throw const FormatException('memory lifecycle: derived_count missing or invalid');
    }
    return MemoryLifecycle(
      status: status,
      memoryId: memoryId,
      lifecycleStatus: json['lifecycle_status'] as String?,
      version: version,
      supersededBy: json['superseded_by'] as int?,
      validTo: json['valid_to'] as String?,
      epistemicStatus: json['epistemic_status'] as String?,
      source: json['source'] as String?,
      isArchived: isArchived,
      sourceEventUid: json['source_event_uid'] as String?,
      sourceEventCount: sourceEventCount,
      receipts: MemoryLifecycleReceipt.listFromJson(json['receipts']),
      receiptCount: receiptCount,
      revisionCount: revisionCount,
      derivedCount: derivedCount,
    );
  }
}
