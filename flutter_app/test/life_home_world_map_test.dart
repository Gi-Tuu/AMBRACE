import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/widgets/life_home_world_map.dart';

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

Map<String, dynamic> _sampleWorld({String location = 'home'}) => {
      'room_origins': {
        'living': {'wx': 0, 'wy': 0},
        'bedroom': {'wx': 16, 'wy': 0},
        'kitchen': {'wx': 0, 'wy': 12},
        'bathroom': {'wx': 16, 'wy': 12},
      },
      'adjacency': [
        {'from': 'living', 'to': 'bedroom', 'door_type': 'wall_gap', 'side': 'east'},
      ],
      'exit': {'room': 'living', 'side': 'west', 'x': 0, 'y': 6},
      'room_size': {'w': 16, 'h': 12},
      'character': {'room': 'living', 'location': location, 'wx': 8, 'wy': 6},
    };

/// 一张带家具的房间数据（供命中/编辑测试）。
List<Map<String, dynamic>> _sampleRooms() => [
      {
        'id': 'living',
        'name': '客厅',
        'furniture': [
          {'key': 'game', 'name': '游戏机', 'gx': 10.0, 'gy': 8.0, 'gw': 1.0, 'gh': 1.0, 'action': 'game'},
        ],
      },
    ];

Widget _worldApp({Map<String, dynamic>? world}) => _wrapChild(
      Builder(
        builder: (context) => LifeHomeWorldMap(
          world: world ?? _sampleWorld(),
          l10n: AppLocalizations.of(context)!,
        ),
      ),
    );

Widget _interactiveApp({
  Map<String, dynamic>? world,
  List<Map<String, dynamic>>? rooms,
  void Function(String roomId, String key)? onFurnitureTap,
}) =>
    _wrapChild(
      Builder(
        builder: (context) => LifeHomeWorldMap(
          world: world ?? _sampleWorld(),
          l10n: AppLocalizations.of(context)!,
          rooms: rooms ?? _sampleRooms(),
          onFurnitureTap: onFurnitureTap ?? (_, __) {},
        ),
      ),
    );

/// 与 `_sampleWorld` 同一份载荷算出的世界尺寸（px）：32×24 格 × 40。
const _sampleWorldPx = Size(1280.0, 960.0);

/// 「画面不许露出世界外的底板」＝世界四条边都压在视口之外或与之对齐。
/// 这是审查报告里排第一的观感缺陷（角色「出门」时左半屏整块深色底板）的反证。
void expectWorldCoversViewport(
    LifeHomeWorldMapState st, {Size? world, double? scale}) {
  final w = world ?? _sampleWorldPx;
  final s = scale ?? st.viewScale;
  expect(st.viewOffset.dx, lessThanOrEqualTo(0.01), reason: '世界左缘露在视口里了');
  expect(st.viewOffset.dy, lessThanOrEqualTo(0.01), reason: '世界上缘露在视口里了');
  expect(st.viewOffset.dx + w.width * s,
      greaterThanOrEqualTo(st.viewSize.width - 0.01),
      reason: '世界右缘没铺到视口右边');
  expect(st.viewOffset.dy + w.height * s,
      greaterThanOrEqualTo(st.viewSize.height - 0.01),
      reason: '世界下缘没铺到视口下边');
}

