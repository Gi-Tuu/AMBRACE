// 数字型开关展示（报告 P3-7 前端部分，2026-09-17）：验证数值型 flag 在开关页
// 渲染为只读数值（不出现 Switch），且老后端缺 type/value 时退化为原 Switch 行为。
// ignore_for_file: avoid_dynamic_calls
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/settings/feature_flags_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:provider/provider.dart';

/// 强制主账号，便于渲染开关页（SettingsProvider 默认非主账号）。
class _AdminSettingsProvider extends SettingsProvider {
  @override
  bool get isAdmin => true;
}

/// 返回给定 feature-flags 列表的 Dio 适配器（避免真实网络）。
class _MockFlagsAdapter implements HttpClientAdapter {
  _MockFlagsAdapter(this.flags);
  final List<Map<String, dynamic>> flags;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final body = jsonEncode({'flags': flags});
    return ResponseBody.fromString(
      body,
      200,
      headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
    );
  }

  @override
  void close({bool force = false}) {}
}

/// 等开关页完成异步加载并展开「其他高级开关」折叠区。
Future<void> _expandAdvancedGroup(tester) async {
  await tester.pumpAndSettle();
  expect(find.text('其他高级开关'), findsOneWidget,
      reason: '数值型 flag 应收在「其他高级开关」折叠区');
  await tester.tap(find.text('其他高级开关'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('数字型 flag 渲染为只读数值、不出现 Switch', (tester) async {
    ApiClient().dio.httpClientAdapter = _MockFlagsAdapter([
      {
        'key': 'domain_event_retention_days',
        'enabled': true,
        'source': 'db',
        'type': 'int',
        'value': 0,
      },
    ]);

    await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>(
      create: (_) => _AdminSettingsProvider(),
      child: MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        // 用 Material 包裹，提供 InkWell 所需的 Material 祖先（等同真实 Scaffold 环境）
        home: Material(child: const FeatureFlagsScreen(showAppBar: false)),
      ),
    ));

    await _expandAdvancedGroup(tester);

    // 数字型行不应渲染可切换的 Switch
    expect(tester.widgetList<Switch>(find.byType(Switch)), isEmpty,
        reason: '数字型 flag 不应渲染 Switch');

    // 只读说明与当前数值应展示（中文文案走 l10n）
    expect(find.text('该开关为数值型，暂不支持在 App 内热改'), findsOneWidget);
    expect(find.text('当前值：0'), findsOneWidget);
  });

  testWidgets('老后端缺 type/value 时退化为原 Switch 行为', (tester) async {
    // 仅下发 enabled，无 type/value（老后端契约）
    ApiClient().dio.httpClientAdapter = _MockFlagsAdapter([
      {
        'key': 'domain_event_retention_days',
        'enabled': true,
        'source': 'default',
      },
    ]);

    await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>(
      create: (_) => _AdminSettingsProvider(),
      child: MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Material(child: const FeatureFlagsScreen(showAppBar: false)),
      ),
    ));

    await _expandAdvancedGroup(tester);

    // 退化为原行为：出现 Switch，且不应出现只读数值说明
    expect(tester.widgetList<Switch>(find.byType(Switch)), isNotEmpty,
        reason: '缺 type/value 时应退化为 Switch');
    expect(find.text('该开关为数值型，暂不支持在 App 内热改'), findsNothing);
  });
}
