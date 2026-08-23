/// 织库卡片模型（2026-08-12）：列表返回概要级，详情接口返回超集。
class WeaveDetail {
  final String time;
  final String weather;
  final String location;
  final String mood;
  final List<String> events;
  final List<String> details;

  const WeaveDetail({
    this.time = '不详',
    this.weather = '不详',
    this.location = '不详',
    this.mood = '不详',
    this.events = const [],
    this.details = const [],
  });

  factory WeaveDetail.fromJson(Map<String, dynamic> json) => WeaveDetail(
    time: json['time'] as String? ?? '不详',
    weather: json['weather'] as String? ?? '不详',
    location: json['location'] as String? ?? '不详',
    mood: json['mood'] as String? ?? '不详',
    events: (json['events'] as List?)?.map((e) => e.toString()).toList() ?? const [],
    details: (json['details'] as List?)?.map((e) => e.toString()).toList() ?? const [],
  );
}

class WeaveMemoryRef {
  final int id;
  final String memoryType;
  final String? subType;
  final String content;
  final double importancePct;
  final String? sourceLabel;
  final String? sourceIcon;
  final String createdAt;

  const WeaveMemoryRef({
    required this.id,
    required this.memoryType,
    this.subType,
    required this.content,
    this.importancePct = 0,
    this.sourceLabel,
    this.sourceIcon,
    this.createdAt = '',
  });

  factory WeaveMemoryRef.fromJson(Map<String, dynamic> json) => WeaveMemoryRef(
    id: json['id'] as int,
    memoryType: json['memory_type'] as String? ?? '',
    subType: json['sub_type'] as String?,
    content: json['content'] as String? ?? '',
    importancePct: (json['importance_pct'] as num?)?.toDouble() ?? 0,
    sourceLabel: json['source_label'] as String?,
    sourceIcon: json['source_icon'] as String?,
    createdAt: json['created_at'] as String? ?? '',
  );
}

class WeaveCard {
  final int id;
  final int characterId;
  final String title;
  final String summary;
  final double importance;
  final int memoryCount;
  final String createdAt;
  final String characterName;
  final WeaveDetail? detail;
  final List<WeaveMemoryRef>? memories;

  const WeaveCard({
    required this.id,
    this.characterId = 0,
    this.title = '',
    this.summary = '',
    this.importance = 0,
    this.memoryCount = 0,
    this.createdAt = '',
    this.characterName = '',
    this.detail,
    this.memories,
  });

  factory WeaveCard.fromJson(Map<String, dynamic> json) => WeaveCard(
    id: json['id'] as int,
    characterId: json['character_id'] as int? ?? 0,
    title: json['title'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    importance: (json['importance'] as num?)?.toDouble() ?? 0,
    memoryCount: json['memory_count'] as int? ?? 0,
    createdAt: json['created_at'] as String? ?? '',
    characterName: json['character_name'] as String? ?? '',
    detail: json['detail'] != null
        ? WeaveDetail.fromJson(json['detail'] as Map<String, dynamic>)
        : null,
    memories: (json['memories'] as List?)
        ?.map((j) => WeaveMemoryRef.fromJson(j as Map<String, dynamic>))
        .toList(),
  );
}
