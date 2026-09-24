// A6 收口（2026-09-24）：「家庭管理员」页此前零覆盖，本文件补齐六类行为：
// 列表渲染 / 「我」与子账号标记 / 自己的开关禁用 / 非管理员页内占位 /
// PUT 成功后重拉列表 / PUT 失败回滚 + 提示（403 优先后端 detail 原文，空则回落本地文案）。
// 断言一律走 l10n getter（与仓库既有测试一致，测试内不写中文字面量）。
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/features/settings/account_admin_screen.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/widgets/home_drawer.dart';
import 'package:provider/provider.dart';

/// 强制家庭管理员（SettingsProvider 默认非管理员）。
class _AdminSettingsProvider extends SettingsProvider {
  @override
  bool get isAdmin => true;
}

/// 一个独立主账号（自己）+ 一个未授权子账号 + 一个已授权子账号。
List<Map<String, dynamic>> _family() => [
      {
        'id': 1,
        'username': 'papa',
        'nickname': 'Papa',
        'avatar_url': null,
        'is_admin': true,
        'parent_id': null,
        'is_self': true,
      },
      {
        'id': 2,
        'username': 'kid2',
        'nickname': 'Kid2',
        'avatar_url': null,
        'is_admin': false,
        'parent_id': 1,
        'is_self': false,
      },
      {
        'id': 3,
        'username': 'kid3',
        'nickname': 'Kid3',
        'avatar_url': null,
        'is_admin': true,
        'parent_id': 1,
        'is_self': false,
      },
    ];

/// 本地假后端：GET /admin/accounts 与 PUT /admin/accounts/{id}/admin，不发真实网络。
class _FakeAdminAdapter implements HttpClientAdapter {
  _FakeAdminAdapter({
    List<Map<String, dynamic>>? accounts,
    this.listStatus = 200,
    this.listDetail = '',
    this.putStatus = 200,
    this.putDetail = '',
  }) : accounts = accounts ?? _family();

  /// 服务端状态（PUT 成功后按后端规则改写，重拉列表才能看到）。
  List<Map<String, dynamic>> accounts;
  final int listStatus;
  final String listDetail;
  final int putStatus;
  final String putDetail;

  int listCalls = 0;
  int putCalls = 0;
  String? lastPutPath;
  Object? lastPutBody;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final path = options.path;
    if (options.method == 'GET' && path.endsWith('/admin/accounts')) {
      listCalls++;
      if (listStatus != 200) {
        return _json({'detail': listDetail}, listStatus);
      }
      return _json({'accounts': accounts}, 200);
    }
    if (options.method == 'PUT' && path.endsWith('/admin')) {
      putCalls++;
      lastPutPath = path;
      lastPutBody = options.data;
      if (putStatus != 200) {
        return _json({'detail': putDetail}, putStatus);
      }
      final id = int.parse(
          RegExp(r'/accounts/(\d+)/admin$').firstMatch(path)!.group(1)!);
      final enabled = (options.data as Map)['enabled'] as bool;
      accounts = [
        for (final a in accounts)
          if (a['id'] != id)
            a
          else
            {...a, 'is_admin': enabled},
      ];
      return _json({'ok': true, 'id': id, 'is_admin': enabled}, 200);
    }
    return _json({'detail': 'unexpected-request'}, 404);
  }

  ResponseBody _json(Object body, int status) => ResponseBody.fromString(
        jsonEncode(body),
        status,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]},
      );

  @override
  void close({bool force = false}) {}
}

