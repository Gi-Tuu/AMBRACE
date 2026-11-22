/// 浏览器实现：dart:html EventSource。
///
/// 鉴权不走 header——`POST /{id}/stream-ticket` 已把 HttpOnly cookie
/// （path 限定到该 run 的 events 路径）写入浏览器，同源 EventSource 自动随发。
library;

// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
// 上面两条 ignore 的理由：SSE 事件流走 dart:html EventSource——M6 契约本就是
// 为浏览器 cookie-ticket 设计的；package:web 迁移随后续批次处理。
import 'dart:async';
import 'dart:html' as html;

import 'events_transport.dart';

class _WebRunEventStream implements RunEventStream {
  html.EventSource? _source;
  final List<StreamSubscription> _subs = [];

  @override
  void start(String runId, void Function(String line) onLine) {
    close();
    final s = html.EventSource('/api/agent/tools/runs/$runId/events');
    _source = s;
    _subs.add(s.onOpen.listen((_) => onLine('── open ──')));
    _subs.add(s.onMessage.listen((e) {
      onLine(e.data?.toString() ?? '');
    }));
    _subs.add(s.onError.listen((_) {
      onLine('── stream error（可关面板重开续传）──');
      close();
    }));
  }

  @override
  void close() {
    for (final s in _subs) {
      try {
        s.cancel();
      } catch (_) {}
    }
    _subs.clear();
    try {
      _source?.close();
    } catch (_) {}
    _source = null;
  }
}

RunEventStream runEventStreamImpl() => _WebRunEventStream();
