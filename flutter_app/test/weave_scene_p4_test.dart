// 织网 3D 低端机体验修复 · 可测单测（2026-08-24）
//
// 覆盖：
// - 两段式降级状态机 WeaveRenderDegrader（full→light→2D，一次性下坡不抖动、不回 full）；
// - light 档不预热判定 weaveShouldWarmTextures；
// - 2.5D 视图挂载后 theta 放开（WeaveSceneView2D initState 走 setThetaLimit(null)）；
// - 2.5D 背面淡化下调（nodeDepthOpacity backFar 0.30→0.22，可自由翻转不刺眼）。
//
// 说明：真实 3D 渲染（flutter_scene / GPU）在 flutter 测试环境不可用，故本测试只覆盖纯逻辑
// 与 2.5D CustomPaint 视图的 theta 放开行为；3D light 档的实际渲染由真机/模拟器冒烟验证。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/weave/weave_scene_controller.dart';
import 'package:ai_companion/features/weave/weave_perf_monitor.dart'
    show WeaveDegradeEvent, WeaveRenderDegrader, WeaveRenderTier;
import 'package:ai_companion/features/weave/weave_card_texture.dart'
    show weaveShouldWarmTextures;
import 'package:ai_companion/features/weave/weave_scene_view.dart'
    show WeaveSceneView2D;
import 'package:ai_companion/utils/sphere_projection.dart' show nodeDepthOpacity;

/// 构造 n 个节点。
List<WeaveSceneNode> _nodes(int n) {
  return [for (var i = 0; i < n; i++) _node(i + 1)];
}

WeaveSceneNode _node(int id) {
  return WeaveSceneNode(
    id: id,
    characterId: id % 8,
    characterIds: [id % 8],
    title: '节点$id',
    summary: '摘要$id',
    importance: 0,
    mood: '',
    lat: 0.0,
    lon: 0.0,
  );
}

