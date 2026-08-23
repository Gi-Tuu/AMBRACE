// UI 截图测试：聊天 / 宠物 / 首页 三张真实渲染 PNG。
// 仅新增测试文件，不改动任何生产代码；用 mock provider + mock ApiClient(dio adapter) 构造正常主界面状态。
// 截图写入 D:\Codex-Projects\output\ui_screenshots\{chat|pet|home}.png。
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:ai_companion/theme/app_theme.dart';
import 'package:ai_companion/models/message.dart';
import 'package:ai_companion/models/character.dart';
import 'package:ai_companion/models/pet.dart';
import 'package:ai_companion/providers/chat_provider.dart';
import 'package:ai_companion/providers/pets_provider.dart';
import 'package:ai_companion/providers/settings_provider.dart';
import 'package:ai_companion/screens/chat/chat_screen.dart';
import 'package:ai_companion/screens/character/pet_screen.dart';
import 'package:ai_companion/screens/home/character_list_screen.dart';
import 'package:ai_companion/services/api_client.dart';

// ---------------------------------------------------------------------------
// 字体：widget test 默认用 Ahem 字体（中文/图标成方框），需把真实 CJK 字体 + Material 图标字体注册进来。
// ---------------------------------------------------------------------------
const String _cjkFontPath = r'C:\Windows\Fonts\msyh.ttc';
const String _iconsFontPath =
    r'D:\flutter\bin\cache\artifacts\material_fonts\materialicons-regular.otf';

Future<void> _loadFont(String family, String path) async {
  final bytes = File(path).readAsBytesSync();
  final loader = FontLoader(family)
    ..addFont(Future.value(ByteData.view(bytes.buffer)));
  await loader.load();
}

/// 测试专用主题：仅把 CJK 字体挂到各处 text style（不动生产 AppTheme 的颜色/间距/圆角/阴影/字号/字重）。
ThemeData _testTheme() {
  final base = AppTheme.light(0);
  const cjk = 'AppCJK';
  TextStyle? withCjk(TextStyle? s) => s?.copyWith(fontFamily: cjk);
  return base.copyWith(
    textTheme: base.textTheme.apply(fontFamily: cjk),
    appBarTheme: base.appBarTheme.copyWith(titleTextStyle: withCjk(base.appBarTheme.titleTextStyle)),
    navigationBarTheme: base.navigationBarTheme.copyWith(
      labelTextStyle: base.navigationBarTheme.labelTextStyle == null
          ? null
          : WidgetStatePropertyAll(withCjk(base.navigationBarTheme.labelTextStyle!.resolve({}))),
    ),
  );
}

// ---------------------------------------------------------------------------
// Mock ApiClient 的 Dio adapter：让 /api/v1/characters 返回角色列表，其余路径返回结构体空对象。
// 用于 CharacterListScreen（首页）真实渲染出角色卡片列表；其它界面用 provider 注入，不走网络。
// ---------------------------------------------------------------------------
class _MockHttpAdapter implements HttpClientAdapter {
  final List<Map<String, dynamic>> characters;

