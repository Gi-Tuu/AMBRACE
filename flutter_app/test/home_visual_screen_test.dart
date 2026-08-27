import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/screens/life/home_visual_screen.dart';
import 'package:ai_companion/widgets/life_home_controls.dart';

Widget _wrap() => MaterialApp(
      locale: const Locale('zh'),
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        ...AppLocalizations.localizationsDelegates,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      home: const HomeVisualScreen(),
    );

Widget _wrapChild(Widget child) => MaterialApp(
      locale: const Locale('zh'),
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        ...AppLocalizations.localizationsDelegates,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: child),
    );

void main() {
  group('HomeLayoutMath 自由摆放几何（纯逻辑）', () {
    test('logicToGrid 逻辑像素 → 浮点格坐标', () {
      final g = HomeLayoutMath.logicToGrid(const Offset(140, 90));
      expect(g.dx, 3.5);
      expect(g.dy, 2.25);
      // 小数像素也换算为小数格（自由摆放，不吸附格子）
      final g2 = HomeLayoutMath.logicToGrid(const Offset(131, 77));
      expect(g2.dx, closeTo(3.275, 1e-9));
      expect(g2.dy, closeTo(1.925, 1e-9));
    });

    test('hitTestRect 浮点矩形命中检测', () {
      // 沙发 gx=3.5 gy=2.25 gw=2 gh=1 → 逻辑矩形 (140,90,80,40)
      bool hit(Offset p) => HomeLayoutMath.hitTestRect(p,
          gx: 3.5, gy: 2.25, gw: 2, gh: 1);
      expect(hit(const Offset(150, 100)), isTrue);
      expect(hit(const Offset(219, 129)), isTrue);
      expect(hit(const Offset(139, 100)), isFalse); // 左侧外
      expect(hit(const Offset(150, 89)), isFalse); // 上方外
    });

    test('clampGrid 落位钳制在画布内（0-16 / 0-12 格）', () {
      // 家具 2x1：gx 上限 14、gy 上限 11
      final c1 = HomeLayoutMath.clampGrid(const Offset(15.5, 10), 2, 1);
      expect(c1.dx, 14);
      expect(c1.dy, 10);
      final c2 = HomeLayoutMath.clampGrid(const Offset(0.2, 11.9), 2, 1);
      expect(c2.dx, closeTo(0.2, 1e-9));
      expect(c2.dy, 11);
      final c3 = HomeLayoutMath.clampGrid(const Offset(-1, -2), 2, 1);
      expect(c3.dx, 0);
      expect(c3.dy, 0);
      // 1x2 家具：gy 上限 10
      final c4 = HomeLayoutMath.clampGrid(const Offset(3.75, 11.5), 1, 2);
      expect(c4.dx, closeTo(3.75, 1e-9));
      expect(c4.dy, 10);
    });
  });

  group('HomeLayoutMath v3.3 家具朝向（纯逻辑）', () {
    test('nextRotation 循环切换 0→1→2→3→0', () {
      expect(HomeLayoutMath.nextRotation(0), 1);
      expect(HomeLayoutMath.nextRotation(1), 2);
      expect(HomeLayoutMath.nextRotation(2), 3);
      expect(HomeLayoutMath.nextRotation(3), 0);
    });

    test('clampRotation 后端字段 0-7 钳制', () {
      expect(HomeLayoutMath.rotationMax, 7);
      expect(HomeLayoutMath.clampRotation(-1), 0);
      expect(HomeLayoutMath.clampRotation(8), 7);
      expect(HomeLayoutMath.clampRotation(5), 5);
      expect(HomeLayoutMath.clampRotation(7), 7);
    });
  });

  group('LifeHomeRoomTabBar 房间 Tab 布局（v3.3 ②）', () {
    const rooms = [
      LifeHomeRoom('living', '客厅'),
      LifeHomeRoom('bedroom', '卧室'),
      LifeHomeRoom('kitchen', '厨房'),
      LifeHomeRoom('bathroom', '浴室'),
    ];

    testWidgets('四房间居中一行 + 右端「家具编辑」按钮；切换/编辑回调', (tester) async {
      String? selected;
      var editTaps = 0;
      await tester.pumpWidget(_wrapChild(LifeHomeRoomTabBar(
        rooms: rooms,
        currentRoomId: 'living',
        editing: false,
        onSelectRoom: (id) => selected = id,
        onEditTap: () => editTaps++,
      )));

      for (final name in ['客厅', '卧室', '厨房', '浴室']) {
        expect(find.text(name), findsOneWidget);
      }
      expect(find.text('家具编辑'), findsOneWidget);
      // 当前房间选中态
      final chip = tester.widget<ChoiceChip>(find.widgetWithText(ChoiceChip, '客厅'));
      expect(chip.selected, isTrue);
      // 切房间
      await tester.tap(find.text('卧室'));
      expect(selected, 'bedroom');
      // 点编辑按钮
      await tester.tap(find.text('家具编辑'));
      expect(editTaps, 1);
    });

    testWidgets('编辑态编辑按钮高亮（edit_off 图标）', (tester) async {
      await tester.pumpWidget(_wrapChild(LifeHomeRoomTabBar(
        rooms: rooms,
        currentRoomId: 'living',
        editing: true,
        onSelectRoom: (_) {},
        onEditTap: () {},
      )));
      expect(find.byIcon(Icons.edit_off), findsOneWidget);
      expect(find.byIcon(Icons.edit_outlined), findsNothing);
    });
  });

  group('LifeHomeWorldToolbar 世界模式顶部工具栏（v1.2 重构）', () {
    testWidgets('家具编辑 + 缩放按钮（+/−/复位）回调', (tester) async {
      var editTaps = 0, zi = 0, zo = 0, rz = 0;
      await tester.pumpWidget(_wrapChild(LifeHomeWorldToolbar(
        editing: false,
        onEditTap: () => editTaps++,
        onZoomIn: () => zi++,
        onZoomOut: () => zo++,
        onResetView: () => rz++,
      )));
      expect(find.text('家具编辑'), findsOneWidget);
      expect(find.byTooltip('放大'), findsOneWidget);
      expect(find.byTooltip('缩小'), findsOneWidget);
      expect(find.byTooltip('复位'), findsOneWidget);
      await tester.tap(find.text('家具编辑'));
      await tester.tap(find.byTooltip('放大'));
      await tester.tap(find.byTooltip('缩小'));
      await tester.tap(find.byTooltip('复位'));
      expect(editTaps, 1);
      expect(zi, 1);
      expect(zo, 1);
      expect(rz, 1);
    });

    testWidgets('编辑态编辑按钮高亮（edit_off 图标）', (tester) async {
      await tester.pumpWidget(_wrapChild(LifeHomeWorldToolbar(
        editing: true,
        onEditTap: () {},
      )));
      expect(find.byIcon(Icons.edit_off), findsOneWidget);
      expect(find.byIcon(Icons.edit_outlined), findsNothing);
    });
  });

  group('LifeHomeEditHintBar / LifeHomeEditActionBar（v3.3 ②③）', () {
    testWidgets('编辑提示条：提示文案 + 完成回调', (tester) async {
      var done = 0;
      await tester.pumpWidget(_wrapChild(LifeHomeEditHintBar(onDone: () => done++)));
      expect(find.text('拖动或点选家具进行编辑'), findsOneWidget);
      await tester.tap(find.text('完成'));
      expect(done, 1);
    });

    testWidgets('被编辑操作栏：回退/旋转/确定 三按钮回调', (tester) async {
      var revert = 0, rotate = 0, confirm = 0;
      await tester.pumpWidget(_wrapChild(LifeHomeEditActionBar(
        onRevert: () => revert++,
        onRotate: () => rotate++,
        onConfirm: () => confirm++,
      )));
      expect(find.text('回退'), findsOneWidget);
      expect(find.text('旋转'), findsOneWidget);
      expect(find.text('确定'), findsOneWidget);
      await tester.tap(find.text('回退'));
      await tester.tap(find.text('旋转'));
      await tester.tap(find.text('确定'));
      expect(revert, 1);
      expect(rotate, 1);
      expect(confirm, 1);
    });
  });

  testWidgets('HomeVisualScreen 页面可构建（无后端 → 错误态 + 重试 + 返回按钮）', (tester) async {
    await tester.pumpWidget(_wrap());
    // 网络请求在测试环境失败 → 进入错误态，AppBar/重试按钮始终渲染
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    expect(find.byType(HomeVisualScreen), findsOneWidget);
    expect(find.text('小家'), findsOneWidget); // AppBar 标题（homeTitle）
    expect(find.byIcon(Icons.arrow_back), findsOneWidget); // v3.3 ④ 返回按钮
    expect(find.textContaining('加载小家失败'), findsOneWidget); // loadHomeFailed
    expect(find.text('重试'), findsOneWidget); // retry 按钮

    // 让 initState 里的 3s 拖动提示定时器走完，避免遗留 pending timer
    await tester.pump(const Duration(seconds: 4));
  });
}
