/// PWA M1a-5 角色创建/基础编辑 —— **共享 core 层数据模型**（无 Flutter UI、无 dart:io）。
///
/// 设计约束（承接 HANDOFF §2/§3）：
/// - **只保留 10 个可编辑字段**的白名单；`system_prompt`、模型配置、凭据等一律不进 DTO；
/// - 响应必须含 `id / user_id / name` 且类型正确，否则 [FormatException]（坏响应不当成功）；
/// - 创建/编辑复用后端既有 `CharacterCreate / CharacterUpdate` 口径：`name` 必填 1–100；
///   `talkativeness` 0–100；`timezone_offset` -12–14；
/// - **「未改」与「主动清空」必须区分**：编辑只提交 `touched` 的字段（对齐服务端
///   `exclude_unset=True`）；清空文本发 `""`、清空数值发 `null`，二者都算显式变更；
/// - 原文保留：不对 `personality / bio / greeting_message / appearance` 做任何翻译或清洗。
library;

/// 本单允许编辑的 10 个字段（白名单，顺序即 UI 分组顺序）。
const List<String> kCharacterEditorFields = <String>[
  'name',
  'bio',
  'personality',
  'chat_style',
  'greeting_message',
  'gender',
  'appearance',
  'talkativeness',
  'talkativeness_locked',
  'timezone_offset',
];

const Set<String> _kEditorFieldSet = <String>{
  'name',
  'bio',
  'personality',
  'chat_style',
  'greeting_message',
  'gender',
  'appearance',
  'talkativeness',
  'talkativeness_locked',
  'timezone_offset',
};

/// 文本类字段（清空语义＝发 `""`）。
const Set<String> _kTextFields = <String>{
  'name',
  'bio',
  'personality',
  'chat_style',
  'greeting_message',
  'gender',
  'appearance',
};

/// 可空整数类字段（清空语义＝发 `null`）。
const Set<String> _kNullableIntFields = <String>{
  'talkativeness',
  'timezone_offset',
};

/// 后端 `CharacterResponse` 的**编辑视图**（只保留白名单字段 + 归属核对字段）。
class CharacterEditorData {
  CharacterEditorData({
    required this.id,
    required this.userId,
    required this.name,
    this.bio,
    this.personality,
    this.chatStyle,
    this.greetingMessage,
    this.gender,
    this.appearance,
    this.talkativeness,
    this.talkativenessLocked = false,
    this.timezoneOffset,
  });

  final int id;
  final int userId;
  final String name;
  final String? bio;
  final String? personality;
  final String? chatStyle;
  final String? greetingMessage;
  final String? gender;
  final String? appearance;
  final int? talkativeness;
  final bool talkativenessLocked;
  final int? timezoneOffset;

  /// 该角色的归属是否就是当前账号（创建后核对 `user_id`；家庭共享角色可能不是本人）。
  bool ownedBy(int? currentUserId) =>
      currentUserId != null && currentUserId == userId;

  /// 严格解析角色响应。**只取白名单字段**；`id / user_id / name` 缺失或类型不符 ⇒
  /// [FormatException]。可选字段「存在但类型错」同样拒绝（坏响应不得半信半疑地落 UI）。
  ///
  /// [expectedId] / [expectedUserId] 用于**归属核对**：编辑返回的 id 必须等于原目标、
  /// 归属必须等于加载时的原角色；创建返回的 owner 必须是当前账号。
  factory CharacterEditorData.fromJson(
    Map<String, dynamic> json, {
    int? expectedId,
    int? expectedUserId,
  }) {
    final rawId = json['id'];
    if (rawId is! int) {
      throw const FormatException('character.id missing or not int');
    }
    final rawUserId = json['user_id'];
    if (rawUserId is! int) {
      throw const FormatException('character.user_id missing or not int');
    }
    final rawName = json['name'];
    if (rawName is! String || rawName.trim().isEmpty) {
      throw const FormatException('character.name missing or blank');
    }
    if (expectedId != null && rawId != expectedId) {
      throw FormatException('character.id mismatch: $rawId != $expectedId');
    }
    if (expectedUserId != null && rawUserId != expectedUserId) {
      throw FormatException('character owner mismatch: $rawUserId != $expectedUserId');
    }

    String? optString(String key) {
      final v = json[key];
      if (v == null) return null;
      if (v is! String) throw FormatException('character.$key must be string');
      return v;
    }

    int? optInt(String key) {
      final v = json[key];
      if (v == null) return null;
      if (v is! int) throw FormatException('character.$key must be int');
      return v;
    }

    final rawLocked = json['talkativeness_locked'];
    if (rawLocked != null && rawLocked is! bool) {
      throw const FormatException('character.talkativeness_locked must be bool');
    }

    return CharacterEditorData(
      id: rawId,
      userId: rawUserId,
      name: rawName,
      bio: optString('bio'),
      personality: optString('personality'),
      chatStyle: optString('chat_style'),
      greetingMessage: optString('greeting_message'),
      gender: optString('gender'),
      appearance: optString('appearance'),
      talkativeness: optInt('talkativeness'),
      talkativenessLocked: (rawLocked as bool?) ?? false,
      timezoneOffset: optInt('timezone_offset'),
    );
  }

  /// 转成可编辑草稿的初值（打开编辑时必须来自本对象 = GET 结果，不用只读 DTO 缺省值反写）。
  CharacterDraft toDraft() => CharacterDraft.fromData(this);
}

