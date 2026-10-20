/// 只读工作台（M1a-2）的 wire DTO：后端 GET 响应的**只读投影**。
///
/// 纪律：
/// - 纯解析、零 IO、不 import flutter / dart:io / 任何手机壳代码；
/// - 与手机端富模型**不同源也不同名**（Reader*），不冒充「已完成移动端 canonical 抽包」；
/// - 只承载展示白名单字段（见 API_CONTRACT）；可空/缺省/未知枚举按现状保留为 null 或原文，
///   绝不编造标题/日期/来源，也不把 INFERRED 升格为 FACT；
/// - 必填字段（id/content 等）缺失或类型错误 ⇒ 抛 [FormatException]（由 ReaderApi 归一为
///   ApiException），**不允许把坏数据伪装成「成功的空列表」**。
library;

int _asInt(dynamic v, [int fallback = 0]) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) return int.tryParse(v) ?? fallback;
  return fallback;
}

int? _asIntN(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) return int.tryParse(v);
  return null;
}

String _asStr(dynamic v) {
  if (v is String) return v;
  if (v == null) return '';
  return '$v';
}

String? _asStrN(dynamic v) => v is String ? v : null;

bool _asBool(dynamic v) {
  if (v is bool) return v;
  if (v is num) return v != 0;
  if (v is String) return v.toLowerCase() == 'true';
  return false;
}

double _asDouble(dynamic v, [double fallback = 0]) {
  if (v is num) return v.toDouble();
  if (v is String) return double.tryParse(v) ?? fallback;
  return fallback;
}

Map<String, dynamic> _reqMap(dynamic v, String what) {
  if (v is Map) return v.cast<String, dynamic>();
  throw FormatException('malformed $what: expected object, got ${v.runtimeType}');
}

String _reqStr(Map<String, dynamic> j, String key, String what) {
  final v = j[key];
  if (v is String && v.isNotEmpty) return v;
  throw FormatException('malformed $what: missing $key');
}

int _reqInt(Map<String, dynamic> j, String key, String what) {
  final v = j[key];
  if (v is int) return v;
  if (v is num) return v.toInt();
  if (v is String) {
    final p = int.tryParse(v);
    if (p != null) return p;
  }
  throw FormatException('malformed $what: missing $key');
}

/// 角色详情（展示白名单；`system_prompt`/LLM 配置等内部字段**不进入**本 DTO）。
class ReaderCharacter {
  final int id;
  final String name;
  final String? bio;
  final String? selfStatement;
  final String? personality;
  final String? relationshipSummary;
  final String? currentStatus;
  final String? gender;
  final String? birthday; // 后端就是字符串（可能是纯日期或自由文本），原样展示
  final int? height;
  final int? weight;
  final String? appearance;
  final int? talkativeness;
  final int? timezoneOffset;
  final String? avatarUrl; // 本单不加载图片，仅占位
  final String createdAt; // UTC ISO 原文

  const ReaderCharacter({
    required this.id,
    required this.name,
    this.bio,
    this.selfStatement,
    this.personality,
    this.relationshipSummary,
    this.currentStatus,
    this.gender,
    this.birthday,
    this.height,
    this.weight,
    this.appearance,
    this.talkativeness,
    this.timezoneOffset,
    this.avatarUrl,
    this.createdAt = '',
  });

  factory ReaderCharacter.fromJson(Map<String, dynamic> j) => ReaderCharacter(
        id: _reqInt(j, 'id', 'character'),
        name: _asStr(j['name']),
        bio: _asStrN(j['bio']),
        selfStatement: _asStrN(j['self_statement']),
        personality: _asStrN(j['personality']),
        relationshipSummary: _asStrN(j['relationship_summary']),
        currentStatus: _asStrN(j['current_status']),
        gender: _asStrN(j['gender']),
        birthday: _asStrN(j['birthday']),
        height: _asIntN(j['height']),
        weight: _asIntN(j['weight']),
        appearance: _asStrN(j['appearance']),
        talkativeness: _asIntN(j['talkativeness']),
        timezoneOffset: _asIntN(j['timezone_offset']),
        avatarUrl: _asStrN(j['avatar_url']),
        createdAt: _asStr(j['created_at']),
      );
}