  _MockHttpAdapter(this.characters);

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final path = options.uri.path;
    String body;
    if (path == '/api/v1/characters') {
      body = jsonEncode({'characters': characters});
    } else {
      body = jsonEncode({
        'characters': <Object>[],
        'items': <Object>[],
        'unread': <Object>[],
        'activities': <Object>[],
        'pets': <Object>[],
      });
    }
    return ResponseBody.fromString(
      body,
      200,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

// ---------------------------------------------------------------------------
// Mock ChatProvider：覆盖 getter 直接返回构造好的消息，避免走后端/WS。
// ---------------------------------------------------------------------------
class MockChatProvider extends ChatProvider {
  final AICharacter _char;
  final List<ChatMessage> _msgs;
  MockChatProvider(this._char, this._msgs);

  @override
  AICharacter? get currentCharacter => _char;
  @override
  int? get sessionId => 100;
  @override
  bool get isLoading => false;
  @override
  bool get isTyping => false;
  @override
  bool get isSending => false;
  @override
  String? get error => null;

  @override
  List<ChatMessage> get messages => List<ChatMessage>.from(_msgs)
    ..sort((a, b) {
      final ta = DateTime.tryParse(a.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
      final tb = DateTime.tryParse(b.createdAt) ?? DateTime.fromMillisecondsSinceEpoch(0);
      return ta.compareTo(tb);
    });

  void addMessage(ChatMessage m) {
    _msgs.add(m);
    notifyListeners();
  }
}

// ---------------------------------------------------------------------------
// Mock PetsProvider：覆盖 getter 直接返回一只宠物 + 互动记录。
// ---------------------------------------------------------------------------
class MockPetsProvider extends PetsProvider {
  final List<Pet> _pets;
  final List<Map<String, dynamic>> _acts;
  MockPetsProvider(this._pets, this._acts);

  @override
  List<Pet> get pets => _pets;
  @override
  int? get selectedId => _pets.isNotEmpty ? _pets.first.id : null;
  @override
  Pet? get selectedPet => _pets.isNotEmpty ? _pets.first : null;
  @override
  bool get loading => false;
  @override
  bool get hasAttention => _pets.any((p) => p.needAttention);
  @override
  String? get error => null;
  @override
  List<Map<String, dynamic>> get activities => _acts;
  @override
  Future<void> loadPets() async {}
  @override
  Future<bool> interact(String action) async => true;
  @override
  void selectPet(int id) {}
}

// 首页底部导航（来自 HomeScreen.build 的真实去处），让截图呈现"首页"而非孤立的好友列表。
class _HomeFrame extends StatelessWidget {
  final Widget child;
  const _HomeFrame({required this.child});
  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: 0,
        destinations: [
          NavigationDestination(icon: const Icon(Icons.people), label: l10n.tabFriends),
          NavigationDestination(icon: const Icon(Icons.favorite_outline), label: l10n.tabMoments),
          NavigationDestination(icon: const Icon(Icons.forum_outlined), label: l10n.tabAiInteraction),
          NavigationDestination(icon: const Icon(Icons.home_outlined), label: '小家'),
        ],
      ),
    );
  }
}

Widget _wrapL10n(ThemeData theme, Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: theme,
      locale: const Locale('zh'),
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        ...AppLocalizations.localizationsDelegates,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      home: home,
    );

Key _captureKey() => const Key('capture_boundary');

Future<void> _capture(WidgetTester tester, Key key, String outPath) async {
  final boundary = tester.renderObject<RenderRepaintBoundary>(find.byKey(key));
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 3.0);
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
    final bytes = byteData!.buffer.asUint8List();
    File(outPath).writeAsBytesSync(bytes);
  });
  final size = File(outPath).lengthSync();
  // ignore: avoid_print
  print('  saved $outPath  $size bytes');
  expect(size > 30 * 1024, isTrue, reason: '$outPath should be > 30KB, got $size bytes');
}

