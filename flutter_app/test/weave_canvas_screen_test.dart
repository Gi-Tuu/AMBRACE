// 织网 2.5D 加强（2026-08-23）：>80 节点仍走球面（聚类）布局且球感参数生效。
// 用自定义 Dio HttpAdapter 模拟后端图数据，验证画布 CustomPaint 走聚类而非 2D 螺旋。
// ignore_for_file: avoid_dynamic_calls
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/screens/weave/weave_canvas_screen.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 返回织网图数据的 JSON 文本。
String _graphJson(int nodeCount, {int edgeCount = 0}) {
  final nodes = [
    for (var i = 0; i < nodeCount; i++)
      {
        'id': i + 1,
        'character_id': i % 8,
        'character_ids': [i % 8],
        'character_name': '角色${i % 8}',
        'title': '节点${i + 1}',
        'summary': '摘要${i + 1}',
        'importance': (i % 100).toDouble(),
        'mood': '',
        'created_at': '',
        'life_type': '',
        'hot_tags': const <String>[],
      },
  ];
  final edges = [
    for (var i = 0; i < edgeCount; i++)
      {
        'source': (i % nodeCount) + 1,
        'target': ((i + 1) % nodeCount) + 1,
        'strength': 0.5 + (i % 5) * 0.1,
      },
  ];
  final characters = [
    for (var i = 0; i < 8; i++) {'id': i, 'name': '角色$i'},
  ];
  return jsonEncode({'nodes': nodes, 'edges': edges, 'characters': characters});
}

/// 恒返回给定 JSON 的 Dio 适配器（避免真实网络）。
class _MockDioAdapter implements HttpClientAdapter {
  _MockDioAdapter(this.responseBody);
  final String responseBody;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    return ResponseBody.fromString(
      responseBody,
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

void main() {
  late HttpClientAdapter origAdapter;

  setUp(() {
    origAdapter = ApiClient().dio.httpClientAdapter;
  });

  tearDown(() {
    ApiClient().dio.httpClientAdapter = origAdapter;
  });

  testWidgets('>80 节点仍走球面（聚类）布局，且球感参数生效', (tester) async {
    ApiClient().dio.httpClientAdapter = _MockDioAdapter(_graphJson(100, edgeCount: 20));

    await tester.pumpWidget(MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const WeaveCanvasScreen(),
    ));

    // 等待异步图数据返回并结束 loading
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    await tester.pump(const Duration(milliseconds: 50));

    // 找到织网画布的 CustomPaint（loading 的 CircularProgressIndicator 也含 CustomPaint，需按 painter 类型区分）
    CustomPaint? canvasPaint;
    for (final cp in tester.widgetList<CustomPaint>(find.byType(CustomPaint))) {
      if ((cp.painter?.runtimeType.toString() ?? '').contains('WeaveCanvas')) {
        canvasPaint = cp;
        break;
      }
    }
    expect(canvasPaint, isNotNull, reason: '织网画布 CustomPaint 应存在');

    final painter = canvasPaint!.painter! as dynamic;
    final bubbles = painter.bubbles as List;
    final nodes = painter.nodes as List;

    // >80 走球面聚类：应生成聚类泡（而非被拍平 2D 螺旋 → 无泡且 depth=0）
    expect(bubbles, isNotEmpty, reason: '>80 节点应生成聚类泡而非 2D 螺旋');
    expect(bubbles.length, lessThanOrEqualTo(70), reason: '聚类泡数量目标 ≤70');

    // 球感参数生效：聚类泡的深度/缩放随球面位置变化（非全等、非 0）
    final depths = bubbles.map((b) => (b as dynamic).depth as double).toList();
    final scales = bubbles.map((b) => (b as dynamic).scale as double).toList();
    expect(depths.toSet().length, greaterThan(1), reason: '深度应随球面位置变化');
    expect(scales.toSet().length, greaterThan(1), reason: '缩放应随深度（近大远小）变化');
    expect(nodes, isEmpty, reason: '聚类泡默认收起，成员节点应在展开后才显示');

    // 清理：替换为空 widget 触发 dispose，停掉抖动画时钟，避免遗留 ticker
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('≤80 节点走直接球面投影（可见节点非空且深度/缩放变化）', (tester) async {
    ApiClient().dio.httpClientAdapter = _MockDioAdapter(_graphJson(20));

    await tester.pumpWidget(MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const WeaveCanvasScreen(),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    CustomPaint? canvasPaint;
    for (final cp in tester.widgetList<CustomPaint>(find.byType(CustomPaint))) {
      if ((cp.painter?.runtimeType.toString() ?? '').contains('WeaveCanvas')) {
        canvasPaint = cp;
        break;
      }
    }
    expect(canvasPaint, isNotNull);

    final painter = canvasPaint!.painter! as dynamic;
    final nodes = painter.nodes as List;
    expect(nodes, isNotEmpty, reason: '≤80 节点应逐个球面投影');
    final depths = nodes.map((n) => (n as dynamic).depth as double).toList();
    final scales = nodes.map((n) => (n as dynamic).scale as double).toList();
    expect(depths.toSet().length, greaterThan(1), reason: '近大远小：深度应变化');
    expect(scales.toSet().length, greaterThan(1), reason: '透视增强：缩放应随深度变化');

    await tester.pumpWidget(const SizedBox());
  });
}
