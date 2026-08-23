import 'package:flutter_test/flutter_test.dart';
import 'package:ai_companion/services/api_client.dart';
import 'package:ai_companion/services/plugin_bridge.dart';

void main() {
  group('pluginBridgeJs 桥 SDK 模板', () {
    test('包含完整 Ambrace API 面', () {
      expect(pluginBridgeJs, contains('global.Ambrace = {'));
      expect(pluginBridgeJs, contains('call: call'));
      expect(pluginBridgeJs, contains('ai: function'));
      expect(pluginBridgeJs, contains('getAiList: function'));
      expect(pluginBridgeJs, contains('getAiInfo: function'));
      expect(pluginBridgeJs, contains('openChat: function'));
      expect(pluginBridgeJs, contains('getUserInfo: function'));
      expect(pluginBridgeJs, contains('store: {'));
      expect(pluginBridgeJs, contains('set: function'));
      expect(pluginBridgeJs, contains('get: function'));
      expect(pluginBridgeJs, contains('toast: function'));
      expect(pluginBridgeJs, contains('copy: function'));
      expect(pluginBridgeJs, contains('navigate: function'));
      expect(pluginBridgeJs, contains('http: function'));
      expect(pluginBridgeJs, contains('AmbraceBridge.postMessage'));
      expect(pluginBridgeJs, contains('AmbraceBridge.__resolve'));
      // Promise 化 + 幂等守卫
      expect(pluginBridgeJs, contains('new Promise'));
      expect(pluginBridgeJs, contains("if (global.Ambrace) { return; }"));
    });
  });

  group('PluginBridgeDispatcher 消息分发', () {
    PluginBridgeDispatcher makeDispatcher({
      Future<Map<String, dynamic>> Function(String api, Map<String, dynamic>)? bridgeCall,
      List<String>? toasts,
      List<String>? copied,
      List<String>? navigated,
      List<int?>? openedChat,
    }) {
      return PluginBridgeDispatcher(
        pluginName: 'ai_diary',
        bridgeCall: bridgeCall ??
            (api, params) async => {'ok': true, 'data': 'backend:$api'},
        onToast: (msg) => toasts?.add(msg),
        onCopy: (text) async => copied?.add(text),
        onNavigate: (url, params) async => navigated?.add(url),
        onOpenChat: (aiId) async => openedChat?.add(aiId),
      );
    }

    test('toast 前端能力直接执行', () async {
      final toasts = <String>[];
      final d = makeDispatcher(toasts: toasts);
      final r = await d.handleMessage(
          '{"id":1,"api":"toast","params":{"msg":"你好"}}');
      expect(r['ok'], isTrue);
      expect(toasts, ['你好']);
    });

    test('copy 前端能力直接执行', () async {
      final copied = <String>[];
      final d = makeDispatcher(copied: copied);
      final r = await d.handleMessage(
          '{"id":2,"api":"copy","params":{"text":"复制我"}}');
      expect(r['ok'], isTrue);
      expect(copied, ['复制我']);
    });

    test('navigate 前端能力直接执行', () async {
      final navigated = <String>[];
      final d = makeDispatcher(navigated: navigated);
      final r = await d.handleMessage(
          '{"id":3,"api":"navigate","params":{"url":"extensions"}}');
      expect(r['ok'], isTrue);
      expect(navigated, ['extensions']);
    });

    test('openChat 前端能力直接执行（aiId 透传）', () async {
      final opened = <int?>[];
      final d = makeDispatcher(openedChat: opened);
      final r = await d.handleMessage(
          '{"id":4,"api":"openChat","params":{"aiId":7}}');
      expect(r['ok'], isTrue);
      expect(opened, [7]);
      // 不传 aiId → null
      await d.handleMessage('{"id":5,"api":"openChat","params":{}}');
      expect(opened, [7, null]);
    });

    test('后端能力走 bridgeCall 且回传 data', () async {
      String? gotApi;
      Map<String, dynamic>? gotParams;
      final d = makeDispatcher(bridgeCall: (api, params) async {
        gotApi = api;
        gotParams = params;
        return {'ok': true, 'data': {'items': []}};
      });
      final r = await d.handleMessage(
          '{"id":6,"api":"getAiList","params":{}}');
      expect(r['ok'], isTrue);
      expect(r['data'], {'items': []});
      expect(gotApi, 'getAiList');
      expect(gotParams, isEmpty);
    });

    test('后端能力错误 → ok:false + error', () async {
      final d = makeDispatcher(
          bridgeCall: (api, params) async => {'ok': false, 'error': '未找到该 AI'});
      final r = await d.handleMessage(
          '{"id":7,"api":"ai","params":{"aiId":999}}');
      expect(r['ok'], isFalse);
      expect(r['error'], '未找到该 AI');
    });

    test('非法消息 → ok:false', () async {
      final d = makeDispatcher();
      final r = await d.handleMessage('not-json');
      expect(r['ok'], isFalse);
    });

    test('未知 api 落到 bridgeCall（后端白名单校验）', () async {
      final d = makeDispatcher(
          bridgeCall: (api, params) async => {'ok': false, 'error': '不支持的桥 API: hack'});
      final r = await d.handleMessage(
          '{"id":8,"api":"hack","params":{}}');
      expect(r['ok'], isFalse);
      expect(r['error'], contains('hack'));
    });
  });

  group('getPluginPageUrl 页面 URL', () {
    test('拼接页面托管端点', () {
      ApiClient().configure(baseUrl: 'http://127.0.0.1:8000');
      expect(ApiClient().getPluginPageUrl('ai_diary', 'index.html'),
          'http://127.0.0.1:8000/api/v1/plugins/ai_diary/page/index.html');
      expect(ApiClient().getPluginPageUrl('ai_diary', 'assets/app.js'),
          'http://127.0.0.1:8000/api/v1/plugins/ai_diary/page/assets/app.js');
    });
  });
}
