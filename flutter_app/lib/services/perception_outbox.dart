import "dart:convert";
import "dart:io";
import "package:dio/dio.dart";
import "package:path_provider/path_provider.dart";
import "package:shared_preferences/shared_preferences.dart";
import "api_client.dart";
import "channel_status.dart";

/// 感知上传本地队列（2026-09-21 P2；2026-09-22 P2b：存储改「一条一文件」）——
/// 治盘点 S3「断网期间采集的感知数据丢了就找不回」。
///
/// 口径：
/// - **先落地再发送**：采集到的快照先入队，直传成功后 [markSent] 摘掉；失败则留在队列里，
///   下一轮 [flush] 按顺序补传。
/// - **账号隔离（硬要求）**：队列内容是用户自己的感知数据（剪贴板/通知文本等），属用户数据，
///   因此按「服务器地址 + user_id」分目录：
///   `getApplicationSupportDirectory()/perception_outbox/<服务器地址安全化>/<user_id>/`。
///   **取不到 user_id（0 或空）时不入队、也不补传**——宁可丢这批数据，也不能串进别的账号的队列。
/// - **一条一文件（P2b）**：每条记录一个 `<clientKey 安全化>_<hash>.json`，写入走
///   「先写 `.tmp` 再 `rename`」原子落位、发送成功即删文件——目录级操作天然并发安全，
///   **不用文件锁**（同一进程内两个 isolate 之间 fcntl 锁并不互斥，锁不是正解）。
/// - **上限 [maxEntries] 条 / 保留 [ttl]**：超限丢最旧、过期丢该条，真有丢弃时记一笔通道状态。
///
/// [clientKeyOf] 是确定性指纹：同一次采集重复入队得到同一个 key，客户端据此去重，
/// 服务端再按「同内容 5 分钟窗口」兜一层（不引入 crypto 依赖）。
class PerceptionOutbox {
  PerceptionOutbox._();

  static const String endpoint = "/api/v1/phone/perception";
  static const int maxEntries = 200;
  static const Duration ttl = Duration(days: 3);
  /// 单轮补传上限（弱网下不堆积，也避免把服务端 MAX_KEEP 窗口挤满）
  static const int flushBatch = 20;
  /// 丢弃观测的通道名：直接传字符串，不占用 channel_status.dart 的常量（P3 正在改那个文件）
  static const String droppedChannel = "perception_outbox_dropped";

  static Directory? _rootOverride;

  /// 测试注入口：path_provider 在 `flutter test` 下没有平台实现，用这个换到临时目录。
  /// 生产不设置（保持 null → 走 getApplicationSupportDirectory）。
  static void overrideRootForTest(Directory? dir) => _rootOverride = dir;

  /// 时间注入口（**仅测试用**）：到期判定统一走它，测试可整体前移/后移来验 [ttl]，
  /// 不改系统时间；生产默认 `DateTime.now`。
  static DateTime Function() nowProvider = DateTime.now;

  /// 把「现在」换成注入的时间源（测试专用）
  static void overrideNowForTest(DateTime Function() f) => nowProvider = f;

  /// 入队序号（进程内单调递增）：同一毫秒内的多条按入队先后保序，
  /// 作 [createdAtMs] 相同（毫秒精度撞车）时的次级排序键，跨进程仍以时间戳为主键。
  static int _seq = 0;
  static int _nextSeq() => ++_seq;

  /// 确定性指纹：`"<source>|<长度>|<hashCode 绝对值>"`（先 trim，保证入队/直传两条路径同 key）
  static String clientKeyOf(String source, String content) {
    final t = content.trim();
    return "$source|${t.length}|${t.hashCode.abs()}";
  }

  // === 目录 / 文件命名 ===

  /// 本账号的队列目录；无 user_id 时返回 null（= 不入队、不补传、计数为 0）
  static Future<Directory?> _queueDir() async {
    final prefs = await SharedPreferences.getInstance();
    final uid = prefs.getInt("user_id") ?? 0;
    if (uid <= 0) return null;
    final server = (prefs.getString("server_url") ?? "")
        .replaceAll(RegExp("[^A-Za-z0-9]"), "_");
    try {
      final root = _rootOverride ?? await getApplicationSupportDirectory();
      final dir = Directory(
        "${root.path}/perception_outbox/${server.isEmpty ? "default" : server}/$uid",
      );
      await dir.create(recursive: true);
      return dir;
    } catch (_) {
      // 拿不到应用目录（未注入目录的测试环境等）→ 按「没有队列」处理，不抛出：
      // 直传路径不该被队列拖下水
      return null;
    }
  }

