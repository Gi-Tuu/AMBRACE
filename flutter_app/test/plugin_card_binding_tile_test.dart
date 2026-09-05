// C4（2026-09-05 落地审查）：渠道绑定统一区块——子账号只读渲染不崩（不渲染 Dropdown）、
// 显示绑定角色名；主账号渲染可写 Dropdown + 保存/解绑按钮。
// 用 FakeApiAdapter 桩后端（/channels/{ch}/bindings 与 /characters），不产生真实网络请求。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/plugin/plugin_card.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/services/api_client.dart';
import 'fake_api_adapter.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<Widget> _host(Widget child) async => MaterialApp(
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

void _stubBackend(FakeApiAdapter api) {
  api.json('GET', '/api/v1/channels/wechat/bindings', {
    'items': [
      {
        'bot_account_id': 'default',
        'bot_label': '',
        'character_id': 101,
        'enabled': true,
        'updated_at': null,
      }
    ],
  });
  api.json('GET', '/api/v1/characters', {
    'characters': [
      {'id': 101, 'name': '小慧'},
      {'id': 102, 'name': '小橙'},
    ],
  });
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient().dio.httpClientAdapter = FakeApiAdapter();
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  testWidgets('C4 子账号：微信渠道卡只读显示绑定角色名，不渲染 Dropdown/写按钮', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    _stubBackend(api);

    await tester.pumpWidget(await _host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: false,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    // 只读角色名可见（cid=101 → 小慧）
    expect(find.text('小慧'), findsOneWidget);
    // 不渲染 Dropdown（C4：彻底避开 value 不在 items 断言）
    expect(find.byType(DropdownButton<int>), findsNothing);
    // 不渲染写按钮
    expect(find.text('保存'), findsNothing);
    expect(find.text('解绑'), findsNothing);
    // 仅主账号提示可见
    expect(find.text('仅主账号可配置渠道绑定'), findsOneWidget);
  });

  testWidgets('C4 主账号：微信渠道卡渲染可写 Dropdown 与保存/解绑按钮', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    _stubBackend(api);

    await tester.pumpWidget(await _host(PluginCard(
      plugin: _plugin('wechat_ilink'),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.byType(DropdownButton<int>), findsOneWidget);
    expect(find.text('保存'), findsOneWidget);
    expect(find.text('解绑'), findsOneWidget);
  });

  testWidgets('C4 子账号：抖音渠道卡同样只读不崩（绑定区块 + 自定义配置共存）', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/douyin/bindings', {
      'items': [
        {
          'bot_account_id': 'default',
          'bot_label': '',
          'character_id': 102,
          'enabled': true,
          'updated_at': null,
        }
      ],
    });
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 102, 'name': '小橙'},
      ],
    });

    await tester.pumpWidget(await _host(PluginCard(
      plugin: _plugin('douyin_mcp'),
      isAdmin: false,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.text('小橙'), findsOneWidget);
    expect(find.byType(DropdownButton<int>), findsNothing);
  });
}
