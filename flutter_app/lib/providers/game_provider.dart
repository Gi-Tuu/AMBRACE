import 'dart:async';
import 'package:flutter/foundation.dart';
import '../models/character.dart';
import '../services/api_client.dart';

/// 游戏会话状态模型：封装 /api/v1/games 的状态拉取与轮询。
class GameProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  List<Map<String, dynamic>> _catalog = [];
  List<AICharacter> _characters = [];
  Map<String, dynamic>? _game; // 当前会话的 `state` 对象
  int? _sessionId;
  int _userSeat = -1;
  bool _loading = false;
  bool _sending = false;
  String? _error;
  Timer? _pollTimer;

  List<Map<String, dynamic>> get catalog => _catalog;
  List<AICharacter> get characters => _characters;
  Map<String, dynamic>? get game => _game;
  int? get sessionId => _sessionId;
  int get userSeat => _userSeat;
  bool get loading => _loading;
  bool get sending => _sending;
  String? get error => _error;

  bool get hasSession => _sessionId != null;
  bool get isPlaying => _game?['status'] == 'playing';
  bool get isFinished => _game?['status'] == 'finished';
  bool get myTurn => _game?['my_turn'] == true;
  String? get myExpectedAction => _game?['my_expected_action'] as String?;
  Map<String, dynamic>? get my => _game?['my'] as Map<String, dynamic>?;
  List<Map<String, dynamic>> get players =>
      ((_game?['players'] as List?) ?? const []).cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get events =>
      ((_game?['events'] as List?) ?? const []).cast<Map<String, dynamic>>();
  Map<String, dynamic>? get archive => _game?['archive'] as Map<String, dynamic>?;

  /// 加载游戏目录 + 候选 AI 角色。
  Future<void> loadCatalogAndCharacters() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final catalog = await _api.getGameCatalog();
      final chars = await _api.getCharacters();
      _catalog = catalog;
      _characters = chars;
    } catch (e) {
      _error = e.toString();
    }
    _loading = false;
    notifyListeners();
  }

  /// 创建游戏会话。返回 state（含 my.seat）。
  Future<bool> createSession({
    required String gameType,
    required List<int> playerIds,
    required List<int> spectatorIds,
    required bool userAsPlayer,
    int? groupId,
  }) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final r = await _api.createGameSession(
        gameType: gameType,
        groupId: groupId,
        playerIds: playerIds,
        spectatorIds: spectatorIds,
        userAsPlayer: userAsPlayer,
      );
      _sessionId = (r['session_id'] as int?) ?? _sessionId;
      _game = (r['state'] as Map<String, dynamic>?) ?? {};
      _userSeat = ((_game?['my'] as Map<String, dynamic>?)?['seat'] as int?) ?? -1;
      _loading = false;
      notifyListeners();
      return true;
    } catch (e) {
      _error = e.toString();
      _loading = false;
      notifyListeners();
      return false;
    }
  }

  /// 发送玩家动作（当前用户座位）。
  Future<bool> sendAction({required String action, Map<String, dynamic> payload = const {}}) async {
    final sid = _sessionId;
    if (sid == null || _userSeat < 0) return false;
    _sending = true;
    _error = null;
    notifyListeners();
    try {
      final r = await _api.postGameAction(
        sessionId: sid, seat: _userSeat, action: action, payload: payload);
      if (r['finished'] == true) {
        await refreshState();
      }
      _sending = false;
      notifyListeners();
      return true;
    } catch (e) {
      _error = e.toString();
      _sending = false;
      notifyListeners();
      return false;
    }
  }

  /// 拉取最新状态（seat=-1 时用当前用户座位）。
  Future<void> refreshState({int? seat}) async {
    final sid = _sessionId;
    if (sid == null) return;
    try {
      final s = seat ?? _userSeat;
      _game = await _api.getGameState(sid, seat: s);
      notifyListeners();
      if (_game?['status'] != 'playing') {
        stopPolling();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  /// 3 秒轮询（游戏房间用）。
  void startPolling() {
    stopPolling();
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) async {
      if (isPlaying) {
        await refreshState(seat: _userSeat >= 0 ? _userSeat : -1);
      }
    });
  }

  void stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
  }

  /// 解散游戏（仅创建者）。
  Future<bool> abort() async {
    final sid = _sessionId;
    if (sid == null) return false;
    try {
      await _api.abortGameSession(sid);
      stopPolling();
      await refreshState();
      return true;
    } catch (e) {
      _error = e.toString();
      notifyListeners();
      return false;
    }
  }

  Future<Map<String, dynamic>?> loadArchive() async {
    final sid = _sessionId;
    if (sid == null || !isFinished) return archive;
    try {
      final r = await _api.getGameArchive(sid);
      return (r['archive'] as Map<String, dynamic>?) ?? archive;
    } catch (_) {
      return archive;
    }
  }

  void reset() {
    stopPolling();
    _game = null;
    _sessionId = null;
    _userSeat = -1;
    _error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    stopPolling();
    super.dispose();
  }
}
