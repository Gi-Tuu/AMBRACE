/// 用户备忘录（供角色聊天阅读）
class UserMemo {
  final int id;
  final String? title;
  final String content;
  final String? updatedAt;

  UserMemo({
    required this.id,
    this.title,
    required this.content,
    this.updatedAt,
  });

  factory UserMemo.fromJson(Map<String, dynamic> json) {
    return UserMemo(
      id: json['id'] as int,
      title: json['title'] as String?,
      content: json['content'] as String? ?? '',
      updatedAt: json['updated_at'] as String?,
    );
  }
}

/// 用户日记条目（按天）
class UserDiaryEntry {
  final int id;
  final String diaryDate;
  final String content;
  final String? updatedAt;

  UserDiaryEntry({
    required this.id,
    required this.diaryDate,
    required this.content,
    this.updatedAt,
  });

  factory UserDiaryEntry.fromJson(Map<String, dynamic> json) {
    return UserDiaryEntry(
      id: json['id'] as int,
      diaryDate: json['diary_date'] as String,
      content: json['content'] as String? ?? '',
      updatedAt: json['updated_at'] as String?,
    );
  }
}
