import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/models/weave_card.dart';

void main() {
  group('WeaveCard.fromJson', () {
    test('列表级响应（无详情）', () {
      final card = WeaveCard.fromJson({
        'id': 1,
        'character_id': 3,
        'title': '第一次野餐',
        'summary': '和角色去公园野餐',
        'importance': 75.5,
        'memory_count': 20,
        'created_at': '2026-08-12T04:20:00',
      });
      expect(card.id, 1);
      expect(card.characterId, 3);
      expect(card.title, '第一次野餐');
      expect(card.memoryCount, 20);
      expect(card.importance, 75.5);
      expect(card.detail, isNull);
      expect(card.memories, isNull);
    });

    test('详情级响应（含 detail 与 memories）', () {
      final card = WeaveCard.fromJson({
        'id': 1,
        'character_id': 3,
        'character_name': '小遥',
        'title': 't',
        'summary': 's',
        'memory_count': 2,
        'created_at': '2026-08-12T04:20:00',
        'detail': {
          'time': '2026-08-10 下午',
          'weather': '晴',
          'location': '北京',
          'mood': '开心',
          'events': ['一起吃饭'],
          'details': ['点了火锅'],
        },
        'memories': [
          {
            'id': 9,
            'memory_type': 'event',
            'content': 'xxx',
            'importance_pct': 80.0,
            'source_label': '聊天',
            'source_icon': '💬',
            'created_at': '2026-08-10T04:20:00',
          },
        ],
      });
      expect(card.characterName, '小遥');
      expect(card.detail!.weather, '晴');
      expect(card.detail!.events, ['一起吃饭']);
      expect(card.detail!.details, ['点了火锅']);
      expect(card.memories!.length, 1);
      expect(card.memories!.first.sourceLabel, '聊天');
      expect(card.memories!.first.importancePct, 80.0);
    });

    test('缺字段兜底', () {
      final card = WeaveCard.fromJson({
        'id': 2,
        'title': 't',
        'summary': 's',
        'created_at': '',
      });
      expect(card.detail, isNull);
      expect(card.importance, 0);
      expect(card.characterId, 0);
      expect(card.memoryCount, 0);
    });
  });
}
