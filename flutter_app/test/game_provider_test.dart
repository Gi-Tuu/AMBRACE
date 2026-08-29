import 'dart:async';
import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/providers/game_provider.dart';
import 'package:flutter_test/flutter_test.dart';

/// GameProvider 加载兜底测试（issue #1：游戏机面板无限转圈无报错）。
///
/// 通过构造函数注入假的目录/角色加载函数（默认走真实 ApiClient），
/// 用一个永不完成的 completer 模拟「请求挂起」，再传一个很短的 [timeout]，
/// 验证 loading 必定在有限时间内结束并写可读错误。真实运行用默认 12s 超时。
void main() {
  group('GameProvider.loadCatalogAndCharacters（并发 + 超时兜底）', () {
    test('请求挂起 → 限时内结束 loading 并写可读超时错误', () async {
      final provider = GameProvider(
        loadCatalog: () async => [
          {'game_type': 'truth_or_dare', 'name': '真心话大冒险', 'player_mode': 'single', 'min_players': 2, 'max_players': 2},
        ],
        loadCharacters: () async {
          // 模拟角色接口挂起（永不返回）
          await Completer<void>().future;
          return <AICharacter>[];
        },
      );

      var sawLoading = false;
      var sawError = false;
      provider.addListener(() {
        if (provider.loading) sawLoading = true;
        if (provider.error != null) sawError = true;
      });

      // 短超时，让测试快速跑完（生产用 GameProvider.loadTimeout=12s）
      await provider.loadCatalogAndCharacters(timeout: const Duration(milliseconds: 80));

      expect(provider.loading, isFalse, reason: '挂起请求必须让 loading 在限时内结束');
      expect(provider.error, GameProvider.timeoutMessage, reason: '应给出可读中文超时提示');
      expect(sawLoading, isTrue, reason: '加载期间应处于 loading 态');
      expect(sawError, isTrue, reason: '结束时应有错误可展示');
    });

    test('两个请求都成功 → loading 结束、目录/角色写入、无错误', () async {
      final provider = GameProvider(
        loadCatalog: () async => [
          {'game_type': 'g1', 'name': 'G1', 'player_mode': 'single', 'min_players': 1, 'max_players': 2},
        ],
        loadCharacters: () async => [AICharacter(id: 1, name: '小美')],
      );

      await provider.loadCatalogAndCharacters();

      expect(provider.loading, isFalse);
      expect(provider.error, isNull);
      expect(provider.catalog.length, 1);
      expect(provider.catalog.single['game_type'], 'g1');
      expect(provider.characters.length, 1);
      expect(provider.characters.single.id, 1);
    });

    test('请求失败 → loading 结束且 error 非空（不再无限转圈）', () async {
      final provider = GameProvider(
        loadCatalog: () => throw Exception('backend down'),
        loadCharacters: () async => <AICharacter>[],
      );

      await provider.loadCatalogAndCharacters();

      expect(provider.loading, isFalse);
      expect(provider.error, isNotNull);
      expect(provider.catalog, isEmpty);
    });
  });
}
