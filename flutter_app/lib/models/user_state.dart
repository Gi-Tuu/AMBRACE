class UserState {
  final int userId;
  final int mood;
  final int bodyTemp;
  final int desire;
  final int possessiveness;
  final int fatigue;
  final int sensitivity;
  final int comfort;
  final int anger;
  final String updatedAt;

  const UserState({
    required this.userId,
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

  factory UserState.fromJson(Map<String, dynamic> json) {
    return UserState(
      userId: json['user_id'] as int? ?? 0,
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

  List<int> toValues() => [mood, bodyTemp, desire, possessiveness, fatigue, sensitivity, comfort, anger];

  UserState withValues(List<int> v) {
    return UserState(
      userId: userId,
      mood: v[0], bodyTemp: v[1], desire: v[2], possessiveness: v[3],
      fatigue: v[4], sensitivity: v[5], comfort: v[6], anger: v[7],
      updatedAt: updatedAt,
    );
  }

  Map<String, dynamic> toSubmitJson() {
    return {
      'mood': mood, 'body_temp': bodyTemp, 'desire': desire, 'possessiveness': possessiveness,
      'fatigue': fatigue, 'sensitivity': sensitivity, 'comfort': comfort, 'anger': anger,
    };
  }
}
