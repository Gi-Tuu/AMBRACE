import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/utils/stage_text.dart';

void main() {
  test('剥离状态更新与 CAL_NOTE 标记（中文括号、无闭合）', () {
    const raw = '【状态更新：准备晚上一起做饭】\n\n【CAL_NOTE】2026-06-12 晚上和小遥一起做饭，计划做番茄牛腩和拍黄瓜';
    final st = StageText.parse(raw);
    expect(st.markers.length, 2);
    expect(st.markers[0], startsWith('状态更新：'));
    expect(st.markers[1], startsWith('日历：'));
    expect(st.markers[1], contains('番茄'));
    expect(st.text, isNot(contains('CAL_NOTE')));
    expect(st.text, isNot(contains('状态更新')));
  });

  test('英文闭合标签 CAL_NOTE', () {
    const raw = '记得开会 [CAL_NOTE]2026-08-15 上午10点开会[/CAL_NOTE]';
    final st = StageText.parse(raw);
    expect(st.markers, hasLength(1));
    expect(st.markers[0], startsWith('日历：'));
    expect(st.text, '记得开会');
  });

  test('MEMO 标记', () {
    const raw = '好，我记下了\n【MEMO】用户喜欢番茄牛腩';
    final st = StageText.parse(raw);
    expect(st.markers, hasLength(1));
    expect(st.markers[0], startsWith('备忘：'));
    expect(st.text, '好，我记下了');
  });

  test('动作括号剥离仍正常', () {
    const raw = '（摸摸头）别担心，我在呢。';
    final st = StageText.parse(raw);
    expect(st.text, '别担心，我在呢。');
    expect(st.above, isNotEmpty);
  });

  test('无标记消息不误伤', () {
    const raw = '今晚做什么菜？番茄牛腩可以。';
    final st = StageText.parse(raw);
    expect(st.markers, isEmpty);
    expect(st.text, '今晚做什么菜？番茄牛腩可以。');
  });
}

