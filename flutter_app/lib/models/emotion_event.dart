/// 状态情绪记忆时间线：三源（情绪记忆/状态触发/剧情线）事件，纯只读展示
class EmotionDimChange {
  final String key; // mood / anger / fatigue ...
  final String cn; // 心情 / 怒气值 ...
  final int? from;
  final int? to;
  final int? delta;

  const EmotionDimChange({
    required this.key,
    required this.cn,
    this.from,
    this.to,
    this.delta,
  });

  factory EmotionDimChange.fromJson(Map<String, dynamic> json) {
    return EmotionDimChange(
      key: json['key'] as String? ?? '',
      cn: json['cn'] as String? ?? '',
      from: (json['from'] as num?)?.toInt(),
      to: (json['to'] as num?)?.toInt(),
      delta: (json['delta'] as num?)?.toInt(),
    );
  }
}

class EmotionEvent {
  final int id;
  final String source; // emotion / state_trigger / storyline
  final int sourceId;
  final String atIso; // UTC naive ISO，展示转北京时间
  final String label;
  final List<EmotionDimChange> dimChanges;
  final String content;

  const EmotionEvent({
    required this.id,
    required this.source,
    required this.sourceId,
    required this.atIso,
    required this.label,
    required this.dimChanges,
    required this.content,
  });

  factory EmotionEvent.fromJson(Map<String, dynamic> json) {
    return EmotionEvent(
      id: (json['id'] as num?)?.toInt() ?? 0,
      source: json['source'] as String? ?? '',
      sourceId: (json['source_id'] as num?)?.toInt() ?? 0,
      atIso: json['at'] as String? ?? '',
      label: json['label'] as String? ?? '情绪波动',
      dimChanges: ((json['dim_changes'] as List?) ?? [])
          .map((j) => EmotionDimChange.fromJson(j as Map<String, dynamic>))
          .toList(),
      content: json['content'] as String? ?? '',
    );
  }
}

class EmotionSummary {
  final int total;
  final int emotionCount;
  final int triggerCount;
  final int storylineCount;
  final String topPeriod;
  final String topDimension;
  final String text;

  const EmotionSummary({
    required this.total,
    required this.emotionCount,
    required this.triggerCount,
    required this.storylineCount,
    required this.topPeriod,
    required this.topDimension,
    required this.text,
  });

  factory EmotionSummary.fromJson(Map<String, dynamic> json) {
    return EmotionSummary(
      total: (json['total'] as num?)?.toInt() ?? 0,
      emotionCount: (json['emotion_count'] as num?)?.toInt() ?? 0,
      triggerCount: (json['trigger_count'] as num?)?.toInt() ?? 0,
      storylineCount: (json['storyline_count'] as num?)?.toInt() ?? 0,
      topPeriod: json['top_period'] as String? ?? '',
      topDimension: json['top_dimension'] as String? ?? '',
      text: json['text'] as String? ?? '',
    );
  }
}

class EmotionTimeline {
  final int characterId;
  final int days;
  final List<EmotionEvent> events;
  final EmotionSummary summary;

  const EmotionTimeline({
    required this.characterId,
    required this.days,
    required this.events,
    required this.summary,
  });

  factory EmotionTimeline.fromJson(Map<String, dynamic> json) {
    return EmotionTimeline(
      characterId: (json['character_id'] as num?)?.toInt() ?? 0,
      days: (json['days'] as num?)?.toInt() ?? 7,
      events: ((json['events'] as List?) ?? [])
          .map((j) => EmotionEvent.fromJson(j as Map<String, dynamic>))
          .toList(),
      summary: EmotionSummary.fromJson(
          (json['summary'] as Map<String, dynamic>?) ?? const {}),
    );
  }
}
