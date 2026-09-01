import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/main.dart';

void main() {
  testWidgets('App should build', (WidgetTester tester) async {
    await tester.pumpWidget(const AICompanionApp());
    // 应用名：中文界面「拥爱」，英文界面「AMBRACE」
    final texts = [find.text('拥爱'), find.text('AMBRACE')];
    expect(texts.any((f) => f.evaluate().isNotEmpty), isTrue);
  });
}