/// 编辑草稿：保存**当前值**与**被显式改动的字段集合**。
///
/// 「未改」= 不在 [touched] 里 ⇒ 编辑提交时**整字段省略**（服务端 `exclude_unset` 保留原值）；
/// 「主动清空」= 文本置 `''` / 数值置 `null` 且计入 touched ⇒ 必须提交，不能吞掉。
class CharacterDraft {
  CharacterDraft._(this.values, this.touched);

  factory CharacterDraft.fromData(CharacterEditorData data) {
    return CharacterDraft._(<String, Object?>{
      'name': data.name,
      'bio': data.bio,
      'personality': data.personality,
      'chat_style': data.chatStyle,
      'greeting_message': data.greetingMessage,
      'gender': data.gender,
      'appearance': data.appearance,
      'talkativeness': data.talkativeness,
      'talkativeness_locked': data.talkativenessLocked,
      'timezone_offset': data.timezoneOffset,
    }, <String>{});
  }

  /// 空草稿（新建角色）。
  factory CharacterDraft.blank() => CharacterDraft._(<String, Object?>{
        'name': '',
        'bio': null,
        'personality': null,
        'chat_style': null,
        'greeting_message': null,
        'gender': null,
        'appearance': null,
        'talkativeness': null,
        'talkativeness_locked': false,
        'timezone_offset': null,
      }, <String>{});

  final Map<String, Object?> values;
  final Set<String> touched;

  Object? operator [](String key) => values[key];

  bool get isDirty => touched.isNotEmpty;

  /// 显式设置一个字段（自动标记 touched）。非白名单键直接抛错（防越界字段混入）。
  void setField(String key, Object? value) {
    if (!_kEditorFieldSet.contains(key)) {
      throw ArgumentError.value(key, 'key', 'not an editable character field');
    }
    values[key] = value;
    touched.add(key);
  }

  /// 校验；返回**人类可读**的错误码（调用方负责本地化）。合法返回 null。
  ///
  /// 与后端口径一致：name 1–100（去空白后非空）；talkativeness 0–100；timezone -12–14。
  String? validate() {
    final name = (values['name'] as String? ?? '').trim();
    if (name.isEmpty) return 'name_required';
    // 后端用 Python len(str)＝**Unicode 码点**数；Dart `String.length` 是 UTF-16 **码元**
    // （单个 emoji 计 2）⇒ 直接用会把 60 个 😀 误判成 120。必须按 runes（码点）比较。
    if (name.runes.length > 100) return 'name_too_long';
    final talk = values['talkativeness'];
    if (talk != null) {
      if (talk is! int) return 'talkativeness_invalid';
      if (talk < 0 || talk > 100) return 'talkativeness_out_of_range';
    }
    final tz = values['timezone_offset'];
    if (tz != null) {
      if (tz is! int) return 'timezone_invalid';
      if (tz < -12 || tz > 14) return 'timezone_out_of_range';
    }
    final locked = values['talkativeness_locked'];
    if (locked is! bool) return 'talkativeness_locked_invalid';
    return null;
  }

  /// 创建负载：**发送全部白名单字段的当前值**（null 表示该字段不填）。
  Map<String, dynamic> toCreatePayload() {
    final out = <String, dynamic>{};
    for (final key in kCharacterEditorFields) {
      final v = values[key];
      if (v == null) continue;
      out[key] = _normalizeForWire(key, v);
    }
    // name 必填：即便为空也交给服务端/本地校验给出明确错误，不在这里静默丢弃。
    out['name'] = (values['name'] as String? ?? '').trim();
    return out;
  }

  /// 编辑负载：**只提交 touched 的字段**（未改字段整字段省略），清空字段显式提交 `''` / `null`。
  ///
  /// [original] 用于兜底：若 touched 字段的值与原始值完全相同，则省略（避免无意义写）。
  Map<String, dynamic> toUpdatePayload(CharacterEditorData original) {
    final out = <String, dynamic>{};
    for (final key in touched) {
      // **末端白名单**：即使调用方绕过 setField 直接往 touched/values 塞键
      // （如 system_prompt / 模型配置），也绝不允许进入负载。
      if (!_kEditorFieldSet.contains(key)) continue;
      final value = values[key];
      if (_sameAsOriginal(key, value, original)) continue;
      out[key] = _normalizeForWire(key, value);
    }
    return out;
  }

  bool _sameAsOriginal(String key, Object? value, CharacterEditorData o) {
    final orig = switch (key) {
      'name' => o.name,
      'bio' => o.bio,
      'personality' => o.personality,
      'chat_style' => o.chatStyle,
      'greeting_message' => o.greetingMessage,
      'gender' => o.gender,
      'appearance' => o.appearance,
      'talkativeness' => o.talkativeness,
      'talkativeness_locked' => o.talkativenessLocked,
      'timezone_offset' => o.timezoneOffset,
      _ => null,
    };
    if (value is String && orig is String) {
      return key == 'name' ? value.trim() == orig.trim() : value == orig;
    }
    return value == orig;
  }

  Object? _normalizeForWire(String key, Object? value) {
    if (_kTextFields.contains(key)) {
      if (value == null) return '';
      if (value is String) return key == 'name' ? value.trim() : value;
      throw ArgumentError.value(value, key, 'text field must be String');
    }
    if (_kNullableIntFields.contains(key)) {
      if (value == null) return null;
      if (value is int) return value;
      throw ArgumentError.value(value, key, 'int field must be int');
    }
    if (key == 'talkativeness_locked') {
      if (value is bool) return value;
      throw ArgumentError.value(value, key, 'must be bool');
    }
    return value;
  }
}
