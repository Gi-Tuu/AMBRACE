class DiaryEntry {
  final int id;
  final int characterId;
  final String diaryDate;
  final String content;
  final String createdAt;

  DiaryEntry({
    required this.id,
    required this.characterId,
    required this.diaryDate,
    required this.content,
    required this.createdAt,
  });

  factory DiaryEntry.fromJson(Map<String, dynamic> json) {
    return DiaryEntry(
      id: json['id'] as int,
      characterId: json['character_id'] as int,
      diaryDate: json['diary_date'] as String,
      content: json['content'] as String,
      createdAt: json['created_at'] as String? ?? '',
    );
  }
}