  /// 记录文件名（不含扩展名）：`<安全化 clientKey>_<hashCode 十六进制>`
  /// clientKey 里可能含 `|` 等文件名不友好字符，先把非 `[A-Za-z0-9._-]` 换成 `_`，
  /// 再拼 hash 降低撞名概率。
  static String _baseName(String clientKey) {
    final safe = clientKey.replaceAll(RegExp(r"[^A-Za-z0-9._-]"), "_");
    return "${safe}_${clientKey.hashCode.abs().toRadixString(16)}";
  }

  static File _recordFile(Directory dir, String clientKey) =>
      File("${dir.path}/${_baseName(clientKey)}.json");

  // === 读写原子操作（不用文件锁）===

  /// 原子落位：先写 `<name>.tmp` 再 `rename` 成 `<name>.json`（同目录 rename 是原子的）。
  /// 同名已存在时先删再 rename（用于 attempts 回写这类「覆盖式」更新）。
  static Future<void> _writeRecord(
    Directory dir,
    Map<String, dynamic> item,
  ) async {
    final key = (item["clientKey"] ?? "").toString();
    final target = _recordFile(dir, key);
    final tmp = File("${dir.path}/${_baseName(key)}.tmp");
    try {
      await tmp.writeAsString(jsonEncode(item), flush: true);
      try {
        // POSIX（Android）上 rename 覆盖同名目标本身就是原子的，先删会多出一个「两边都不存在」
        // 的空窗（期间崩溃＝丢这条）；只有 rename 失败（Windows 上目标已存在会抛）才退化为先删再 rename。
        await tmp.rename(target.path);
      } catch (_) {
        if (await target.exists()) await target.delete();
        await tmp.rename(target.path);
      }
    } catch (_) {
      // 落盘失败不留垃圾，也不让异常冒到调用方（直传路径不该被队列拖下水）
      await _deleteFile(tmp);
    }
  }

  static Future<void> _deleteFile(File f) async {
    try {
      if (await f.exists()) await f.delete();
    } catch (_) {
      // 删不掉就让它在下一轮读写里再暴露一次，不影响调用方
    }
  }

  /// 读目录：只认 `*.json`（**`.tmp` 残留一律忽略**），按 [createdAtMs] 升序；
  /// 坏文件（JSON 解析失败 / 字段缺失）直接删掉，不让异常冒出来。
  static Future<List<_Rec>> _loadDir(Directory dir) async {
    final out = <_Rec>[];
    List<FileSystemEntity> ents;
    try {
      ents = await dir.list().toList();
    } catch (_) {
      return out;
    }
    for (final e in ents) {
      if (e is! File || !e.path.endsWith(".json")) continue;
      Map<String, dynamic>? item;
      try {
        final j = jsonDecode(await e.readAsString());
        if (j is Map) item = Map<String, dynamic>.from(j);
      } catch (_) {
        item = null;
      }
      final key = item?["clientKey"]?.toString() ?? "";
      final content = item?["content"]?.toString() ?? "";
      if (item == null || key.isEmpty || content.isEmpty) {
        await _deleteFile(e);
        continue;
      }
      out.add(_Rec(e, item));
    }
    out.sort(_byAge);
    return out;
  }

  /// 保序：先 [createdAtMs]，同一毫秒再看入队序号 `seq`
  static int _byAge(_Rec a, _Rec b) {
    final ta = (a.item["createdAtMs"] as num?)?.toInt() ?? 0;
    final tb = (b.item["createdAtMs"] as num?)?.toInt() ?? 0;
    if (ta != tb) return ta.compareTo(tb);
    return ((a.item["seq"] as num?)?.toInt() ?? 0)
        .compareTo((b.item["seq"] as num?)?.toInt() ?? 0);
  }

