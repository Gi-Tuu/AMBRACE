import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import 'platform/browser_origin.dart';
import 'l10n/reader_strings.dart';
import 'l10n/character_editor_strings.dart';
import 'l10n/session_strings.dart';
import 'l10n/editor_strings.dart';
import 'l10n/attachment_strings.dart';
import 'features/chat/session_strip.dart';
import 'features/chat/attachment_state.dart';
import 'features/chat/attachment_pending_card.dart';
import 'features/chat/file_message_card.dart';
import 'features/character_management/character_editor_sheet.dart';
import 'features/reader/profile_pane.dart';
import 'features/reader/memories_pane.dart';
import 'features/reader/char_diary_pane.dart';
import 'features/reader/moments_pane.dart';
import 'features/reader/memos_pane.dart';
import 'features/reader/my_diary_pane.dart';

/// 当前语言（首单用极简状态：壳内切换 zh/en，文案全部走 CoreStrings 成对表）。
final ValueNotifier<String> appLang = ValueNotifier<String>('zh');

void main() {
  runApp(const AmbracePwaApp());
}

class AmbracePwaApp extends StatelessWidget {
  const AmbracePwaApp({super.key, this.defaultBaseUrl});

  /// 可注入的同源默认地址（宿主/测试用）；null ⇒ 运行时取当前页面 origin（浏览器里即同源）。
  final String? defaultBaseUrl;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<String>(
      valueListenable: appLang,
      builder: (context, lang, _) {
        return MaterialApp(
          title: CoreStrings(lang).t('appTitle'),
          debugShowCheckedModeBanner: false,
          theme: ThemeData(
            colorSchemeSeed: const Color(0xFF6C63FF),
            useMaterial3: true,
            brightness: Brightness.light,
          ),
          home: WorkspacePage(lang: lang, defaultBaseUrl: defaultBaseUrl),
        );
      },
    );
  }
}

/// 薄壳的隔离层：Web 只能用内存存储（**不落** localStorage/缓存/URL）。
class WebShellEnv {
  WebShellEnv({String? defaultBaseUrl})
      : defaultBase = defaultBaseUrl ?? pageOrigin() ?? '';

  /// 默认连接地址 = 当前页面 **origin**（同源：scheme+host+port，不含 `/pwa/` 子路径）。
  /// 浏览器里由 [pageOrigin] 取到；非浏览器环境（VM/测试）取不到即留空，可由构造参数注入。
  /// 曾写死 `http://127.0.0.1:18080`（旧合成 mock 地址）——那是错的默认，已移除。
  final String defaultBase;

  final KeyValueStore store = InMemoryKeyValueStore();
  late final AmbIdentityVerifier identity =
      AmbIdentityVerifier(store: store, currentBaseUrl: defaultBase);
  late final AmbApiClient client = AmbApiClient(identity: identity);
  late final ChatTransport transport = ChatTransport(client);
  late final AmbAttachmentApi attachment = AmbAttachmentApi(client);
}

class WorkspacePage extends StatefulWidget {
  const WorkspacePage({
    super.key,
    required this.lang,
    this.defaultBaseUrl,
    this.picker,
  });

  final String lang;
  final String? defaultBaseUrl;

  /// 可注入的文本文件选择器（Web 默认走 DOM `<input type=file>`；测试注入假实现）。
  final TextFilePicker? picker;

  @override
  State<WorkspacePage> createState() => _WorkspacePageState();
}

class _WorkspacePageState extends State<WorkspacePage> {
  late final WebShellEnv env = WebShellEnv(defaultBaseUrl: widget.defaultBaseUrl);

  late final TextEditingController _server = TextEditingController(text: env.defaultBase);
  final _user = TextEditingController();
  final _pass = TextEditingController();
  final _code = TextEditingController();
  final _input = TextEditingController();

  String? _token;
  String _status = '';
  String _pairInfo = '';
  List<Character> _characters = const [];
  Character? _current;
  int? _sessionId;
  List<ChatMessage> _messages = const [];
  String _streaming = '';

  // ── 工作台导航（M1a-2）：chat 之外六个只读模块 ─────────────────────
  String _section = 'chat';
  static const List<String> _sections = [
    'chat', 'profile', 'memories', 'charDiary', 'moments', 'memos', 'myDiary',
  ];
  bool get _sectionNeedsCharacter =>
      _section == 'profile' || _section == 'memories' || _section == 'charDiary';

  ReaderStrings get rs => ReaderStrings(widget.lang);

  // ── 并发隔离（主审 §3）：登录/换号代次 + 会话代次 + busy 归属 ──────────────────
  int _authGen = 0; // 登录 / 换号 / 退出 / 401 会递增
  /// 角色资料版本：创建/编辑成功后 +1，用于强制 ProfilePane 重新 GET（同 id 也能刷新）。
  int _characterRev = 0;
  int _sessionGen = 0; // 选择角色（建会话）会递增
  Object? _busyOwner; // busy 归属令牌：旧任务的 finally 不得清掉新任务的 busy
  CancelToken? _inflight; // 当前在飞的请求/流：切会话/退出时**真实取消**

  // ── 会话工作台（M1a-3）：会话列表 / 选中项 / 历史分页游标 / 新建 ──────────────
  List<ChatSessionSummary> _sessions = const [];
  String _sessionListStatus = 'ready'; // loading | ready | error
  String _sessionListError = '';
  int? _selectedSessionId; // 当前聊天会话（未 ready 前为 null ⇒ _send 自然被挡住）
  int _sessionListGen = 0; // 会话列表请求代次（旧列表响应不得覆盖新列表）
  bool _creatingSession = false; // 显式新建 pending 禁重入
  /// 本角色**本次已有**的有效选中会话：换角色回来优先恢复它（只用 GET，不 POST）。
  final Map<int, int> _selectedByCharacter = {};

  // 历史分页：_historyRaw＝服务端**原始窗口**已消费行数（skip 的来源，非展示条数）；
  // _historyTotal＝服务端 total。加载更早只按 raw 推进，按稳定 message.id 去重。
  int _historyTotal = 0;
  int _historyRaw = 0;
  bool _loadingEarlier = false;
  String _earlierError = '';
  final ScrollController _historyScroll = ScrollController();

