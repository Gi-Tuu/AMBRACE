import 'package:web/web.dart' as web;

/// 浏览器：新窗口打开外链（浏览记录的真实网页）。
Future<bool> openExternalUrl(String url) async {
  if (url.isEmpty) return false;
  web.window.open(url, '_blank');
  return true;
}