void main() {
  group('clampHomeViewOffset 镜头钳制（纯逻辑）', () {
    const world = Size(1280, 960);
    const view = Size(800, 520);

    test('角色在世界左边缘：镜头不许继续往左走，画面不露底', () {
      // 角色在 x=0 ⇒ 未钳制的"居中"目标 = 视口中点 = +400，会把世界左缘推进屏幕
      final got = clampHomeViewOffset(
          target: const Offset(400, 260),
          worldSize: world,
          viewSize: view,
          scale: 1.0);
      expect(got, const Offset(0, 0));
    });

    test('角色在世界右边缘：钳到 -(世界-视口)', () {
      final got = clampHomeViewOffset(
          target: const Offset(400 - 1280, 260 - 960),
          worldSize: world,
          viewSize: view,
          scale: 1.0);
      expect(got, const Offset(-480.0, -440.0));
    });

    test('角色在世界中间：合法区间内原样通过（居中跟随不受影响）', () {
      final got = clampHomeViewOffset(
          target: const Offset(-200, -300),
          worldSize: world,
          viewSize: view,
          scale: 1.0);
      expect(got, const Offset(-200, -300));
    });

    test('世界比视口小（低倍率）：改成居中，两侧留白对称', () {
      final got = clampHomeViewOffset(
          target: const Offset(400, 260),
          worldSize: const Size(300, 200),
          viewSize: view,
          scale: 1.0);
      expect(got, const Offset(250.0, 160.0));
    });

    test('缩放要按新倍率钳（旧倍率算出的边界会放走露底）', () {
      // 真机视口量级（1260×2800 / 560dpi ⇒ 逻辑约 360×800，地图区取 450 高）
      const phone = Size(360, 450);
      // 目标远超边界 ⇒ 必须钳到"当前倍率下"的下界
      final at06 = clampHomeViewOffset(
          target: const Offset(-950, -950),
          worldSize: world,
          viewSize: phone,
          scale: 0.6);
      expect(at06, const Offset(-408.0, -126.0)); // 360−1280×0.6 ／ 450−960×0.6
      final at10 = clampHomeViewOffset(
          target: const Offset(-950, -950),
          worldSize: world,
          viewSize: phone,
          scale: 1.0);
      expect(at10, const Offset(-920.0, -510.0)); // 360−1280 ／ 450−960
      // 低倍率把世界缩到比视口还窄时改成居中（两侧留白对称，不偏成一侧）
      final tiny = clampHomeViewOffset(
          target: const Offset(400, 260),
          worldSize: world,
          viewSize: const Size(800, 520),
          scale: 0.6);
      expect(tiny.dx, closeTo((800 - 768) / 2, 1e-9));
      expect(tiny.dy, closeTo(0, 1e-9)); // 576 仍高于 520 ⇒ 纵向还是钳住
    });

    test('视口还没量出来（Size.zero）时原样返回，不炸', () {
      final got = clampHomeViewOffset(
          target: const Offset(400, 260),
          worldSize: world,
          viewSize: Size.zero,
          scale: 1.0);
      expect(got, const Offset(400, 260));
    });
  });

  group('LifeHomeWorldMap 小家大地图（v1.1，2026-08-26）', () {
    testWidgets('world 载荷渲染：图例 homeWorldMap + homeExit，室内态不显示 homeGoOut', (tester) async {
      await tester.pumpWidget(_worldApp());
      await tester.pump();
      expect(find.text('小家地图'), findsOneWidget); // homeWorldMap
      expect(find.text('出口'), findsOneWidget);      // homeExit
      expect(find.text('出门'), findsNothing);        // homeGoOut（室内态隐藏）
    });

    testWidgets('location != home 时显示 homeGoOut（出门）', (tester) async {
      await tester.pumpWidget(_worldApp(world: _sampleWorld(location: 'world')));
      await tester.pump();
      // 图例切为 homeGoOut（出门）；出口标签画在画布上（CustomPaint 文本不入 widget 树）
      expect(find.text('出门'), findsOneWidget); // homeGoOut
      expect(find.text('小家地图'), findsOneWidget);
    });
  });

  group('LifeHomeWorldMap v1.2（交互画布，2026-08-27）', () {
    testWidgets('进入界面镜头跟随角色：角色可见，且画面不许露出世界外的底板', (tester) async {
      await tester.pumpWidget(_interactiveApp());
      await tester.pump();  // 让 post-frame 的 centerOnCharacter 生效
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      // 角色世界坐标 = character.wx/wy * 40 = (8*40, 6*40)
      expect(st.characterWorld, const Offset(320, 240));
      // 角色屏幕位置 = characterWorld * scale + offset，必须落在视口内。
      // 注意：这里**不再要求严格居中**——角色贴世界边缘时镜头会先停住（钳制的定义），
      // 旧断言把"居中"当成了契约，实际契约是"角色可见 + 不露底"。
      final screen = st.characterWorld * st.viewScale + st.viewOffset;
      expect(screen.dx, inInclusiveRange(0, st.viewSize.width));
      expect(screen.dy, inInclusiveRange(0, st.viewSize.height));
      expectWorldCoversViewport(st);
    });

    testWidgets('缩放按钮已移至宿主工具栏；地图只暴露 zoomIn/zoomOut/resetView 方法', (tester) async {
      await tester.pumpWidget(_interactiveApp());
      await tester.pump();
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      // 地图组件内不再渲染悬浮缩放按钮（已移到宿主工具栏）
      expect(find.byTooltip('放大'), findsNothing);
      expect(find.byTooltip('缩小'), findsNothing);
      expect(find.byTooltip('复位'), findsNothing);
      expect(st.viewScale, 1.0);
      // zoomIn → 1.25
      st.zoomIn();
      await tester.pump();
      expect(st.viewScale, closeTo(1.25, 1e-9));
      // zoomOut → 1.0
      st.zoomOut();
      await tester.pump();
      expect(st.viewScale, closeTo(1.0, 1e-9));
      // zoomIn 后再 resetView → 1.0（且角色回到可见区、画面不露底）
      st.zoomIn();
      st.resetView();
      await tester.pump();
      expect(st.viewScale, 1.0);
      final screen = st.characterWorld * st.viewScale + st.viewOffset;
      expect(screen.dx, inInclusiveRange(0, st.viewSize.width));
      expect(screen.dy, inInclusiveRange(0, st.viewSize.height));
      expectWorldCoversViewport(st);
    });

    testWidgets('角色「出门」站在世界左边缘出口：左半屏不许露出深色底板（真机回归）', (tester) async {
      // 后端 exit = {room: living, side: west, x: 0}，而 room_origins.living = (0,0)
      // ⇒ 出门时角色恰在世界最左边缘；未钳制的镜头会把它居中，左半屏于是整块露底。
      await tester.pumpWidget(_interactiveApp(world: _sampleWorld(location: 'world')));
      await tester.pump();
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      expect(st.characterWorld.dx, 0.0);      // 出口格：世界最左
      expectWorldCoversViewport(st);
      // 角色仍可见（贴着视口左缘，而不是被推出屏幕）
      final sx = st.characterWorld.dx * st.viewScale + st.viewOffset.dx;
      expect(sx, greaterThanOrEqualTo(0.0));
      expect(sx, lessThanOrEqualTo(st.viewSize.width));
    });

    testWidgets('点家具触发 onFurnitureTap 回调（房间 + key）', (tester) async {
      final taps = <String>[];
      await tester.pumpWidget(_interactiveApp(onFurnitureTap: (r, k) => taps.add('$r:$k')));
      await tester.pump();
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      // 家具（客厅 game）格中心 = (10.5, 8.5) → 世界 px (420, 340)
      final target = const Offset(420, 340);
      final screen = target * st.viewScale + st.viewOffset;
      await tester.tapAt(screen);
      await tester.pump();
      expect(taps, ['living:game']);
    });

    testWidgets('无拖动平移手势：拖动不改变视图变换', (tester) async {
      await tester.pumpWidget(_interactiveApp());
      await tester.pump();
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      final beforeOffset = st.viewOffset;
      final beforeScale = st.viewScale;
      // 快速拖动地图（非长按 300ms），不应触发平移/缩放
      await tester.dragFrom(Offset(st.viewSize.width / 2, st.viewSize.height / 2),
          const Offset(120, 80));
      await tester.pump();
      expect(st.viewOffset, beforeOffset);
      expect(st.viewScale, beforeScale);
    });
  });
}
