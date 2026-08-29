import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/home/profile_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/aurora_card.dart';
import 'package:ai_companion/widgets/empty_state.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P9 个人主页测试：
/// 用户卡渲染、功能入口 AuroraCard 分组、编辑表单展开/收起、错误态 + retry 恢复。
void _mockProfile(FakeApiAdapter api) {
  api.json('GET', '/api/v1/auth/profile', {
    'id': 1,
    'username': 'tester',
    'nickname': 'Tester',
    'bio': 'hello bio',
    'gender': 'other',
  });
}

void main() {
  late FakeApiAdapter api;
  late SettingsProvider settings;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    settings = SettingsProvider();
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    _mockProfile(api);
  });

  Widget app() => MultiProvider(
        providers: [ChangeNotifierProvider.value(value: settings)],
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const ProfileScreen(),
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(ProfileScreen)))!;

  testWidgets('user card renders nickname/ID/bio/edit entry', (tester) async {
    await settings.setNickname('Tester'); // 昵称由 provider 提供（登录态持久化）
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text('Tester'), findsOneWidget);
    expect(find.text('ID: tester'), findsOneWidget);
    expect(find.text('hello bio'), findsOneWidget);
    expect(find.text(l10n.profileEditInfo), findsOneWidget);
    expect(find.byType(AuroraCard), findsAtLeastNWidgets(1));
  });

  testWidgets('feature entry group renders rows', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    expect(find.text(l10n.profileMySpace), findsOneWidget);
    expect(find.text(l10n.profileMyState), findsOneWidget);
    expect(find.text(l10n.myDiary), findsOneWidget);
    expect(find.text(l10n.profileMyMemos), findsOneWidget);
  });

  testWidgets('edit form expands/collapses', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    final l10n = l10nOf(tester);

    await tester.tap(find.text(l10n.profileEditInfo));
    await tester.pumpAndSettle();
    // 表单展开：昵称输入框出现，编辑入口变收起
    expect(find.text(l10n.nickname), findsOneWidget);
    expect(find.text(l10n.collapse), findsOneWidget);

    await tester.tap(find.text(l10n.collapse));
    await tester.pumpAndSettle();
    expect(find.text(l10n.profileEditInfo), findsOneWidget);
  });

  testWidgets('error -> EmptyState loadFailed + retry recovers', (tester) async {
    // profile 走 ApiClient dio（已 mock）；这里换未 mock 的适配器模拟网络失败
    ApiClient().dio.httpClientAdapter = FakeApiAdapter(); // 空适配器：未匹配一律 404
    await tester.pumpWidget(app());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    final l10n = l10nOf(tester);

    expect(find.byType(EmptyState), findsOneWidget);
    expect(find.text(l10n.loadFailed), findsOneWidget);
    expect(find.text(l10n.retry), findsOneWidget);

    // 恢复 mock → 重试成功
    ApiClient().dio.httpClientAdapter = api;
    _mockProfile(api);
    _mockProfile(api);
    await tester.tap(find.text(l10n.retry));
    await tester.pumpAndSettle();
    expect(find.byType(EmptyState), findsNothing);
    expect(find.text('ID: tester'), findsOneWidget);
  });
}
