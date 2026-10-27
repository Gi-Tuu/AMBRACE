import 'dart:async';
import 'dart:convert';

import 'package:ai_companion/services/websocket_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

class _Timer implements Timer {
  _Timer(this.delay, this.callback);
  final Duration delay;
  final void Function() callback;
  bool active = true;
  @override
  bool get isActive => active;
  @override
  int tick = 0;
  @override
  void cancel() => active = false;
  void fire() {
    expect(active, isTrue);
    active = false;
    tick++;
    callback();
  }
}

class _Sink implements WebSocketSink {
  final sent = <dynamic>[];
  bool closed = false;
  @override
  void add(dynamic data) => sent.add(data);
  @override
  Future<void> close([int? code, String? reason]) async => closed = true;
  @override
  Future<void> get done => Future.value();
  @override
  Future<void> addStream(Stream<dynamic> stream) async {
    await for (final item in stream) {
      add(item);
    }
  }

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}
}

class _Channel implements WebSocketChannel {
  final input = StreamController<dynamic>.broadcast(sync: true);
  final handshake = Completer<void>();
  @override
  final _Sink sink = _Sink();
  @override
  Stream<dynamic> get stream => input.stream;
  @override
  Future<void> get ready => handshake.future;
  @override
  String? get protocol => null;
  @override
  int? get closeCode => null;
  @override
  String? get closeReason => null;
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _Harness {
  final channels = <_Channel>[];
  final uris = <Uri>[];
  final timers = <_Timer>[];
  bool throwOnConnect = false;
  int calls = 0;
  late final service = WebSocketService(
    connector: (uri) {
      calls++;
      uris.add(uri);
      if (throwOnConnect) {
        throw StateError('synthetic connect failure');
      }
      final channel = _Channel();
      channels.add(channel);
      return channel;
    },
    timerFactory: (delay, callback) {
      final timer = _Timer(delay, callback);
      timers.add(timer);
      return timer;
    },
  );
  List<_Timer> get active => timers.where((timer) => timer.isActive).toList();
  _Channel get current => channels.last;
  void start({MessageCallback? onMessage}) => service.connect(
        'https://synthetic.invalid',
        301,
        token: 'synthetic +/&',
        onMessage: onMessage,
      );
  Future<void> dispose() async {
    service.disconnect();
    for (final channel in channels) {
      await channel.input.close();
    }
  }
}

void main() {
  late _Harness h;
  setUp(() {
    h = _Harness();
  });
  tearDown(() async {
    await h.dispose();
  });

  test('failed establishment keeps backoff and stops after ten retries', () {
    h.start();
    const seconds = [3, 6, 12, 24, 48, 60, 60, 60, 60, 60];
    for (final delay in seconds) {
      h.current.input.addError(StateError('synthetic stream failure'));
      expect(h.active, hasLength(1));
      expect(h.active.single.delay.inSeconds, delay);
      h.active.single.fire();
    }
    h.current.input.addError(StateError('final failure'));
    expect(h.active, isEmpty);
    expect(h.calls, 11); // One initial attempt plus ten retries.
  });

  test('error plus done consumes one retry and one timer', () async {
    h.start();
    h.current.input.addError(StateError('failure'));
    await h.current.input.close();
    expect(h.active, hasLength(1));
    expect(h.timers, hasLength(1));
    expect(h.active.single.delay.inSeconds, 3);
    h.active.single.fire();
    h.current.input.addError(StateError('next failure'));
    expect(h.active.single.delay.inSeconds, 6);
  });

  test('only confirmed current handshake resets backoff', () async {
    h.start();
    h.current.input.addError(StateError('failure'));
    h.active.single.fire();
    final stale = h.channels.first;
    stale.handshake.complete();
    await Future<void>.value();
    h.current.input.addError(StateError('failure'));
    expect(h.active.single.delay.inSeconds, 6);
    h.active.single.fire();
    h.current.handshake.complete();
    await Future<void>.value();
    h.current.input.addError(StateError('new outage'));
    expect(h.active.single.delay.inSeconds, 3);
  });

  test('ready error and stream error cannot double schedule', () async {
    h.start();
    h.current.handshake.completeError(StateError('handshake refused'));
    await Future<void>.value();
    h.current.input.addError(StateError('same failure'));
    expect(h.active, hasLength(1));
    expect(h.timers, hasLength(1));
    expect(h.active.single.delay.inSeconds, 3);
  });

  test('disconnect cancels pending retry and ignores late handshake', () async {
    h.start();
    final stale = h.current;
    stale.input.addError(StateError('failure'));
    h.service.disconnect();
    stale.handshake.complete();
    await Future<void>.value();
    expect(h.active, isEmpty);
    expect(stale.sink.closed, isTrue);
    expect(h.calls, 1);
  });

  test('explicit new session cancels old retry and replaces identity',
      () async {
    final delivered = <Map<String, dynamic>>[];
    h.start();
    h.current.input.addError(StateError('failure'));
    final stale = h.current;
    h.service.connect('http://next.invalid/root', 302,
        token: 'next-synthetic', onMessage: delivered.add);
    expect(h.active, isEmpty);
    expect(h.uris.last.toString(),
        'ws://next.invalid/root/api/v1/chat/ws/302?token=next-synthetic');
    stale.handshake.complete();
    stale.input.add(jsonEncode({'old': true}));
    h.current.handshake.complete();
    await Future<void>.value();
    h.current.input.add(jsonEncode({'new': true}));
    expect(delivered, [
      {'new': true}
    ]);
    h.current.input.addError(StateError('new failure'));
    expect(h.active.single.delay.inSeconds, 3);
  });

  test('synchronous connector failure shares bounded retry policy', () {
    h.throwOnConnect = true;
    h.start();
    for (var i = 0; i < 10; i++) {
      expect(h.active, hasLength(1));
      h.active.single.fire();
    }
    expect(h.active, isEmpty);
    expect(h.calls, 11);
  });

  test('explicit reconnect after exhaustion gives a fresh budget', () {
    h.throwOnConnect = true;
    h.start();
    for (var i = 0; i < 10; i++) {
      h.active.single.fire();
    }
    expect(h.active, isEmpty);
    h.start();
    expect(h.active.single.delay.inSeconds, 3);
    expect(h.calls, 12);
  });

  test('clean remote close also schedules only one retry', () async {
    h.start();
    await h.current.input.close();
    expect(h.active, hasLength(1));
    expect(h.active.single.delay.inSeconds, 3);
  });

  test('late refused handshake after disconnect is handled without reconnect',
      () async {
    h.start();
    final old = h.current;
    h.service.disconnect();
    old.handshake.completeError(StateError('late refusal'));
    await Future<void>.value();
    expect(h.active, isEmpty);
    expect(h.calls, 1);
  });

  test('stale timer callback cannot erase cancellation of a newer retry', () {
    h.start();
    h.current.input.addError(StateError('old failure'));
    final oldTimer = h.active.single;
    h.start();
    h.current.input.addError(StateError('new failure'));
    oldTimer
        .callback(); // Simulate an already queued callback from an old generation.
    h.service.disconnect();
    expect(h.active, isEmpty);
    expect(h.calls, 2);
  });

  test('retry preserves route token callback and outbound JSON', () async {
    final delivered = <Map<String, dynamic>>[];
    h.start(onMessage: delivered.add);
    h.current.input.addError(StateError('failure'));
    h.active.single.fire();
    expect(h.uris.last.scheme, 'wss');
    expect(h.uris.last.path, '/api/v1/chat/ws/301');
    expect(h.uris.last.queryParameters['token'], 'synthetic +/&');
    h.current.handshake.complete();
    await Future<void>.value();
    h.current.input.add('{"type":"synthetic_event"}');
    h.service.send({'type': 'synthetic_request'});
    expect(delivered.single['type'], 'synthetic_event');
    expect(
        jsonDecode(h.current.sink.sent.single), {'type': 'synthetic_request'});
  });
}
