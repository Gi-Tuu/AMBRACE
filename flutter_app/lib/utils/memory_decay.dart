import 'dart:math' as math;

/// 艾宾浩斯记忆衰减曲线（2026-08-24）：保留率 R = exp(-Δt / S)，S = strengthDays（天）。
///
/// 后端惰性结算取 importance = R * 120（见 backend/app/memory/decay.py 的 retention_pct）；
/// 此处仅供 0-100% 可视化，故把 R 归一为 0-100%（Δt=0 时 100%，随后按 S 指数衰减），
/// 与后端在同一 R=exp(-Δt/S) 口径上只表达「相对保留率」趋势，不引重型图表库。
double memoryRetentionPct(double dtDays, double strengthDays) {
  final s = strengthDays > 0 ? strengthDays : 7.0;
  return (math.exp(-dtDays / s) * 100).clamp(0.0, 100.0);
}

/// 自遗忘起点（last_reinforce_at，缺失回退 created_at）至今流逝的天数（毫秒精度转天）。
/// [lastReinforceAt]/[createdAt] 为已解析的本地时间；均缺失返回 0。
double memoryElapsedDays(DateTime now, DateTime? lastReinforceAt, DateTime? createdAt) {
  final base = lastReinforceAt ?? createdAt;
  if (base == null) return 0;
  final span = now.difference(base);
  if (span.isNegative) return 0;
  return span.inMilliseconds / Duration.millisecondsPerDay;
}

/// 衰减曲线上的一个采样点。
class MemoryDecayPoint {
  /// 距「今天」的偏移天数（>=0）。
  final double day;

  /// 保留率 0-100。
  final double pct;

  const MemoryDecayPoint(this.day, this.pct);
}

/// 生成记忆衰减曲线采样：横轴 [0, horizonDays] 天，纵轴保留率 0-100%。
///
/// [elapsedDays] 为已流逝天数，曲线起点即当前保留率；[isLocked] 为真时曲线冻结在
/// 当前保留率（水平线，表达锁住后不再衰减、不强化）。
List<MemoryDecayPoint> memoryDecayCurve({
  required double strengthDays,
  required double elapsedDays,
  required int horizonDays,
  required bool isLocked,
  int samples = 40,
}) {
  if (horizonDays <= 0) horizonDays = 1;
  if (samples < 2) samples = 2;
  final lockedPct = memoryRetentionPct(elapsedDays, strengthDays);
  final points = <MemoryDecayPoint>[];
  for (var i = 0; i <= samples; i++) {
    final day = horizonDays * i / samples;
    final pct = isLocked ? lockedPct : memoryRetentionPct(elapsedDays + day, strengthDays);
    points.add(MemoryDecayPoint(day, pct));
  }
  return points;
}

/// 把 [nextReviewAt]（可 null）换算为距今天的偏移天数；超出 [0, horizonDays] 返回 null（不画标记）。
double? nextReviewOffsetDays(DateTime now, DateTime? nextReviewAt, int horizonDays) {
  if (nextReviewAt == null) return null;
  final days = nextReviewAt.difference(now).inMilliseconds / Duration.millisecondsPerDay;
  if (days < 0 || days > horizonDays) return null;
  return days;
}
