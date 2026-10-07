import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/features/memory/memory_book_screen.dart';
import 'package:ai_companion/models/memory.dart';

/// 记忆内置顶摘要「确定性取条」回归（A33/A.3.2）：
/// 后端接口返回顺序不保证，旧写法靠遍历顺序隐式保留最后一条 ⇒ 显示哪条不确定。
/// 现由纯函数 [pickPinnedSummary] 定序：普通摘要优先于身份画像，组内取时间最新、同时间取 id 最大。
Memory _m({
  required int id,
  String memoryType = 'user_info',
  String? subType,
  String createdAt = '2026-10-01 00:00:00',
  String? updatedAt,
  bool isPinned = true,
}) =>
    Memory(
      id: id,
      memoryType: memoryType,
      subType: subType,
      content: '摘要#$id',
      importance: 3,
      createdAt: createdAt,
      updatedAt: updatedAt,
      isPinned: isPinned,
    );

void main() {
  test('乱序输入：取 updatedAt 最新的普通摘要，不看返回顺序', () {
    final list = [
      _m(id: 3, createdAt: '2026-10-05 08:00:00'), // 最新普通摘要（乱序放在最前）
      _m(id: 1, createdAt: '2026-10-01 08:00:00', updatedAt: '2026-10-02 08:00:00'),
      _m(id: 2, createdAt: '2026-10-04 08:00:00'),
    ];
    expect(pickPinnedSummary(list)!.id, 3);
    // 打乱顺序结果不变
    expect(pickPinnedSummary(list.reversed.toList())!.id, 3);
    expect(pickPinnedSummary([list[1], list[2], list[0]])!.id, 3);
  });

  test('identity 混入：身份画像不占「印象」位，普通摘要胜出（即使 identity 更新）', () {
    final list = [
      _m(id: 9, subType: 'identity', createdAt: '2026-10-09 00:00:00'),
      _m(id: 4, createdAt: '2026-10-03 00:00:00'),
    ];
    expect(pickPinnedSummary(list)!.id, 4);
    expect(pickPinnedSummary(list.reversed.toList())!.id, 4);
  });

  test('只有 identity 时回退选 identity（不留空位）', () {
    final list = [
      _m(id: 9, subType: 'identity', createdAt: '2026-10-01 00:00:00'),
      _m(id: 8, subType: 'identity', createdAt: '2026-10-06 00:00:00'),
    ];
    expect(pickPinnedSummary(list)!.id, 8);
  });

  test('updatedAt 缺失时退到 createdAt；同时间取 id 更大者（与后端 A31 口径一致）', () {
    expect(pickPinnedSummary([
      _m(id: 1, createdAt: '2026-10-02 00:00:00'),
      _m(id: 2, createdAt: '2026-10-01 00:00:00', updatedAt: '2026-10-03 00:00:00'),
    ])!.id, 2);
    final sameSecond = [
      _m(id: 11, createdAt: '2026-10-02 00:00:00.123'),
      _m(id: 12, createdAt: '2026-10-02 00:00:00'),
    ];
    expect(pickPinnedSummary(sameSecond)!.id, 12);
    expect(pickPinnedSummary(sameSecond.reversed.toList())!.id, 12);
  });

  test('T 分隔与空格分隔同口径（脏值/空串按 0 处理且不抛）', () {
    expect(pickPinnedSummary([
      _m(id: 1, createdAt: '2026-10-02T00:00:00'),
      _m(id: 2, createdAt: '2026-10-01 00:00:00'),
    ])!.id, 1);
    expect(pickPinnedSummary([
      _m(id: 1, createdAt: ''),
      _m(id: 2, createdAt: '乱码', updatedAt: ''),
      _m(id: 3, createdAt: '2026-09-30 00:00:00'),
    ])!.id, 3);
    // 全部脏值 ⇒ 退到「同时间取 id 最大」，仍确定
    expect(pickPinnedSummary([_m(id: 5, createdAt: ''), _m(id: 6, createdAt: '乱码')])!.id, 6);
  });

  test('无置顶/空列表返回 null', () {
    expect(pickPinnedSummary([]), isNull);
    expect(pickPinnedSummary([_m(id: 1, isPinned: false)]), isNull);
  });
}
