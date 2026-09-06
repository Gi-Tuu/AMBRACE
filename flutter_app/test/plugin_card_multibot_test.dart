// C8（2026-09-06 多 ClawBot）：渠道卡 per-bot 渲染 + 「添加另一个 bot」引导。
// - GET bindings 返回双 bot → 每 bot 一行 Dropdown（主账号）；
// - bot 显示名优先 bot_label，其次「默认」；
// - wechat 渠道显示添加引导（扫码在网关侧）+ 刷新按钮；douyin 不显示；
// - 子账号多 bot 只读不变。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/plugin/plugin_card.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/services/api_client.dart';
import 'fake_api_adapter.dart';
import 'package:shared_preferences/shared_preferences.dart';

Widget _host(Widget child) => MaterialApp(
      locale: const Locale('zh'),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(body: SingleChildScrollView(child: child)),
    );

Map<String, dynamic> _plugin(String name) => {
      'name': name,
      'version': '1.0.0',
      'description': '',
      'enabled': true,
      'config': <String, dynamic>{},
    };

const _twoBots = {
  'items': [
    {'bot_account_id': 'botA-im-bot', 'bot_label': '', 'character_id': 101, 'enabled': true, 'updated_at': null},
    {'bot_account_id': 'botB-im-bot', 'bot_label': '二号', 'character_id': 102, 'enabled': true, 'updated_at': null},
  ],
};

const _chars = {
  'characters': [
    {'id': 101, 'name': '小慧'},
    {'id': 102, 'name': '小橙'},
  ],
};

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient().dio.httpClientAdapter = FakeApiAdapter();
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  testWidgets('C8 主账号：双 bot 渲染两行 Dropdown，bot_label 优先展示，wechat 显示添加引导', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/wechat/bindings', _twoBots);
    api.json('GET', '/api/v1/characters', _chars);

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.byType(DropdownButton<int>), findsNWidgets(2)); // 每 bot 一行
    expect(find.text('二号'), findsOneWidget); // bot_label 优先
    expect(find.text('默认'), findsOneWidget); // 空 label → 默认
    expect(find.text('新增 bot：先在网关（openclaw）扫码登录新微信号，再点「刷新 bot 列表」绑定角色'),
        findsOneWidget); // 添加引导
    expect(find.text('刷新 bot 列表'), findsOneWidget);
  });

  testWidgets('C8 修正：双 bot 各自回显自己绑定的角色（不共用选中值）', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/wechat/bindings', _twoBots); // botA→101, botB→102
    api.json('GET', '/api/v1/characters', _chars);

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    final dropdowns = tester.widgetList<DropdownButton<int>>(find.byType(DropdownButton<int>)).toList();
    expect(dropdowns.length, 2);
    // 各 bot 行回显自己绑定的角色（C8 复核缺口：旧实现两行共用同一 value）
    expect(dropdowns[0].value, 101); // botA 行 → 101
    expect(dropdowns[1].value, 102); // botB 行 → 102
  });

  testWidgets('C8 修正：botA 行改选后 botB 行选中值不受影响', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/wechat/bindings', _twoBots); // botA→101, botB→102
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 101, 'name': '小慧'},
        {'id': 102, 'name': '小橙'},
        {'id': 103, 'name': '小黄'},
      ],
    });

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    // 在 botA 行（第一行）打开下拉并改选「小黄」(103)
    await tester.tap(find.byType(DropdownButton<int>).first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('小黄').last);
    await tester.pumpAndSettle();

    final dropdowns = tester.widgetList<DropdownButton<int>>(find.byType(DropdownButton<int>)).toList();
    expect(dropdowns[0].value, 103); // botA 行已改选
    expect(dropdowns[1].value, 102); // botB 行保持 102 不受影响（per-bot key 生效）
  });

  testWidgets('C8 douyin 渠道不显示添加引导（physical_singleton 单 bot）', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/douyin/bindings', {
      'items': [
        {'bot_account_id': 'default', 'bot_label': '', 'character_id': 101, 'enabled': true, 'updated_at': null},
      ],
    });
    api.json('GET', '/api/v1/characters', _chars);

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin('douyin_mcp'),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.byType(DropdownButton<int>), findsOneWidget);
    expect(find.text('刷新 bot 列表'), findsNothing);
  });

  testWidgets('C8 子账号：多 bot 只读（无 Dropdown、显示角色名、无添加引导按钮）', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/wechat/bindings', _twoBots);
    api.json('GET', '/api/v1/characters', _chars);

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: false,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.byType(DropdownButton<int>), findsNothing);
    expect(find.text('小慧'), findsOneWidget);
    expect(find.text('小橙'), findsOneWidget);
    expect(find.text('刷新 bot 列表'), findsNothing); // 子账号无写入口
    expect(find.text('仅主账号可配置渠道绑定'), findsOneWidget);
  });
}
