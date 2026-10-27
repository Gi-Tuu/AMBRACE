/// RC2 G1：记忆「修正历史」的只读 DTO。
///
/// 数据来自 `GET /api/v1/memories/{id}/history`（后端既有接口，不改）。
/// 约定：
/// - 服务端已按时间升序、缺时间置后并以 `old_id` 稳定排序；客户端**不重排**。
/// - 未知字段向前兼容（忽略）；必需容器缺失/形状错误 → 抛 [FormatException]，
///   由调用方呈现「加载失败」，不得降级成「暂无记录」。
/// - `reason`/`changed_at`/`old_source`/`new_source`/`actor` 允许为 null；null 不伪造。
class MemoryCorrection {
  final int? seq;
  final int? oldId;
  final int? newId;
  final String oldValue;
  final String newValue;
  final String? changedAt;
  final String? changedAtFrom;
  final String? oldSource;
  final String? newSource;
  final String? reason;
  final String? actor;

  const MemoryCorrection({
    this.seq,
    this.oldId,
    this.newId,
    required this.oldValue,
    required this.newValue,
    this.changedAt,
    this.changedAtFrom,
    this.oldSource,
    this.newSource,
    this.reason,
    this.actor,
  });

  factory MemoryCorrection.fromJson(Map<String, dynamic> json) {
    final oldValue = json['old_value'];
    final newValue = json['new_value'];
    if (oldValue is! String || newValue is! String) {
      throw const FormatException('memory correction: old_value/new_value missing or invalid');
    }
    return MemoryCorrection(
      seq: json['seq'] as int?,
      oldId: json['old_id'] as int?,
      newId: json['new_id'] as int?,
      oldValue: oldValue,
      newValue: newValue,
      changedAt: json['changed_at'] as String?,
      changedAtFrom: json['changed_at_from'] as String?,
      oldSource: json['old_source'] as String?,
      newSource: json['new_source'] as String?,
      reason: json['reason'] as String?,
      actor: json['actor'] as String?,
    );
  }

  static List<MemoryCorrection> listFromJson(Object? raw) {
    if (raw is! List) {
      throw const FormatException('memory history: corrections is not a list');
    }
    return raw
        .map((e) => MemoryCorrection.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList(growable: false);
  }
}

/// 一条记忆的修正历史快照。
class MemoryHistory {
  final int memoryId;
  final List<MemoryCorrection> corrections;

  /// 服务端是否截断了历史（只回最近 N 版）。
  final bool truncated;

  const MemoryHistory({
    required this.memoryId,
    required this.corrections,
    required this.truncated,
  });

  factory MemoryHistory.fromJson(Map<String, dynamic> json) {
    final memoryId = json['memory_id'];
    if (memoryId is! int) {
      throw const FormatException('memory history: memory_id missing or invalid');
    }
    final truncated = json['truncated'];
    if (truncated is! bool) {
      // R2：truncated 是必需布尔；缺失/null/字符串等一律拒绝，不能归 false 冒充完整历史。
      throw const FormatException('memory history: truncated missing or invalid');
    }
    return MemoryHistory(
      memoryId: memoryId,
      corrections: MemoryCorrection.listFromJson(json['corrections']),
      truncated: truncated,
    );
  }
}