/// 记忆条目（列表项与详情同构）。
class ReaderMemory {
  final int id;
  final int characterId;
  final String memoryType;
  final String? subType;
  final String? source;
  final String? sourceLabel; // 后端中文标签，原文展示
  final String? epistemicStatus; // FACT/INFERRED/... 原文展示，不升格
  final String? title;
  final String content;
  final int importance; // 1-5 星
  final double importancePct; // 0-100
  final bool isPinned;
  final bool isLocked;
  final bool isArchived;
  final String? chainId;
  final String? nodeType;
  final String? whyItMatters;
  final String createdAt; // UTC ISO 原文
  final String updatedAt;

  const ReaderMemory({
    required this.id,
    required this.characterId,
    required this.memoryType,
    this.subType,
    this.source,
    this.sourceLabel,
    this.epistemicStatus,
    this.title,
    required this.content,
    this.importance = 0,
    this.importancePct = 0,
    this.isPinned = false,
    this.isLocked = false,
    this.isArchived = false,
    this.chainId,
    this.nodeType,
    this.whyItMatters,
    this.createdAt = '',
    this.updatedAt = '',
  });

  factory ReaderMemory.fromJson(Map<String, dynamic> j) => ReaderMemory(
        id: _reqInt(j, 'id', 'memory'),
        characterId: _asInt(j['character_id']),
        memoryType: _asStr(j['memory_type']),
        subType: _asStrN(j['sub_type']),
        source: _asStrN(j['source']),
        sourceLabel: _asStrN(j['source_label']),
        epistemicStatus: _asStrN(j['epistemic_status']),
        title: _asStrN(j['title']),
        content: _reqStr(j, 'content', 'memory'),
        importance: _asInt(j['importance']),
        importancePct: _asDouble(j['importance_pct']),
        isPinned: _asBool(j['is_pinned']),
        isLocked: _asBool(j['is_locked']),
        isArchived: _asBool(j['is_archived']),
        chainId: _asStrN(j['chain_id']),
        nodeType: _asStrN(j['node_type']),
        whyItMatters: _asStrN(j['why_it_matters']),
        createdAt: _asStr(j['created_at']),
        updatedAt: _asStr(j['updated_at']),
      );
}

/// 记忆分页：total 是服务端「过滤后全量条数」（真分页口径）。
class ReaderMemoryPage {
  final List<ReaderMemory> items;
  final int total;
  const ReaderMemoryPage(this.items, this.total);
}

/// 记忆链条节点（`/{id}/chain` 数组元素）。
class ReaderMemoryChainNode {
  final int id;
  final String content;
  final String memoryType;
  final String? subType;
  final String? nodeType;
  final int? parentId;
  final String? chainId;
  final String createdAt; // 后端 str[:19]（UTC，无时区后缀）
  final bool isCurrent;
  final bool isArchived;

  const ReaderMemoryChainNode({
    required this.id,
    required this.content,
    required this.memoryType,
    this.subType,
    this.nodeType,
    this.parentId,
    this.chainId,
    this.createdAt = '',
    this.isCurrent = false,
    this.isArchived = false,
  });

  factory ReaderMemoryChainNode.fromJson(Map<String, dynamic> j) =>
      ReaderMemoryChainNode(
        id: _reqInt(j, 'id', 'chain node'),
        content: _asStr(j['content']),
        memoryType: _asStr(j['memory_type']),
        subType: _asStrN(j['sub_type']),
        nodeType: _asStrN(j['node_type']),
        parentId: _asIntN(j['parent_id']),
        chainId: _asStrN(j['chain_id']),
        createdAt: _asStr(j['created_at']),
        isCurrent: _asBool(j['is_current']),
        isArchived: _asBool(j['is_archived']),
      );
}

