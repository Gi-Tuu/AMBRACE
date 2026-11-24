/// 非浏览器环境（测试/VM）兜底：不打开外链。
Future<bool> openExternalUrl(String url) async => false;
