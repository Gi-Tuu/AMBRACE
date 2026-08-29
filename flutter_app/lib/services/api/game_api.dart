import '../api_client.dart';

/// GameApi：群聊游戏领域 API（/api/v1/games，Phase 1）
extension GameApi on ApiClient {
  /// 游戏目录（游戏机面板展示）。
  Future<List<Map<String, dynamic>>> getGameCatalog() async {
    final r = await dio.get('/api/v1/games/catalog');
    return ((r.data as Map<String, dynamic>)['games'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }

  /// 创建游戏会话。
  Future<Map<String, dynamic>> createGameSession({
    required String gameType,
    int? groupId,
    List<int> playerIds = const [],
    List<int> spectatorIds = const [],
    bool userAsPlayer = false,
  }) async {
    final r = await dio.post('/api/v1/games/sessions', data: {
      'game_type': gameType,
      if (groupId != null) 'group_id': groupId,
      'player_ids': playerIds,
      'spectator_ids': spectatorIds,
      'user_as_player': userAsPlayer,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 玩家动作（用户操作）。
  Future<Map<String, dynamic>> postGameAction({
    required int sessionId,
    required int seat,
    required String action,
    Map<String, dynamic> payload = const {},
  }) async {
    final r = await dio.post(
      '/api/v1/games/sessions/$sessionId/action',
      data: {'seat': seat, 'action': action, 'payload': payload},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 拉取游戏状态（seat=-1 = 观战视角）。
  Future<Map<String, dynamic>> getGameState(int sessionId, {int seat = -1}) async {
    final r = await dio.get(
      '/api/v1/games/sessions/$sessionId/state',
      queryParameters: {'seat': seat},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 中途加入观战。
  Future<Map<String, dynamic>> joinGameSession(int sessionId, {int? characterId}) async {
    final r = await dio.post(
      '/api/v1/games/sessions/$sessionId/join',
      data: {if (characterId != null) 'character_id': characterId},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 解散游戏（仅创建者）。
  Future<Map<String, dynamic>> abortGameSession(int sessionId) async {
    final r = await dio.post('/api/v1/games/sessions/$sessionId/abort');
    return r.data as Map<String, dynamic>;
  }

  /// 投降（仅在场玩家）。返回 {ok, finished, winner_side}。
  Future<Map<String, dynamic>> surrenderGameSession({
    required int sessionId,
    required int seat,
  }) async {
    final r = await dio.post(
      '/api/v1/games/sessions/$sessionId/surrender',
      data: {'seat': seat},
    );
    return r.data as Map<String, dynamic>;
  }

  /// 游乐手札。
  Future<Map<String, dynamic>> getGameArchive(int sessionId) async {
    final r = await dio.get('/api/v1/games/sessions/$sessionId/archive');
    return r.data as Map<String, dynamic>;
  }

  /// 游乐手札列表（倒序）。
  Future<List<Map<String, dynamic>>> getGameHistory({int limit = 20, String? gameType}) async {
    final r = await dio.get(
      '/api/v1/games/history',
      queryParameters: {
        'limit': limit,
        if (gameType != null) 'game_type': gameType,
      },
    );
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }
}