  // ── 未发草稿归属（M1a-3 细化）：按「角色 + 会话」分桶 ────────────────────────
  // key = '<characterId>#<sessionId|new>'；同角色不同会话不串草稿；换号/401 整体清空。
  final Map<String, String> _drafts = {};
  String? _draftKey;

  int? _myUserId; // GET /api/v1/auth/profile 的本人 id（编辑归属判定）；未就绪 = null
  late final EditorApi _editor = EditorApi(env.client);

  // ── 文本附件（M1a-4）：单文件待发卡 / 阶段 / 取消 / 未知结果 ────────────────
  late final TextFilePicker _picker =
      widget.picker ?? createPlatformTextFilePicker();
  PendingAttachment? _pending; // 绑定发起时的角色 + 会话；切换会话即作废
  String _pendingError = ''; // 本地拒绝/失败/取消文案（已本地化）
  CancelToken? _attachCancel; // 当前在飞的附件请求：可真实取消本地等待
  int _attachGen = 0; // 附件代次：作废旧回调（会话/账号/移除时递增）
  // 选择代次（返工 R2）：latest-wins——更新的选择会让更早的**未回**选择结果失效；
  // 切角色/会话/退出/401/移除（经 _clearPendingAttachment）同样递增 ⇒ 晚到结果不恢复旧文件名/字节。
  int _pickGen = 0;

  bool _busy = false;

  SessionStrings get ss => SessionStrings(widget.lang);
  EditorStrings get es => EditorStrings(widget.lang);
  AttachmentStrings get as_ => AttachmentStrings(widget.lang);

  CoreStrings get s => CoreStrings(widget.lang);

  bool _staleAuth(int gen) => !mounted || gen != _authGen;

  bool _staleSession(int gen, int authGen) =>
      !mounted || gen != _sessionGen || authGen != _authGen;

  void _busyBegin(Object owner) {
    _busyOwner = owner;
    if (mounted) setState(() => _busy = true);
  }

  void _busyEnd(Object owner) {
    if (!identical(_busyOwner, owner)) return; // 旧任务不得清除新任务的 busy
    _busyOwner = null;
    if (mounted) setState(() => _busy = false);
  }

  void _cancelInflight() {
    final c = _inflight;
    _inflight = null;
    if (c != null && !c.isCancelled) c.cancel('superseded');
  }

  /// 会话代次作废 + 取消在飞请求 + 附件作废（退出/401/换号共用）。
  void _invalidateAll() {
    _authGen++;
    _sessionGen++;
    _cancelInflight();
    _clearPendingAttachment();
  }

  /// 待发附件一并作废：取消在飞请求、递增附件代次（旧回调/晚到进度/结果全部丢弃），
  /// 释放文件字节引用（字节只曾驻内存，不落 localStorage/缓存/URL）。
  /// 返工 R2：同时递增**选择代次** ⇒ 在飞的（未回的）文件选择结果也一并失效，
  /// 晚到后不会把旧文件名/字节重新挂回待发卡。
  void _clearPendingAttachment() {
    _attachGen++;
    _pickGen++;
    final ct = _attachCancel;
    _attachCancel = null;
    if (ct != null && !ct.isCancelled) ct.cancel('attachment-cleared');
    _pending = null;
    _pendingError = '';
  }

  @override
  void initState() {
    super.initState();
    // 壳启动不阻塞首屏：网络初始化/身份加载放后台，失败也不黑屏。
    env.client.onUnauthorized = () {
      if (!mounted) return;
      _closeTransientRoutes(); // 私人详情/选择器等临时 route 立即作废（不留可返回的旧内容）
      _invalidateAll();
      env.client.clearAuth();
      _clearPersonalInputs(); // 换号/退出：草稿与输入框属个人数据，一并清空
      setState(() {
        _token = null;
        _messages = const [];
        _characters = const [];
        _sessionId = null;
        _current = null;
        _streaming = '';
        _section = 'chat';
        _status = s.t('unauthorized');
      });
    };
    env.client.configure(baseUrl: _server.text); // 未登录：仅设地址，清认证头
  }

  @override
  void dispose() {
    _invalidateAll(); // 销毁页面：作废所有代次并真实取消在飞请求
    _historyScroll.dispose();
    _server.dispose();
    _user.dispose();
    _pass.dispose();
    _code.dispose();
    _input.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    final gen = ++_authGen;
    _cancelInflight();
    final owner = Object();
    _busyBegin(owner);
    if (mounted) setState(() => _status = s.t('connecting'));
    try {
      env.client.configure(baseUrl: _server.text);
      final token = await env.transport.login(_user.text.trim(), _pass.text);
      if (_staleAuth(gen)) return;
      env.client.configure(baseUrl: _server.text, token: token);
      final chars = await env.transport.getCharacters();
      if (_staleAuth(gen)) return;
      setState(() {
        _token = token;
        _characters = chars;
        _status = '';
      });
      _loadProfile(gen); // 取本人 id（编辑归属判定）；不阻塞首屏
    } on Object catch (e) {
      if (_staleAuth(gen)) return;
      setState(() => _status = localizedError(e, s));
    } finally {
      _busyEnd(owner);
    }
  }

  Future<void> _logout() async {
    _closeTransientRoutes(); // 退出：临时 route（详情/选择器）一并作废
    _invalidateAll();
    env.client.clearAuth();
    _clearPersonalInputs();
    await env.identity.unpair();
    if (!mounted) return;
    setState(() {
      _token = null;
      _characters = const [];
      _current = null;
      _sessionId = null;
      _messages = const [];
      _streaming = '';
      _status = '';
      _pairInfo = '';
      _section = 'chat';
    });
  }

  Future<void> _pair() async {
    final owner = Object();
    _busyBegin(owner);
    try {
      final r = await env.identity.pair(_code.text.trim(), _server.text);
      if (!mounted) return;
      setState(() => _pairInfo = r.ok
          ? '${s.t('pairOk')} · ${r.serverName} · ${r.fingerprintDisplay}'
          : '${s.t('pairFailed')}：${s.pairFailureText(r.failure)}');
    } finally {
      _busyEnd(owner);
    }
  }

