/// 个人内容编辑（M1a-3）的**写**客户端：备忘录与个人日记的增/改/删，
/// 以及「本人 id」的权威来源（`GET /api/v1/auth/profile`）。
///
/// 为什么另起一个类而不扩 `ReaderApi`：只读工作台（ReaderApi）的承诺是「只发 GET」，
/// 给它塞写方法会把只读面变成混合写客户端（契约与守卫都会失去意义）。
///
/// 纪律：
/// - 全部经同一个 [AmbApiClient]（身份验签 / 401 钩子 / 错误归一），不另造 Dio、不绕过认证；
/// - 备忘录：`POST /api/v1/user/memos`（201）、`PUT /api/v1/user/memos/{id}`、
///   `DELETE /api/v1/user/memos/{id}`；
/// - 个人日记：`POST /api/v1/user/diaries`（body `diary_date`+`content`，后端按
///   **actor + 日期** upsert，201）、`DELETE /api/v1/user/diaries/{数值 id}`（**不是日期**）；
/// - 读仍走 `ReaderApi`（GET），本类不重复实现；
/// - 契约以 `backend/app/api/user_content.py` 与 `backend/app/auth/router.py` 为准。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import '../reader/reader_models.dart';

class EditorApi {
  EditorApi(this.client);

  final AmbApiClient client;

  Never _bad(String what, Object e) =>
      throw ApiException(kind: 'unknown', message: 'malformed $what', cause: e);

  Map<String, dynamic> _obj(dynamic data, String what) {
    if (data is Map) return data.cast<String, dynamic>();
    return _bad(what, 'expected object, got ${data.runtimeType}');
  }

  /// 当前认证本人的 id（编辑归属判定的唯一权威来源；**不**解码 JWT、**不**猜用户名）。
  Future<int> getProfileId({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/auth/profile', cancelToken: cancelToken);
    final j = _obj(r.data, 'profile');
    final id = j['id'];
    if (id is int) return id;
    if (id is num) return id.toInt();
    return _bad('profile', 'missing id (${id.runtimeType})');
  }

  // ── 备忘录 ───────────────────────────────────────────────────────
  /// 新建备忘：标题可空（后端 `title or None`），正文必填（空正文后端 400）。
  Future<ReaderMemo> createMemo({
    String? title,
    required String content,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.post(
      '/api/v1/user/memos',
      data: {'title': title ?? '', 'content': content},
      cancelToken: cancelToken,
    );
    try {
      return ReaderMemo.fromJson(_obj(r.data, 'memo'));
    } on FormatException catch (e) {
      _bad('memo', e);
    }
  }

  /// 更新备忘：只传需要改的字段（后端按「字段是否存在」分支）。
  Future<ReaderMemo> updateMemo(
    int memoId, {
    String? title,
    String? content,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.put(
      '/api/v1/user/memos/$memoId',
      data: {
        if (title != null) 'title': title,
        if (content != null) 'content': content,
      },
      cancelToken: cancelToken,
    );
    try {
      return ReaderMemo.fromJson(_obj(r.data, 'memo'));
    } on FormatException catch (e) {
      _bad('memo', e);
    }
  }

  /// 删除备忘：按数值 id（跨家庭/不存在 ⇒ 后端 404）。
  Future<void> deleteMemo(int memoId, {CancelToken? cancelToken}) async {
    await client.dio.delete('/api/v1/user/memos/$memoId', cancelToken: cancelToken);
  }

  // ── 个人日记 ─────────────────────────────────────────────────────
  /// 按「本人 + 纯日期 `YYYY-MM-DD`」upsert（后端语义：写了恒为本人那一天，
  /// 不影响家庭其他成员的同日行）。日期原样透传，**不做任何 UTC 换算**。
  Future<ReaderUserDiary> upsertUserDiary(
    String diaryDate, {
    required String content,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.post(
      '/api/v1/user/diaries',
      data: {'diary_date': diaryDate, 'content': content},
      cancelToken: cancelToken,
    );
    try {
      return ReaderUserDiary.fromJson(_obj(r.data, 'user diary'));
    } on FormatException catch (e) {
      _bad('user diary', e);
    }
  }

  /// 删除个人日记：**必须**按数值 id（后端 `DELETE /api/v1/user/diaries/{diary_id}`）；
  /// 不存在「按日期删除」的端点，不得伪造。
  Future<void> deleteUserDiaryById(int diaryId, {CancelToken? cancelToken}) async {
    await client.dio.delete('/api/v1/user/diaries/$diaryId', cancelToken: cancelToken);
  }
}
