/// PWA M1a-5 角色创建/基础编辑 —— **共享 core 层写入 API**。
///
/// 只用 [AmbApiClient]（身份/401 钩子/错误归一复用），不另造 Dio、不绕过认证。
/// 契约以真实后端为准：
/// - `POST /api/v1/characters`（`CharacterCreate` → 201 `CharacterResponse`）；
/// - `GET  /api/v1/characters/{id}`（`CharacterResponse`，`lang` 走请求头 `lang`）；
/// - `PUT  /api/v1/characters/{id}`（`CharacterUpdate`，服务端 `exclude_unset=True`）。
///
/// 三条硬约束：
/// 1. **只提交白名单字段**，且编辑只发 `touched` 的字段（未改不覆盖、清空要显式发）；
/// 2. **归属核对**：创建返回 `user_id` 必为当前账号；编辑返回 `id` 必等于原目标、
///    `user_id` 必等于加载时原角色（家庭共享角色 owner 可能不是本人，不能用「本人」硬判）；
/// 3. **坏响应不得当成功**：封套缺 `id/user_id/name` 或类型错 ⇒ `ApiException(kind='unknown')`。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import 'character_editor_models.dart';

class CharacterEditorApi {
  CharacterEditorApi(this.client);

  final AmbApiClient client;

  Never _bad(String what, Object e) {
    throw ApiException(kind: 'unknown', message: 'malformed $what', cause: e);
  }

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    _bad(what, 'expected object, got ${data.runtimeType}');
  }

  CharacterEditorData _parse(
    dynamic data,
    String what, {
    int? expectedId,
    int? expectedUserId,
  }) {
    try {
      return CharacterEditorData.fromJson(
        _obj(data, what),
        expectedId: expectedId,
        expectedUserId: expectedUserId,
      );
    } on FormatException catch (e) {
      _bad(what, e);
    }
  }

  Options _langOptions(String? lang) {
    if (lang == null || lang.isEmpty) return Options();
    return Options(headers: <String, String>{'lang': lang});
  }

  /// **创建专用**请求头：`lang` 与 `Idempotency-Key` 合并。
  ///
  /// 只挂在本次创建 POST 上，绝不写进 `client.dio.options`（那会污染 GET/PUT/登录/新账号）。
  /// 键**原样发送**：不 trim、不把空串悄悄当无键（空键应由后端 422 显式拒绝）。
  Options _createOptions({String? lang, String? idempotencyKey}) {
    final headers = <String, String>{};
    if (lang != null && lang.isNotEmpty) headers['lang'] = lang;
    if (idempotencyKey != null) headers['Idempotency-Key'] = idempotencyKey;
    return headers.isEmpty ? Options() : Options(headers: headers);
  }

  /// 打开编辑前必须 GET 当前资料（不得用只读 DTO 的缺省值反写）。
  Future<CharacterEditorData> load(int id, {String? lang, CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/characters/$id',
        cancelToken: cancelToken, options: _langOptions(lang));
    return _parse(r.data, 'character', expectedId: id);
  }

  /// 创建角色。成功以**服务端稳定 id** 为准；[expectedUserId] 用于核对返回归属。
  ///
  /// [idempotencyKey]（可选）：同一次「创建意图」在重试/传输层重发时必须复用同一个键，
  /// 后端据此把重复请求收敛为同一个角色；不带则保持旧语义（每次可新建）。
  Future<CharacterEditorData> create(
    CharacterDraft draft, {
    int? expectedUserId,
    String? lang,
    String? idempotencyKey,
    CancelToken? cancelToken,
  }) async {
    final invalid = draft.validate();
    if (invalid != null) {
      throw ApiException(kind: 'client', message: invalid);
    }
    final r = await client.dio.post(
      '/api/v1/characters',
      data: draft.toCreatePayload(),
      cancelToken: cancelToken,
      options: _createOptions(lang: lang, idempotencyKey: idempotencyKey),
    );
    return _parse(r.data, 'created character', expectedUserId: expectedUserId);
  }

  /// 编辑角色。[original] 必须是**打开表单时 GET 到的原对象**（用于目标/归属核对与差异计算）。
  ///
  /// 无任何实际变化 ⇒ **不发写请求**，直接返回 [original]。
  ///
  /// ⚠️ 这**不是**双击/重复提交保护：同一个旧 [original] 上的两次非空 update 都会各发一次
  /// PUT（第二次仍以旧 original 计算差异）。「只写一次」必须由 UI 层**同步 saving 锁 +
  /// 请求快照**保证，不能依赖这里的 early-return。
  Future<CharacterEditorData> update(
    int id,
    CharacterDraft draft,
    CharacterEditorData original, {
    CancelToken? cancelToken,
    String? lang,
  }) async {
    if (id != original.id) {
      throw const ApiException(kind: 'client', message: 'target character mismatch');
    }
    final invalid = draft.validate();
    if (invalid != null) {
      throw ApiException(kind: 'client', message: invalid);
    }
    final payload = draft.toUpdatePayload(original);
    if (payload.isEmpty) return original;
    final r = await client.dio.put(
      '/api/v1/characters/$id',
      data: payload,
      cancelToken: cancelToken,
      options: _langOptions(lang),
    );
    return _parse(
      r.data,
      'updated character',
      expectedId: id,
      expectedUserId: original.userId,
    );
  }
}
