import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/widgets/character_entry_carousel.dart';

void main() {
  Widget buildCarousel({
    VoidCallback? onCenterTap,
    VoidCallback? onCenterLongPress,
    Map<String, VoidCallback>? entryTaps,
  }) {
    return MaterialApp(
      home: Scaffold(
        body: Center(
          child: CharacterEntryCarousel(
            height: 116,
            center: GestureDetector(
              key: const Key('center'),
              onTap: onCenterTap,
              onLongPress: onCenterLongPress,
              child: Container(
                width: 96,
                height: 96,
                color: Colors.red,
              ),
            ),
            entries: [
              EntryCarouselItem(
                icon: Icons.settings,
                label: 'Settings',
                onTap: entryTaps?['Settings'] ?? () {},
              ),
              EntryCarouselItem(
                icon: Icons.psychology_alt_outlined,
                label: 'MindWorld',
                onTap: entryTaps?['MindWorld'] ?? () {},
              ),
              EntryCarouselItem(
                icon: Icons.auto_awesome,
                label: 'Life',
                onTap: entryTaps?['Life'] ?? () {},
              ),
            ],
          ),
        ),
      ),
    );
  }

  testWidgets('renders center page initially with entry cards', (tester) async {
    await tester.pumpWidget(buildCarousel());

    // 头像（center）默认停在中页；初始仅构建视口内/相邻窥探的入口卡片。
    expect(find.byKey(const Key('center')), findsOneWidget);
    // 默认页为 center（指示器每页一颗，共 4 页条目：[settings, center, mind, life]）。
    expect(find.byType(PageView), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('taps entry card triggers callback and snaps back to center',
      (tester) async {
    final tapped = <String>[];
    await tester.pumpWidget(buildCarousel(
      entryTaps: {'Settings': () => tapped.add('Settings')},
    ));

    final centerDefaultDx =
        tester.getCenter(find.byKey(const Key('center'))).dx;

    // 右滑到左侧第一个入口页（Settings, page 0）。
    await tester.drag(find.byType(PageView), const Offset(600, 0));
    await tester.pumpAndSettle();

    final centerMovedDx = tester.getCenter(find.byKey(const Key('center'))).dx;
    expect((centerMovedDx - centerDefaultDx).abs(), greaterThan(20),
        reason: 'swiping should move the avatar away from center');

    // 点入口卡片 → 触发回调。
    await tester.tap(find.text('Settings').first);
    await tester.pump(const Duration(milliseconds: 120));
    await tester.pumpAndSettle();

    expect(tapped, ['Settings']);

    // 吸附回 center 页（头像回到视口中央）。
    final centerBackDx = tester.getCenter(find.byKey(const Key('center'))).dx;
    expect((centerBackDx - centerDefaultDx).abs(), lessThan(20),
        reason: 'after tapping an entry the carousel should snap back to center');
  });

  testWidgets('center tap and long-press are not swallowed by PageView',
      (tester) async {
    var tapCount = 0;
    var longPressCount = 0;
    await tester.pumpWidget(buildCarousel(
      onCenterTap: () => tapCount++,
      onCenterLongPress: () => longPressCount++,
    ));

    // 点击头像 → 触发 onTap。
    await tester.tap(find.byKey(const Key('center')));
    expect(tapCount, 1);

    // 长按头像 → 触发 onLongPress。
    await tester.longPress(find.byKey(const Key('center')));
    expect(longPressCount, 1);
  });
}
