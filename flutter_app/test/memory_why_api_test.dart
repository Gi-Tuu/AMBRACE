import 'package:ai_companion/services/api_client.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_api_adapter.dart';

/// RC2 G5：`GET /api/v1/memories/{id}/why` 的解析契约。
///
/// 重点：有来源完整解析、无来源是明确回答（不是失败）、null 不伪造、
/// 坏形状/错 memory_id 必须抛 [FormatException]。
void main() {
  late FakeApiAdapter api;

  setUp(() {
    api = FakeApiAdapter();
    ApiClient().dio.httpClientAdapter = api;
    ApiClient().configure(baseUrl: 'http://127.0.0.1:9', token: 'test-token');
  });

  Map<String, dynamic> source({Object? excerpt = '记得带材料', Object? sessionId = 31}) => {
        'kind': 'chat_message',
        'source_row_id': 7,
        'session_id': sessionId,
        'event_at': '2026-10-25T08:00:00',
        'message_id': 7,
        'message_excerpt': excerpt,
        'message_created_at': '2026-10-25T08:00:00',
        'future_unknown_field': {'x': 1},
      };

  test('有来源：完整解析来源事件、会话与摘录', () async {
    api.json('GET', '/api/v1/memories/7/why', {
      'status': 'ok',
      'memory_id': 7,
      'source_event_uid': 'mse-1',
      'has_source': true,
      'sources': [source()],
      'receipt_count': 2,
      'note': '',
    });
    final why = await ApiClient().getMemoryWhy(7);
    expect(why.memoryId, 7);
    expect(why.sourceEventUid, 'mse-1');
    expect(why.hasSource, isTrue);
    expect(why.receiptCount, 2);
    expect(why.sources, hasLength(1));
    final s = why.sources.single;
    expect(s.kind, 'chat_message');
    expect(s.sessionId, 31);
    expect(s.messageId, 7);
    expect(s.messageExcerpt, '记得带材料');
    expect(s.eventAt, '2026-10-25T08:00:00');
  });

  test('无来源是明确回答：has_source=false、sources 空、note 原样保留', () async {
    api.json('GET', '/api/v1/memories/7/why', {
      'status': 'ok',
      'memory_id': 7,
      'source_event_uid': null,
      'has_source': false,
      'sources': [],
      'receipt_count': 0,
      'note': 'no_source_event',
    });
    final why = await ApiClient().getMemoryWhy(7);
    expect(why.hasSource, isFalse);
    expect(why.sources, isEmpty);
    expect(why.note, 'no_source_event');
    expect(why.sourceEventUid, isNull);
  });

  test('可选字段为 null 正常保留（不伪造时间/摘录）', () async {
    api.json('GET', '/api/v1/memories/7/why', {
      'status': 'ok',
      'memory_id': 7,
      'source_event_uid': 'mse-1',
      'has_source': true,
      'sources': [source(excerpt: null, sessionId: null)],
      'receipt_count': 0,
      'note': '',
    });
    final why = await ApiClient().getMemoryWhy(7);
    expect(why.sources.single.messageExcerpt, isNull);
    expect(why.sources.single.sessionId, isNull);
  });

  test('memory_id 与请求不一致抛 FormatException', () async {
    api.json('GET', '/api/v1/memories/7/why', {
      'status': 'ok',
      'memory_id': 8,
      'has_source': false,
      'sources': [],
      'receipt_count': 0,
      'note': 'no_source_event',
    });
    await expectLater(ApiClient().getMemoryWhy(7), throwsFormatException);
  });

  test('坏形状：必需字段缺失抛 FormatException', () async {
    api.json('GET', '/api/v1/memories/7/why', {
      'status': 'ok',
      'memory_id': 7,
      'has_source': true,
      'sources': [
        {'session_id': 1},
      ],
      'receipt_count': 0,
      'note': '',
    });
    await expectLater(ApiClient().getMemoryWhy(7), throwsFormatException);
  });

  test('404 原样抛出（不冒充无来源）', () async {
    api.json('GET', '/api/v1/memories/7/why', {'detail': 'not found'}, status: 404);
    await expectLater(ApiClient().getMemoryWhy(7), throwsA(anything));
  });
}
