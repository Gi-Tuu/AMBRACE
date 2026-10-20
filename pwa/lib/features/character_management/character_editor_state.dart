// PWA M1a-5 · 角色编辑状态机（可单测，不依赖具体 UI）。
//
// 承载 HANDOFF §4 的写入纪律：
// - **同步 saving 锁**：重入直接返回（UI 的 enabled:false 是体验，锁才是最后一道）；
// - **快照校验**：保存/加载返回时校验 authGen / routeGen，晚到回调不得污染新账号/新对象；
// - **未知结果**：POST/PUT 超时/断线 ⇒ `unconfirmed=true`，提示只 GET 核对，不自动重试；
// - **错误归一为文案 key**（不硬编码中文），由 UI 用 [CharacterEditorStrings] 本地化；
// - **dirty**：保存成功后草稿重置，dirty 清空；失败保留原文。
import 'dart:math';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

/// 判断「本次异步结果是否已过期」：壳在回调里比较当前 authGen/routeGen。
typedef StaleCheck = bool Function(int authGen, int routeGen);

class CharacterEditorController extends ChangeNotifier {
  CharacterEditorController({
    required this.api,
    required this.authGen,
    required this.routeGen,
    required this.isStale,
    this.characterId,
    this.currentUserId,
    String Function()? keyFactory,
  }) : _keyFactory = keyFactory ?? newIdempotencyKey;

  final CharacterEditorApi api;
  final int authGen;
  final int routeGen;
  final StaleCheck isStale;

  /// 编辑模式的目标 id；null ⇒ 创建模式。
  final int? characterId;

  /// 当前账号 id（创建后核对返回 owner；身份未就绪时为 null ⇒ 禁止提交）。
  final int? currentUserId;

  /// 幂等键工厂（测试 seam）。生产用 [newIdempotencyKey]；**不对外暴露可变 key**。
  final String Function() _keyFactory;

  /// 创建意图的稳定幂等键：首次创建保存时生成，此后**重试/超时/核对/失败再保存都不换**；
  /// 只有「创建成功」或「显式放弃/身份作废」才丢弃。仅存内存，不入 storage/URL/日志。
  String? _createKey;