  // ── 会话工作台（M1a-3）──────────────────────────────────────────────

  String _dkey(int characterId, int? sid) => '$characterId#${sid ?? 'new'}';

  /// 草稿跟随**选中会话**：切换会话/角色时先把当前输入存回旧桶，再载入新桶
  /// （既不静默丢失，也不会被当成新会话的草稿发出去）。
  void _retargetDraft(int characterId, int? sid) {
    final old = _draftKey;
    if (old != null) _drafts[old] = _input.text;
    final k = _dkey(characterId, sid);
    _draftKey = k;
    _input.text = _drafts[k] ?? '';
  }

  /// 拉取当前**账号**的会话列表（接口没有角色过滤/排序/分页 query，筛选在客户端做）。
  Future<void> _loadSessionList(int authGen, int gen, CancelToken ct) async {
    final lgen = ++_sessionListGen;
    setState(() {
      _sessionListStatus = 'loading';
      _sessionListError = '';
    });
    try {
      final list = await env.transport.listSessions(cancelToken: ct);
      if (_staleSession(gen, authGen) || lgen != _sessionListGen) return;
      setState(() {
        _sessions = list;
        _sessionListStatus = 'ready';
      });
    } on Object catch (e) {
      if (_staleSession(gen, authGen) || lgen != _sessionListGen) return;
      setState(() {
        _sessionListStatus = 'error';
        _sessionListError = localizedError(e, s);
      });
    }
  }

  /// 历史首屏（窗口 skip=0）：按 total 重置原始游标；结果只在代次仍当前时提交。
  Future<void> _loadHistoryFirst(
      int sid, int authGen, int gen, CancelToken ct) async {
    final page = await env.transport
        .getMessagePage(sid, skip: 0, limit: 50, cancelToken: ct);
    if (_staleSession(gen, authGen)) return;
    setState(() {
      _messages = page.messages;
      _historyTotal = page.total;
      _historyRaw = page.messages.length;
      _loadingEarlier = false;
      _earlierError = '';
    });
  }

  /// 进入 chat：为**当前对象**恢复/建立会话。
  /// ① 若该角色**本次**已显式选中过某会话且它仍在列表里 ⇒ 只 GET（不 POST、不改已读）；
  /// ② 否则保留既有默认复用语义（POST createSession，默认 force_new=false）。
  Future<void> _openCharacter(Character c) async {
    final authGen = _authGen;
    final gen = ++_sessionGen; // 每次选择都开新代次
    _cancelInflight();
    final ct = CancelToken();
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    _retargetDraft(c.id, null);
    _clearPendingAttachment(); // 切角色：作废旧附件（不能把 A 的文件写到新对象）
    setState(() {
      _current = c;
      _messages = const [];
      _streaming = '';
      _sessionId = null;
      _selectedSessionId = null;
      _historyTotal = 0;
      _historyRaw = 0;
      _loadingEarlier = false;
      _earlierError = '';
      _status = '';
    });
    try {
      await _loadSessionList(authGen, gen, ct);
      if (_staleSession(gen, authGen)) return;
      final remembered = _selectedByCharacter[c.id];
      int sid;
      if (remembered != null && _sessions.any((x) => x.id == remembered)) {
        sid = remembered; // 只 GET：不 POST 建会话、不改已读标记
      } else {
        sid = await env.transport.createSession(c.id, cancelToken: ct); // 旧默认复用语义
        if (_staleSession(gen, authGen)) return;
        await _loadSessionList(authGen, gen, ct); // 默认分支可能新建：列表刷新
        if (_staleSession(gen, authGen)) return;
      }
      await _loadHistoryFirst(sid, authGen, gen, ct);
      if (_staleSession(gen, authGen)) return;
      setState(() {
        _sessionId = sid;
        _selectedSessionId = sid;
        _selectedByCharacter[c.id] = sid;
      });
      _retargetDraft(c.id, sid);
    } on Object catch (e) {
      if (_staleSession(gen, authGen)) return;
      setState(() => _status = localizedError(e, s));
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      _busyEnd(owner);
    }
  }

  /// 选择**已存在**会话：只 GET 该会话历史，不 POST 建会话、不改已读标记。
  Future<void> _selectSession(ChatSessionSummary sum) async {
    final c = _current;
    if (c == null || sum.characterId != c.id) return;
    if (_selectedSessionId == sum.id && _sessionId == sum.id) return; // 已选中：无操作
    final authGen = _authGen;
    final gen = ++_sessionGen;
    _cancelInflight();
    final ct = CancelToken();
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    _retargetDraft(c.id, sum.id);
    _clearPendingAttachment(); // 切会话：作废旧附件请求与待发文件
    setState(() {
      _sessionId = null; // ready 前禁止发送
      _selectedSessionId = sum.id;
      _messages = const [];
      _streaming = '';
      _historyTotal = 0;
      _historyRaw = 0;
      _loadingEarlier = false;
      _earlierError = '';
      _status = '';
    });
    try {
      await _loadHistoryFirst(sum.id, authGen, gen, ct);
      if (_staleSession(gen, authGen)) return;
      setState(() {
        _sessionId = sum.id;
        _selectedByCharacter[c.id] = sum.id;
      });
    } on Object catch (e) {
      if (_staleSession(gen, authGen)) return;
      setState(() => _status = localizedError(e, s));
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      _busyEnd(owner);
    }
  }

