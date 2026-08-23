import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../providers/chat_provider.dart';
import '../../services/api_client.dart';
import '../../services/plugin_bridge.dart';
import '../chat/chat_screen.dart';
import '../home/profile_screen.dart';
import 'extensions_screen.dart';
import 'marketplace_screen.dart';

/// 48a：插件页面容器 — WebView + 桥通道 + 导航拦截。
///
/// - 参数 {pluginName, pageUrl}；AppBar 显示插件名 + 关闭按钮；
/// - 注入桥 SDK（pluginBridgeJs）+ JavascriptChannel('AmbraceBridge') 桥通道；
/// - NavigationDelegate 拦截跨源导航：非插件页 origin 一律拦截，外链交给 url_launcher 外部浏览器；
/// - WebView 内返回键：可后退则后退，否则关页。
class PluginWebviewScreen extends StatefulWidget {
  const PluginWebviewScreen({
    super.key,
    required this.pluginName,
    required this.pageUrl,
  });

  final String pluginName;
  final String pageUrl;

  @override
  State<PluginWebviewScreen> createState() => _PluginWebviewScreenState();
}

class _PluginWebviewScreenState extends State<PluginWebviewScreen> {
  late final WebViewController _controller;
  late final PluginBridgeDispatcher _dispatcher;
  late final Uri _pluginOrigin; // 页面托管端点 origin（跨源导航拦截基准）
  bool _loadFailed = false;

  @override
  void initState() {
    super.initState();
    final uri = Uri.parse(widget.pageUrl);
    _pluginOrigin = Uri.parse('${uri.scheme}://${uri.authority}');
    _dispatcher = PluginBridgeDispatcher(
      pluginName: widget.pluginName,
      bridgeCall: (api, params) =>
          ApiClient().bridgeCall(widget.pluginName, api, params),
      onToast: (msg) {
        if (!mounted) return;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(msg)));
      },
      onCopy: (text) async {
        await defaultCopy(text);
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(AppLocalizations.of(context)!.pluginCopied)),
        );
      },
      onNavigate: _handleNavigate,
      onOpenChat: _handleOpenChat,
    );
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(Colors.white)
      ..setNavigationDelegate(NavigationDelegate(
        onNavigationRequest: _onNavigationRequest,
        onPageFinished: (_) => _injectBridge(),
        onWebResourceError: (_) {
          if (mounted) setState(() => _loadFailed = true);
        },
      ))
      ..addJavaScriptChannel('AmbraceBridge',
          onMessageReceived: _onBridgeMessage)
      ..loadRequest(uri);
  }

  /// 跨源导航拦截：同源（插件页）放行；跨源一律交给系统浏览器，不在 WebView 内停留。
  Future<NavigationDecision> _onNavigationRequest(
      NavigationRequest request) async {
    final target = Uri.parse(request.url);
    if (target.scheme == _pluginOrigin.scheme &&
        target.authority == _pluginOrigin.authority) {
      return NavigationDecision.navigate;
    }
    final l10n = AppLocalizations.of(context);
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(l10n?.pluginNavBlocked ?? 'External navigation blocked'),
        duration: const Duration(seconds: 1),
      ));
    }
    await launchUrl(target, mode: LaunchMode.externalApplication);
    return NavigationDecision.prevent;
  }

  /// 页面加载完成后注入桥 SDK（幂等：SDK 自带 if(global.Ambrace) return 守卫）。
  void _injectBridge() {
    _controller.runJavaScript(pluginBridgeJs);
  }

  /// JavascriptChannel 桥通道：JS postMessage → Flutter 分发 → __resolve 回传 JS Promise。
  Future<void> _onBridgeMessage(JavaScriptMessage message) async {
    final result = await _dispatcher.handleMessage(message.message);
    final id = result['id'];
    final ok = result['ok'] == true;
    final payload = ok ? result['data'] : result['error'];
    await _controller.runJavaScript(
      'window.AmbraceBridge.__resolve(${_jsLiteral(id)}, $ok, ${_jsLiteral(payload)});',
    );
  }

  /// 把 Dart 值转成合法 JS 表达式（数字/字符串/对象/数组/null 均可）。
  String _jsLiteral(Object? value) {
    if (value is num || value == null || value is bool) {
      return value == null ? 'null' : value.toString();
    }
    return jsonEncode(value);
  }

  /// navigate 白名单（48a）：插件页/聊天页/扩展页/设置页等固定列表；外链走系统浏览器。
  Future<void> _handleNavigate(String url, Map<String, dynamic> params) async {
    final nav = Navigator.of(context);
    switch (url) {
      case 'extensions':
        await nav.push(
            MaterialPageRoute(builder: (_) => const ExtensionsScreen()));
      case 'marketplace':
        await nav.push(
            MaterialPageRoute(builder: (_) => const MarketplaceScreen()));
      case 'chat':
        await nav.push(MaterialPageRoute(builder: (_) => const ChatScreen()));
      case 'settings':
        await nav.push(MaterialPageRoute(builder: (_) => const ProfileScreen()));
      default:
        final u = Uri.tryParse(url);
        if (u != null && (u.scheme == 'http' || u.scheme == 'https')) {
          await launchUrl(u, mode: LaunchMode.externalApplication);
        } else {
          throw Exception('navigate whitelist: $url');
        }
    }
  }

  /// openChat：有 aiId → 选中对应角色后进入聊天页；无 → 直接进入聊天页（角色选择）。
  Future<void> _handleOpenChat(int? aiId) async {
    final nav = Navigator.of(context);
    if (aiId != null) {
      final chatProvider = context.read<ChatProvider>();
      try {
        final chars = await ApiClient().getCharacters();
        final match = chars.where((c) => c.id == aiId).toList();
        if (match.isNotEmpty) chatProvider.setCharacter(match.first);
      } catch (_) {
        // 拉取失败仍进入聊天页
      }
    }
    if (!mounted) return;
    await nav.push(MaterialPageRoute(builder: (_) => const ChatScreen()));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) async {
        if (didPop) return;
        final navigator = Navigator.of(context);
        if (await _controller.canGoBack()) {
          await _controller.goBack();
          return;
        }
        if (mounted) navigator.pop();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.pluginName),
          actions: [
            IconButton(
              tooltip: l10n.pluginClose,
              icon: const Icon(Icons.close),
              onPressed: () => Navigator.of(context).pop(),
            ),
          ],
        ),
        body: _loadFailed
            ? Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.error_outline,
                        size: 42, color: Colors.grey),
                    const SizedBox(height: 8),
                    Text(l10n.pluginPageLoadFailed,
                        style: const TextStyle(color: Colors.grey)),
                  ],
                ),
              )
            : WebViewWidget(controller: _controller),
      ),
    );
  }
}
