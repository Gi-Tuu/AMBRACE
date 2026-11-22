import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

/// 织库卡片只读客户端（列表概要；生成/去重/画布未做）。
class WeaveApi {
  WeaveApi(this.client);

  final AmbApiClient client;

  Future<Map<String, dynamic>> detail(int cardId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/weave/cards/',
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed weave detail');
    }
    return data.cast<String, dynamic>();
  }

  Future<List<Map<String, dynamic>>> cards({
    int? characterId,
    String domain = 'shared',
    String? q,
    int limit = 50,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.get('/api/v1/weave/cards',
        queryParameters: {
          if (characterId != null) 'character_id': characterId,
          'domain': domain,
          'limit': limit,
          if (q != null && q.trim().isNotEmpty) 'q': q.trim(),
        },
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['cards'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed weave cards');
    }
    return [
      for (final c in data['cards'] as List)
        if (c is Map) c.cast<String, dynamic>(),
    ];
  }

  /// 手动整理：把重要记忆编排成织库卡片（POST /weave/cards/generate）。
  /// LLM 批量 ≤20 条/卡，单次 ≤10 卡；domain=shared/private。
  Future<Map<String, dynamic>> generateCards(int characterId,
      {bool force = false, String domain = 'shared',
       CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/weave/cards/generate',
        queryParameters: {
          'character_id': characterId,
          'force': force,
          'domain': domain,
        },
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(
          kind: 'unknown', message: 'malformed weave generate');
    }
    return data.cast<String, dynamic>();
  }
}

