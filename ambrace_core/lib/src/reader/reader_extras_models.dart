class ReaderRelationship {
  const ReaderRelationship({
    required this.characterId,
    required this.name,
    required this.relationType,
    required this.isPartner,
    required this.summary,
  });

  final int characterId;
  final String name;
  final String relationType;
  final bool isPartner;
  final String summary;

  factory ReaderRelationship.fromJson(Map<String, dynamic> j) => ReaderRelationship(
        characterId: _int(j['character_id']),
        name: _str(j['name']),
        relationType: _str(j['relation_type']),
        isPartner: j['is_partner'] == true,
        summary: _str(j['relationship_summary']),
      );
}

class ReaderWorldFact {
  const ReaderWorldFact({
    required this.id,
    required this.predicate,
    required this.objectValue,
    required this.author,
    required this.isAuthoritative,
    required this.assertedAt,
  });

  final int id;
  final String predicate;
  final String objectValue;
  final String author;
  final bool isAuthoritative;
  final String assertedAt;

  factory ReaderWorldFact.fromJson(Map<String, dynamic> j) => ReaderWorldFact(
        id: _int(j['id']),
        predicate: _str(j['predicate']),
        objectValue: _str(j['object_value']),
        author: _str(j['author']),
        isAuthoritative: j['is_authoritative'] == true,
        assertedAt: _str(j['asserted_at']),
      );
}

int _int(dynamic v) => v is num ? v.toInt() : int.tryParse('$v') ?? 0;
String _str(dynamic v) => v == null ? '' : '$v';

class ReaderLorebookEntry {
  const ReaderLorebookEntry({
    required this.id,
    required this.title,
    required this.content,
    required this.keywords,
    required this.active,
  });

  final int id;
  final String title;
  final String content;
  final String keywords;
  final bool active;

  factory ReaderLorebookEntry.fromJson(Map<String, dynamic> j) => ReaderLorebookEntry(
        id: _int(j['id']),
        title: _str(j['title']),
        content: _str(j['content']),
        keywords: (j['keywords'] is List)
            ? (j['keywords'] as List).map((e) => '').join('、')
            : _str(j['keywords']),
        active: j['active'] != false,
      );
}
class ReaderPet {
  const ReaderPet({
    required this.id,
    required this.name,
    required this.speciesLabel,
    required this.level,
    required this.hunger,
    required this.mood,
    required this.energy,
    required this.cleanliness,
    required this.statusText,
    required this.needAttention,
  });

  final int id;
  final String name;
  final String speciesLabel;
  final int level;
  final int hunger;
  final int mood;
  final int energy;
  final int cleanliness;
  final String statusText;
  final bool needAttention;

  factory ReaderPet.fromJson(Map<String, dynamic> j) => ReaderPet(
        id: _int(j['id']),
        name: _str(j['name']),
        speciesLabel: _str(j['species_label']),
        level: _int(j['level']),
        hunger: _int(j['hunger']),
        mood: _int(j['mood']),
        energy: _int(j['energy']),
        cleanliness: _int(j['cleanliness']),
        statusText: _str(j['status_text']),
        needAttention: j['need_attention'] == true,
      );
}
class ReaderLifeEvent {
  const ReaderLifeEvent({
    required this.id,
    required this.subType,
    required this.content,
    required this.importance,
    required this.createdAt,
  });

  final int id;
  final String subType;
  final String content;
  final double importance;
  final String createdAt;

  factory ReaderLifeEvent.fromJson(Map<String, dynamic> j) => ReaderLifeEvent(
        id: _int(j['id']),
        subType: _str(j['sub_type']),
        content: _str(j['content']),
        importance: (j['importance'] is num) ? (j['importance'] as num).toDouble() : 0,
        createdAt: _str(j['created_at']),
      );
}
class ReaderLifeArtifact {
  const ReaderLifeArtifact({
    required this.id,
    required this.type,
    required this.title,
    required this.contentText,
    required this.contentUrl,
    required this.createdAt,
  });

  final int id;
  final String type;
  final String title;
  final String contentText;
  final String? contentUrl;
  final String createdAt;

  factory ReaderLifeArtifact.fromJson(Map<String, dynamic> j) => ReaderLifeArtifact(
        id: _int(j['id']),
        type: _str(j['type']),
        title: _str(j['title']),
        contentText: _str(j['content_text']),
        contentUrl: j['content_url'] as String?,
        createdAt: _str(j['created_at']),
      );
}

/// AI 生活兴趣（Phase 3）：level 越高越热爱，按等级降序返回。
class ReaderLifeInterest {
  const ReaderLifeInterest({
    required this.id,
    required this.name,
    required this.level,
    required this.status,
    required this.source,
    required this.lastEngagedAt,
  });

  final int id;
  final String name;
  final int level;
  final String status;
  final String source;
  final String? lastEngagedAt;

  factory ReaderLifeInterest.fromJson(Map<String, dynamic> j) =>
      ReaderLifeInterest(
        id: _int(j['id']),
        name: _str(j['name']),
        level: _int(j['level']),
        status: _str(j['status']),
        source: _str(j['source']),
        lastEngagedAt: j['last_engaged_at'] as String?,
      );
}

/// AI 生活目标（Phase 3）：progress / progressTotal 用于进度条（0 表示无进度）。
class ReaderLifeGoal {
  const ReaderLifeGoal({
    required this.id,
    required this.type,
    required this.title,
    required this.description,
    required this.priority,
    required this.progress,
    required this.progressTotal,
    required this.status,
    required this.relatedUser,
  });

  final int id;
  final String type;
  final String title;
  final String description;
  final int priority;
  final int progress;
  final int progressTotal;
  final String status;
  final bool relatedUser;

  factory ReaderLifeGoal.fromJson(Map<String, dynamic> j) => ReaderLifeGoal(
        id: _int(j['id']),
        type: _str(j['type']),
        title: _str(j['title']),
        description: _str(j['description']),
        priority: _int(j['priority']),
        progress: _int(j['progress']),
        progressTotal: _int(j['progress_total']),
        status: _str(j['status']),
        relatedUser: j['related_user'] == true,
      );
}

/// AI 真实浏览记录（Phase B）：browse/learn 活动的网页记录（URL / 标题 / 时长）。
class ReaderLifeBrowsing {
  const ReaderLifeBrowsing({
    required this.id,
    required this.title,
    required this.url,
    required this.durationSec,
    required this.activityType,
    required this.createdAt,
  });

  final int id;
  final String title;
  final String url;
  final int durationSec;
  final String activityType;
  final String createdAt;

  factory ReaderLifeBrowsing.fromJson(Map<String, dynamic> j) =>
      ReaderLifeBrowsing(
        id: _int(j['id']),
        title: _str(j['title']),
        url: _str(j['url']),
        durationSec: _int(j['duration_sec']),
        activityType: _str(j['activity_type']),
        createdAt: _str(j['created_at']),
      );
}
