import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/utils/memory_decay.dart';

/// 艾宾浩斯记忆衰减曲线纯逻辑测试（2026-08-24）：
/// R = exp(-Δt / S) * 100，S = strengthDays；锁定冻结为水平线；next_review 标记换算。
void main() {
  group('memoryRetentionPct', () {
    test('Δt=0 时保留率恒为 100%', () {
      expect(memoryRetentionPct(0, 7), 100);
      expect(memoryRetentionPct(0, 14), 100);
      expect(memoryRetentionPct(0, 3), 100);
    });

    test('Δt=S 时保留率 ≈ exp(-1)*100 ≈ 36.79', () {
      final v = memoryRetentionPct(7, 7);
      expect(v, closeTo(36.79, 0.01));
    });

    test('strengthDays 越大衰减越慢（同 Δt 保留率更高）', () {
      expect(memoryRetentionPct(10, 14), greaterThan(memoryRetentionPct(10, 3)));
    });

    test('保留率保持在 0-100 内（长 Δt 钳到 0）', () {
      expect(memoryRetentionPct(1e9, 7), 0);
      expect(memoryRetentionPct(-5, 7), 100); // 负 Δt 视为起点
    });

    test('strengthDays 非法（<=0）回退默认 7 天', () {
      expect(memoryRetentionPct(7, 0), closeTo(36.79, 0.01));
      expect(memoryRetentionPct(7, -2), closeTo(36.79, 0.01));
    });
  });

  group('memoryElapsedDays', () {
    test('无任何基准时返回 0', () {
      expect(memoryElapsedDays(DateTime.now(), null, null), 0);
    });

    test('按 lastReinforceAt 优先、createdAt 兜底', () {
      final now = DateTime.utc(2026, 8, 24, 12, 0, 0);
      final reinforce = DateTime.utc(2026, 8, 22, 12, 0, 0);
      final created = DateTime.utc(2026, 8, 1);
      expect(memoryElapsedDays(now, reinforce, created), closeTo(2, 1e-6));
      expect(memoryElapsedDays(now, null, created), closeTo(23.5, 1e-6));
    });

    test('未来的基准返回 0', () {
      final now = DateTime.utc(2026, 8, 24);
      expect(memoryElapsedDays(now, DateTime.utc(2026, 8, 25), null), 0);
    });
  });

  group('memoryDecayCurve', () {
    test('起点保留率 = 当前保留率', () {
      final pts = memoryDecayCurve(strengthDays: 7, elapsedDays: 2, horizonDays: 30, isLocked: false);
      expect(pts.first.pct, closeTo(memoryRetentionPct(2, 7), 1e-6));
      expect(pts.first.day, 0);
      expect(pts.last.day, 30);
    });

    test('锁定后曲线冻结为当前保留率水平线', () {
      final pts = memoryDecayCurve(strengthDays: 7, elapsedDays: 5, horizonDays: 30, isLocked: true);
      final current = memoryRetentionPct(5, 7);
      for (final p in pts) {
        expect(p.pct, closeTo(current, 1e-6));
      }
    });

    test('samples 至少 2，horizonDays<=0 回退为 1', () {
      final pts = memoryDecayCurve(strengthDays: 7, elapsedDays: 0, horizonDays: 0, isLocked: false, samples: 1);
      expect(pts.length, 3);
      expect(pts.last.day, 1);
    });
  });

  group('nextReviewOffsetDays', () {
    test('null 下次复习返回 null', () {
      expect(nextReviewOffsetDays(DateTime.utc(2026, 8, 24), null, 30), isNull);
    });

    test('负偏移（已过期）返回 null', () {
      expect(nextReviewOffsetDays(DateTime.utc(2026, 8, 24), DateTime.utc(2026, 8, 20), 30), isNull);
    });

    test('超出 horizon 返回 null', () {
      expect(nextReviewOffsetDays(DateTime.utc(2026, 8, 24), DateTime.utc(2026, 10, 24), 30), isNull);
    });

    test('范围内返回天数偏移', () {
      expect(nextReviewOffsetDays(DateTime.utc(2026, 8, 24), DateTime.utc(2026, 8, 29), 30), closeTo(5, 1e-6));
    });
  });
}
