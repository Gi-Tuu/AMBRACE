import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:record/record.dart';

import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/features/chat/voice_call_screen.dart';
import 'package:ai_companion/services/voice_call_recorder.dart';
import 'package:ai_companion/services/voice_call_transport.dart';
import 'package:ai_companion/services/voice_playback_queue.dart';

/// 测试注入 fake 传输层：不发真实 WS，手动推帧/触发 done。
class FakeVoiceTransport implements VoiceCallTransport {
  Uri? connectedUri;
  VoiceFrameHandler? onFrame;
  VoidCallback? onDone;
  void Function(Object)? onError;
  final List<Map<String, dynamic>> sentText = [];
  final List<Uint8List> sentBinary = [];
  bool closed = false;

  @override
  bool get isConnected => !closed;

  @override
  void connect(
    Uri uri, {
    VoiceFrameHandler? onFrame,
    VoidCallback? onDone,
    void Function(Object error)? onError,
  }) {
    connectedUri = uri;
    this.onFrame = onFrame;
    this.onDone = onDone;
    this.onError = onError;
  }

  @override
  void sendText(Map<String, dynamic> data) => sentText.add(data);

  @override
  void sendBinary(Uint8List bytes) => sentBinary.add(bytes);

  @override
  Future<void> close() async => closed = true;

  // 测试辅助
  void emitFrame(Map<String, dynamic> d) => onFrame?.call(d);
  void emitDone() => onDone?.call();
  bool hasTypeInSent(String type) => sentText.any((m) => m['type'] == type);
}

/// 测试注入 fake 录音：不发平台通道。
class FakeVoiceRecorder implements VoiceCallRecorder {
  bool permission = true;
  final List<String> startedPaths = [];
  String? lastPath;
  int stopCount = 0;
  final StreamController<Amplitude> _amp = StreamController<Amplitude>.broadcast();

  @override
  Future<bool> hasPermission() async => permission;

  @override
  Future<void> start({required String path}) async {
    startedPaths.add(path);
    lastPath = path;
  }

  @override
  Future<String?> stop() async {
    stopCount++;
    return lastPath;
  }

  @override
  Stream<Amplitude>? get amplitude => _amp.stream;

  @override
  Future<void> dispose() async {
    await _amp.close();
  }

  void emitAmplitude(double db) => _amp.add(Amplitude(current: db, max: 0));
}

/// 测试注入 fake 播放器（复用语音队列的抽象）。
class FakeVoicePlayer implements VoiceAudioPlayer {
  final StreamController<void> _complete = StreamController<void>.broadcast();
  final List<String> played = [];
  bool stopped = false;
  bool disposed = false;

  @override
  Stream<void> get onComplete => _complete.stream;

  @override
  Future<void> play(String url) async {
    played.add(url);
  }

  @override
  Future<void> stop() async => stopped = true;

  @override
  Future<void> dispose() async {
    disposed = true;
    await _complete.close();
  }

  void completeCurrent() => _complete.add(null);
}

