// MCP 工具分区（Phase 3）：Server 列表渲染 / 权限三档切换回调 / 添加表单校验。
// 用自定义 Dio HttpAdapter 模拟后端响应，验证 UI 渲染与回调行为。
// ignore_for_file: avoid_dynamic_calls
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/features/plugin/mcp_tools_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 记录一次 HTTP 请求（供断言），并按 path 路由返回预设 JSON。
class _MockDioAdapter implements HttpClientAdapter {
  _MockDioAdapter(this.handler);

  final Future<ResponseBody> Function(RequestOptions options) handler;
  final List<RequestOptions> requests = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    return handler(options);
  }

  @override
  void close({bool force = false}) {}
}

/// 从 RequestOptions 解析请求体 Map（data 可能是 String 或 Map）。
Map<String, dynamic> _bodyOf(RequestOptions o) {
  final d = o.data;
  if (d is Map) return Map<String, dynamic>.from(d);
  if (d is String && d.isNotEmpty) {
    try {
      return Map<String, dynamic>.from(jsonDecode(d) as Map);
    } catch (_) {}
  }
  return {};
}

ResponseBody _json(Object obj) => ResponseBody.fromString(
      jsonEncode(obj),
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );

const _serverList = {
  'total': 2,
  'items': [
    {
      'id': 1,
      'name': 'filesystem',
      'transport': 'stdio',
      'enabled': true,
      'auto_connect': true,
      'status': 'connected',
      'tools': 2,
      'last_error': null,
    },
    {
      'id': 2,
      'name': 'sse-server',
      'transport': 'sse',
      'enabled': false,
      'auto_connect': false,
      'status': 'disconnected',
      'tools': 0,
      'last_error': null,
    },
  ],
};

const _tools = {
  'total': 2,
  'items': [
    {
      'name': 'read_file',
      'description': '读取文件',
      'risk_level': 'low',
      'mode': 'allow',
      'scope': 'mcp_filesystem',
    },
    {
      'name': 'write_file',
      'description': '写文件',
      'risk_level': 'high',
      'mode': 'ask',
      'scope': 'mcp_filesystem',
    },
  ],
};

late HttpClientAdapter _origAdapter;

void main() {
  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({'is_admin': true});
    _origAdapter = ApiClient().dio.httpClientAdapter;
  });

  tearDown(() {
    ApiClient().dio.httpClientAdapter = _origAdapter;
  });

  Future<void> pumpScreen(WidgetTester tester, _MockDioAdapter adapter) async {
    ApiClient().dio.httpClientAdapter = adapter;
    final sp = SettingsProvider();
    await sp.load();
    await tester.pumpWidget(ChangeNotifierProvider<SettingsProvider>(
      create: (_) => sp,
      child: MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        locale: const Locale('zh'),
        home: const MCPToolsScreen(),
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));
  }

  testWidgets('Server 列表渲染：名称/传输徽标/状态灯/工具数', (tester) async {
    final adapter = _MockDioAdapter((o) async {
      if (o.method == 'GET' && o.path == '/api/v1/mcp/servers') {
        return _json(_serverList);
      }
      return _json({'items': [], 'total': 0});
    });
    await pumpScreen(tester, adapter);

    // 标题 + 两个 server 名称
    expect(find.text('MCP 工具'), findsOneWidget);
    expect(find.text('filesystem'), findsOneWidget);
    expect(find.text('sse-server'), findsOneWidget);

    // 传输徽标
    expect(find.text('stdio'), findsOneWidget);
    expect(find.text('SSE'), findsOneWidget);

    // 状态灯文案
    expect(find.text('已连接'), findsOneWidget);
    expect(find.text('未连接'), findsOneWidget);

    // 工具数：2 个工具 / 0 个工具
    expect(find.text('2 个工具'), findsOneWidget);
    expect(find.text('0 个工具'), findsOneWidget);
  });

  testWidgets('权限三档切换：点「禁止」回调后端 PUT /tools/{name}', (tester) async {
    final adapter = _MockDioAdapter((o) async {
      if (o.method == 'GET' && o.path == '/api/v1/mcp/servers') {
        // 单个已连接 server
        return _json({
          'total': 1,
          'items': [
            {'id': 1, 'name': 'filesystem', 'transport': 'stdio', 'enabled': true, 'status': 'connected', 'tools': 2, 'last_error': null},
          ],
        });
      }
      if (o.method == 'GET' && o.path == '/api/v1/mcp/servers/1/tools') {
        return _json(_tools);
      }
      if (o.method == 'PUT' && o.path.contains('/tools/write_file')) {
        return _json({'server_id': 1, 'tool_name': 'write_file', 'scope': 'mcp_filesystem', 'mode': 'forbid'});
      }
      return _json({'ok': true});
    });
    await pumpScreen(tester, adapter);

    // 展开工具列表
    await tester.tap(find.text('工具'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(find.text('read_file'), findsOneWidget);
    expect(find.text('write_file'), findsOneWidget);
    // 风险等级徽标
    expect(find.text('低风险'), findsOneWidget);
    expect(find.text('高风险'), findsOneWidget);

    // 点击 write_file（第 2 个工具）的「禁止」权限档
    await tester.tap(find.text('禁止').at(1));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    final put = adapter.requests.where((r) => r.method == 'PUT' && r.path.contains('/tools/write_file'));
    expect(put, hasLength(1), reason: '应以 PUT 写权限');
    expect(_bodyOf(put.first)['mode'], 'forbid');
  });

  testWidgets('添加表单校验：名称为空时阻止保存并提示', (tester) async {
    final adapter = _MockDioAdapter((o) async {
      if (o.method == 'GET' && o.path == '/api/v1/mcp/servers') {
        return _json({'items': [], 'total': 0});
      }
      return _json({'ok': true});
    });
    await pumpScreen(tester, adapter);

    // 空状态 + 添加按钮
    expect(find.text('还没有 MCP Server，点击右上角添加。'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();

    // 直接点保存（名称空）→ 显示校验错误且不发创建请求
    await tester.tap(find.text('保存'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    expect(find.text('名称不能为空'), findsOneWidget);
    final creates = adapter.requests.where((r) => r.method == 'POST' && r.path == '/api/v1/mcp/servers');
    expect(creates, isEmpty, reason: '名称为空不应发起创建请求');
  });
}
