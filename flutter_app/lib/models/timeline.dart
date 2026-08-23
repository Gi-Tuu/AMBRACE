class TimelineItem {
  final String date;
  final String type; // first_chat / blessing / pet / memory
  final String title;
  final String desc;
  final int? importance;

  const TimelineItem({
    required this.date,
    required this.type,
    required this.title,
    required this.desc,
    this.importance,
  });

  factory TimelineItem.fromJson(Map<String, dynamic> j) => TimelineItem(
        date: j["date"] as String? ?? "",
        type: j["type"] as String? ?? "memory",
        title: j["title"] as String? ?? "",
        desc: j["desc"] as String? ?? "",
        importance: j["importance"] as int?,
      );
}

class TimelineData {
  final int characterId;
  final String characterName;
  final int daysKnown;
  final String? firstChatAt;
  final bool hasMilestones;
  final List<TimelineItem> items;

  const TimelineData({
    required this.characterId,
    required this.characterName,
    required this.daysKnown,
    this.firstChatAt,
    required this.hasMilestones,
    required this.items,
  });

  factory TimelineData.fromJson(Map<String, dynamic> j) => TimelineData(
        characterId: j["character_id"] as int? ?? 0,
        characterName: j["character_name"] as String? ?? "",
        daysKnown: j["days_known"] as int? ?? 0,
        firstChatAt: j["first_chat_at"] as String?,
        hasMilestones: j["has_milestones"] as bool? ?? false,
        items: (j["items"] as List? ?? [])
            .map((e) => TimelineItem.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}