void main() {
  late FakeVoiceTransport transport;
  late FakeVoiceRecorder recorder;
  late FakeVoicePlayer player;
  late VoicePlaybackQueue queue;

  setUp(() {
    transport = FakeVoiceTransport();
    recorder = FakeVoiceRecorder();
    player = FakeVoicePlayer();
    queue = VoicePlaybackQueue(playerFactory: () => player);
  });

  Widget app({int maxReconnect = 2}) => MaterialApp(
        locale: const Locale('zh'),
        localizationsDelegates: const [
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
          ...AppLocalizations.localizationsDelegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: VoiceCallScreen(
          baseUrl: 'http://127.0.0.1:9',
          token: 'test-token',
          sessionId: 7,
          character: AICharacter(id: 1, name: 'Alpha'),
          transport: transport,
          recorder: recorder,
          playbackQueue: queue,
          maxReconnectAttempts: maxReconnect,
        ),
      );

  final micFinder = find.byKey(const Key('voiceMicButton'));
  final hangupFinder = find.byKey(const Key('voiceHangupButton'));

  testWidgets('连接并发送 session_start；收到 ready.ok=true → 通话中', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();

    // 发起连接并已发 session_start（含 session_id / character_id）
    expect(transport.connectedUri, isNotNull);
    expect(transport.connectedUri!.queryParameters['token'], 'test-token');
    expect(transport.connectedUri!.path, '/api/v1/voice/stream');
    expect(transport.hasTypeInSent('session_start'), isTrue);
    expect(transport.sentText.first['session_id'], 7);
    expect(transport.sentText.first['character_id'], 1);

    // 初始：正在接通…
    expect(find.text('正在接通…'), findsOneWidget);

    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();
    expect(find.text('通话中，请说话'), findsOneWidget);
  });

  testWidgets('收到 ai_thinking 播放思考音一次 + tts_audio 逐句排队', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    // 思考音 → 入队自动播放（URL 解析为绝对地址）
    transport.emitFrame({'type': 'ai_thinking', 'url': '/uploads/tts/think.mp3'});
    await tester.pump();
    expect(player.played, contains('http://127.0.0.1:9/uploads/tts/think.mp3'));

    // tts_audio → 状态切到「对方正在说话…」并逐句排队（思考音在播，a.mp3 待播）
    transport.emitFrame({'type': 'ai_speaking_start'});
    transport.emitFrame({'type': 'llm_sentence', 'text': '你好呀'});
    transport.emitFrame({'type': 'tts_audio', 'url': '/uploads/tts/a.mp3'});
    await tester.pump();
    expect(find.text('对方正在说话…'), findsOneWidget);
    expect(find.text('你好呀'), findsOneWidget);
    expect(queue.pendingCount, 1);

    // 播完思考音 → a.mp3 自动播放（顺序队列）
    player.completeCurrent();
    await tester.pump();
    expect(player.played, contains('http://127.0.0.1:9/uploads/tts/a.mp3'));

    transport.emitFrame({'type': 'ai_speaking_end'});
    await tester.pump();
    expect(find.text('通话中，请说话'), findsOneWidget);
  });

  testWidgets('asr_final 回显用户文字；空文本提示没听清', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    transport.emitFrame({'type': 'asr_final', 'text': '早上好呀'});
    await tester.pump();
    expect(find.text('早上好呀'), findsOneWidget);

    transport.emitFrame({'type': 'asr_final', 'text': ''});
    await tester.pump();
    expect(find.text('没听清，再说一遍'), findsOneWidget);
  });

  testWidgets('ai_interrupted 停止播放并提示打断', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    transport.emitFrame({'type': 'ai_speaking_start'});
    transport.emitFrame({'type': 'tts_audio', 'url': '/uploads/tts/a.mp3'});
    await tester.pump();
    expect(player.played, isNotEmpty);

    transport.emitFrame({'type': 'ai_interrupted'});
    await tester.pump();
    expect(player.stopped, isTrue);
    expect(find.text('已打断对方'), findsOneWidget);
    expect(find.text('通话中，请说话'), findsOneWidget);
  });

  testWidgets('error 帧显示错误信息', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    transport.emitFrame({'type': 'error', 'message': '语音回复失败'});
    await tester.pump();
    expect(find.text('语音回复失败'), findsOneWidget);
  });

  testWidgets('按住说话：长按录音、松手停止并回通话中', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    final gesture = await tester.startGesture(tester.getCenter(micFinder));
    await tester.pump(const Duration(milliseconds: 600));
    await tester.pump();
    // 进入录音状态，recorder.start 被调用
    expect(recorder.startedPaths, hasLength(1));
    expect(find.textContaining('说话中'), findsOneWidget);

    await gesture.up();
    await tester.pump();
    await tester.pump();
    // 松手停止录音并回到通话中
    expect(recorder.stopCount, greaterThanOrEqualTo(1));
    expect(find.text('通话中，请说话'), findsOneWidget);
  });

  testWidgets('挂断：发送 session_end、关闭连接', (tester) async {
    await tester.pumpWidget(app());
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    await tester.tap(hangupFinder);
    await tester.pump();
    await tester.pump();
    expect(transport.hasTypeInSent('session_end'), isTrue);
    expect(transport.closed, isTrue);
  });

  testWidgets('断线且超过重连上限 → 显示连接已断开', (tester) async {
    await tester.pumpWidget(app(maxReconnect: 0));
    await tester.pump();
    transport.emitFrame({'type': 'ready', 'ok': true});
    await tester.pump();

    transport.emitDone();
    await tester.pump();
    expect(find.text('连接已断开'), findsOneWidget);
  });
}
