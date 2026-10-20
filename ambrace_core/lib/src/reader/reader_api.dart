/// 只读工作台（M1a-2）的共享只读 API：六个模块的 GET 读取，全部走同一个
/// [AmbApiClient]（身份/401 钩子/错误归一），不另造 Dio、不绕过认证、不新增任何写请求。
///
/// 契约以**真实后端实现**为准（`backend/app/api/{characters,memories,diary,moments,user_content}.py`
/// 与 `backend/app/schemas/*`），并由 `test/reader_contract_test.dart` 与
/// `backend/tests/test_pwa_reader_contract.py` 逐条守卫；要点：
/// - 只发 GET；query 参数与后端真实端点一致，**不伪造接口不支持的过滤**（如 moments 无
///   character_id 过滤）；
/// - 封套字段缺失/类型错误 ⇒ [ApiException]（kind='unknown'），坏响应不得伪装成空列表；
/// - 每个方法接受 [CancelToken]：切换角色/账号/退出/页面销毁时由壳**真实取消**在飞请求。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import 'reader_models.dart';

class ReaderApi {
  ReaderApi(this.client);

  final AmbApiClient client;

  Never _bad(String what, Object e) {
    throw ApiException(kind: 'unknown', message: 'malformed $what', cause: e);
  }

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    return _bad(what, 'expected object, got ${data.runtimeType}');
  }

  // ── 1. 角色详情 ────────────────────────────────────────────────
  Future<ReaderCharacter> getCharacter(int id, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/characters/$id', cancelToken: cancelToken);
    try {
      return ReaderCharacter.fromJson(_obj(r.data, 'character'));
    } on FormatException catch (e) {
      _bad('character', e);
    }
  }

  // ── 2. 记忆 ───────────────────────────────────────────────────
  /// 记忆列表。[readOnly] 默认 **true**：显式声明「无维护读取」——后端对该模式不物理
  /// 删除到期行、不 commit 维护、不清向量（查询/租户过滤/响应字段与默认路径一致）。
  /// 只读工作台的**首屏/筛选/续页**都必须走这个模式，不得偷偷回退到有维护的默认读。
  Future<ReaderMemoryPage> listMemories({
    int? characterId,
    String? memoryType,
    int skip = 0,
    int limit = 50,
    bool readOnly = true,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.get(
      '/api/v1/memories',
      queryParameters: {
        if (characterId != null) 'character_id': characterId,
        if (memoryType != null && memoryType.isNotEmpty) 'memory_type': memoryType,
        'skip': skip,
        'limit': limit,
        'read_only': readOnly,
      },
      cancelToken: cancelToken,
    );
    try {
      final j = _obj(r.data, 'memories page');
      final items = [
        for (final m in _reqListFor(j['memories'], 'memories page'))
          ReaderMemory.fromJson(_obj(m, 'memory')),
      ];
      return ReaderMemoryPage(items, (j['total'] as num?)?.toInt() ?? items.length);
    } on FormatException catch (e) {
      _bad('memories page', e);
    }
  }

  Future<ReaderMemory> getMemory(int id, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/memories/$id', cancelToken: cancelToken);
    try {
      return ReaderMemory.fromJson(_obj(r.data, 'memory'));
    } on FormatException catch (e) {
      _bad('memory', e);
    }
  }

  Future<List<ReaderMemoryChainNode>> getMemoryChain(int id, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/memories/$id/chain', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'memory chain');
      return [
        for (final n in _reqListFor(j['chain'], 'memory chain'))
          ReaderMemoryChainNode.fromJson(_obj(n, 'chain node')),
      ];
    } on FormatException catch (e) {
      _bad('memory chain', e);
    }
  }

  Future<ReaderFactHistory> getMemoryHistory(int id, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/memories/$id/history', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'memory history');
      final fact = j['fact'] is Map ? (j['fact'] as Map).cast<String, dynamic>() : const <String, dynamic>{};
      return ReaderFactHistory(
        factContent: fact['content'] is String ? fact['content'] as String : '',
        factStatus: fact['status'] is String ? fact['status'] as String : null,
        factEpistemic: fact['epistemic_status'] is String ? fact['epistemic_status'] as String : null,
        corrections: [
          for (final c in _reqListFor(j['corrections'], 'memory history'))
            ReaderFactCorrection.fromJson(_obj(c, 'fact correction')),
        ],
        truncated: j['truncated'] == true,
      );
    } on FormatException catch (e) {
      _bad('memory history', e);
    }
  }

  // ── 3. 角色日记（无分页：一次全量，客户端只分段展示） ─────────────
  Future<List<ReaderDiaryEntry>> listCharacterDiary(int characterId, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/diary/$characterId', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'character diary');
      return [
        for (final e in _reqListFor(j['entries'], 'character diary'))
          ReaderDiaryEntry.fromJson(_obj(e, 'diary entry')),
      ];
    } on FormatException catch (e) {
      _bad('character diary', e);
    }
  }

  /// 某月有日记的日期（`month` 格式 YYYY-MM-DD 的前缀 YYYY-MM；纯日期字符串，不做时区换算）。
  Future<List<String>> getDiaryDates(int characterId, String month, {CancelToken? cancelToken}) async {
    final r = await client.dio.get(
      '/api/v1/diary/$characterId/dates',
      queryParameters: {'month': month},
      cancelToken: cancelToken,
    );
    try {
      final j = _obj(r.data, 'diary dates');
      return [for (final d in _reqListFor(j['dates'], 'diary dates')) '$d'];
    } on FormatException catch (e) {
      _bad('diary dates', e);
    }
  }

  // ── 4. 动态（无 character_id 过滤——接口本身没有，不得伪造） ──────
  Future<ReaderMomentPage> listMoments({
    int skip = 0,
    int limit = 20,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.get(
      '/api/v1/moments',
      queryParameters: {'skip': skip, 'limit': limit},
      cancelToken: cancelToken,
    );
    try {
      final j = _obj(r.data, 'moments page');
      final items = [
        for (final m in _reqListFor(j['moments'], 'moments page'))
          ReaderMoment.fromJson(_obj(m, 'moment')),
      ];
      return ReaderMomentPage(items, (j['total'] as num?)?.toInt() ?? items.length);
    } on FormatException catch (e) {
      _bad('moments page', e);
    }
  }

  Future<List<ReaderComment>> getMomentComments(int momentId, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/moments/$momentId/comments', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'moment comments');
      return [
        for (final c in _reqListFor(j['comments'], 'moment comments'))
          ReaderComment.fromJson(_obj(c, 'comment')),
      ];
    } on FormatException catch (e) {
      _bad('moment comments', e);
    }
  }

  // ── 5. 个人备忘（无分页） ─────────────────────────────────────
  Future<List<ReaderMemo>> listMemos({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/user/memos', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'memos');
      return [
        for (final m in _reqListFor(j['memos'], 'memos'))
          ReaderMemo.fromJson(_obj(m, 'memo')),
      ];
    } on FormatException catch (e) {
      _bad('memos', e);
    }
  }

  // ── 6. 个人日记（无分页；与角色日记是不同数据） ──────────────────
  Future<List<ReaderUserDiary>> listUserDiaries({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/user/diaries', cancelToken: cancelToken);
    try {
      final j = _obj(r.data, 'user diaries');
      return [
        for (final d in _reqListFor(j['diaries'], 'user diaries'))
          ReaderUserDiary.fromJson(_obj(d, 'user diary')),
      ];
    } on FormatException catch (e) {
      _bad('user diaries', e);
    }
  }

  Future<ReaderUserDiary> getUserDiary(String diaryDate, {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/user/diaries/$diaryDate', cancelToken: cancelToken);
    try {
      return ReaderUserDiary.fromJson(_obj(r.data, 'user diary'));
    } on FormatException catch (e) {
      _bad('user diary', e);
    }
  }

  List<dynamic> _reqListFor(dynamic v, String what) {
    if (v is List) return v;
    throw FormatException('malformed $what: expected list, got ${v.runtimeType}');
  }
}
