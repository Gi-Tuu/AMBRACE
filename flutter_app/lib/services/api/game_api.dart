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

  // ── 内容源（#62 Phase 3：用户自定义 > 插件内容包 > 内置常量）──

  /// 列出某游戏当前生效内容：items = [{key, source: user|plugin|builtin, count, values}]。
  Future<List<Map<String, dynamic>>> getGameContent(String gameType) async {
    final r = await dio.get(
      '/api/v1/games/content',
      queryParameters: {'game_type': gameType},
    );
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }

  /// 写入/覆盖某游戏某 key 的用户自定义内容（整段替换）。
  Future<Map<String, dynamic>> putGameContent({
    required String gameType,
    required String key,
    required List<dynamic> values,
  }) async {
    final r = await dio.put('/api/v1/games/content', data: {
      'game_type': gameType,
      'key': key,
      'values': values,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 删除用户自定义内容（回落插件内容包 / 内置常量）。
  Future<Map<String, dynamic>> deleteGameContent({
    required String gameType,
    required String key,
  }) async {
    final r = await dio.delete('/api/v1/games/content/$gameType/$key');
    return r.data as Map<String, dynamic>;
  }

  // ── 成就与统计（#62 Phase 3）──

  /// 累计统计（character_id 为空 = 用户本人）。
  /// items = [{game_type, character_id, games_played, wins, losses, draws,
  /// aborted, total_rounds, win_rate, last_played_at}]。
  Future<List<Map<String, dynamic>>> getGameStats({
    int? characterId,
    String? gameType,
  }) async {
    final r = await dio.get(
      '/api/v1/games/stats',
      queryParameters: {
        if (characterId != null) 'character_id': characterId,
        if (gameType != null) 'game_type': gameType,
      },
    );
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }

  /// 成就列表（含未达成进度）。
  /// items = [{key, game_type, title, description, metric, target, progress,
  /// unlocked, unlocked_at}]。
  Future<List<Map<String, dynamic>>> getGameAchievements({int? characterId}) async {
    final r = await dio.get(
      '/api/v1/games/achievements',
      queryParameters: {
        if (characterId != null) 'character_id': characterId,
      },
    );
    return ((r.data as Map<String, dynamic>)['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }
}
