// App 添加未绑定 ClawBot（2026-09-06）：查看可用 bot → 选角色 → 绑定 → 行出现。
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

Map<String, dynamic> _plugin() => {
      'name': 'wechat_ilink',
      'version': '1.0.0',
      'description': '',
      'enabled': true,
      'config': <String, dynamic>{},
    };

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient().dio.httpClientAdapter = FakeApiAdapter();
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  testWidgets('添加未绑定 bot：查看→选角色→绑定→新 bot 行出现', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    // 绑定列表当前只有主力 bot；可用列表有备用 bot
    api.json('GET', '/api/v1/channels/wechat/bindings', {
      'items': [
        {'bot_account_id': 'botA-im-bot', 'bot_label': '', 'character_id': 101, 'enabled': true, 'updated_at': null},
      ],
    });
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 101, 'name': '小慧'},
        {'id': 102, 'name': '小橙'},
      ],
    });
    api.json('GET', '/api/v1/plugins/wechat_ilink/available-bots', {
      'items': [
        {'bot_account_id': 'botB-im-bot', 'ilink_user_id_masked': '4ced***im.w', 'saved_at': 't'},
      ],
    });
    api.json('POST', '/api/v1/plugins/wechat_ilink/bind-available', {'ok': true, 'bot_account_id': 'botB-im-bot'});
    // 绑定后刷新：两行
    api.json('GET', '/api/v1/channels/wechat/bindings', {
      'items': [
        {'bot_account_id': 'botA-im-bot', 'bot_label': '', 'character_id': 101, 'enabled': true, 'updated_at': null},
        {'bot_account_id': 'botB-im-bot', 'bot_label': '', 'character_id': 102, 'enabled': true, 'updated_at': null},
      ],
    });

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin(),
      isAdmin: true,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    // 点「查看可添加的 bot」→ 列表出现备用 bot
    await tester.tap(find.text('查看可添加的 bot'));
    await tester.pumpAndSettle();
    expect(find.text('botB-im-bot'), findsOneWidget);

    // 选角色「小橙」并绑定
    await tester.tap(find.byType(DropdownButton<int>).last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('小橙').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('绑定').last);
    await tester.pumpAndSettle();

    // 新 bot 行出现（绑定列表刷新后两行 Dropdown；botB 无 label → 「默认」）
    expect(find.byType(DropdownButton<int>), findsNWidgets(2));
    final reqs = api.requests.where((r) => r.path.contains('bind-available')).toList();
    expect(reqs, isNotEmpty);
  });

  testWidgets('子账号不显示「查看可添加的 bot」入口', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/channels/wechat/bindings', {
      'items': [
        {'bot_account_id': 'botA-im-bot', 'bot_label': '', 'character_id': 101, 'enabled': true, 'updated_at': null},
      ],
    });
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 101, 'name': '小慧'},
      ],
    });

    await tester.pumpWidget(_host(PluginCard(
      plugin: _plugin(),
      isAdmin: false,
      onChanged: () {},
      onToast: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(find.text('查看可添加的 bot'), findsNothing);
  });
}
