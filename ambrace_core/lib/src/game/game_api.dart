import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

/// 游戏只读入口：目录与历史（对局创建/行动/WS 暂不做）。
class GameApi {
  GameApi(this.client);

  final AmbApiClient client;

  Future<List<Map<String, dynamic>>> catalog({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/games/catalog', cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['games'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed game catalog');
    }
    return [
      for (final g in data['games'] as List)
        if (g is Map) g.cast<String, dynamic>(),
    ];
  }

  Future<List<Map<String, dynamic>>> achievements(
      {int? characterId, CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/games/achievements',
        queryParameters: {if (characterId != null) 'character_id': characterId},
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed achievements');
    }
    return [
      for (final g in data['items'] as List)
        if (g is Map) g.cast<String, dynamic>(),
    ];
  }

  Future<List<Map<String, dynamic>>> history(
      {int limit = 30, CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/games/history',
        queryParameters: {'limit': limit}, cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed game history');
    }
    return [
      for (final g in data['items'] as List)
        if (g is Map) g.cast<String, dynamic>(),
    ];
  }

  // ── 游戏交互生命周期（2026-11-22 批 13）────────────────────────────

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    throw ApiException(
        kind: 'unknown', message: 'malformed $what', cause: data.runtimeType);
  }

  /// 新建对局：`POST /api/v1/games/sessions`。
  /// body: {game_type, player_ids, spectator_ids, user_as_player, group_id?}。
  Future<Map<String, dynamic>> createSession(Map<String, dynamic> body,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/games/sessions',
        data: body, cancelToken: cancelToken);
    return _obj(r.data, 'game create');
  }

  /// 游戏状态：`GET /api/v1/games/sessions/{sid}/state?seat=`（seat=-1＝观战）。
  Future<Map<String, dynamic>> gameState(int sid, {int seat = -1,
      CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/games/sessions/$sid/state',
        queryParameters: {'seat': seat}, cancelToken: cancelToken);
    return _obj(r.data, 'game state');
  }

  /// 玩家行动：`POST /api/v1/games/sessions/{sid}/action`
  /// body: {seat, action, payload}。
  Future<Map<String, dynamic>> gameAction(int sid,
      {required int seat, required String action,
      Map<String, dynamic>? payload, CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/games/sessions/$sid/action',
        data: {'seat': seat, 'action': action, 'payload': payload ?? {}},
        cancelToken: cancelToken);
    return _obj(r.data, 'game action');
  }

  /// 加入对局（AI 角色入场）：`POST /api/v1/games/sessions/{sid}/join`
  /// body: {character_id}。
  Future<Map<String, dynamic>> gameJoin(int sid,
      {int? characterId, CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/games/sessions/$sid/join',
        data: {if (characterId != null) 'character_id': characterId},
        cancelToken: cancelToken);
    return _obj(r.data, 'game join');
  }

  /// 认输：`POST /api/v1/games/sessions/{sid}/surrender`。
  Future<Map<String, dynamic>> gameSurrender(int sid,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/games/sessions/$sid/surrender',
        cancelToken: cancelToken);
    return _obj(r.data, 'game surrender');
  }

  /// 中止：`POST /api/v1/games/sessions/{sid}/abort`。
  Future<Map<String, dynamic>> gameAbort(int sid,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/games/sessions/$sid/abort',
        cancelToken: cancelToken);
    return _obj(r.data, 'game abort');
  }

  /// 对局归档（按日期分组）：`GET /api/v1/games/sessions/{sid}/archive`。
  Future<Map<String, dynamic>> gameArchive(int sid,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/games/sessions/$sid/archive',
        cancelToken: cancelToken);
    return _obj(r.data, 'game archive');
  }
}
