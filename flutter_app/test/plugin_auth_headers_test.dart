import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/services/api/plugins_api.dart';

void main() {
  group('pluginAuthHeaders (#65)', () {
    test('带 token 时返回 Bearer 鉴权头', () {
      final headers = pluginAuthHeaders('abc123');
      expect(headers, {'Authorization': 'Bearer abc123'});
    });

    test('空 token 返回空 map（不污染请求头）', () {
      expect(pluginAuthHeaders(''), isEmpty);
      expect(pluginAuthHeaders('   '), isEmpty);
    });

    test('始终只包含 Authorization 键（不泄漏其它头）', () {
      final headers = pluginAuthHeaders('tok');
      expect(headers.keys, ['Authorization']);
    });
  });
}
