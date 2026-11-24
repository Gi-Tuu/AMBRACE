/// 角色设置（PWA 角色详情「设置」）——**共享 core 层写入 API**。
///
/// 只用 [AmbApiClient]（认证头/401 钩子/错误归一复用），不另造 Dio、不绕过认证。
/// 契约以真实后端为准：
/// - `PUT /api/v1/scheduler/settings/{id}`（`app/api/scheduler.py::update_settings`，
///   只写 `allowed_fields` 白名单，返回 `{status:'ok'}`；非法 frequency/reasoning_level/
///   dnd 时间会被后端 400 拒绝）；
/// - `PUT /api/v1/characters/{id}`（`CharacterUpdate`，`exclude_unset`；此处只写角色级
///   `cognitive_loop_enabled`）。
///
/// 纪律：
/// 1. **一次只发一个字段的差异**（与 App 设置页同口径：改哪个写哪个，不做整页回写）；
/// 2. 空 patch 不发请求（本地 `ApiException(kind='client')`，与 EditorApi 同款 early-return）；
/// 3. 坏响应不当成功：scheduler 写回封套必须 `status == 'ok'`。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';

class CharacterSettingsApi {
  CharacterSettingsApi(this.client);

  final AmbApiClient client;

  /// 写角色主动交流/生活/隐私配置（单字段或少量字段的 patch）。
  Future<void> updateProactive(
    int characterId,
    Map<String, dynamic> patch, {
    CancelToken? cancelToken,
  }) async {
    if (patch.isEmpty) {
      throw const ApiException(kind: 'client', message: 'empty settings patch');
    }
    final r = await client.dio.put(
      '/api/v1/scheduler/settings/$characterId',
      data: patch,
      cancelToken: cancelToken,
    );
    final data = r.data;
    final ok = data is Map && data['status'] == 'ok';
    if (!ok) {
      throw const ApiException(
          kind: 'unknown', message: 'settings update not acknowledged');
    }
  }

  /// 写角色级认知循环开关（`cognitive_loop_enabled`，经角色更新接口）。
  Future<void> setCognitiveLoop(
    int characterId, {
    required bool enabled,
    CancelToken? cancelToken,
  }) async {
    final r = await client.dio.put(
      '/api/v1/characters/$characterId',
      data: {'cognitive_loop_enabled': enabled},
      cancelToken: cancelToken,
    );
    final data = r.data;
    if (data is! Map || data['id'] == null || data['id'] != characterId) {
      throw const ApiException(
          kind: 'unknown', message: 'character update not acknowledged');
    }
  }
}