  /// 显式「新建会话」：真正新建（force_new=true），旧会话/消息保持不变。
  /// 新建响应**丢失**时**不自动重发**（避免生成多份），刷新列表核对并如实告知未确认。
  Future<void> _newSession() async {
    final c = _current;
    if (c == null || _creatingSession || _busy) return;
    final authGen = _authGen;
    final gen = ++_sessionGen;
    _cancelInflight();
    final ct = CancelToken();
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    _clearPendingAttachment(); // 新建会话：作废旧附件请求与待发文件
    final beforeIds = _sessions.map((x) => x.id).toSet();
    setState(() {
      _creatingSession = true;
      _status = '';
    });
    try {
      final sid = await env.transport
          .createSession(c.id, forceNew: true, cancelToken: ct);
      if (_staleSession(gen, authGen)) return;
      _retargetDraft(c.id, sid);
      setState(() {
        _sessionId = null;
        _selectedSessionId = sid;
        _selectedByCharacter[c.id] = sid;
        _messages = const [];
        _streaming = '';
        _historyTotal = 0;
        _historyRaw = 0;
      });
      await _loadSessionList(authGen, gen, ct);
      if (_staleSession(gen, authGen)) return;
      await _loadHistoryFirst(sid, authGen, gen, ct);
      if (_staleSession(gen, authGen)) return;
      setState(() => _sessionId = sid);
    } on Object catch (e) {
      if (_staleSession(gen, authGen)) return;
      // 结果未确认：**不**自动重发 force_new（避免生成多份）；刷新列表核对。
      // 政策①：**保持当前（旧）会话的会话/历史/草稿完整不变**——绝不在旧草稿、旧历史上
      // 把发送归属悄悄改到新会话（禁止半切换）。刷新后若列表里出现新会话，只如实提示
      // 「结果未确认」，让用户自己点选核对，**不猜 fresh.first 就是本次创建的那个**。
      bool unconfirmed = false;
      try {
        final list = await env.transport.listSessions();
        if (!_staleSession(gen, authGen)) {
          unconfirmed = list
              .any((x) => x.characterId == c.id && !beforeIds.contains(x.id));
          setState(() {
            _sessions = list;
            _sessionListStatus = 'ready';
            _sessionListError = '';
          });
        }
      } catch (_) {}
      if (!_staleSession(gen, authGen)) {
        setState(() => _status = unconfirmed
            ? ss.t('newSessionUnconfirmed')
            : '${ss.t('newSessionFailed')}：${localizedError(e, s)}');
      }
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      _creatingSession = false;
      if (mounted) setState(() {});
      _busyEnd(owner);
    }
  }

