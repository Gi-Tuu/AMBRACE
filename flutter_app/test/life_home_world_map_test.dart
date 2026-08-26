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

Widget _worldApp({Map<String, dynamic>? world}) => _wrapChild(
      Builder(
        builder: (context) => LifeHomeWorldMap(
          world: world ?? _sampleWorld(),
          l10n: AppLocalizations.of(context)!,
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
}
