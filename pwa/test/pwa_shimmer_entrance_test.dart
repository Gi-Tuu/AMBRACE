// 批 1 守卫（2026-11-24）：加载骨架、入场动画、列表首帧不播。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:ambrace_pwa/features/reader/reader_state.dart';
import 'package:ambrace_pwa/l10n/reader_strings.dart';
import 'package:ambrace_pwa/widgets/pwa_entrance.dart';
import 'package:ambrace_pwa/widgets/pwa_shimmer.dart';
import 'package:ambrace_pwa/widgets/reader_async_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _app(Widget child) => MaterialApp(home: Scaffold(body: child));

void main() {
  testWidgets('加载态用骨架屏，不再是转圈', (tester) async {
    final loader = ReaderLoader<List<int>>(); // 默认即 loading
    await tester.pumpWidget(_app(ReaderAsyncView<List<int>>(
      loader: loader,
      rs: const ReaderStrings('zh'),
      cs: const CoreStrings('zh'),
      onRetry: () {},
      dataBuilder: (d) => Text('${d.length}'),
    )));
    expect(find.byType(PwaShimmer), findsWidgets);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('骨架流光在「减少动态效果」下静止（可 settle）', (tester) async {
    await tester.pumpWidget(_app(ReaderSkeletons.list(rows: 2)));
    await tester.pumpAndSettle();
    expect(find.byType(PwaShimmer), findsWidgets);
  });

  testWidgets('PwaEntrance：animate=false 直接呈现，不建动画层', (tester) async {
    await tester.pumpWidget(_app(const PwaEntrance(
      animate: false,
      child: Text('历史项'),
    )));
    expect(find.text('历史项'), findsOneWidget);
    expect(
      find.descendant(
          of: find.byType(PwaEntrance), matching: find.byType(AnimatedBuilder)),
      findsNothing,
    );
  });

  testWidgets('PwaEntrance：animate=true 从透明开始，结束落到终态', (tester) async {
    await tester.pumpWidget(_app(Builder(
      builder: (context) => MediaQuery(
        data: MediaQuery.of(context).copyWith(disableAnimations: false),
        child: const PwaEntrance(
          animate: true,
          child: Text('新项'),
        ),
      ),
    )));
    await tester.pump();
    // 动画进行中：外层有 Opacity/Transform 包装
    expect(find.byType(Opacity), findsWidgets);
    await tester.pumpAndSettle();
    expect(find.text('新项'), findsOneWidget);
  });

  testWidgets('PwaListEntrance：首帧 false（历史不弹），第二帧起 true（新项可弹）',
      (tester) async {
    final seen = <bool>[];
    await tester.pumpWidget(_app(PwaListEntrance(
      builder: (animate) {
        seen.add(animate);
        return const SizedBox();
      },
    )));
    expect(seen.first, isFalse, reason: '首帧必须不播动画');
    await tester.pump();
    expect(seen.last, isTrue, reason: '首帧后应切到可播状态');
  });
}
