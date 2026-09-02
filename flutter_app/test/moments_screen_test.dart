import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/features/social/moments_screen.dart';
import 'package:ai_companion/features/social/moment_compose_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/empty_state.dart';
import 'package:ai_companion/widgets/moment_card.dart';

import 'fake_api_adapter.dart';

/// Aurora Phase 3 P1 朋友圈测试：
/// 玻璃顶栏、Aurora 卡片渲染、点赞（弹性动画/reduceMotion）、评论折叠、
/// 独立发布页（push 后自动 pop 并刷新）、EmptyState 空态/错误态、图片全屏查看。
void main() {
  late FakeApiAdapter api;
  late SettingsProvider settings;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
    settings = SettingsProvider();
    _mockMoments(api);
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
          home: const MomentsScreen(),
        ),
      );

  AppLocalizations l10nOf(WidgetTester tester) =>
      AppLocalizations.of(tester.element(find.byType(MomentsScreen)))!;

  group('AppBar', () {
    testWidgets('title + menu + view toggle + publish entry exist',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.text(l10n.moments), findsOneWidget);
      expect(find.byTooltip(l10n.menu), findsOneWidget);
      expect(find.byTooltip(l10n.dateArchive), findsOneWidget);
      expect(find.byTooltip(l10n.publishMoment), findsOneWidget);
    });
  });

  group('Moment cards', () {
    testWidgets('renders name/content/like count/comments', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      expect(find.text('Alpha'), findsOneWidget);
      expect(find.text('hello world'), findsOneWidget);
      expect(find.text('2'), findsOneWidget); // 点赞数
      expect(find.byType(MomentCard), findsOneWidget);
    });

    testWidgets('tap like calls API and updates count + bounce animation',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await tester.tap(find.byIcon(Icons.favorite_border));
      await tester.pumpAndSettle();

      expect(find.text('3'), findsOneWidget); // likes_count 2→3
      expect(find.byIcon(Icons.favorite), findsOneWidget); // 已点赞实心
      // 弹性动画容器在点赞瞬间挂载
      expect(find.byKey(const Key('likeHeartBounce')), findsOneWidget);
    });

    testWidgets('reduceMotion=true: like without bounce widget',
        (tester) async {
      await settings.setReduceMotion(true);
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await tester.tap(find.byIcon(Icons.favorite_border));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.favorite), findsOneWidget);
      expect(find.byKey(const Key('likeHeartBounce')), findsNothing);
    });

    testWidgets('comments collapse: >3 shows view-all, tap expands',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      // 只显示前 3 条（评论已改为 Text.rich，用 findRichText 匹配内容）
      expect(find.textContaining('comment 0', findRichText: true), findsOneWidget);
      expect(find.textContaining('comment 3', findRichText: true), findsNothing);
      expect(find.text(l10n.viewAllComments(5)), findsOneWidget);

      await tester.tap(find.text(l10n.viewAllComments(5)));
      await tester.pumpAndSettle();
      expect(find.textContaining('comment 3', findRichText: true), findsOneWidget);
      expect(find.textContaining('comment 4', findRichText: true), findsOneWidget);
      expect(find.text(l10n.collapse), findsOneWidget);
    });

    testWidgets('tap image opens fullscreen viewer dialog', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();

      await tester.tap(find.byType(MomentImageView));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsOneWidget);

      // 点击图片关闭
      await tester.tap(find.byType(InteractiveViewer));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsNothing);
    });
  });

  group('Publish (compose page)', () {
    testWidgets('entry pushes compose page; publish posts and pops to refresh',
        (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      // 点发布入口 → push 独立页（微信式：标题 + 输入框）
      await tester.tap(find.byTooltip(l10n.publishMoment));
      await tester.pumpAndSettle();

      expect(find.byType(MomentComposeScreen), findsOneWidget);
      expect(find.text(l10n.publishMoment), findsOneWidget); // AppBar 标题
      expect(find.byType(TextField), findsOneWidget);

      await tester.enterText(find.byType(TextField), 'new moment text');
      await tester.pump();
      await tester.tap(find.text(l10n.publish));
      await tester.pumpAndSettle();

      // 发布成功 → 自动 pop 回列表并刷新（POST /api/v1/moments/user 已在 setUp 中 mock）
      expect(find.byType(MomentComposeScreen), findsNothing);
      expect(find.text('hello world'), findsOneWidget); // 刷新后仍渲染列表
    });

    testWidgets('empty content publish does not submit', (tester) async {
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      await tester.tap(find.byTooltip(l10n.publishMoment));
      await tester.pumpAndSettle();
      expect(find.byType(MomentComposeScreen), findsOneWidget);

      // 空内容点「发布」：不调用 onSubmit，仍停留独立页
      await tester.tap(find.text(l10n.publish));
      await tester.pumpAndSettle();
      expect(find.byType(MomentComposeScreen), findsOneWidget);
      expect(find.text(l10n.publishMoment), findsWidgets); // 仍显示 AppBar/提示
    });
  });

  group('Empty / error state', () {
    testWidgets('empty list renders EmptyState with noMoments',
        (tester) async {
      api.json('GET', '/api/v1/moments', {'moments': []});
      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.noMoments), findsOneWidget);
    });

    testWidgets('load failure renders error EmptyState with retry',
        (tester) async {
      api.json('GET', '/api/v1/moments', {'detail': 'boom'}, status: 500);      await tester.pumpWidget(app());
      await tester.pumpAndSettle();
      final l10n = l10nOf(tester);

      expect(find.byType(EmptyState), findsOneWidget);
      expect(find.text(l10n.loadFailedCheckServer), findsOneWidget);
      expect(find.text(l10n.retry), findsOneWidget);
    });
  });
}

List<Map<String, Object?>> _sampleMoments() => [
      {
        'id': 1,
        'character_id': 1,
        'character_name': 'Alpha',
        'sender_type': 'ai',
        'content': 'hello world',
        'image_url': '/uploads/a.jpg',
        'likes_count': 2,
        'likers': ['u1', 'u2'],
        'liked_by_me': false,
        'created_at': '2026-08-28T02:00:00Z',
        'comments': [
          for (var i = 0; i < 5; i++)
            {
              'id': i + 1,
              'moment_id': 1,
              'sender_type': 'ai',
              'sender_name': 'user$i',
              'content': 'comment $i',
              'created_at': '2026-08-28T02:0$i:00Z',
            },
        ],
      },
    ];

void _mockMoments(FakeApiAdapter api) {
  api.json('POST', '/api/v1/moments/read', {});
  api.json('GET', '/api/v1/moments', {'moments': _sampleMoments()});
  api.json('POST', '/api/v1/moments/1/like', {'liked': true, 'likes_count': 3});
  api.json('POST', '/api/v1/moments/user', {'id': 99});
  api.json('GET', '/api/v1/moments/1/comments', {
    'comments': [
      for (var i = 0; i < 5; i++)
        {
          'id': i + 1,
          'moment_id': 1,
          'sender_type': 'ai',
          'sender_name': 'user$i',
          'content': 'comment $i',
          'created_at': '2026-08-28T02:0$i:00Z',
        },
    ],
  });
}
