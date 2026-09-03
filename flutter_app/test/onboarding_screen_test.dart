// Onboarding 首次引导 widget 测试（2026-08-24）：
// 用自定义 Dio adapter 模拟后端，验证 4 步引导的渲染、连接测试、步骤切换与返回。
// ignore_for_file: avoid_dynamic_calls
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/features/auth/onboarding_screen.dart';
import 'package:ai_companion/services/api_client.dart';

/// 按请求路径返回固定 JSON 的 Dio adapter（避免真实网络）。
class _MockAdapter implements HttpClientAdapter {
  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final path = options.path;
    String body;
    if (path.contains('/api/v1/system/health')) {
      body = '{"status":"ok"}';
    } else if (path.contains('/api/v1/auth/')) {
      body = '{"access_token":"test-token","user_id":1,"nickname":"小遥"}';
    } else if (path.contains('/api/v1/characters')) {
      body = '{"id":1,"name":"小遥"}';
    } else if (path.contains('/api/v1/system/api-config/test')) {
      body = '{"ok":true,"latency_ms":100,"model":"deepseek-chat","api_key_tail":"abc"}';
    } else {
      body = '{"ok":true}';
    }
    return ResponseBody.fromString(
      body,
      200,
      headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  late HttpClientAdapter origAdapter;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    origAdapter = ApiClient().dio.httpClientAdapter;
    ApiClient().dio.httpClientAdapter = _MockAdapter();
  });

  tearDown(() {
    ApiClient().dio.httpClientAdapter = origAdapter;
  });

  Future<void> pumpOnboarding(WidgetTester tester) async {
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider<SettingsProvider>(create: (_) => SettingsProvider()),
          ChangeNotifierProvider<ChatProvider>(create: (_) => ChatProvider()),
        ],
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: const [
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
            ...AppLocalizations.localizationsDelegates,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const OnboardingScreen(),
        ),
      ),
    );
    await tester.pump();
  }

  testWidgets('首屏：4 步步进指示 + 服务器地址输入与连接测试按钮', (tester) async {
    await pumpOnboarding(tester);

    // 步骤指示器
    expect(find.text('连接服务器'), findsOneWidget);
    expect(find.text('账号'), findsOneWidget);
    expect(find.text('创建角色'), findsOneWidget);
    expect(find.text('API Key'), findsOneWidget);

    // 步骤 1：服务器地址 + 连接测试
    expect(find.byType(TextField), findsOneWidget);
    expect(find.text('检测连接'), findsOneWidget);
    expect(find.text('下一步'), findsOneWidget);

    // 未连接时「下一步」不可用：直接点击不前进
    await tester.tap(find.text('下一步'));
    await tester.pumpAndSettle();
    expect(find.text('连接你的服务器'), findsOneWidget);
  });

  testWidgets('输入地址并检测连接成功后进入账号步骤，且可返回', (tester) async {
    await pumpOnboarding(tester);

    await tester.enterText(find.byType(TextField), 'http://127.0.0.1:8000');
    await tester.tap(find.text('检测连接'));
    await tester.pumpAndSettle();

    // 连接成功后「下一步」可用 → 进入账号步骤
    await tester.tap(find.text('下一步'));
    await tester.pumpAndSettle();
    expect(find.text('登录或注册账号'), findsOneWidget);
    expect(find.text('登录'), findsWidgets);

    // 返回上一步（步骤指示器左上角返回箭头）
    await tester.tap(find.byIcon(Icons.arrow_back));
    await tester.pumpAndSettle();
    expect(find.text('连接你的服务器'), findsOneWidget);
  });

  testWidgets('注册/登录切换切换账号表单字段', (tester) async {
    await pumpOnboarding(tester);

    await tester.enterText(find.byType(TextField), 'http://127.0.0.1:8000');
    await tester.tap(find.text('检测连接'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('下一步'));
    await tester.pumpAndSettle();

    // 默认登录：无昵称字段
    expect(find.text('昵称（可选）'), findsNothing);

    // 切到注册：出现昵称字段
    await tester.tap(find.text('注册'));
    await tester.pumpAndSettle();
    expect(find.text('昵称（可选）'), findsOneWidget);
  });

  testWidgets('账号登录成功后自动进入创建角色步骤', (tester) async {
    await pumpOnboarding(tester);

    await tester.enterText(find.byType(TextField), 'http://127.0.0.1:8000');
    await tester.tap(find.text('检测连接'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('下一步'));
    await tester.pumpAndSettle();

    // 用户名 + 密码，登录
    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), 'user');
    await tester.enterText(fields.at(1), 'pass');
    await tester.tap(find.text('登录').last);
    await tester.pumpAndSettle();

    expect(find.text('创建你的第一个 AI 角色'), findsOneWidget);
    expect(find.text('一句话性格'), findsOneWidget);
  });
}
