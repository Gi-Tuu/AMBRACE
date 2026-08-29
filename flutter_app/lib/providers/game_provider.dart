import 'dart:async';
import 'dart:convert';
import 'dart:ui' as ui;
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/character.dart';
import '../services/api_client.dart';

/// 游戏会话状态模型：封装 /api/v1/games 的状态拉取与轮询。
class GameProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  /// 目录 + 角色加载的「整体超时」兜底：后端异常/挂起时保证 loading 在有限时间内
  /// 结束并给出可读错误，绝不让游戏机面板无限转圈无报错。
  ///
  /// 测试可通过 [loadCatalogAndCharacters] 的 [timeout] 参数传一个短超时来验证兜底逻辑。
  static const Duration loadTimeout = Duration(seconds: 12);

  /// 超时兜底的可读中文文案（provider 非 Widget，无法取 l10n；UI 会再包一层 l10n）。
  static String get timeoutMessage {
    final code = ui.PlatformDispatcher.instance.locale.languageCode.toLowerCase();
    return code.startsWith('en')
        ? 'Loading timed out, check your network and try again'
        : '加载超时，请检查网络后再试';
  }

  /// 可注入的「目录加载」函数（默认走真实 API；测试可传假实现模拟挂起/失败）。
  late final Future<List<Map<String, dynamic>>> Function() _loadCatalog;

  /// 可注入的「角色加载」函数（默认走真实 API；测试可传假实现模拟挂起/失败）。
  late final Future<List<AICharacter>> Function() _loadCharacters;

  GameProvider({
    Future<List<Map<String, dynamic>>> Function()? loadCatalog,
    Future<List<AICharacter>> Function()? loadCharacters,
  }) {
    _loadCatalog = loadCatalog ?? _api.getGameCatalog;
    _loadCharacters = loadCharacters ?? _api.getCharacters;
  }

  List<Map<String, dynamic>> _catalog = [];
  List<AICharacter> _characters = [];
  Map<String, dynamic>? _game; // 当前会话的 `state` 对象
  int? _sessionId;
  int _userSeat = -1;
  bool _loading = false;
  bool _sending = false;
  String? _error;
  Timer? _pollTimer;
  WebSocketChannel? _ws;
  StreamSubscription? _wsSub;
  Timer? _wsReconnectTimer;
  bool _wsConnected = false;
  bool _disposed = false;

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

  /// 当前用户是否为「在场玩家」（非观战、未出局）——决定是否显示投降按钮。
  bool get iAmPlayer {
    final m = _game?['my'] as Map<String, dynamic>?;
    if (m == null) return false;
    return m['is_spectator'] != true && m['alive'] != false;
  }

  List<Map<String, dynamic>> get players =>
      ((_game?['players'] as List?) ?? const []).cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get events =>
      ((_game?['events'] as List?) ?? const []).cast<Map<String, dynamic>>();
  Map<String, dynamic>? get archive => _game?['archive'] as Map<String, dynamic>?;

  /// 加载游戏目录 + 候选 AI 角色。
  ///
  /// - 目录与角色并发拉取（比原串行更快返回）；
  /// - 整体加 [timeout]（默认 [loadTimeout]=12s）兜底：任一请求挂起/超时都必定抛错，
  ///   从而保证 [loading] 一定会在有限时间内结束，避免无限转圈无报错；
  /// - 失败时写可读的 [_error]，UI 据此展示错误与重试。
  Future<void> loadCatalogAndCharacters({Duration? timeout}) async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final limit = timeout ?? loadTimeout;
      final results = await Future.wait<Object?>([
        _loadCatalog(),
        _loadCharacters(),
      ], eagerError: true).timeout(limit);
      _catalog = (results[0] as List).cast<Map<String, dynamic>>();
      _characters = (results[1] as List).cast<AICharacter>();
    } catch (e) {
      _error = e is TimeoutException ? timeoutMessage : e.toString();
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
      // 行动后立即刷新状态：让用户看到自己的动作结果，同时拿到最新阶段
      await refreshState();
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

  /// 3 秒轮询（游戏房间用；WebSocket 连接时暂停，断开回退轮询）。
  void startPolling() {
    stopPolling();
    _connectWs();
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) async {
      if (_wsConnected) return; // WS 在线时不必轮询
      if (isPlaying) {
        await refreshState(seat: _userSeat >= 0 ? _userSeat : -1);
      }
    });
  }

  void stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
    _wsReconnectTimer?.cancel();
    _wsReconnectTimer = null;
    _disconnectWs();
  }

  /// 打开游戏 WebSocket（实时推送，收到 game_event 就刷新状态）。
  void _connectWs() {
    final sid = _sessionId;
    if (sid == null || _disposed) return;
    try {
      _disconnectWs();
    } catch (_) {}
    final base = _api.baseUrl;
    final wsBase = base.replaceFirst('http://', 'ws://').replaceFirst('https://', 'wss://');
    final uri = Uri.parse('$wsBase/api/v1/games/ws/$sid').replace(queryParameters: {
      if (_api.token.isNotEmpty) 'token': _api.token,
    });
    _ws = WebSocketChannel.connect(uri);
    _ws?.ready.then((_) {
      if (!_disposed) {
        _wsConnected = true;
        notifyListeners();
      }
    }).catchError((_) {});
    _wsSub = _ws!.stream.listen(
      (data) {
        if (data is String) {
          try {
            final json = jsonDecode(data) as Map<String, dynamic>;
            if (json['type'] == 'game_event') {
              refreshState();
            }
          } catch (_) {}
        }
      },
      onError: (_) => _onWsDisconnect(),
      onDone: _onWsDisconnect,
    );
  }

  void _onWsDisconnect() {
    _wsConnected = false;
    try {
      _ws?.sink.close();
    } catch (_) {}
    _wsSub?.cancel();
    _ws = null;
    if (_disposed || _sessionId == null) return;
    _wsReconnectTimer?.cancel();
    _wsReconnectTimer = Timer(const Duration(seconds: 3), _connectWs);
  }

  void _disconnectWs() {
    _wsConnected = false;
    try {
      _ws?.sink.close();
    } catch (_) {}
    _wsSub?.cancel();
    _wsSub = null;
    _ws = null;
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

  /// 投降：结束则刷新到结算视图；多人转观战则刷新为观战态。不主动 pop 页面。
  Future<bool> surrender() async {
    final sid = _sessionId;
    if (sid == null || _userSeat < 0) return false;
    _sending = true;
    _error = null;
    notifyListeners();
    try {
      final r = await _api.surrenderGameSession(sessionId: sid, seat: _userSeat);
      await refreshState();
      if (r['finished'] == true) await refreshState();
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
    _disposed = true;
    stopPolling();
    super.dispose();
  }
}
