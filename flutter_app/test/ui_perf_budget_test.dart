// 前端性能审计（2026-09-29，报告 output/AMBRACE_前端性能审计_20260929.md）落下来的三条预算锁。
//
// 这三条断言的都是"不许回潮"的口径，不是新功能：
//   1. 网络图必须按显示尺寸解码（全工程 28 处 Image.network 此前只有 1 处设了宽度上限）；
//   2. BackdropFilter 只许在 glass 皮肤出现（AppGlass 的设计契约，floating_sheet 此前漏门禁）；
//   3. 未读快照没变就不许 notifyListeners（否则好友列表每 5 秒被无条件整页重建）。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/notification_service.dart';
import 'package:ai_companion/services/unread_engine.dart';
import 'package:ai_companion/widgets/floating_sheet.dart';
import 'package:ai_companion/widgets/moment_card.dart';

void main() {
  // SettingsProvider 构造/setSkinId 走 SharedPreferences 通道；不 mock 的话在
  // 测试 binding 里永不返回，用例直接挂到 10 分钟超时（test/character_list_screen_test.dart 同款前置）。
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('朋友圈头像与缩略图按显示尺寸解码（cacheWidth 必须已设）',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: ListView(
          children: const [
            MomentAvatar(avatarUrl: '/uploads/a.png', name: 'X', radius: 22),
            MomentImageView(imageUrl: '/uploads/b.png'),
          ],
        ),
      ),
    ));
    await tester.pump();

    final images = tester.widgetList<Image>(find.byType(Image)).toList();
    expect(images, hasLength(2), reason: '两处图都没建出来，用例失效');
    for (final im in images) {
      // `cacheWidth` 不是 Image 的字段，它在构造时就把 provider 包进 ResizeImage
      // ⇒ 断言"被包了一层、且目标宽度是个正数"才等价于"按显示尺寸解码"。
      final provider = im.image;
      expect(provider, isA<ResizeImage>(),
          reason: '没设 cacheWidth＝原图整张进 GPU 纹理，是 OOM 的头号来源');
      final w = (provider as ResizeImage).width;
      expect(w, isNotNull);
      expect(w, greaterThan(0));
    }
    // 缩略图上限要贴着显示宽度（≤240 逻辑 × dpr），不是随手给的大数
    expect((images[1].image as ResizeImage).width, lessThanOrEqualTo(240 * 4));
  });

  testWidgets('FloatingSheet 只在 glass 皮肤下发 BackdropFilter', (tester) async {
    Future<int> blurLayers(String skinId) async {
      final settings = SettingsProvider();
      await settings.setSkinId(skinId);
      await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>.value(
        value: settings,
        child: MaterialApp(
          home: Scaffold(
            body: FloatingSheet(child: const SizedBox(height: 40)),
          ),
        ),
      ));
      await tester.pump();
      return find.byType(BackdropFilter).evaluate().length;
    }

    // 用户实际在用的纸感皮肤：一层模糊都不该建（此前它是全 App 唯一没门禁的重量级模糊）
    expect(await blurLayers('aegean'), 0);
    expect(await blurLayers('paper'), 0);
    // glass 皮肤保留模糊
    expect(await blurLayers('glass'), 1);
  });

  testWidgets('未读快照没变时不通知；真变了必须通知（只通知一次）', (tester) async {
    SharedPreferences.setMockInitialValues(
        {'unread_snapshot': NotifyPrefs.encodeSnapshot({7: 3})});
    final svc = NotificationService();
    svc.stopPolling(); // 单例：别让上一轮的定时器留着
    var notifies = 0;
    void count() => notifies++;
    svc.addListener(count);

    svc.startPolling();
    await tester.pump();                        // 首轮 _refreshFromPrefs 的 await 落地
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    // 空 → {7:3} 是真变化（该通知）；之后两轮快照一模一样 ⇒ 不该再吵
    expect(notifies, 1, reason: '快照没变仍在 notify＝每 5 秒整页重建好友列表');

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        NotifyPrefs.snapshotKey, NotifyPrefs.encodeSnapshot({7: 4}));
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(notifies, 2, reason: '红点变了却不通知＝未读角标不再刷新');

    svc.removeListener(count);
    svc.stopPolling();
  });
}