void main() {
  Future<void> pumpScreen(WidgetTester tester, SettingsProvider provider) async {
    await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>(
      create: (_) => provider,
      child: MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const AccountAdminScreen(),
      ),
    ));
    await tester.pumpAndSettle();
  }

  AppLocalizations l10nOf(WidgetTester tester, Type widget) =>
      AppLocalizations.of(tester.element(find.byType(widget)))!;

  SwitchListTile tileFor(WidgetTester tester, String title) => tester.widget(
        find.ancestor(
          of: find.text(title),
          matching: find.byType(SwitchListTile),
        ),
      );

  testWidgets('列表渲染：三行账号 + 「我」/子账号标记 + 列表标题与说明', (tester) async {
    ApiClient().dio.httpClientAdapter = _FakeAdminAdapter();
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    expect(find.byType(SwitchListTile), findsNWidgets(3));
    // 「我」标记：自己的副标题带家庭管理员 + 我（不再显示用户名/编号）
    expect(find.text('Papa'), findsOneWidget);
    expect(find.text(l10n.accountMainLabel), findsOneWidget);
    // 子账号标记：角色 · 用户名 · #id
    expect(find.text('${l10n.accountSubLabel} · kid2 · #2'), findsOneWidget);
    expect(find.text(l10n.accountAdminListTitle), findsOneWidget);
    expect(find.text(l10n.accountAdminHint), findsOneWidget);
    // 开关值取服务端 is_admin
    expect(tileFor(tester, 'Kid2').value, isFalse);
    expect(tileFor(tester, 'Kid3').value, isTrue);
    // 自己的开关禁用（不能取消自己）
    expect(tileFor(tester, 'Papa').onChanged, isNull);
  });

  testWidgets('家庭工具卡（管理员）：页内有「账号关联」入口，抽屉那条已收走', (tester) async {
    ApiClient().dio.httpClientAdapter = _FakeAdminAdapter();
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    expect(find.text(l10n.accountAdminTools), findsOneWidget);
    expect(find.text(l10n.accountLinking), findsOneWidget);
    expect(find.text(l10n.accountLinkingHint), findsOneWidget);
  });

  testWidgets('非管理员进入：页内保留可读占位，不渲染列表', (tester) async {
    ApiClient().dio.httpClientAdapter =
        _FakeAdminAdapter(listStatus: 403, listDetail: 'server-refused');
    await pumpScreen(tester, SettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    expect(find.byType(SwitchListTile), findsNothing);
    expect(find.text(l10n.accountAdminOnly), findsOneWidget);
    expect(find.text(l10n.accountAdminOnlyHint), findsOneWidget);
    // 子账号：抽屉那条入口按 isAdmin 收走了，所以页内这条必须还在
    expect(find.text(l10n.accountAdminTools), findsOneWidget);
    expect(find.text(l10n.accountLinking), findsOneWidget);
  });

  testWidgets('PUT 成功：重拉一次列表并以服务端返回值渲染 + 已保存提示', (tester) async {
    final adapter = _FakeAdminAdapter();
    ApiClient().dio.httpClientAdapter = adapter;
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);
    expect(adapter.listCalls, 1, reason: '首屏加载一次');

    await tester.tap(find.text('Kid2'));
    await tester.pumpAndSettle();

    expect(adapter.putCalls, 1);
    expect(adapter.lastPutPath, '/api/v1/admin/accounts/2/admin');
    expect((adapter.lastPutBody as Map)['enabled'], isTrue);
    expect(adapter.listCalls, 2, reason: 'PUT 成功后必须重新拉一次列表');
    expect(tileFor(tester, 'Kid2').value, isTrue);
    expect(find.text(l10n.accountAdminSaved), findsOneWidget);
  });

  testWidgets('PUT 失败 403：回滚乐观状态 + 优先展示后端 detail 原文', (tester) async {
    final adapter = _FakeAdminAdapter(putStatus: 403, putDetail: 'server-denied');
    ApiClient().dio.httpClientAdapter = adapter;
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    await tester.tap(find.text('Kid3'));
    await tester.pumpAndSettle();

    expect(adapter.putCalls, 1);
    expect(tileFor(tester, 'Kid3').value, isTrue, reason: '失败必须回滚到原值');
    expect(find.text('server-denied'), findsOneWidget,
        reason: '403 有 detail 时展示后端原文');
    expect(find.text(l10n.accountAdminOnly), findsNothing);
    expect(adapter.listCalls, 1, reason: '失败不触发重拉');
  });

  testWidgets('PUT 失败 403 且 detail 为空：回落本地文案', (tester) async {
    final adapter = _FakeAdminAdapter(putStatus: 403, putDetail: '');
    ApiClient().dio.httpClientAdapter = adapter;
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    await tester.tap(find.text('Kid3'));
    await tester.pumpAndSettle();

    expect(tileFor(tester, 'Kid3').value, isTrue, reason: '失败必须回滚到原值');
    expect(find.text(l10n.accountAdminOnly), findsOneWidget);
  });

  testWidgets('PUT 失败 400：回滚并展示后端 detail 原文', (tester) async {
    final adapter = _FakeAdminAdapter(putStatus: 400, putDetail: 'keep-one-admin');
    ApiClient().dio.httpClientAdapter = adapter;
    await pumpScreen(tester, _AdminSettingsProvider());
    final l10n = l10nOf(tester, AccountAdminScreen);

    await tester.tap(find.text('Kid3'));
    await tester.pumpAndSettle();

    expect(tileFor(tester, 'Kid3').value, isTrue);
    expect(find.text('keep-one-admin'), findsOneWidget);
    expect(find.text(l10n.accountAdminFailed), findsNothing);
  });

  testWidgets('抽屉入口：管理员显示、非管理员隐藏（双保险其一）', (tester) async {
    tester.view.physicalSize = const Size(600, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    Future<bool> hasEntry(SettingsProvider provider) async {
      await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>(
        create: (_) => provider,
        child: MaterialApp(
          locale: const Locale('zh'),
          localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: Material(child: HomeDrawer(settings: provider, onClose: () {})),
        ),
      ));
      await tester.pumpAndSettle();
      final l10n = AppLocalizations.of(tester.element(find.byType(HomeDrawer)))!;
      final found = find.text(l10n.accountAdminTitle).evaluate().isNotEmpty;
      expect(find.text(l10n.permissionManagementTitle), findsOneWidget,
          reason: '原「权限管理」入口不受影响');
      await tester.pumpWidget(const SizedBox());
      return found;
    }

    expect(await hasEntry(_AdminSettingsProvider()), isTrue);
    expect(await hasEntry(SettingsProvider()), isFalse);
  });
}
