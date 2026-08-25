class CharacterState {
  final int characterId;
  final int mood;
  final int bodyTemp;
  final int desire;
  final int possessiveness;
  final int fatigue;
  final int sensitivity;
  final int comfort;
  final int anger;
  final String updatedAt;

  const CharacterState({
    required this.characterId,
    required this.mood,
    required this.bodyTemp,
    required this.desire,
    required this.possessiveness,
    required this.fatigue,
    required this.sensitivity,
    required this.comfort,
    required this.anger,
    required this.updatedAt,
  });

  factory CharacterState.fromJson(Map<String, dynamic> json) {
    return CharacterState(
      characterId: json['character_id'] as int? ?? 0,
      mood: json['mood'] as int? ?? 50,
      bodyTemp: json['body_temp'] as int? ?? 50,
      desire: json['desire'] as int? ?? 50,
      possessiveness: json['possessiveness'] as int? ?? 50,
      fatigue: json['fatigue'] as int? ?? 50,
      sensitivity: json['sensitivity'] as int? ?? 50,
      comfort: json['comfort'] as int? ?? 50,
      anger: json['anger'] as int? ?? 50,
      updatedAt: json['updated_at'] as String? ?? '',
    );
  }
}