  /// 加载更早：用真实 skip/limit/total，按**原始窗口**推进（不拿展示长度当 cursor），
  /// 按稳定 message.id 去重并保持时间正序；保留可见锚点（不强跳末尾）；
  /// 重复页/空页都不死循环（游标按服务端返回条数前进）。
  Future<void> _loadEarlier() async {
    final sid = _sessionId;
    if (sid == null || _loadingEarlier) return;
    if (_historyRaw >= _historyTotal) return; // 已是窗口最早
    final authGen = _authGen;
    final gen = _sessionGen;
    final ct = CancelToken();
    final bottomGap = _historyScroll.hasClients
        ? _historyScroll.position.maxScrollExtent - _historyScroll.position.pixels
        : null;
    setState(() {
      _loadingEarlier = true;
      _earlierError = '';
    });
    try {
      final page = await env.transport
          .getMessagePage(sid, skip: _historyRaw, limit: 50, cancelToken: ct);
      if (_staleSession(gen, authGen) || _sessionId != sid) return;
      final seen = _messages.map((m) => m.id).toSet();
      final older = [for (final m in page.messages) if (seen.add(m.id)) m];
      setState(() {
        _messages = [...older, ..._messages]; // 更早的在前，整体仍按时间正序
        _historyTotal = page.total;
        _historyRaw = _historyRaw + page.messages.length; // 原始窗口推进，与去重无关
        _loadingEarlier = false;
      });
      if (bottomGap != null) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!_historyScroll.hasClients) return;
          final pos = _historyScroll.position;
          final target = (pos.maxScrollExtent - bottomGap)
              .clamp(pos.minScrollExtent, pos.maxScrollExtent);
          _historyScroll.jumpTo(target);
        });
      }
    } on Object catch (e) {
      if (_staleSession(gen, authGen) || _sessionId != sid) return;
      setState(() {
        _loadingEarlier = false;
        _earlierError = localizedError(e, s);
      });
    }
  }

  /// 关闭挂在 Navigator 上的**临时 route**（详情 BottomSheet、角色选择器）：
  /// 401 / 退出 / 换查看对象时调用——私人详情不能留在「可返回」的位置。
  /// 注意：全局 401 钩子本身不动，它的登录态清理仍在 onUnauthorized 里统一完成。
  void _closeTransientRoutes() {
    if (!mounted) return;
    final nav = Navigator.of(context);
    if (nav.canPop()) nav.popUntil((r) => r.isFirst);
  }

  /// 换号 / 401 / 退出：草稿、会话列表、选中项、本人 id、历史游标、待发附件全部作废
  /// （私人内容一律清空，不因未保存提示挡住退出）。
  void _clearPersonalInputs() {
    _drafts.clear();
    _draftKey = null;
    _sessions = const [];
    _sessionListGen++;
    _selectedSessionId = null;
    _selectedByCharacter.clear();
    _historyTotal = 0;
    _historyRaw = 0;
    _loadingEarlier = false;
    _earlierError = '';
    _creatingSession = false;
    _myUserId = null;
    _input.clear();
    _clearPendingAttachment(); // 私文/文件名/字节一并清（不受 dirty 确认挡住）
  }

  /// 登录/换号后取本人 id（编辑归属判定的唯一权威来源）；失败不阻塞首屏。
  Future<void> _loadProfile(int authGen) async {
    try {
      final id = await _editor.getProfileId();
      if (_staleAuth(authGen)) return;
      setState(() => _myUserId = id);
    } on Object {
      if (_staleAuth(authGen)) return;
      setState(() => _myUserId = null);
    }
  }

  /// 工作台分区切换：进入 chat 时为**当前对象**恢复/建立会话——
  /// 会话 ready 前 `_sessionId == null`，`_send()` 自然被挡住，
  /// 不会把新对象的消息发进旧角色的会话（首审 P1）。
  void _selectSection(String sec) {
    setState(() => _section = sec);
    final c = _current;
    if (sec == 'chat' && c != null && _sessionId == null && !_busy) {
      _openCharacter(c);
    }
  }

  /// reader 区切换查看对象（**不**建会话）：必须同时把 chat 的会话归属改到新对象，
  /// 否则返回 chat 时仍绑着旧角色的 session/history/在飞流。
  void _selectReaderCharacter(Character c) {
    if (_current?.id == c.id) return; // 同一对象：无操作
    _closeTransientRoutes(); // 旧对象的私人详情立即作废
    _cancelInflight(); // 取消旧会话在飞的建会话/流
    _sessionGen++; // 作废旧会话代次：旧流/旧历史不能再落到新对象上
    _retargetDraft(c.id, null); // 草稿跟着「角色 + 未选会话」桶走
    _clearPendingAttachment(); // 切查看对象：作废旧附件（同一私有内容纪律）
    setState(() {
      _current = c;
      _sessionId = null;
      _selectedSessionId = null;
      _messages = const [];
      _streaming = '';
      _historyTotal = 0;
      _historyRaw = 0;
      _loadingEarlier = false;
      _earlierError = '';
      _status = '';
    });
  }

  // ── 文本附件（M1a-4）─────────────────────────────────────────────────────

  /// 是否「取消」类错误（用户主动取消本地等待）。
  bool _isCancelError(Object e) {
    if (e is DioException) {
      if (e.type == DioExceptionType.cancel) return true;
      final inner = e.error;
      return inner is ApiException && inner.kind == 'cancelled';
    }
    return e is ApiException && e.kind == 'cancelled';
  }

  /// 选择单个文本文件（**不发请求**）。取消 ⇒ 静默；类型/体积不符 ⇒ 本地拒绝（0 网络）。
  ///
  /// 返工 R2：每次选择领取**选择代次**，且切角色/会话/退出/401/移除也会递增它；
  /// 结果回来时若代次已过期（有更晚的选择或期间已作废）⇒ 丢弃 ⇒ **latest-wins**，
  /// 较早选择的晚到结果不会覆盖较新的选择，也不会在作废后恢复旧文件名与字节。
  Future<void> _pickAttachment() async {
    final sid = _sessionId;
    final c = _current;
    if (sid == null || c == null || _busy || _pending != null) return;
    final authGen = _authGen;
    final gen = _sessionGen;
    final pickGen = ++_pickGen; // 领取本次选择代次
    final picked = await _picker.pick();
    if (!mounted) return;
    // 返回选择结果前再校验账号/角色/会话代次：期间 401/换号/切会话 ⇒ 丢弃（不发请求）
    if (authGen != _authGen || gen != _sessionGen || _sessionId != sid) return;
    if (pickGen != _pickGen) return; // 更晚的选择或期间作废 ⇒ 晚到结果丢弃
    if (picked == null) return; // 用户取消：静默
    if (picked.reject == TextFileRejectReason.tooLarge) {
      setState(() => _pendingError = as_.t('rejectedSize'));
      return;
    }
    if (picked.reject != null || !picked.accepted) {
      setState(() => _pendingError = as_.t('rejectedType'));
      return;
    }
    setState(() {
      _pending = PendingAttachment(file: picked, characterId: c.id, sessionId: sid);
      _pendingError = '';
    });
  }

  /// 移除待发附件（释放字节引用）。
  void _removePending() {
    _clearPendingAttachment();
    setState(() {});
  }

  /// 取消本地等待（**不保证**撤销服务端已开始的写入，也不保证阻止已开始的模型调用）。
  void _cancelAttachmentWait() {
    final ct = _attachCancel;
    if (ct != null && !ct.isCancelled) ct.cancel('user-cancel');
  }

  /// 明确发送（**唯一**发起点；初始化/刷新/选择取消/加载历史都不得触发）。
  Future<void> _sendAttachment() async {
    final p = _pending;
    final sid = _sessionId;
    if (p == null || sid == null || _busy) return;
    if (p.phase != AttachPhase.ready) return; // pending 防重复提交
    if (p.sessionId != sid) {
      _clearPendingAttachment();
      setState(() => _pendingError = as_.t('needSession'));
      return;
    }
    final authGen = _authGen;
    final gen = _sessionGen;
    final attachGen = _attachGen;
    final ct = CancelToken();
    _attachCancel = ct;
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    setState(() {
      _pending = p.copyWith(phase: AttachPhase.uploading);
      _pendingError = '';
    });
    try {
      final result = await env.attachment.uploadTextFile(
        sessionId: sid,
        file: p.file,
        lang: widget.lang,
        cancelToken: ct,
        onUploadProgress: (progress) {
          if (!mounted) return;
          if (authGen != _authGen || gen != _sessionGen || attachGen != _attachGen) return;
          if (_sessionId != sid) return;
          final cur = _pending;
          if (cur == null) return;
          setState(() {
            _pending = cur.copyWith(
              phase: progress.done ? AttachPhase.waiting : AttachPhase.uploading,
              loaded: progress.loaded,
              total: progress.total,
            );
          });
        },
      );
      // 代次/会话变化 ⇒ 丢弃结果：绝不把 A 的 file_message / chunks 写到 B
      if (_staleSession(gen, authGen) || attachGen != _attachGen || _sessionId != sid) {
        return;
      }
      final seen = _messages.map((m) => m.id).toSet();
      final added = <ChatMessage>[];
      for (final m in [result.fileMessage, ...result.chunks]) {
        if (seen.add(m.id)) added.add(m); // 相同稳定 id 去重：即时与历史刷新不出现两份
      }
      setState(() {
        _messages = [..._messages, ...added];
        _historyRaw += added.length;
        _historyTotal += added.length;
        _pending = null;
        _pendingError = '';
      });
    } on Object catch (e) {
      if (!mounted) return;
      if (authGen != _authGen || attachGen != _attachGen || _sessionId != sid) return;
      final cancelled = _isCancelError(e);
      setState(() {
        _pending =
            p.copyWith(phase: cancelled ? AttachPhase.cancelled : AttachPhase.unconfirmed);
        _pendingError = cancelled
            ? as_.t('phaseCancelled')
            : '${as_.t('phaseFailed')}：${localizedError(e, s)}';
      });
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      if (identical(_attachCancel, ct)) _attachCancel = null;
      _busyEnd(owner);
    }
  }

  /// 未知结果的**只读**核对：仅 GET 原会话历史（保留当前会话归属，不半切换、不重发）。
  Future<void> _verifyAttachmentHistory() async {
    final p = _pending;
    if (p == null) return;
    if (_sessionId != p.sessionId) {
      setState(() => _pendingError = as_.t('goBackVerify'));
      return;
    }
    final authGen = _authGen;
    final gen = _sessionGen;
    final sid = p.sessionId;
    final ct = CancelToken();
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    try {
      await _loadHistoryFirst(sid, authGen, gen, ct);
      if (_staleSession(gen, authGen) || _sessionId != sid) return;
      setState(() {
        _pending = null;
        _pendingError = '';
      });
    } on Object catch (e) {
      if (_staleSession(gen, authGen) || _sessionId != sid) return;
      setState(() => _pendingError = localizedError(e, s));
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      _busyEnd(owner);
    }
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    final sid = _sessionId;
    if (text.isEmpty || sid == null || _busy) return;
    final authGen = _authGen;
    final gen = _sessionGen;
    final ct = CancelToken();
    _inflight = ct;
    final owner = Object();
    _busyBegin(owner);
    _input.clear();
    if (_draftKey != null) _drafts[_draftKey!] = ''; // 已发出：该会话草稿桶清空
    final local = ChatMessage(
      id: -DateTime.now().millisecondsSinceEpoch,
      sessionId: sid,
      senderType: 'user',
      content: text,
      createdAt: DateTime.now().toUtc().toIso8601String(),
      isLocal: true,
    );
    setState(() {
      _messages = [..._messages, local];
      _streaming = '';
    });
    try {
      await env.transport.streamMessage(sid, text, lang: widget.lang,
          cancelToken: ct, onEvent: (ev) {
        // 迟到事件：代次或会话已变 ⇒ 丢弃（不污染新会话），且不恢复旧身份
        if (_staleSession(gen, authGen) || _sessionId != sid) return;
        switch (ev['type']) {
          case 'delta':
            setState(() => _streaming += (ev['text'] as String? ?? ''));
          case 'block':
            final content = ev['content'] as String? ?? '';
            setState(() {
              _messages = [
                ..._messages,
                ChatMessage(
                  id: (ev['id'] as num?)?.toInt() ?? -1,
                  sessionId: sid,
                  senderType: 'ai',
                  content: content,
                  createdAt: DateTime.now().toUtc().toIso8601String(),
                ),
              ];
              _streaming = '';
            });
          case 'error':
            setState(() => _status = s.t('errorServer'));
          case 'done':
            setState(() => _streaming = '');
        }
      });
    } on Object catch (e) {
      if (_staleSession(gen, authGen)) return;
      setState(() => _status = localizedError(e, s));
    } finally {
      if (identical(_inflight, ct)) _inflight = null;
      _busyEnd(owner);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    return Scaffold(
      appBar: AppBar(
        title: Text(s.t('appTitle')),
        actions: [
          TextButton(
            onPressed: () => appLang.value = widget.lang == 'zh' ? 'en' : 'zh',
            child: Text(widget.lang == 'zh' ? 'EN' : '中文'),
          ),
          if (_token != null)
            TextButton(onPressed: _logout, child: Text(s.t('logout'))),
        ],
      ),
      body: _token == null
          ? _loginPane()
          : (wide ? _wideShell() : _sectionBody()),
      drawer: (!wide && _token != null) ? _sectionDrawer() : null,
    );
  }

  // ── 工作台导航（宽屏 NavigationRail / 窄屏 Drawer）────────────────────
  IconData _sectionIcon(String sec) => switch (sec) {
        'chat' => Icons.chat_bubble_outline,
        'profile' => Icons.person_outline,
        'memories' => Icons.psychology_outlined,
        'charDiary' => Icons.menu_book_outlined,
        'moments' => Icons.dynamic_feed_outlined,
        'memos' => Icons.sticky_note_2_outlined,
        'myDiary' => Icons.book_outlined,
        _ => Icons.circle_outlined,
      };

  String _sectionLabel(String sec) => switch (sec) {
        'chat' => s.t('tabChat'),
        'profile' => rs.t('modProfile'),
        'memories' => rs.t('modMemories'),
        'charDiary' => rs.t('modCharDiary'),
        'moments' => rs.t('modMoments'),
        'memos' => rs.t('modMemos'),
        'myDiary' => rs.t('modMyDiary'),
        _ => sec,
      };

  Widget _wideShell() => Row(children: [
        NavigationRail(
          selectedIndex: _sections.indexOf(_section),
          onDestinationSelected: (i) => _selectSection(_sections[i]),
          labelType: NavigationRailLabelType.all,
          destinations: [
            for (final sec in _sections)
              NavigationRailDestination(
                  icon: Icon(_sectionIcon(sec)), label: Text(_sectionLabel(sec))),
          ],
        ),
        const VerticalDivider(width: 1),
        Expanded(child: _sectionBody()),
      ]);

  Widget _sectionDrawer() => Drawer(
        child: SafeArea(
          child: ListView(children: [
            for (final sec in _sections)
              ListTile(
                leading: Icon(_sectionIcon(sec)),
                title: Text(_sectionLabel(sec)),
                selected: _section == sec,
                onTap: () {
                  Navigator.of(context).pop(); // 先关抽屉（它挂在 Scaffold 上，不是 route）
                  _selectSection(sec); // 进 chat 时按当前对象恢复/建立会话
                },
              ),
          ]),
        ),
      );

  /// 当前 section 的内容区（窄屏=整个 body；宽屏=NavigationRail 右侧）。
  Widget _sectionBody() {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    if (_section == 'chat') {
      return wide ? _wideChatBody() : _narrowChatBody();
    }
    if (_sectionNeedsCharacter) {
      // 角色相关模块：key 含 authGen+角色 id——换号/401/切角色 ⇒ 面板重建重载，
      // 旧账号/旧角色的在飞请求由面板自身 loader 的代次+CancelToken 真实取消。
      final pane = _current == null
          ? Center(child: Text(rs.t('pickCharacter')))
          : _readerPane(
              Key('${_section}_${_authGen}_${_current!.id}_$_characterRev'), _current!);
      if (wide) {
        return Row(children: [
          SizedBox(width: 280, child: _characterList()),
          const VerticalDivider(width: 1),
          Expanded(child: pane),
        ]);
      }
      return Column(children: [
        _characterPickerBar(),
        const Divider(height: 1),
        Expanded(child: pane),
      ]);
    }
    // 账号级模块（动态/备忘/个人日记）：key 含 authGen + 本人 id——
    // 身份未就绪时先只读展示，profile id 到达后重建面板、编辑入口随之启停。
    return _readerPane(Key('${_section}_${_authGen}_${_myUserId ?? '-'}'), null);
  }

  Widget _characterPickerBar() => InkWell(
        onTap: _characters.isEmpty
            ? null
            : () async {
                final authGen = _authGen; // 选择期间可能发生 401 / 换号
                final c = await showModalBottomSheet<Character>(
                  context: context,
                  builder: (ctx) => SafeArea(
                    child: ListView(shrinkWrap: true, children: [
                      for (final ch in _characters)
                        ListTile(
                            title: Text(ch.name),
                            onTap: () => Navigator.pop(ctx, ch)),
                    ]),
                  ),
                );
                // await 返回后必须重新校验：mounted + 认证代次未变——
                // 否则失效的选择结果会复活旧账号的私有状态（窄屏曾绕过统一入口，首审 P1）。
                if (c == null || !mounted || authGen != _authGen) return;
                _selectReaderCharacter(c); // 与宽屏 ListTile **共用同一处理入口**
              },
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(children: [
            Expanded(
              child: Text(_current?.name ?? rs.t('pickCharacter'),
                  style: Theme.of(context).textTheme.titleMedium),
            ),
            const Icon(Icons.arrow_drop_down),
          ]),
        ),
      );

  /// 各 reader 模块面板分发（六模块全部落真实实现）。
  Widget _readerPane(Key key, Character? character) {
    switch (_section) {
      case 'profile':
        return ProfilePane(
            key: key,
            character: character!,
            client: env.client,
            rs: rs,
            cs: s,
            onEdit: () => _openCharacterEditor(characterId: character.id),
            editLabel: CharacterEditorStrings(widget.lang).t('editAction'));
      case 'memories':
        return MemoriesPane(
            key: key, character: character!, client: env.client, rs: rs, cs: s);
      case 'charDiary':
        return CharDiaryPane(
            key: key, character: character!, client: env.client, rs: rs, cs: s);
      case 'moments':
        return MomentsPane(key: key, client: env.client, rs: rs, cs: s);
      case 'memos':
        return MemosPane(
            key: key, client: env.client, editor: _editor, myUserId: _myUserId,
            rs: rs, cs: s, es: es);
      case 'myDiary':
        return MyDiaryPane(
            key: key, client: env.client, editor: _editor, myUserId: _myUserId,
            rs: rs, cs: s, es: es);
      default:
        return Center(
          key: key,
          child: Text('${rs.t('workbench')} · ${_sectionLabel(_section)}'),
        );
    }
  }

  Widget _loginPane() {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _server,
                decoration: InputDecoration(labelText: s.t('serverAddress')),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _user,
                decoration: InputDecoration(labelText: s.t('username')),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _pass,
                obscureText: true,
                onSubmitted: (_) => _login(),
                decoration: InputDecoration(labelText: s.t('password')),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _busy ? null : _login,
                child: Text(s.t('login')),
              ),
              const Divider(height: 32),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _code,
                    decoration: InputDecoration(labelText: s.t('pairingCode')),
                  ),
                ),
                const SizedBox(width: 8),
                OutlinedButton(onPressed: _busy ? null : _pair, child: Text(s.t('pair'))),
              ]),
              if (_pairInfo.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(_pairInfo, style: const TextStyle(fontSize: 12)),
              ],
              if (_status.isNotEmpty) ...[
                const SizedBox(height: 12),
                Text(_status, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _wideChatBody() => Row(children: [
        SizedBox(width: 280, child: _characterList()),
        const VerticalDivider(width: 1),
        Expanded(child: _chatPane()),
      ]);

  Widget _narrowChatBody() => DefaultTabController(
        length: 2,
        child: Column(children: [
          TabBar(tabs: [Tab(text: s.t('tabCharacters')), Tab(text: s.t('tabChat'))]),
          Expanded(
            child: TabBarView(children: [_characterList(), _chatPane()]),
          ),
        ]),
      );

  /// 创建/编辑成功后刷新列表与当前角色；**写入成功但刷新失败**不引导重复创建。
  Future<bool> _refreshCharactersAfterSave(int savedId) async {
    final gen = _authGen;
    try {
      final chars = await env.transport.getCharacters();
      if (!mounted || gen != _authGen) return false;
      setState(() {
        _characters = chars;
        _characterRev++;
        final match = chars.where((c) => c.id == savedId).toList();
        if (match.isNotEmpty) _current = match.first;
      });
      return true;
    } on Object {
      if (!mounted || gen != _authGen) return false;
      // 写入已成功、刷新失败：只让详情重读，**不**提示重新创建
      setState(() => _characterRev++);
      return false;
    }
  }

  /// 打开创建（characterId=null）或编辑表单；成功后刷新，创建额外显式进入聊天。
  Future<void> _openCharacterEditor({int? characterId}) async {
    final authGen = _authGen;
    final sessionGen = _sessionGen;
    final saved = await CharacterEditorSheet.show(
      context,
      client: env.client,
      strings: CharacterEditorStrings(widget.lang),
      authGen: authGen,
      routeGen: sessionGen,
      isStale: (a, r) => !mounted || a != _authGen || r != _sessionGen,
      currentUserId: _myUserId,
      characterId: characterId,
      onUnauthorized: () => _invalidateAll(),
      onVerify: () => _refreshCharactersAfterSave(-1),
      onSaved: (data) {},
    );
    if (saved == null || !mounted || authGen != _authGen) return;
    await _refreshCharactersAfterSave(saved.id);
    if (!mounted || authGen != _authGen) return;
    if (characterId == null) {
      // 创建：按服务端稳定 id 选中并进入聊天（不发消息、不调模型）
      final match = _characters.where((c) => c.id == saved.id).toList();
      if (match.isNotEmpty) await _openCharacter(match.first);
    }
  }

  Widget _characterList() {
    final ces = CharacterEditorStrings(widget.lang);
    final canWrite = _token != null;
    if (_characters.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(s.t('emptyCharacters')),
            const SizedBox(height: 6),
            Text(ces.t('emptyHint'), textAlign: TextAlign.center),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: canWrite ? () => _openCharacterEditor() : null,
              icon: const Icon(Icons.add),
              label: Text(ces.t('createAction')),
            ),
          ],
        ),
      );
    }
    return ListView(
      children: [
        ListTile(
          leading: const Icon(Icons.add),
          title: Text(ces.t('createAction')),
          onTap: canWrite ? () => _openCharacterEditor() : null,
        ),
        const Divider(height: 1),
        for (final c in _characters)
          ListTile(
            selected: _current?.id == c.id,
            title: Text(c.name), // 角色名是**用户内容**，不翻译
            // 允许在上一角色仍在加载时**切换**（真实场景必须能切走一个慢请求）：
            // 并发安全由「会话代次 + 真实取消」保证，而不是靠禁用点击。
            // chat 区：建会话进聊天；reader 区：只切换查看对象（不发会话请求），
            // 但同时**作废 chat 的旧会话归属**，返回 chat 时按新对象重建。
            onTap: () => _section == 'chat'
                ? _openCharacter(c)
                : _selectReaderCharacter(c),
          ),
      ],
    );
  }

  Widget _chatPane() {
    if (_current == null) {
      return Center(child: Text(s.t('emptyMessages')));
    }
    return Column(children: [
      // 会话条：当前角色的会话列表（选中/加载/空/失败/重试）+ 显式「新建会话」
      SessionStrip(
        sessions:
            _sessions.where((x) => x.characterId == _current!.id).toList(),
        selectedId: _selectedSessionId,
        status: _sessionListStatus,
        errorText: _sessionListError,
        creating: _creatingSession,
        onSelect: _selectSession,
        onCreate: _newSession,
        onRetry: () => _openCharacter(_current!),
        ss: ss,
      ),
      const Divider(height: 1),
      Padding(
        padding: const EdgeInsets.all(12),
        child: Row(children: [
          Expanded(
            child: Text('${s.t('history')} · ${_current!.name}',
                style: Theme.of(context).textTheme.titleMedium),
          ),
        ]),
      ),
      Expanded(
        child: ListView(
          controller: _historyScroll,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          children: [
            _earlierControl(),
            for (final m in _messages)
              Align(
                alignment: m.isUser ? Alignment.centerRight : Alignment.centerLeft,
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(10),
                    // 文件消息复用共享 fileMeta 渲染文件卡：不展开 summary、不预览下载
                    child: m.fileMeta != null
                        ? ConstrainedBox(
                            constraints: const BoxConstraints(maxWidth: 320),
                            child: FileMessageCard(message: m, as_: as_),
                          )
                        : Text(m.content), // 消息正文是用户/角色内容，不翻译
                  ),
                ),
              ),
            if (_streaming.isNotEmpty)
              Align(
                alignment: Alignment.centerLeft,
                child: Card(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  child: Padding(
                    padding: const EdgeInsets.all(10),
                    child: Text(_streaming),
                  ),
                ),
              ),
          ],
        ),
      ),
      if (_pending != null)
        AttachmentPendingCard(
          pending: _pending!,
          as_: as_,
          canSend: _sessionId != null && !_busy,
          onSend: _sendAttachment,
          onRemove: _removePending,
          onCancel: _cancelAttachmentWait,
          onVerify: _verifyAttachmentHistory,
        ),
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 4),
        child: Row(children: [
          const Icon(Icons.attach_file, size: 14),
          const SizedBox(width: 6),
          Expanded(
            child: Text(as_.t('onlyText'),
                style: Theme.of(context).textTheme.bodySmall,
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          ),
        ]),
      ),
      Padding(
        padding: const EdgeInsets.all(12),
        child: Row(children: [
          IconButton(
            tooltip: as_.t('pick'),
            // 会话就绪、未在忙、且无待发文件时才可选（单文件；避免选到后误发旧 sid）
            onPressed: (_sessionId == null || _busy || _pending != null)
                ? null
                : _pickAttachment,
            icon: const Icon(Icons.attach_file),
          ),
          Expanded(
            child: TextField(
              controller: _input,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(hintText: s.t('messageHint')),
            ),
          ),
          const SizedBox(width: 8),
          FilledButton(
            onPressed: _busy ? null : _send,
            child: Text(_busy ? s.t('sending') : s.t('send')),
          ),
        ]),
      ),
      if (_pendingError.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(_pendingError,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ),
      if (_status.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(_status, style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ),
    ]);
  }

  /// 「加载更早」控件（置于历史列表**顶部**）：真实 skip/limit/total；到最早即如实提示，
  /// 失败给本地化错误 + 重试（同一按钮）。
  Widget _earlierControl() {
    if (_historyTotal <= 0) return const SizedBox.shrink();
    final hasMore = _historyRaw < _historyTotal;
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(children: [
        if (_loadingEarlier)
          const SizedBox(
              height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
        else if (hasMore)
          OutlinedButton(onPressed: _loadEarlier, child: Text(ss.t('loadEarlier')))
        else
          Text(ss.t('noEarlier'), style: theme.textTheme.bodySmall),
        if (_earlierError.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text('${ss.t('loadEarlierFailed')}：$_earlierError',
              style: TextStyle(color: theme.colorScheme.error, fontSize: 12)),
        ],
      ]),
    );
  }
}