/// 事实修正历史（`/{id}/history`）：fact 当前值 + corrections 旧值→新值时间线。
class ReaderFactHistory {
  final String factContent;
  final String? factStatus;
  final String? factEpistemic;
  final List<ReaderFactCorrection> corrections;
  final bool truncated;

  const ReaderFactHistory({
    required this.factContent,
    this.factStatus,
    this.factEpistemic,
    required this.corrections,
    this.truncated = false,
  });
}

class ReaderFactCorrection {
  final int seq;
  final int oldId;
  final int newId;
  final String oldValue;
  final String newValue;
  final String? changedAt; // 可空：两端时间都缺时后端给 null（不猜）
  final String? changedAtFrom; // valid_to / receipt_created_at / null
  final String? oldSource;
  final String? newSource;
  final String? reason; // 无回执 → null

  const ReaderFactCorrection({
    required this.seq,
    required this.oldId,
    required this.newId,
    required this.oldValue,
    required this.newValue,
    this.changedAt,
    this.changedAtFrom,
    this.oldSource,
    this.newSource,
    this.reason,
  });

  factory ReaderFactCorrection.fromJson(Map<String, dynamic> j) =>
      ReaderFactCorrection(
        seq: _asInt(j['seq']),
        oldId: _asInt(j['old_id']),
        newId: _asInt(j['new_id']),
        oldValue: _asStr(j['old_value']),
        newValue: _asStr(j['new_value']),
        changedAt: _asStrN(j['changed_at']),
        changedAtFrom: _asStrN(j['changed_at_from']),
        oldSource: _asStrN(j['old_source']),
        newSource: _asStrN(j['new_source']),
        reason: _asStrN(j['reason']),
      );
}

/// 角色日记条目（diary_date 是纯日期字符串 YYYY-MM-DD，**不做时区换算**）。
class ReaderDiaryEntry {
  final int id;
  final int characterId;
  final String diaryDate;
  final String content;
  final String createdAt;

  const ReaderDiaryEntry({
    required this.id,
    required this.characterId,
    required this.diaryDate,
    required this.content,
    this.createdAt = '',
  });

  factory ReaderDiaryEntry.fromJson(Map<String, dynamic> j) => ReaderDiaryEntry(
        id: _reqInt(j, 'id', 'diary entry'),
        characterId: _asInt(j['character_id']),
        diaryDate: _reqStr(j, 'diary_date', 'diary entry'),
        content: _reqStr(j, 'content', 'diary entry'),
        createdAt: _asStr(j['created_at']),
      );
}

/// 动态（朋友圈）。作者：senderType=='ai' → 角色（characterId/characterName）；
/// =='user' → 用户（userId，characterName 承载昵称）。时间显示按 authorTzOffset。
class ReaderMoment {
  final int id;
  final int characterId;
  final String characterName;
  final int userId;
  final String senderType;
  final String content;
  final String? imageDesc; // 图片只展示既有描述/占位，不加载 image_url
  final int likesCount;
  final String createdAt;
  final int authorTzOffset;
  final bool likedByMe;
  final List<String> likers;
  final List<ReaderComment> comments;

  const ReaderMoment({
    required this.id,
    required this.characterId,
    required this.characterName,
    required this.userId,
    required this.senderType,
    required this.content,
    this.imageDesc,
    this.likesCount = 0,
    this.createdAt = '',
    this.authorTzOffset = 8,
    this.likedByMe = false,
    this.likers = const [],
    this.comments = const [],
  });

  bool get isAi => senderType == 'ai';

