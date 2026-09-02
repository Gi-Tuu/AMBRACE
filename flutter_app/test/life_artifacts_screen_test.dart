import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/features/life/life_artifacts_screen.dart';
import 'package:ai_companion/services/api_client.dart';

import 'fake_api_adapter.dart';

/// AI 生活产物库（B1/B2/B3 回归）：
/// 1. 列表缩略图 content_url 相对路径补全为服务器地址（Image.network 用完整 URL）；
/// 2. 加载 500 显示 loadFailedCheckServer + retry，重试后列表恢复。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    _mockArtifacts(api);
  });

  Widget app() => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LifeArtifactsScreen(characterId: 1, characterName: 'Alpha'),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(LifeArtifactsScreen)))!;

  const baseUrl = 'http://127.0.0.1:9';
  const fullUrl = '$baseUrl/uploads/images/1/x.png';

  testWidgets('normal list: renders thumbnail with content_url resolved to server URL',
      (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();

    // 缩略图存在，且 Image.network 用的是补全后的完整服务器地址（B1）
    final imageFinder = find.byWidgetPredicate((w) {
      if (w is! Image) return false;
      final provider = w.image;
      return provider is NetworkImage && provider.url == fullUrl;
    });
    expect(imageFinder, findsOneWidget);
    // 唯一漏网断言：地址以服务器 baseUrl 开头（双保险）
    expect(fullUrl, startsWith(baseUrl));
  });

  testWidgets('load failed -> loadFailedCheckServer + retry recovers list', (tester) async {
    api.json('GET', '/api/v1/life/artifacts', {'detail': 'boom'}, status: 500);
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.loadFailedCheckServer), findsOneWidget);
    expect(find.text(l10n.retry), findsOneWidget);

    // 恢复 mock 后点重试，列表恢复
    _mockArtifacts(api);
    await tester.tap(find.text(l10n.retry));
    await tester.pumpAndSettle();
    expect(find.text(l10n.loadFailedCheckServer), findsNothing);

    final imageFinder = find.byWidgetPredicate((w) {
      if (w is! Image) return false;
      final provider = w.image;
      return provider is NetworkImage && provider.url == fullUrl;
    });
    expect(imageFinder, findsOneWidget);
  });
}

Map<String, Object?> _artifactData() => {
      'items': [
        {
          'type': 'image',
          'title': '夕阳速写',
          'content_url': '/uploads/images/1/x.png',
          'content_text': '',
          'created_at': '2026-08-28T02:00:00Z',
        },
      ],
    };

void _mockArtifacts(FakeApiAdapter api) {
  api.json('GET', '/api/v1/life/artifacts', _artifactData());
}