  /// 128 位强随机 → 32 位小写 hex（不依赖浏览器 crypto / dart:html，VM 测试同样可用）。
  static String newIdempotencyKey() {
    final rnd = Random.secure();
    final bytes = List<int>.generate(16, (_) => rnd.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  /// 只读：供 UI/测试确认「同一意图键未变」（不提供 setter）。
  String? get createIdempotencyKey => _createKey;

  CharacterEditorData? _original;
  CharacterDraft? _draft;
  bool _loading = false;
  bool _saving = false;
  String? _errorKey;
  bool _unconfirmed = false;
  String? _verifyResult;
  bool _disposed = false;

  CharacterEditorData? get original => _original;
  CharacterDraft? get draft => _draft;
  bool get loading => _loading;
  bool get saving => _saving;
  String? get errorKey => _errorKey;
  bool get unconfirmed => _unconfirmed;

  /// 只读核对的结果：`verifySaved` / `verifyNotSaved`（null＝未核对）。
  String? get verifyResult => _verifyResult;
  bool get isEdit => characterId != null;
  bool get isDirty => _draft?.isDirty ?? false;

  /// 身份未就绪 / 加载中 / 保存中 / **结果未确认** / 无草稿 ⇒ 一律禁提交。
  ///
  /// 「未确认」必须挡住再次 POST/PUT：未知结果可能已在服务端落库，重试会造成重复创建。
  bool get _inactive => _disposed || isStale(authGen, routeGen);

  bool get canSubmit =>
      !_inactive &&
      !_loading &&
      !_saving &&
      !_unconfirmed &&
      _draft != null &&
      (!isEdit ? currentUserId != null : _original != null);

  void _safeNotify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _createKey = null;
    super.dispose();
  }

  /// 创建模式：空草稿，**不发请求**；编辑模式：先 GET 当前资料再填（不得用只读 DTO 缺省值反写）。
  Future<void> load({CancelToken? cancelToken}) async {
    if (_inactive) return;
    if (!isEdit) {
      _draft = CharacterDraft.blank();
      _errorKey = null;
      _safeNotify();
      return;
    }
    _loading = true;
    _errorKey = null;
    _safeNotify();
    try {
      if (_inactive) return;
      final d = await api.load(characterId!, cancelToken: cancelToken);
      if (_inactive) return; // 晚到丢弃
      _original = d;
      _draft = d.toDraft();
    } on Object {
      if (_inactive) return;
      _errorKey = 'loadFailed';
    } finally {
      _loading = false;
      _safeNotify();
    }
  }

  /// 保存（创建或编辑）。返回保存后的服务端对象；重入/校验失败/失败/过期一律 null。
  ///
  /// **注意**：这里只保证「重入不写」；同一个旧 original 的两次串行 save 仍会写两次——
  /// 真正的一次写由本方法的 [_saving] 锁 + UI 禁用共同保证。
  Future<CharacterEditorData?> save({CancelToken? cancelToken}) async {
    // 未确认结果期间**禁止**任何写：未知的 POST/PUT 可能已在服务端生效。
    if (_inactive || _saving || _unconfirmed) return null;
    final d = _draft;
    if (d == null) return null;

    final invalid = d.validate();
    if (invalid != null) {
      _errorKey = invalid;
      _safeNotify();
      return null;
    }
    if (!isEdit && currentUserId == null) {
      _errorKey = 'unauthorized'; // 身份未就绪：禁提交
      _safeNotify();
      return null;
    }

    _saving = true;
    _errorKey = null;
    _unconfirmed = false;
    _safeNotify();
    try {
      // 发请求**前**再查一次代次：旧 controller/旧账号的回调不得用新 client 的 token 发写。
      if (_inactive) return null;
      final CharacterEditorData saved;
      if (isEdit) {
        saved = await api.update(characterId!, d, _original!, cancelToken: cancelToken);
      } else {
        // 一次创建意图 = 一个稳定键：重试复用，成功后才丢弃。
        final key = _createKey ??= _keyFactory();
        saved = await api.create(
          d,
          expectedUserId: currentUserId,
          idempotencyKey: key,
          cancelToken: cancelToken,
        );
      }
      if (_inactive) return null;
      if (!isEdit) _createKey = null; // 已成功：本次意图结束
      _original = saved;
      _draft = saved.toDraft(); // 成功后 dirty 清空
      return saved;
    } on DioException catch (e) {
      if (_inactive) return null;
      _mapError(e.error);
      return null;
    } on ApiException catch (e) {
      if (_inactive) return null;
      _mapError(e);
      return null;
    } finally {
      _saving = false;
      _safeNotify();
    }
  }

  /// 只读核对（**绝不发写**）。
  ///
  /// - 编辑模式：GET 原 id，比较服务端当前值是否已包含本地改动 → [verifyResult]；
  ///   **不改草稿**（「确认失败前保留原文」），只解除未确认，让用户明确决定后续；
  /// - 创建模式：无 id 可查 ⇒ 由 UI 刷新真实列表让用户识别，**不按同名猜成功**。
  Future<void> verifyByGet({CancelToken? cancelToken}) async {
    if (_inactive || !isEdit) return;
    try {
      final server = await api.load(characterId!, cancelToken: cancelToken);
      if (_inactive) return;
      _verifyResult = _serverHasDraft(server) ? 'verifySaved' : 'verifyNotSaved';
      _unconfirmed = false;
      _errorKey = null;
      _safeNotify();
    } on Object {
      if (_inactive) return;
      _verifyResult = 'verifyFailed';
      _safeNotify();
    }
  }

  bool _serverHasDraft(CharacterEditorData server) {
    final d = _draft;
    if (d == null || !d.isDirty) return true;
    for (final key in d.touched) {
      final local = d[key];
      final Object? remote = switch (key) {
        'name' => server.name,
        'bio' => server.bio,
        'personality' => server.personality,
        'chat_style' => server.chatStyle,
        'greeting_message' => server.greetingMessage,
        'gender' => server.gender,
        'appearance' => server.appearance,
        'talkativeness' => server.talkativeness,
        'talkativeness_locked' => server.talkativenessLocked,
        'timezone_offset' => server.timezoneOffset,
        _ => null,
      };
      if (key == 'name') {
        if ((local as String? ?? '').trim() != server.name.trim()) return false;
      } else if (local != remote) {
        return false;
      }
    }
    return true;
  }

  void _mapError(Object? raw) {    if (raw is ApiException) {
      if (raw.kind == 'timeout' || raw.kind == 'network' || raw.kind == 'unknown') {
        // R4：`unknown` = 写已发出但封套/归属不可信（如 201 返回 {ok:true}）。
        // 它**可能已在服务端落库**，必须按未确认处理并禁止重复写；
        // 本地校验（client）与明确的 400/403/404 仍可更正后重试。
        _unconfirmed = true;
        _errorKey = 'resultUnconfirmed';
        return;
      }
      if (raw.kind == 'unauthorized') {
        _errorKey = 'unauthorized';
        return;
      }
      if (raw.statusCode == 403) {
        _errorKey = 'forbidden';
        return;
      }
      if (raw.statusCode == 404) {
        _errorKey = 'notFound';
        return;
      }
      if (raw.kind == 'client') {
        _errorKey = raw.message; // 本地校验 key（name_required 等）
        return;
      }
    }
    _errorKey = 'saveFailed';
  }
}
