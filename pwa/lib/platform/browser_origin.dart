/// 当前页面的 origin（`scheme://host[:port]`），供 PWA 默认连接地址使用（M1a-1，2026-10-16）。
///
/// 为什么要有这个文件：薄壳原先默认写死 `http://127.0.0.1:18080`（旧合成 mock 地址），
/// 那是「访问者打开自己的站点、却默认连访问者本机」的错。同源入口下，默认就应是**自己这个
/// origin**（后端与页面同源提供，见 backend/app/pwa_static.py）。
///
/// 口径：
/// - 只认 `http` / `https` 且有 host 的 URI；`/pwa/` 这类**子路径不进 origin**
///   （同源 = scheme + host + port，页面路径与 API 前缀都不参与）。
/// - 取不到（VM/Widget 测试、`file:` URI、`about:blank`）返回 null ⇒ 由壳决定给确定值或留空，
///   **不伪造**一个假 host，也不依赖运行机器的 `file:` 路径。
/// - [base] 可注入（默认 `Uri.base`）：纯函数、无 DOM/平台依赖，VM 单测直接传值即可验证，
///   widget 测试也不必伪造浏览器。
String? pageOrigin([Uri? base]) {
  final u = base ?? Uri.base;
  final scheme = u.scheme.toLowerCase();
  if (scheme != 'http' && scheme != 'https') return null;
  if (u.host.isEmpty) return null;
  return u.origin;
}