void main() {
  setUpAll(() async {
    await _loadFont('AppCJK', _cjkFontPath);
    await _loadFont('MaterialIcons', _iconsFontPath);
    // 让所有界面里的 incidental 网络调用走 mock adapter，避免 dio 空 baseUrl 的同步/异步不确定性。
    ApiClient().configure(baseUrl: 'http://test.local', token: 'test-token');
    ApiClient().dio.httpClientAdapter = _MockHttpAdapter(_mockCharacters);
  });

  // 构造 mock 角色（供首页 / 聊天页共用）
  final characters = [
    AICharacter(
      id: 1, name: '苏晓', personality: '温柔细腻，喜欢倾听',
      greetingMessage: '早呀，今天想聊点什么？', isActive: true,
    ),
    AICharacter(id: 2, name: '林澈', personality: '理性睿智，爱讲冷知识', isActive: true),
    AICharacter(id: 3, name: '星野', personality: '元气满满，话痨小太阳', isActive: true),
    AICharacter(id: 4, name: '白露', personality: '清冷寡言，百科全书', isActive: true),
  ];

  testWidgets('截图：聊天界面（含最近一条入场动画稳定帧）', (tester) async {
    tester.view.physicalSize = const Size(1170, 2532);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    final chat = MockChatProvider(characters[0], [
      _msg(1, 'user', '早呀，今天天气不错', '2026-08-01T09:00:00.000Z'),
      _msg(2, 'ai', '早！是啊，阳光特别好。今天有什么想做的事吗？', '2026-08-01T09:00:30.000Z'),
      _msg(3, 'user', '我想去公园走走，顺便拍点照片', '2026-08-01T09:01:00.000Z'),
      _msg(4, 'ai', '那太好了。记得带瓶水，我帮你把要带的都列好啦～', '2026-08-01T09:01:40.000Z'),
      _msg(5, 'user', '好呀，你真是太贴心了', '2026-08-01T09:03:00.000Z'),
      _msg(6, 'ai', '举手之劳嘛。对了，你上次说的那本书，我读到一半了。', '2026-08-01T09:03:30.000Z'),
      _msg(7, 'user', '哈哈哈你居然真在看，我以为你只会聊天呢', '2026-08-01T09:05:00.000Z'),
    ]);

    final key = _captureKey();
    await tester.pumpWidget(
      _wrapL10n(
        _testTheme(),
        MultiProvider(
          providers: [
            ChangeNotifierProvider<SettingsProvider>.value(value: SettingsProvider()),
            ChangeNotifierProvider<ChatProvider>.value(value: chat),
          ],
          child: RepaintBoundary(key: key, child: const ChatScreen()),
        ),
      ),
    );
    // 首帧装载历史消息（入场动画针对"新插入项"，装载历史不播）
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));

    // 追加最后一条 AI 消息 → 触发其 EntranceFade 入场动画，播放到稳定帧（opacity=1）。
    chat.addMessage(_msg(8, 'ai', '这也是在向你靠近呀。你开心，我就开心。', '2026-08-01T09:06:00.000Z'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300)); // 入场动画 280ms 播完
    await tester.pump(const Duration(milliseconds: 150)); // 自动滚到底

    await _capture(tester, key, r'D:\Codex-Projects\output\ui_screenshots\chat.png');
  });

  testWidgets('截图：宠物界面（正常待机/交互后状态）', (tester) async {
    tester.view.physicalSize = const Size(1170, 2532);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    final pets = MockPetsProvider([
      const Pet(
        id: 1, name: '团子', species: 'cat', speciesLabel: '猫',
        level: 3, exp: 120, hunger: 72, mood: 88, energy: 66, cleanliness: 90,
        statusText: '快乐地摇尾巴', needAttention: false, isSpecial: false,
        createdAt: '2026-08-01T10:00:00Z',
      ),
    ], [
      {'content': '你喂了 团子 一块小鱼干', 'created_at': '2026-08-01T10:30:00.000Z'},
      {'content': '团子 开心地打了个滚', 'created_at': '2026-08-01T09:20:00.000Z'},
      {'content': '你轻轻摸了摸 团子 的绒毛', 'created_at': '2026-08-01T08:05:00.000Z'},
    ]);

    final key = _captureKey();
    await tester.pumpWidget(
      _wrapL10n(
        _testTheme(),
        ChangeNotifierProvider<PetsProvider>.value(
          value: pets,
          child: RepaintBoundary(key: key, child: const PetScreen()),
        ),
      ),
    );
    // 宠物有 repeat 待机动画，不能 pumpAndSettle；用固定帧推进并让 Image.network 落到 errorBuilder。
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump(const Duration(milliseconds: 200));

    await _capture(tester, key, r'D:\Codex-Projects\output\ui_screenshots\pet.png');
  });

  testWidgets('截图：首页（好友列表 + 入口 + 底部导航）', (tester) async {
    tester.view.physicalSize = const Size(1170, 2532);
    tester.view.devicePixelRatio = 3.0;
    addTearDown(tester.view.reset);

    final key = _captureKey();
    await tester.pumpWidget(
      _wrapL10n(
        _testTheme(),
        MultiProvider(
          providers: [
            ChangeNotifierProvider<ChatProvider>(create: (_) => MockChatProvider(characters[0], [])),
            ChangeNotifierProvider<SettingsProvider>(create: (_) => SettingsProvider()),
          ],
          child: RepaintBoundary(
            key: key,
            child: _HomeFrame(child: const CharacterListScreen()),
          ),
        ),
      ),
    );
    // 让 getCharacters(mock adapter) 的 Future 微任务落地，渲染出角色卡片列表。
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pump(const Duration(milliseconds: 200));

    await _capture(tester, key, r'D:\Codex-Projects\output\ui_screenshots\home.png');
  });
}

ChatMessage _msg(int id, String sender, String content, String createdAt) =>
    ChatMessage(
      id: id,
      sessionId: 100,
      senderType: sender,
      content: content,
      createdAt: createdAt,
      isLocal: sender == 'user',
    );

final List<Map<String, dynamic>> _mockCharacters = [
  {'id': 1, 'name': '苏晓', 'personality': '温柔细腻，喜欢倾听', 'avatar_url': null},
  {'id': 2, 'name': '林澈', 'personality': '理性睿智，爱讲冷知识', 'avatar_url': null},
  {'id': 3, 'name': '星野', 'personality': '元气满满，话痨小太阳', 'avatar_url': null},
  {'id': 4, 'name': '白露', 'personality': '清冷寡言，百科全书', 'avatar_url': null},
];