  factory ReaderMoment.fromJson(Map<String, dynamic> j) => ReaderMoment(
        id: _reqInt(j, 'id', 'moment'),
        characterId: _asInt(j['character_id']),
        characterName: _asStr(j['character_name']),
        userId: _asInt(j['user_id']),
        senderType: _asStr(j['sender_type']),
        content: _reqStr(j, 'content', 'moment'),
        imageDesc: _asStrN(j['image_desc']),
        likesCount: _asInt(j['likes_count']),
        createdAt: _asStr(j['created_at']),
        authorTzOffset: _asInt(j['author_tz_offset'], 8),
        likedByMe: _asBool(j['liked_by_me']),
        likers: [
          for (final v in (j['likers'] is List ? j['likers'] as List : const []))
            _asStr(v),
        ],
        comments: [
          for (final c in (j['comments'] is List ? j['comments'] as List : const []))
            ReaderComment.fromJson(_reqMap(c, 'moment comment')),
        ],
      );
}

/// 动态评论（树形；replies 嵌套）。
class ReaderComment {
  final int id;
  final int momentId;
  final int? parentId;
  final String senderType;
  final int senderId;
  final String senderName;
  final String content;
  final String createdAt;
  final List<ReaderComment> replies;

  const ReaderComment({
    required this.id,
    required this.momentId,
    this.parentId,
    required this.senderType,
    required this.senderId,
    required this.senderName,
    required this.content,
    this.createdAt = '',
    this.replies = const [],
  });

  factory ReaderComment.fromJson(Map<String, dynamic> j) => ReaderComment(
        id: _reqInt(j, 'id', 'comment'),
        momentId: _asInt(j['moment_id']),
        parentId: _asIntN(j['parent_id']),
        senderType: _asStr(j['sender_type']),
        senderId: _asInt(j['sender_id']),
        senderName: _asStr(j['sender_name']),
        content: _reqStr(j, 'content', 'comment'),
        createdAt: _asStr(j['created_at']),
        replies: [
          for (final r in (j['replies'] is List ? j['replies'] as List : const []))
            ReaderComment.fromJson(_reqMap(r, 'comment reply')),
        ],
      );
}

/// 动态分页：后端 total＝**本页条数**（非全量总数）；续页判据由调用方按「返回数==limit」。
class ReaderMomentPage {
  final List<ReaderMoment> items;
  final int pageCount;
  const ReaderMomentPage(this.items, this.pageCount);
}

/// 个人备忘。
///
/// [userId] 为**可空**（旧响应可能没有该字段，兼容降级）：仅用于「本人条目才给编辑入口」
/// 的归属判定；缺失时一律按**只读**处理，不猜本人。
class ReaderMemo {
  final int id;
  final int? userId;
  final String? title;
  final String content;
  final String createdAt;
  final String updatedAt;

  const ReaderMemo({
    required this.id,
    this.userId,
    this.title,
    required this.content,
    this.createdAt = '',
    this.updatedAt = '',
  });

  factory ReaderMemo.fromJson(Map<String, dynamic> j) => ReaderMemo(
        id: _reqInt(j, 'id', 'memo'),
        userId: _asIntN(j['user_id']),
        title: _asStrN(j['title']),
        content: _reqStr(j, 'content', 'memo'),
        createdAt: _asStr(j['created_at']),
        updatedAt: _asStr(j['updated_at']),
      );
}

/// 个人日记（diary_date 纯日期字符串，与「角色日记」是不同数据）。
///
/// [userId] 为**可空**（旧响应可能没有该字段）：编辑归属判定用；缺失即按只读处理。
class ReaderUserDiary {
  final int id;
  final int? userId;
  final String diaryDate;
  final String content;
  final String createdAt;
  final String updatedAt;

  const ReaderUserDiary({
    required this.id,
    this.userId,
    required this.diaryDate,
    required this.content,
    this.createdAt = '',
    this.updatedAt = '',
  });

  factory ReaderUserDiary.fromJson(Map<String, dynamic> j) => ReaderUserDiary(
        id: _reqInt(j, 'id', 'user diary'),
        userId: _asIntN(j['user_id']),
        diaryDate: _reqStr(j, 'diary_date', 'user diary'),
        content: _reqStr(j, 'content', 'user diary'),
        createdAt: _asStr(j['created_at']),
        updatedAt: _asStr(j['updated_at']),
      );
}