void main() {
  group('WeaveRenderDegrader 两段式降级（full→light→2D）', () {
    test('full 持续低帧率 → 切 light；light 持续低帧率 → 回退 2D（一次性下坡）', () {
      final d = WeaveRenderDegrader(
          windowMs: 2000, fullMinFps: 30, lightMinFps: 20);
      expect(d.tier, WeaveRenderTier.full);
      expect(d.degradedTo2D, isFalse);

      // full 阶段：21 帧 / 2000ms ≈ 10.5fps < 30 → 切 light。
      WeaveDegradeEvent ev = WeaveDegradeEvent.none;
      var t = 0;
      for (var i = 0; i < 21; i++) {
        final e = d.recordFrame(t += 100);
        if (e != WeaveDegradeEvent.none) ev = e;
      }
      expect(ev, WeaveDegradeEvent.switchedToLight);
      expect(d.tier, WeaveRenderTier.light);
      expect(d.degradedTo2D, isFalse, reason: '切 light 不视为回退 2D');

      // light 阶段：继续 21 帧 / 2000ms ≈ 10.5fps < 20 → 回退 2D。
      ev = WeaveDegradeEvent.none;
      for (var i = 0; i < 21; i++) {
        final e = d.recordFrame(t += 100);
        if (e != WeaveDegradeEvent.none) ev = e;
      }
      expect(ev, WeaveDegradeEvent.fallbackTo2D);
      expect(d.tier, WeaveRenderTier.light, reason: '档位保持 light（已到底）');
      expect(d.degradedTo2D, isTrue);

      // 一次性：之后 recordFrame 恒返回 none，不抖动。
      expect(d.recordFrame(t += 100), WeaveDegradeEvent.none);
      expect(d.degradedTo2D, isTrue);
    });

    test('full 帧率足够时不降级；之后持续低帧才下坡', () {
      final d = WeaveRenderDegrader(
          windowMs: 500, fullMinFps: 30, lightMinFps: 20);
      // 高帧（约 100fps）：不降级。
      for (var t = 0; t <= 500; t += 10) {
        expect(d.recordFrame(t), WeaveDegradeEvent.none);
      }
      expect(d.tier, WeaveRenderTier.full);
      // 之后持续低帧：下坡到 light。
      WeaveDegradeEvent ev = WeaveDegradeEvent.none;
      var t = 600;
      for (var i = 0; i < 6; i++) {
        final e = d.recordFrame(t += 100);
        if (e != WeaveDegradeEvent.none) ev = e;
      }
      expect(ev, WeaveDegradeEvent.switchedToLight);
    });

    test('一次性下坡：full 降 light 后即使帧率恢复也不回 full', () {
      final d = WeaveRenderDegrader(
          windowMs: 500, fullMinFps: 30, lightMinFps: 20);
      for (var t = 0; t <= 500; t += 100) {
        d.recordFrame(t); // 6 帧 / 0.5s ≈ 12fps → 切 light
      }
      expect(d.tier, WeaveRenderTier.light);
      // light 阶段帧率恢复（高帧）→ 不回 full、不触发 2D。
      for (var t = 600; t <= 2000; t += 10) {
        expect(d.recordFrame(t), WeaveDegradeEvent.none);
      }
      expect(d.tier, WeaveRenderTier.light, reason: 'light 不回 full（下坡不抖动）');
      expect(d.degradedTo2D, isFalse, reason: '帧率恢复不触发回退 2D');
    });

    test('warmupFrames 只作用于 full 档（瞬态低谷不误切 light）', () {
      final d = WeaveRenderDegrader(
          windowMs: 2000,
          fullMinFps: 30,
          lightMinFps: 20,
          warmupFrames: 5);
      // 前 5 帧宽限，之后仍低帧 → 切 light。
      WeaveDegradeEvent ev = WeaveDegradeEvent.none;
      var t = 0;
      for (var i = 0; i < 30; i++) {
        final e = d.recordFrame(t += 100);
        if (e != WeaveDegradeEvent.none) ev = e;
      }
      expect(ev, WeaveDegradeEvent.switchedToLight);
    });

    test('reset 复位回 full 档并重新暖机', () {
      final d = WeaveRenderDegrader(
          windowMs: 500,
          fullMinFps: 30,
          lightMinFps: 20,
          warmupFrames: 3);
      // 暖机 3 帧 + 之后低帧 → 切 light。
      for (var t = 0; t <= 900; t += 100) {
        d.recordFrame(t);
      }
      expect(d.tier, WeaveRenderTier.light);
      d.reset();
      expect(d.tier, WeaveRenderTier.full);
      expect(d.degradedTo2D, isFalse);
      // reset 后重新暖机：前 3 帧不降级。
      for (var i = 0; i < 3; i++) {
        expect(d.recordFrame(i * 100), WeaveDegradeEvent.none);
      }
    });
  });

  group('weaveShouldWarmTextures（light 档不预热）', () {
    test('light 档不预热；full 档按节点数阈值决定', () {
      expect(weaveShouldWarmTextures(50, WeaveRenderTier.full), isTrue);
      expect(weaveShouldWarmTextures(151, WeaveRenderTier.full), isFalse,
          reason: '节点数超 >150 不预热（纯色圆点）');
      expect(weaveShouldWarmTextures(50, WeaveRenderTier.light), isFalse,
          reason: 'light 档不预热纹理');
      expect(weaveShouldWarmTextures(151, WeaveRenderTier.light), isFalse);
    });
  });

  group('2.5D 视图挂载后 theta 放开', () {
    testWidgets('WeaveSceneView2D initState 调用 setThetaLimit(null)（可自由翻转）',
        (tester) async {
      final c = WeaveSceneController()..setGraph(nodes: _nodes(10));
      await tester.pumpWidget(MaterialApp(
        home: WeaveSceneView2D(controller: c, onCardTap: (_) {}),
      ));
      expect(c.thetaLimit, isNull, reason: '2.5D 视图应放开 theta（与 3D 一致）');
      await tester.pumpWidget(const SizedBox());
    });

    test('nodeDepthOpacity backFar 已调低（0.22），翻转不刺眼', () {
      expect(nodeDepthOpacity(-1.0), 0.22);
      expect(nodeDepthOpacity(-1.0), lessThan(0.30));
    });
  });
}
