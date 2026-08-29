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

void main() {
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
    testWidgets('进入界面镜头以角色为中心跟随（角色可见）', (tester) async {
      await tester.pumpWidget(_interactiveApp());
      await tester.pump();  // 让 post-frame 的 centerOnCharacter 生效
      final st = tester.state<LifeHomeWorldMapState>(find.byType(LifeHomeWorldMap));
      // 角色世界坐标 = character.wx/wy * 40 = (8*40, 6*40)
      expect(st.characterWorld, const Offset(320, 240));
      // 角色屏幕位置 = characterWorld * scale + offset，应等于视口中心
      final screen = st.characterWorld * st.viewScale + st.viewOffset;
      expect(screen.dx, closeTo(st.viewSize.width / 2, 0.5));
      expect(screen.dy, closeTo(st.viewSize.height / 2, 0.5));
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
      // zoomIn 后再 resetView → 1.0（且以角色为中心）
      st.zoomIn();
      st.resetView();
      await tester.pump();
      expect(st.viewScale, 1.0);
      final screen = st.characterWorld * st.viewScale + st.viewOffset;
      expect(screen.dx, closeTo(st.viewSize.width / 2, 0.5));
      expect(screen.dy, closeTo(st.viewSize.height / 2, 0.5));
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
