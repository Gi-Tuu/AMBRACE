import 'dart:convert';

import 'package:dio/dio.dart';
import '../api_client.dart';

/// PluginsApi：扩展（插件）系统领域 API（extension 挂到 ApiClient）
extension PluginsApi on ApiClient {
  /// 插件列表（含启用状态与配置；所有登录用户可读）
  Future<List<Map<String, dynamic>>> getPlugins() async {
    final r = await dio.get('/api/v1/plugins');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 启用 / 禁用 / 更新配置（仅主账号）
  Future<Map<String, dynamic>> updatePlugin(
    String name, {
    bool? enabled,
    Map<String, dynamic>? config,
  }) async {
    final r = await dio.put('/api/v1/plugins/$name', data: {
      if (enabled != null) 'enabled': enabled,
      if (config != null) 'config': config,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 市场列表（?q=搜索 &category=过滤 &installed=筛选；条目含 installed/enabled）
  Future<List<Map<String, dynamic>>> getMarketplace({
    String? q,
    String? category,
    bool? installed,
  }) async {
    final r = await dio.get('/api/v1/marketplace', queryParameters: {
      if (q != null && q.isNotEmpty) 'q': q,
      if (category != null) 'category': category,
      if (installed != null) 'installed': installed,
    });
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 市场总览（含顶层 allow_remote_install 开关，3.9）：{items, total, allow_remote_install}
  Future<Map<String, dynamic>> getMarketplaceOverview({
    String? q,
    String? category,
    bool? installed,
  }) async {
    final r = await dio.get('/api/v1/marketplace', queryParameters: {
      if (q != null && q.isNotEmpty) 'q': q,
      if (category != null) 'category': category,
      if (installed != null) 'installed': installed,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 市场条目详情（含 readme_text）
  Future<Map<String, dynamic>> getMarketplaceDetail(String name) async {
    final r = await dio.get('/api/v1/marketplace/$name');
    return r.data as Map<String, dynamic>;
  }

  /// 从市场安装插件（仅主账号；内置=复制示例目录 / 远程=下载 zip）。
  /// 3.9：manifest.permissions 非空时须携带 {consent: true, permissions: [...]}（一致才被接受）。
  Future<Map<String, dynamic>> installMarketplacePlugin(
    String name, {
    bool consent = false,
    List<String>? permissions,
  }) async {
    final r = await dio.post('/api/v1/marketplace/$name/install', data: {
      'consent': consent,
      if (permissions != null) 'permissions': permissions,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 远程市场配置（读，仅主账号）
  Future<Map<String, dynamic>> getMarketplaceConfig() async {
    final r = await dio.get('/api/v1/marketplace/config');
    return r.data as Map<String, dynamic>;
  }

  /// 远程市场配置（写，仅主账号）
  Future<Map<String, dynamic>> updateMarketplaceConfig(
    Map<String, dynamic> config,
  ) async {
    final r = await dio.put('/api/v1/marketplace/config', data: config);
    return r.data as Map<String, dynamic>;
  }

  /// 刷新远程市场索引（仅主账号；force=true 强制刷新）
  Future<Map<String, dynamic>> refreshMarketplace({bool force = false}) async {
    final r = await dio.post(
      '/api/v1/marketplace/refresh',
      queryParameters: {'force': force},
    );
    return r.data as Map<String, dynamic>;
  }

  /// zip 安装插件（仅主账号）。3.9：manifest.permissions 非空时须携带 consent=true + permissions 一致。
  Future<Map<String, dynamic>> installPluginZip(
    String filePath, {
    bool consent = false,
    List<String>? permissions,
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: filePath.split('/').last.split('\\').last),
      'consent': consent,
      'permissions': jsonEncode(permissions ?? []),
    });
    final r = await dio.post(
      '/api/v1/plugins/install',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  /// 探测本地 zip 包 manifest（只读取，不安装/不写库），返回 {name, version, permissions, source}
  Future<Map<String, dynamic>> probePluginZip(String filePath) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: filePath.split('/').last.split('\\').last),
    });
    final r = await dio.post(
      '/api/v1/plugins/probe',
      data: form,
      options: Options(
        contentType: 'multipart/form-data',
        sendTimeout: const Duration(seconds: 60),
        receiveTimeout: const Duration(seconds: 60),
      ),
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// douyin_mcp：AI 生成草稿（kind=image_post|reply_comment，hint 为灵感/prompt）
  Future<Map<String, dynamic>> aiDouyinDraft(String kind, String hint) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/ai_draft', data: {
      'kind': kind,
      'hint': hint,
    });
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：待确认任务列表（图文发布 / 评论回复）
  Future<List<Map<String, dynamic>>> getDouyinPending() async {
    final r = await dio.get('/api/v1/plugins/douyin_mcp/pending');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// douyin_mcp：已确认待发布任务（发布倒计时：含剩余秒数）
  Future<List<Map<String, dynamic>>> getDouyinUpcoming() async {
    final r = await dio.get('/api/v1/plugins/douyin_mcp/upcoming');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// douyin_mcp：确认并执行任务（真实发布/回复）
  Future<Map<String, dynamic>> confirmDouyinTask(int taskId) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/confirm/$taskId');
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：拒绝任务
  Future<Map<String, dynamic>> rejectDouyinTask(int taskId) async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/reject/$taskId');
    return r.data as Map<String, dynamic>;
  }

  /// douyin_mcp：为图文草稿上传配图（multipart）
  Future<Map<String, dynamic>> uploadDouyinImage(int taskId, String filePath) async {
    final form = FormData.fromMap({
      'task_id': taskId,
      'file': await MultipartFile.fromFile(filePath),
    });
    final r = await dio.post('/api/v1/plugins/douyin_mcp/upload_image',
        data: form,
        options: Options(contentType: 'multipart/form-data', sendTimeout: const Duration(seconds: 60), receiveTimeout: const Duration(seconds: 60)));
    return r.data as Map<String, dynamic>;
  }

  /// chat 型插件通用对话（48c）：persona 作 system prompt，BYOK 三级回退，不写记忆不建会话
  Future<Map<String, dynamic>> pluginChat(
    String name, {
    required String input,
    List<Map<String, dynamic>>? history,
    int? maxTokens,
  }) async {
    final r = await dio.post('/api/v1/plugins/$name/chat', data: {
      'input': input,
      if (history != null && history.isNotEmpty) 'history': history,
      if (maxTokens != null) 'maxTokens': maxTokens,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 插件页面托管 URL（48a）：{base}/api/v1/plugins/{name}/page/{file}（file 为包内相对路径）
  String getPluginPageUrl(String name, String file) {
    final base = baseUrl.replaceAll(RegExp(r'/+$'), '');
    return '$base/api/v1/plugins/$name/page/$file';
  }

  /// 插件桥调用（48a）：body {api, params} → {"ok": true, "data"} / {"ok": false, "error"}
  Future<Map<String, dynamic>> bridgeCall(
    String name,
    String api,
    Map<String, dynamic>? params,
  ) async {
    final r = await dio.post('/api/v1/plugins/$name/bridge', data: {
      'api': api,
      'params': params ?? {},
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 卸载插件（48a，仅主账号）：删目录 + plugin_stores 行 + 禁用
  Future<Map<String, dynamic>> uninstallPlugin(String name) async {
    final r = await dio.delete('/api/v1/plugins/$name');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// wechat_ilink：换绑角色（任务 B，仅主账号）。
  /// 必须走插件 /rebind 端点（内核先解绑裁决再单选绑定裁决 + 同一事务迁移绑定行），
  /// 而不是 douyin 的 updatePlugin(PUT config) 保存路径——否则会被 occupied 裁决挡 400。
  Future<Map<String, dynamic>> rebindWechatPlugin(int characterId) async {
    final r = await dio.post('/api/v1/plugins/wechat_ilink/rebind', data: {
      'character_id': characterId,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  // ── 一机多主（S3，2026-09-05）：渠道绑定统一 API（/api/v1/channels/{ch}/bindings）──
  // 渠道卡绑定区块改走本组方法（不再 updatePlugin config / rebindWechatPlugin）；
  // botAccountId 拼路径前 Uri.encodeComponent（C7，2026-09-05 审查：bot 键未来含 @ 等特殊字符）；
  // 旧端点保留给其它客户端。仅主账号调写操作（前端按 isAdmin 控制）。

  /// 列出当前主账号在某渠道的 bot 绑定（flag 关时后端回落旧全局 config 合成行）
  Future<List<Map<String, dynamic>>> listChannelBindings(String channel) async {
    final r = await dio.get('/api/v1/channels/$channel/bindings');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 绑定/换绑指定 bot（仅主账号）
  Future<Map<String, dynamic>> putChannelBinding(
    String channel,
    String botAccountId,
    int characterId, {
    String? botLabel,
  }) async {
    final r = await dio.put('/api/v1/channels/$channel/bindings/${Uri.encodeComponent(botAccountId)}', data: {
      'character_id': characterId,
      if (botLabel != null) 'bot_label': botLabel,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 解绑指定 bot（仅主账号）
  Future<Map<String, dynamic>> deleteChannelBinding(String channel, String botAccountId) async {
    final r = await dio.delete('/api/v1/channels/$channel/bindings/${Uri.encodeComponent(botAccountId)}');
    return Map<String, dynamic>.from(r.data as Map);
  }

  // ── 微信桥 per-tenant 共享密钥（一机多主收尾，2026-09-18；后端包 C 已上线）──
  // GET 只回脱敏预览（has_secret/masked），明文不回传；密钥仅在本端生成/输入后由 PUT 落库，
  // 并在保存成功时于 UI 完整展示一次。仅独立主账号可调（后端 assert_standalone_owner，子账号 403）。

  /// 查看本家庭桥接密钥状态（脱敏）。
  Future<Map<String, dynamic>> getWechatBridgeSecret() async {
    final r = await dio.get('/api/v1/plugins/wechat_ilink/bridge-secret');
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 写入/轮换本家庭桥接密钥（后端要求 ≥16 字符；Fernet 加密落库）。
  Future<Map<String, dynamic>> putWechatBridgeSecret(String secret) async {
    final r = await dio.put(
      '/api/v1/plugins/wechat_ilink/bridge-secret',
      data: {'secret': secret},
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 删除本家庭桥接密钥（回落服务器全局 env 语义）。
  Future<Map<String, dynamic>> deleteWechatBridgeSecret() async {
    final r = await dio.delete('/api/v1/plugins/wechat_ilink/bridge-secret');
    return Map<String, dynamic>.from(r.data as Map);
  }

  // ── App 添加未绑定 ClawBot（2026-09-06）：可用 bot 列表 + 绑定执行 ──

  /// 网关已登录、拥爱未绑定的 bot 列表（仅主账号；后端同机读取 openclaw accounts）。
  /// [pluginName] 为绑定插件名（端点挂插件路由：`/api/v1/plugins/<plugin>/available-bots`，
  /// 与渠道名不同——wechat → wechat_ilink）。
  Future<List<Map<String, dynamic>>> listAvailableBots(String pluginName) async {
    final r = await dio.get('/api/v1/plugins/$pluginName/available-bots');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 绑定一个可用 bot（选角色后保存；幂等——已绑属本租户时直接改绑）
  Future<Map<String, dynamic>> bindAvailableBot(
    String pluginName,
    String botAccountId,
    int characterId,
  ) async {
    final r = await dio.post('/api/v1/plugins/$pluginName/bind-available', data: {
      'bot_account_id': botAccountId,
      'character_id': characterId,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  // ── 扫码绑定下放手机（2026-09-12）：微信 ClawBot 取码 / 轮询 / 绑定 ──

  /// 取绑定二维码。qrcode_img_content 是**待渲染成二维码的 URL 字符串**（2026-09-12 实测，
  /// 非 base64 图片），App 用 qr_flutter 自行渲染。
  Future<Map<String, dynamic>> fetchWechatLoginQrcode() async {
    final r = await dio.get(
      '/api/v1/plugins/wechat_ilink/qrcode',
      options: Options(receiveTimeout: const Duration(seconds: 20)),
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 轮询扫码状态。后端是 ~35s 长轮询（无变化返回 {ok,status:"wait"}），receiveTimeout 必须
  /// 覆盖 40s 服务端超时。status 全集：wait/scaned/confirmed/expired/scaned_but_redirect/
  /// need_verifycode/verify_code_blocked/binded_redirect；confirmed 含 bot_token/baseurl/
  /// ilink_user_id/ilink_bot_id（原样回传 [bindWechatLogin]）。
  Future<Map<String, dynamic>> pollWechatLoginStatus(String qrcode, {String verifyCode = ''}) async {
    final r = await dio.get(
      '/api/v1/plugins/wechat_ilink/qrcode/${Uri.encodeComponent(qrcode)}',
      queryParameters: {if (verifyCode.isNotEmpty) 'verify_code': verifyCode},
      options: Options(receiveTimeout: const Duration(seconds: 50)),
    );
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 绑定扫码确认的新 bot：confirmed 载荷原样回传（后端做白名单/裁决后落库 + 写网关账号文件）。
  /// 响应含 gateway_registered / gateway_restart_pending（网关需重启一次才拉新 bot 消息）。
  Future<Map<String, dynamic>> bindWechatLogin({
    required int characterId,
    required String botToken,
    required String baseurl,
    required String ilinkUserId,
    required String ilinkBotId,
  }) async {
    final r = await dio.post('/api/v1/plugins/wechat_ilink/bind', data: {
      'character_id': characterId,
      'bot_token': botToken,
      'baseurl': baseurl,
      'ilink_user_id': ilinkUserId,
      'ilink_bot_id': ilinkBotId,
    });
    return Map<String, dynamic>.from(r.data as Map);
  }

  // ── 扫码绑定下放手机（2026-09-12）：抖音二维码回传会话 ──

  /// 发起抖音扫码会话：服务器弹有头 Edge 打开抖音并回传登录二维码截图（base64 PNG）。
  Future<Map<String, dynamic>> startDouyinQrBind() async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/bind/qr/start',
        options: Options(receiveTimeout: const Duration(seconds: 60)));
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 轮询抖音扫码会话状态：{state, image_png_base64?, account_name?, message?}。
  Future<Map<String, dynamic>> pollDouyinQrBind(String sessionId) async {
    final r = await dio.get('/api/v1/plugins/douyin_mcp/bind/qr/status',
        queryParameters: {'session_id': sessionId},
        options: Options(receiveTimeout: const Duration(seconds: 20)));
    return Map<String, dynamic>.from(r.data as Map);
  }

  /// 取消抖音扫码会话（App 关弹层时调用；服务器 worker 随即关窗，不占 profile 锁）。
  Future<void> cancelDouyinQrBind(String sessionId) async {
    try {
      await dio.post('/api/v1/plugins/douyin_mcp/bind/qr/cancel',
          data: {'session_id': sessionId});
    } catch (_) {} // 尽力而为：失败靠会话 TTL 自动过期兜底
  }

  /// 兜底：走旧 POST /bind（服务器上弹有头 Edge，电脑前直接扫码）。
  Future<Map<String, dynamic>> bindDouyinLegacy() async {
    final r = await dio.post('/api/v1/plugins/douyin_mcp/bind',
        options: Options(receiveTimeout: const Duration(seconds: 320)));
    return Map<String, dynamic>.from(r.data as Map);
  }
}

/// #65：构造插件页面/图标鉴权请求头（纯函数，可单测）。
///
/// 后端 `plugin_page` 用 `get_current_user_id`（HTTPBearer），只认
/// `Authorization: Bearer <token>`；WebView `loadRequest` 与 `Image.network`
/// 裸加载会 401，必须带上该头。空 token 返回空 map（不额外污染请求头）。
/// [token] 从 ApiClient 现有登录态取（`ApiClient().token`）。
Map<String, String> pluginAuthHeaders(String token) {
  final t = token.trim();
  if (t.isEmpty) return const <String, String>{};
  return {'Authorization': 'Bearer $t'};
}
