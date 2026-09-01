import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/core/design_system.dart';

/// F7-a 设计系统测试：桶导出可解析、别名同实现、新件（GlassScaffold/StateBadge）渲染。
void main() {
  test('AppCard/AppSheet 别名指向同一实现（零双轨）', () {
    expect(AppCard, AuroraCard);
    expect(AppSheet, FloatingSheet);
  });

  testWidgets('GlassScaffold：真玻璃页骨架渲染标题与 body', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: GlassScaffold(
          title: '测试页',
          body: const Center(child: Text('内容')),
        ),
      ),
    );
    expect(find.text('测试页'), findsOneWidget);
    expect(find.text('内容'), findsOneWidget);
    // extendBodyBehindAppBar 生效（真玻璃前提）
    final scaffold = tester.widget<Scaffold>(find.byType(Scaffold));
    expect(scaffold.extendBodyBehindAppBar, isTrue);
    expect(find.byType(GlassBar), findsOneWidget);
  });

  testWidgets('StateBadge：渲染标签与可选图标', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Column(
            children: [
              StateBadge(
                label: '进行中',
                background: Colors.blue.withValues(alpha: 0.15),
                foreground: Colors.blue,
              ),
              StateBadge(
                label: '权威',
                icon: Icons.verified,
                background: Colors.green.withValues(alpha: 0.15),
                foreground: Colors.green,
              ),
            ],
          ),
        ),
      ),
    );
    expect(find.text('进行中'), findsOneWidget);
    expect(find.text('权威'), findsOneWidget);
    expect(find.byIcon(Icons.verified), findsOneWidget);
  });

  testWidgets('桶导出的 EmptyState/AuroraCard/SkeletonLine 可直接使用', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Column(
            children: [
              EmptyState(icon: Icons.inbox, title: '空空如也'),
              const AuroraCard(child: SizedBox(height: 8, width: 8)),
              const SkeletonLine(width: 40),
            ],
          ),
        ),
      ),
    );
    expect(find.text('空空如也'), findsOneWidget);
    expect(find.byType(AuroraCard), findsOneWidget);
    expect(find.byType(SkeletonLine), findsOneWidget);
  });
}
