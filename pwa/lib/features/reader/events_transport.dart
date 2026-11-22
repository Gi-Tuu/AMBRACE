/// Agent 运行事件流传输抽象（2026-11-22 批 7）。
///
/// 条件导出：浏览器（dart.library.html）→ `events_transport_web.dart`
/// （dart:html EventSource，HttpOnly ticket cookie 同源自动随发）；
/// VM/测试（dart.library.io）→ `events_transport_io.dart`（桩：直接给出
/// 「仅浏览器可用」提示行，不连网）。
///
/// 事件行由调用方消费；`run_terminal` 终态检测也在调用方（pane）做。
library;

import 'events_transport_io.dart'
    if (dart.library.html) 'events_transport_web.dart';

abstract class RunEventStream {
  /// 建立事件流并持续回调行文本（open/事件 JSON/结束提示都走同一回调）。
  void start(String runId, void Function(String line) onLine);

  /// 关闭底层连接（可重复调用，幂等）。
  void close();
}

/// 平台工厂：浏览器拿 EventSource 实现，VM/测试拿桩。
RunEventStream createRunEventStream() => runEventStreamImpl();
