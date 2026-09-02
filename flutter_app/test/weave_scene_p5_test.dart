// 织网 3D · 手动渲染档位模式（2026-08-24，手动档位切换）可测单测
//
// 覆盖：
// - WeaveViewMode 语义与优先级：auto 才是唯一启用自动降级监测的模式；light3d 初始 light（不预热）；
// - shouldUse3DView 手动优先于 weave_3d flag（full3d/light3d 绕过 flag 与 force2D；twoD 直选 2.5D）；
// - WeaveRenderDegrader.startAt 固定初始档 + weaveShouldWarmTextures（light3d 不预热）；
// - 持久化：WeaveViewMode 稳定名 round-trip + SettingsProvider 经 SharedPreferences 读写 round-trip。
//
// 说明：真实 3D 渲染（flutter_scene / GPU）在 flutter 测试环境不可用，故只覆盖纯逻辑与持久化。
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/features/weave/weave_card_texture.dart'
    show weaveShouldWarmTextures;
import 'package:ai_companion/features/weave/weave_perf_monitor.dart'
    show WeaveRenderDegrader, WeaveRenderTier;
import 'package:ai_companion/features/weave/weave_view_mode.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('WeaveViewMode 语义与优先级', () {
    test('只有 auto 启用自动降级监测；其余为手动模式', () {
      expect(WeaveViewMode.auto.enableAutoDegrade, isTrue,
          reason: 'auto（全自动）才启用自动降级监测');
      expect(WeaveViewMode.auto.isManual, isFalse);
      for (final m in [
        WeaveViewMode.full3d,
        WeaveViewMode.light3d,
        WeaveViewMode.twoD,
      ]) {
        expect(m.enableAutoDegrade, isFalse,
            reason: '$m 为手动模式，禁用自动降级监测');
        expect(m.isManual, isTrue);
      }
    });

    test('全量/轻量强制 3D；twoD 强制 2.5D', () {
      expect(WeaveViewMode.full3d.forces3D, isTrue);
      expect(WeaveViewMode.light3d.forces3D, isTrue);
      expect(WeaveViewMode.twoD.forces2D, isTrue);
      expect(WeaveViewMode.twoD.forces3D, isFalse);
      expect(WeaveViewMode.auto.forces3D, isFalse);
      expect(WeaveViewMode.auto.forces2D, isFalse);
    });

    test('初始档位：light3d 初始 light，auto/full3d 初始 full', () {
      expect(WeaveViewMode.light3d.initialTier, WeaveRenderTier.light,
          reason: 'light3d 初始即 light 档渲染（不预热纹理）');
      expect(WeaveViewMode.full3d.initialTier, WeaveRenderTier.full);
      expect(WeaveViewMode.auto.initialTier, WeaveRenderTier.full);
    });
  });

  group('shouldUse3DView（手动优先于 weave_3d flag）', () {
    test('auto 按 flag 决定，且排除自动降级到 2D 的 force2D', () {
      expect(shouldUse3DView(WeaveViewMode.auto, weave3dFlag: true, force2D: false),
          isTrue);
      expect(shouldUse3DView(WeaveViewMode.auto, weave3dFlag: false, force2D: false),
          isFalse);
      expect(shouldUse3DView(WeaveViewMode.auto, weave3dFlag: true, force2D: true),
          isFalse, reason: 'auto 已被自动降级 2D 时不再回 3D（避免抖动）');
    });

    test('手动全量/轻量强制 3D，绕过 weave_3d flag 与 force2D', () {
      expect(
          shouldUse3DView(WeaveViewMode.full3d, weave3dFlag: false, force2D: true),
          isTrue,
          reason: 'full3d 手动强制 3D（即使 flag 关/已降级）');
      expect(
          shouldUse3DView(WeaveViewMode.light3d, weave3dFlag: false, force2D: true),
          isTrue,
          reason: 'light3d 手动强制 3D（即使 flag 关/已降级）');
    });

    test('twoD 直选 2.5D，无视 flag', () {
      expect(shouldUse3DView(WeaveViewMode.twoD, weave3dFlag: true, force2D: false),
          isFalse, reason: 'twoD 始终 2.5D');
      expect(shouldUse3DView(WeaveViewMode.twoD, weave3dFlag: false, force2D: false),
          isFalse);
    });
  });

  group('startAt 固定初始档 + light3d 不预热', () {
    test('startAt 切换档位；light 档 weaveShouldWarmTextures 不预热', () {
      final d = WeaveRenderDegrader();
      expect(d.tier, WeaveRenderTier.full, reason: '默认 full 档');
      d.startAt(WeaveRenderTier.light);
      expect(d.tier, WeaveRenderTier.light);
      // 与 light3d.initialTier=light 一致：light 档不预热纹理（纯色圆点+低细分球）。
      expect(weaveShouldWarmTextures(50, WeaveViewMode.light3d.initialTier), isFalse,
          reason: 'light3d 不预热纹理');
      d.startAt(WeaveRenderTier.full);
      expect(d.tier, WeaveRenderTier.full);
      expect(weaveShouldWarmTextures(80, WeaveViewMode.full3d.initialTier), isTrue,
          reason: 'full3d 初始 full 档，节点数阈值内预热纹理');
      expect(weaveShouldWarmTextures(151, WeaveRenderTier.full), isFalse,
          reason: 'full 档节点数超阈值不预热（既有性能分级）');
    });

    test('auto 经 startAt(full)（初始档），恢复自动监测前先 reset 到 full', () {
      final d = WeaveRenderDegrader(
          windowMs: 500, fullMinFps: 30, lightMinFps: 20);
      d.startAt(WeaveRenderTier.light);
      expect(d.tier, WeaveRenderTier.light);
      // 切回 auto：reset 回到 full 档并重新暖机。
      d.reset();
      expect(d.tier, WeaveRenderTier.full);
      expect(d.degradedTo2D, isFalse);
    });
  });

  group('持久化（shared_preferences round-trip）', () {
    test('WeaveViewMode 稳定名 round-trip；缺失/未知回退 auto', () {
      for (final m in WeaveViewMode.values) {
        expect(WeaveViewMode.fromStorageValue(m.storageValue), m,
            reason: '${m.storageValue} 应能解析回 $m');
      }
      expect(WeaveViewMode.fromStorageValue(null), WeaveViewMode.auto);
      expect(WeaveViewMode.fromStorageValue('bogus'), WeaveViewMode.auto);
      expect(WeaveViewMode.storageKey, 'weave_view_mode');
    });

    test('SettingsProvider 经 SharedPreferences 读写 round-trip（模拟重启）', () async {
      SharedPreferences.setMockInitialValues({});
      final s = SettingsProvider();
      expect(s.weaveViewMode, WeaveViewMode.auto, reason: '首次默认 auto');
      await s.setWeaveViewMode(WeaveViewMode.light3d);
      expect(s.weaveViewMode, WeaveViewMode.light3d);

      // 模拟 App 重启：新实例从持久化 key 读回。
      final s2 = SettingsProvider();
      expect(s2.weaveViewMode, WeaveViewMode.auto, reason: '新实例未 load 前为默认');
      await s2.load();
      expect(s2.weaveViewMode, WeaveViewMode.light3d, reason: '重启后保持用户所选档位');

      // 写回默认 auto 再重启 = auto。
      await s2.setWeaveViewMode(WeaveViewMode.auto);
      final s3 = SettingsProvider();
      await s3.load();
      expect(s3.weaveViewMode, WeaveViewMode.auto);
    });
  });
}
