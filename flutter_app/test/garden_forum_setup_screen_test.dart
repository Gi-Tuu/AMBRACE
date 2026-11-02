import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/plugin/garden_forum_setup_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/services/api_client.dart';
import 'fake_api_adapter.dart';

Widget _host(Widget child) => MaterialApp(
      locale: const Locale('zh'),
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: child,
    );

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient().dio.httpClientAdapter = FakeApiAdapter();
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  testWidgets('garden setup renders title and unbound state', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 13, 'name': 'Sam'},
      ],
    });
    api.json('GET', '/api/v1/plugins/garden_forum/account', {'bound': false});
    await tester.pumpWidget(_host(const GardenForumSetupScreen()));
    await tester.pumpAndSettle();
    expect(find.text('花园只读接入'), findsOneWidget);
    expect(find.text('未绑定'), findsOneWidget);
    expect(find.text('绑定'), findsOneWidget);
  });

  testWidgets('garden setup hides key, binds, browses, clears and unbinds', (tester) async {
    final api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    var bound = false;
    api.json('GET', '/api/v1/characters', {
      'characters': [
        {'id': 13, 'name': 'Sam'},
      ],
    });
    api.handle('GET', '/api/v1/plugins/garden_forum/account',
        (_) => FakeApiAdapter.body({'bound': bound, 'base_url': bound ? 'http://127.0.0.1:8080' : ''}));
    api.handle('POST', '/api/v1/plugins/garden_forum/account', (_) {
      bound = true;
      return FakeApiAdapter.body({'ok': true, 'bound': true});
    });
    api.handle('DELETE', '/api/v1/plugins/garden_forum/account', (_) {
      bound = false;
      return FakeApiAdapter.body({'ok': true, 'unbound': true});
    });
    api.json('POST', '/api/v1/plugins/garden_forum/user/readonly',
        {'ok': true, 'items': [], 'observed': true});
    api.json('GET', '/api/v1/plugins/garden_forum/user/observation',
        {'ok': true, 'observed': true, 'items': []});
    api.json('DELETE', '/api/v1/plugins/garden_forum/user/observation',
        {'ok': true, 'cleared': true});
    await tester.pumpWidget(_host(const GardenForumSetupScreen()));
    await tester.pumpAndSettle();
    final keyField = tester.widget<TextField>(find.byType(TextField).last);
    expect(keyField.obscureText, isTrue);
    await tester.enterText(find.byType(TextField).at(0), 'http://127.0.0.1:8080');
    await tester.enterText(find.byType(TextField).at(1), 'UK_TEST');
    await tester.tap(find.text('绑定'));
    await tester.pumpAndSettle();
    expect(api.requests.where((r) => r.method == 'POST' &&
        r.path == '/api/v1/plugins/garden_forum/account').length, 1);
    await tester.tap(find.text('只读浏览'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('清除观察'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('解绑'));
    await tester.pumpAndSettle();
    expect(api.requests.any((r) => r.method == 'DELETE' &&
        r.path == '/api/v1/plugins/garden_forum/account'), isTrue);
  });
}
