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
import 'reader_extras_models.dart';

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
    String? search,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.get(
      '/api/v1/memories',
      queryParameters: {
        if (characterId != null) 'character_id': characterId,
        if (memoryType != null && memoryType.isNotEmpty) 'memory_type': memoryType,
        if (search != null && search.trim().isNotEmpty) 'search': search.trim(),
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

  Future<ReaderMemoryDerivations> getMemoryDerivations(int id,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/memories/$id/derivations',
        cancelToken: cancelToken);
    try {
      return ReaderMemoryDerivations.fromJson(_obj(r.data, 'derivations'));
    } on FormatException catch (e) {
      _bad('derivations', e);
    }
  }

  /// 来源链；404（功能未开放/记忆不存在）返回 null，由 UI 隐藏卡片。
  Future<ReaderMemoryWhy?> getMemoryWhy(int id, {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/memories/$id/why',
          cancelToken: cancelToken);
      return ReaderMemoryWhy.fromJson(_obj(r.data, 'why'));
    } on DioException catch (e) {
      if (e.response?.statusCode == 404) return null;
      rethrow;
    } on FormatException catch (e) {
      _bad('why', e);
    }
  }

  Future<ReaderMemoryLifecycle?> getMemoryLifecycle(int id,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/memories/$id/lifecycle',
          cancelToken: cancelToken);
      return ReaderMemoryLifecycle.fromJson(_obj(r.data, 'lifecycle'));
    } on DioException catch (e) {
      if (e.response?.statusCode == 404) return null;
      rethrow;
    } on FormatException catch (e) {
      _bad('lifecycle', e);
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

  Future<ReaderRelationship?> getRelationship(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/relationships',
          cancelToken: cancelToken);
      final data = _obj(r.data, 'relationships');
      for (final item in _reqListFor(data['relationships'], 'relationships')) {
        final rel = ReaderRelationship.fromJson(_mapObj(item, 'relationship'));
        if (rel.characterId == characterId) return rel;
      }
      return null;
    } on FormatException catch (e) {
      _bad('relationship', e);
    }
  }

  Future<List<ReaderWorldFact>> getWorldFacts(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/characters/$characterId/world-facts',
          cancelToken: cancelToken);
      final data = _obj(r.data, 'world facts');
      return [
        for (final item in _reqListFor(data['items'], 'world facts'))
          ReaderWorldFact.fromJson(_mapObj(item, 'world fact')),
      ];
    } on FormatException catch (e) {
      _bad('world facts', e);
    }
  }

  Future<List<ReaderLorebookEntry>> getLorebook(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/characters/$characterId/lorebook',
          cancelToken: cancelToken);
      final data = _obj(r.data, 'lorebook');
      return [
        for (final item in _reqListFor(data['items'], 'lorebook'))
          ReaderLorebookEntry.fromJson(_mapObj(item, 'lorebook entry')),
      ];
    } on FormatException catch (e) {
      _bad('lorebook', e);
    }
  }

  Future<Map<String, dynamic>> getAgentMind(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get(
          '/api/v1/characters/$characterId/agent-mind',
          cancelToken: cancelToken);
      return _obj(r.data, 'agent mind');
    } on FormatException catch (e) {
      _bad('agent mind', e);
    }
  }

  /// 角色级主动交流/生活/隐私配置（`GET /api/v1/scheduler/settings/{id}`）。
  ///
  /// 返回**原样对象**：字段集由后端 `ProactiveSettings` 决定（布尔开关 + frequency /
  /// dnd_start·end / life_intensity / reasoning_level 等）。本层不裁剪、不改名、不补默认值，
  /// 缺字段由 UI 按与 App 相同的默认解释。
  Future<Map<String, dynamic>> getProactiveSettings(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/scheduler/settings/$characterId',
          cancelToken: cancelToken);
      return _obj(r.data, 'settings');
    } on FormatException catch (e) {
      _bad('settings', e);
    }
  }

  /// 记忆检索轨迹（`GET /api/v1/characters/{id}/memory-trace`，只读调试数据）。
  /// 返回 `{character_id, traces:[{route,status,latency_ms,created_at,steps}]}` 原样对象。
  Future<Map<String, dynamic>> getMemoryTrace(int characterId,
      {int limit = 20, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get(
          '/api/v1/characters/$characterId/memory-trace',
          queryParameters: {'limit': limit},
          cancelToken: cancelToken);
      return _obj(r.data, 'memory trace');
    } on FormatException catch (e) {
      _bad('memory trace', e);
    }
  }

  Future<List<ReaderPet>> getPets({CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/pets', cancelToken: cancelToken);
      final data = _obj(r.data, 'pets');
      return [
        for (final item in _reqListFor(data['pets'], 'pets'))
          ReaderPet.fromJson(_mapObj(item, 'pet')),
      ];
    } on FormatException catch (e) {
      _bad('pets', e);
    }
  }

  Future<List<ReaderLifeEvent>> getLifeTimeline(int characterId,
      {int limit = 50, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/life/timeline',
          queryParameters: {'character_id': characterId, 'limit': limit},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life timeline');
      return [
        for (final item in _reqListFor(data['items'], 'life timeline'))
          ReaderLifeEvent.fromJson(_mapObj(item, 'life event')),
      ];
    } on FormatException catch (e) {
      _bad('life timeline', e);
    }
  }

  Future<List<ReaderLifeArtifact>> getLifeArtifacts(int characterId,
      {int limit = 50, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/life/artifacts',
          queryParameters: {'character_id': characterId, 'limit': limit},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life artifacts');
      return [
        for (final item in _reqListFor(data['items'], 'life artifacts'))
          ReaderLifeArtifact.fromJson(_mapObj(item, 'life artifact')),
      ];
    } on FormatException catch (e) {
      _bad('life artifacts', e);
    }
  }

  Future<List<ReaderLifeInterest>> getLifeInterests(int characterId,
      {int limit = 50, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/life/interests',
          queryParameters: {'character_id': characterId, 'limit': limit},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life interests');
      return [
        for (final item in _reqListFor(data['items'], 'life interests'))
          ReaderLifeInterest.fromJson(_mapObj(item, 'life interest')),
      ];
    } on FormatException catch (e) {
      _bad('life interests', e);
    }
  }

  Future<List<ReaderLifeGoal>> getLifeGoals(int characterId,
      {int limit = 50, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/life/goals',
          queryParameters: {'character_id': characterId, 'limit': limit},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life goals');
      return [
        for (final item in _reqListFor(data['items'], 'life goals'))
          ReaderLifeGoal.fromJson(_mapObj(item, 'life goal')),
      ];
    } on FormatException catch (e) {
      _bad('life goals', e);
    }
  }

  Future<List<ReaderLifeBrowsing>> getLifeBrowsing(int characterId,
      {int limit = 50, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/life/browsing',
          queryParameters: {'character_id': characterId, 'limit': limit},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life browsing');
      return [
        for (final item in _reqListFor(data['items'], 'life browsing'))
          ReaderLifeBrowsing.fromJson(_mapObj(item, 'life browsing entry')),
      ];
    } on FormatException catch (e) {
      _bad('life browsing', e);
    }
  }

  /// 角色八维状态（只读）：`GET /api/v1/characters/{id}/states` 原样对象。
  Future<Map<String, dynamic>> getCharacterStates(int characterId,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get('/api/v1/characters/$characterId/states',
          cancelToken: cancelToken);
      return _obj(r.data, 'character states');
    } on FormatException catch (e) {
      _bad('character states', e);
    }
  }

  /// 保存小家布局（仅家具位置/尺寸/朝向）：`PUT /api/v1/life-home/layout`。
  Future<void> saveLifeHomeLayout(int characterId, List<Map<String, dynamic>> rooms,
      {CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.put('/api/v1/life-home/layout',
          data: {'character_id': characterId, 'rooms': rooms},
          cancelToken: cancelToken);
      final data = _obj(r.data, 'life home layout');
      if (data['saved'] != true) {
        throw const FormatException('malformed life home layout: saved != true');
      }
    } on FormatException catch (e) {
      _bad('life home layout', e);
    }
  }

  /// 小家生活/宠物动作：`POST /api/v1/life-home/event`，原样返回。
  Future<Map<String, dynamic>> lifeHomeEvent(int characterId, String action,
      {int? petId, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.post('/api/v1/life-home/event',
          data: {
            'character_id': characterId,
            'action': action,
            if (petId != null) 'pet_id': petId,
          },
          cancelToken: cancelToken);
      return _obj(r.data, 'life home event');
    } on FormatException catch (e) {
      _bad('life home event', e);
    }
  }

  Future<Map<String, dynamic>> getStateHistory(int characterId,
      {int days = 30, CancelToken? cancelToken}) async {
    try {
      final r = await client.dio.get(
          '/api/v1/characters/$characterId/state-history',
          queryParameters: {'days': days},
          cancelToken: cancelToken);
      return _obj(r.data, 'state history');
    } on FormatException catch (e) {
      _bad('state history', e);
    }
  }

  /// 用户八维状态（只读）：`GET /api/v1/users/states` →
  /// `{mood,body_temp,desire,possessiveness,fatigue,sensitivity,comfort,anger}`。
  Future<Map<String, dynamic>> getUserStates({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/users/states',
        cancelToken: cancelToken);
    try {
      return _mapObj(r.data, 'user states');
    } on FormatException catch (e) {
      _bad('user states', e);
    }
  }

  /// Life 首页状态（只读）：`GET /api/v1/life-home/state?character_id=` →
  /// `{character_id,character_name,current_room,location,rooms,world,pets,
  ///   player:{stamina,mood,hunger},ai:{name,current_status},...}`。
  Future<Map<String, dynamic>> getLifeHomeState(int characterId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/life-home/state',
        queryParameters: {'character_id': characterId},
        cancelToken: cancelToken);
    try {
      return _mapObj(r.data, 'life home state');
    } on FormatException catch (e) {
      _bad('life home state', e);
    }
  }

  // ── 虚拟手机（phone-desktop 只读面，2026-11-22 批 10）──────────────────

  /// 桌面布局＋壁纸＋应用目录：`GET /api/v1/phone-desktop/layouts?character_id=`。
  Future<Map<String, dynamic>> getPhoneLayouts(int characterId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/phone-desktop/layouts',
        queryParameters: {'character_id': characterId},
        cancelToken: cancelToken);
    try {
      return _mapObj(r.data, 'phone layouts');
    } on FormatException catch (e) {
      _bad('phone layouts', e);
    }
  }

  /// 相册：`GET /api/v1/phone-desktop/photos` →
  /// `{ai_photos:[url], user_photos:[url]}`（uploads 相对路径）。
  Future<Map<String, dynamic>> getPhonePhotos({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/phone-desktop/photos',
        cancelToken: cancelToken);
    try {
      return _mapObj(r.data, 'phone photos');
    } on FormatException catch (e) {
      _bad('phone photos', e);
    }
  }

  /// 日历备注：`GET /api/v1/phone-desktop/calendar-notes?character_id=` →
  /// `{notes:[{id,date,text,author,status}]}`。
  Future<List<Map<String, dynamic>>> getPhoneCalendarNotes(int characterId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/phone-desktop/calendar-notes',
        queryParameters: {'character_id': characterId},
        cancelToken: cancelToken);
    try {
      final j = _mapObj(r.data, 'calendar notes');
      return ((j['notes'] as List?) ?? const [])
          .cast<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
    } on FormatException catch (e) {
      _bad('calendar notes', e);
    }
  }

  /// 备忘录：`GET /api/v1/phone-desktop/memos?character_id=` →
  /// `{items:[{id,text,author,status}]}`（倒序 ≤100）。
  Future<List<Map<String, dynamic>>> getPhoneMemos(int characterId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/phone-desktop/memos',
        queryParameters: {'character_id': characterId},
        cancelToken: cancelToken);
    try {
      final j = _mapObj(r.data, 'phone memos');
      return ((j['items'] as List?) ?? const [])
          .cast<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
    } on FormatException catch (e) {
      _bad('phone memos', e);
    }
  }

  // ── 虚拟手机写面（2026-11-22 批 11）─────────────────────────────────

  /// 保存桌面布局（\`PUT /api/v1/phone-desktop/layouts\`，全量覆盖 apps＋wallpaper）。
  Future<void> savePhoneLayouts(int characterId,
      {required List<Map<String, dynamic>> apps,
      String? wallpaper,
      CancelToken? cancelToken}) async {
    await client.dio.put('/api/v1/phone-desktop/layouts',
        queryParameters: {'character_id': characterId},
        data: {'apps': apps, 'wallpaper': wallpaper ?? ''},
        cancelToken: cancelToken);
  }

  /// 保存 AI 生成图片到相册（\`POST /photos/save\`，幂等）。
  Future<void> savePhoneAiPhoto(String filename, {CancelToken? cancelToken}) async {
    await client.dio.post('/api/v1/phone-desktop/photos/save',
        data: {'filename': filename}, cancelToken: cancelToken);
  }

  /// 删除相册图片（\`DELETE /photos?source=ai|user&filename=\`）。
  Future<void> deletePhonePhoto(String source, String filename,
      {CancelToken? cancelToken}) async {
    await client.dio.delete('/api/v1/phone-desktop/photos',
        queryParameters: {'source': source, 'filename': filename},
        cancelToken: cancelToken);
  }

  /// 新增日历备注（\`POST /calendar-notes\`；同日多备注前端合并展示）。
  Future<void> addPhoneCalendarNote(int characterId,
      {required String date, required String text,
      CancelToken? cancelToken}) async {
    await client.dio.post('/api/v1/phone-desktop/calendar-notes',
        data: {'character_id': characterId, 'date': date, 'text': text},
        cancelToken: cancelToken);
  }

  /// 新增备忘录（\`POST /memos\`；同内容去重刷新）。
  Future<void> addPhoneMemo(int characterId, {required String text,
      CancelToken? cancelToken}) async {
    await client.dio.post('/api/v1/phone-desktop/memos',
        data: {'character_id': characterId, 'text': text},
        cancelToken: cancelToken);
  }

  /// 角色时光线（只读）：`GET /api/v1/timeline/{characterId}` →
  /// `{character_id,character_name,days_known,first_chat_at,has_milestones,
  ///   items:[{date,type,title,desc,importance}]}`。
  Future<Map<String, dynamic>> getTimeline(int characterId,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/timeline/$characterId',
        cancelToken: cancelToken);
    try {
      return _mapObj(r.data, 'timeline');
    } on FormatException catch (e) {
      _bad('timeline', e);
    }
  }

  Map<String, dynamic> _mapObj(dynamic v, String what) {
    if (v is Map) return v.cast<String, dynamic>();
    throw FormatException('malformed $what: expected object, got ${v.runtimeType}');
  }

  List<dynamic> _reqListFor(dynamic v, String what) {
    if (v is List) return v;
    throw FormatException('malformed $what: expected list, got ${v.runtimeType}');
  }
}
