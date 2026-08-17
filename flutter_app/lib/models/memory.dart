
class Memory {
  final int id;
  final String memoryType;
  final String? subType;
  final String? source;
  final int? sourceId;
  final String? sourceLabel;
  final String? sourceIcon;
  final String? speakerType;
  final int? speakerId;
  final String? title;
  final String content;
  final int importance;
  final double importancePct;
  final String createdAt;
  final String? updatedAt;
  final String? deleteAt;
  final bool isPinned;
  final bool isLocked;
  final String? whyItMatters;

  Memory({
    required this.id,
    required this.memoryType,
    this.subType,
    this.source,
    this.sourceId,
    this.sourceLabel,
    this.sourceIcon,
    this.speakerType,
    this.speakerId,
    this.title,
    required this.content,
    required this.importance,
    this.importancePct = 0,
    required this.createdAt,
    this.updatedAt,
    this.deleteAt,
    this.isPinned = false,
    this.isLocked = false,
    this.whyItMatters,
  });

  factory Memory.fromJson(Map<String, dynamic> json) {
    return Memory(
      id: json['id'] as int,
      memoryType: json['memory_type'] as String,
      subType: json['sub_type'] as String?,
      source: json['source'] as String?,
      sourceId: json['source_id'] as int?,
      sourceLabel: json['source_label'] as String?,
      sourceIcon: json['source_icon'] as String?,
      speakerType: json['speaker_type'] as String?,
      speakerId: json['speaker_id'] as int?,
      title: json['title'] as String?,
      content: json['content'] as String,
      importance: json['importance'] as int? ?? 1,
      importancePct: (json['importance_pct'] as num?)?.toDouble() ?? 0,
      createdAt: json['created_at'] as String? ?? '',
      updatedAt: json['updated_at'] as String?,
      deleteAt: json['delete_at'] as String?,
      isPinned: json['is_pinned'] as bool? ?? false,
      isLocked: json['is_locked'] as bool? ?? false,
      whyItMatters: json['why_it_matters'] as String?,
    );
  }
}