  /// 裁剪（每次入队后做一次）：先剔 [ttl] 过期，再按 [maxEntries] 丢最旧；
  /// 真有丢弃时记一笔通道状态（通道名直接用字符串，不改 channel_status.dart）
  static Future<void> _crop(Directory dir) async {
    final recs = await _loadDir(dir);
    if (recs.isEmpty) return;
    final cutoff = nowProvider().subtract(ttl).millisecondsSinceEpoch;
    final fresh = recs
        .where((r) => ((r.item["createdAtMs"] as num?)?.toInt() ?? 0) >= cutoff)
        .toList();
    final keep = fresh.length > maxEntries
        ? fresh.sublist(fresh.length - maxEntries)
        : fresh;
    final dropped = recs.length - keep.length;
    if (dropped <= 0) return;
    final keepPaths = keep.map((r) => r.file.path).toSet();
    for (final r in recs) {
      if (keepPaths.contains(r.file.path)) continue;
      await _deleteFile(r.file);
    }
    await ChannelStatusTracker.recordFail(
      droppedChannel,
      ChannelCode.empty,
      detail: "dropped=$dropped",
    );
  }

  // === 对外 API（P2b：签名逐字不变，只换内部存储结构）===

  /// 入队：空内容不入队；同 clientKey 不重复入队（文件已在即视为已入队，不覆盖内容）；
  /// 无 user_id 不入队（账号隔离）
  static Future<void> enqueue({
    required String source,
    required String content,
  }) async {
    final text = content.trim();
    if (text.isEmpty) return;
    final dir = await _queueDir();
    if (dir == null) return;
    final key = clientKeyOf(source, text);
    if (!await _recordFile(dir, key).exists()) {
      await _writeRecord(dir, {
        "source": source,
        "content": text,
        "clientKey": key,
        "createdAtMs": nowProvider().millisecondsSinceEpoch,
        "attempts": 0,
        "seq": _nextSeq(),
      });
    }
    await _crop(dir);
  }

  /// 直传成功后摘掉该条（文件不存在也算成功）
  static Future<void> markSent(String clientKey) async {
    if (clientKey.isEmpty) return;
    final dir = await _queueDir();
    if (dir == null) return;
    await _deleteFile(_recordFile(dir, clientKey));
  }

  /// 补传历史：按 [createdAtMs] 升序逐条重传，每轮最多 [flushBatch] 条；成功即删文件，
  /// 单条失败即停止本轮（保持顺序、断网时不无谓重试后面），返回成功条数。
  ///
  /// 记账（P2b）：本轮**真发送过**才记，且一轮只记一次——全成记一次 [recordOk]，
  /// 有失败记一次 [recordFail]（`retriable: true`）；直传路径那次记账仍归它自己，不重复计。
  static Future<int> flush() async {
    final dir = await _queueDir();
    if (dir == null) return 0;
    final recs = await _loadDir(dir);
    if (recs.isEmpty) return 0; // 空队列不记账
    final batch = recs.take(flushBatch).toList();
    final dio = ApiClient().dio;
    var sent = 0;
    for (final r in batch) {
      final item = r.item;
      final key = item["clientKey"].toString();
      try {
        await dio.post(
          endpoint,
          data: FormData.fromMap({
            "source": item["source"],
            "content": item["content"],
            "client_key": key,
          }),
        );
      } catch (_) {
        // 本条失败：attempts+1 回写它的文件（覆盖式原子写），然后停本轮
        item["attempts"] = ((item["attempts"] as num?)?.toInt() ?? 0) + 1;
        await _writeRecord(dir, item);
        await ChannelStatusTracker.recordFail(
          ChannelStatusTracker.kPerceptionUpload,
          ChannelCode.networkError,
          detail: "flush failed ${batch.length - sent}/${batch.length}",
          retriable: true,
        );
        return sent;
      }
      await _deleteFile(r.file);
      sent++;
    }
    await ChannelStatusTracker.recordOk(ChannelStatusTracker.kPerceptionUpload);
    return sent;
  }

  static Future<int> pendingCount() async {
    final dir = await _queueDir();
    if (dir == null) return 0;
    return (await _loadDir(dir)).length;
  }

  /// 隐私一键清除：删掉本账号队列目录下所有 `*.json`（含 `.tmp` 残留）
  static Future<void> clear() async {
    final dir = await _queueDir();
    if (dir == null) return;
    try {
      if (!await dir.exists()) return;
      for (final e in await dir.list().toList()) {
        if (e is! File) continue;
        if (e.path.endsWith(".json") || e.path.endsWith(".tmp")) {
          await _deleteFile(e);
        }
      }
    } catch (_) {
      // 删不掉也已在下一次读写中暴露，不让清除动作抛到 UI 层
    }
  }
}

/// 队列里的一条记录：文件即记录（一条一文件，目录级操作天然并发安全）
class _Rec {
  final File file;
  final Map<String, dynamic> item;
  _Rec(this.file, this.item);
}
