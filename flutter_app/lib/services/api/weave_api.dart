import 'package:dio/dio.dart';

import '../../models/weave_card.dart';
import '../api_client.dart';

/// WeaveApi：织库领域 API（extension 挂到 ApiClient）
extension WeaveApi on ApiClient {
  /// 织库卡片列表（概要级；characterId 为空 = 全部角色）
  Future<({List<WeaveCard> cards, int total})> getWeaveCards({
    int? characterId,
    int skip = 0,
    int limit = 50,
    String? q,
    String domain = 'shared',
  }) async {
    final params = <String, dynamic>{
      'skip': skip,
      'limit': limit,
      'domain': domain,
      if (characterId != null) 'character_id': characterId,
      if (q != null && q.isNotEmpty) 'q': q,
    };
    final r = await dio.get('/api/v1/weave/cards', queryParameters: params);
    final data = r.data as Map<String, dynamic>;
    return (
      cards: (data['cards'] as List)
          .map((j) => WeaveCard.fromJson(j as Map<String, dynamic>))
          .toList(),
      total: data['total'] as int? ?? 0,
    );
  }

  /// 卡片详情（含结构化详情 + 参与记忆清单）
  Future<WeaveCard> getWeaveCardDetail(int id) async {
    final r = await dio.get('/api/v1/weave/cards/$id');
    return WeaveCard.fromJson(r.data as Map<String, dynamic>);
  }

  /// 删除卡片（仅删卡片，不删记忆）
  Future<void> deleteWeaveCard(int id) async {
    await dio.delete('/api/v1/weave/cards/$id');
  }

  /// 手动整理生成卡片（LLM 批量编排，可能耗时较长）
  Future<Map<String, dynamic>> generateWeaveCards({
    int? characterId,
    bool force = false,
    String domain = 'shared',
  }) async {
    final r = await dio.post(
      '/api/v1/weave/cards/generate',
      queryParameters: {
        'domain': domain,
        if (characterId != null) 'character_id': characterId,
        if (force) 'force': 'true',
      },
      options: Options(receiveTimeout: const Duration(seconds: 180)),
    );
    return r.data as Map<String, dynamic>;
  }

  /// 画布图数据（节点 + 关联边）
  Future<Map<String, dynamic>> getWeaveGraph({int? characterId, String domain = 'shared'}) async {
    final r = await dio.get(
      '/api/v1/weave/graph',
      queryParameters: {
        'domain': domain,
        if (characterId != null) 'character_id': characterId,
      },
    );
    return r.data as Map<String, dynamic>;
  }

  /// 织库卡片查重（返回重复组预览，不修改数据）
  /// → {groups: [{keeper: {...}, duplicates: [...]}], total_groups}
  Future<Map<String, dynamic>> dedupWeaveCardsCheck({String domain = 'shared'}) async {
    final r = await dio.post('/api/v1/weave/cards/dedup-check', queryParameters: {'domain': domain});
    return r.data as Map<String, dynamic>;
  }

  /// 织库卡片去重（每组保留信息最全一张，删除其余，不删原始记忆）
  /// → {groups, removed}
  Future<Map<String, dynamic>> dedupWeaveCards({String domain = 'shared'}) async {
    final r = await dio.post('/api/v1/weave/cards/dedup', queryParameters: {'domain': domain});
    return r.data as Map<String, dynamic>;
  }
}
