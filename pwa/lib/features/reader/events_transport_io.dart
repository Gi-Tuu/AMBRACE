/// VM/测试桩：无 dart:html，直接给提示行（真实流只在浏览器实现）。
library;

import 'events_transport.dart';

class _IoRunEventStream implements RunEventStream {
  @override
  void start(String runId, void Function(String line) onLine) {
    onLine('EventSource 仅在浏览器可用（VM/测试桩）');
  }

  @override
  void close() {}
}

RunEventStream runEventStreamImpl() => _IoRunEventStream();
