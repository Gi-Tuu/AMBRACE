import 'dart:convert';

import 'package:flutter/services.dart';

import 'api_client.dart';

/// 插件桥 JS SDK 模板（48a）：与后端 GET /{name}/page/plugin-bridge.js 同一契约。
///
/// 链路：JS `Ambrace.call(api, params)` → `window.AmbraceBridge.postMessage(JSON)` →
/// Flutter WebView JavascriptChannel 收到 → [PluginBridgeDispatcher] 分发 →
/// `window.AmbraceBridge.__resolve(id, ok, value)` 回传 JS Promise。
const String pluginBridgeJs = '''
(function (global) {
  'use strict';
  if (global.Ambrace) { return; }
  var seq = 0;
  var pending = {};
  function call(api, params) {
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      pending[id] = { resolve: resolve, reject: reject };
      try {
        global.AmbraceBridge.postMessage(JSON.stringify({ id: id, api: api, params: params || {} }));
      } catch (e) {
        delete pending[id];
        reject(e);
      }
    });
  }
  function resolve(id, ok, value) {
    var p = pending[id];
    if (!p) { return; }
    delete pending[id];
    if (ok) { p.resolve(value); } else { p.reject(new Error(typeof value === 'string' ? value : ((value && value.error) || 'bridge error'))); }
  }
  global.Ambrace = {
    call: call,
    ai: function (params) { return call('ai', params || {}); },
    getAiList: function () { return call('getAiList', {}); },
    getAiInfo: function (aiId) { return call('getAiInfo', { aiId: aiId }); },
    openChat: function (aiId) { return call('openChat', { aiId: aiId }); },
    getUserInfo: function () { return call('getUserInfo', {}); },
    store: {
      set: function (key, value) { return call('store.set', { key: key, value: value }); },
      get: function (key) { return call('store.get', key === undefined || key === null ? {} : { key: key }); }
    },
    toast: function (msg) { return call('toast', { msg: msg }); },
    copy: function (text) { return call('copy', { text: text }); },
    navigate: function (url) { return call('navigate', { url: url }); },
    http: function (url, method, data, headers) { return call('http', { url: url, method: method || 'GET', data: data, headers: headers }); }
  };
  global.AmbraceBridge.__resolve = resolve;
})(window);
''';

/// 前端能力回调集合（由 PluginWebviewScreen 注入真实实现；测试注入记录桩）。
typedef BridgeCall = Future<Map<String, dynamic>> Function(
    String api, Map<String, dynamic> params);
typedef BridgeToast = void Function(String message);
typedef BridgeCopy = Future<void> Function(String text);
typedef BridgeNavigate = Future<void> Function(
    String url, Map<String, dynamic> params);
typedef BridgeOpenChat = Future<void> Function(int? aiId);

/// 桥消息分发器（48a，纯逻辑可单测）：
/// - 前端能力（toast/copy/navigate/openChat）直接执行；
/// - 其余后端能力（ai/getAiList/getAiInfo/getUserInfo/store.set/store.get/http/call）
///   走 [bridgeCall] → POST /api/v1/plugins/{name}/bridge → 结果回传 JS。
class PluginBridgeDispatcher {
  PluginBridgeDispatcher({
    required this.pluginName,
    required this.bridgeCall,
    required this.onToast,
    required this.onCopy,
    required this.onNavigate,
    required this.onOpenChat,
  });

  final String pluginName;
  final BridgeCall bridgeCall;
  final BridgeToast onToast;
  final BridgeCopy onCopy;
  final BridgeNavigate onNavigate;
  final BridgeOpenChat onOpenChat;

  /// 处理一条来自 JS 的桥消息（{id, api, params}），返回 {id, ok, data|error}。
  Future<Map<String, dynamic>> handleMessage(String rawMessage) async {
    Map<String, dynamic>? msg;
    try {
      final decoded = jsonDecode(rawMessage);
      if (decoded is Map<String, dynamic>) msg = decoded;
    } catch (_) {
      msg = null;
    }
    if (msg == null) {
      return {'id': null, 'ok': false, 'error': 'bad bridge message'};
    }
    final id = msg['id'];
    final api = (msg['api'] as String?) ?? '';
    final rawParams = msg['params'];
    final params = rawParams is Map<String, dynamic>
        ? rawParams
        : <String, dynamic>{};
    try {
      switch (api) {
        case 'toast':
          onToast((params['msg'] as String?) ?? '');
          return {'id': id, 'ok': true, 'data': null};
        case 'copy':
          await onCopy((params['text'] as String?) ?? '');
          return {'id': id, 'ok': true, 'data': null};
        case 'navigate':
          await onNavigate((params['url'] as String?) ?? '', params);
          return {'id': id, 'ok': true, 'data': null};
        case 'openChat':
          await onOpenChat((params['aiId'] as num?)?.toInt());
          return {'id': id, 'ok': true, 'data': null};
        default:
          final resp = await bridgeCall(api, params);
          if (resp['ok'] == true) {
            return {'id': id, 'ok': true, 'data': resp['data']};
          }
          return {
            'id': id,
            'ok': false,
            'error': (resp['error'] as String?) ?? 'bridge error',
          };
      }
    } catch (e) {
      return {'id': id, 'ok': false, 'error': e.toString()};
    }
  }
}

/// 默认复制实现（可被测试替换）。
Future<void> defaultCopy(String text) async {
  await Clipboard.setData(ClipboardData(text: text));
}

/// 默认桥后端调用：POST /api/v1/plugins/{name}/bridge。
Future<Map<String, dynamic>> defaultBridgeCall(
  String pluginName,
  String api,
  Map<String, dynamic> params,
) async {
  return ApiClient().bridgeCall(pluginName, api, params);
}
