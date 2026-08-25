class AICharacter {
  final int id;
  final String name;
  final String? personality;
  final String? chatStyle;
  final String? systemPrompt;
  final String? greetingMessage;
  final String? avatarUrl;
  final String? bio;
  final String? selfStatement;
  final String? currentStatus;
  final String? relationshipSummary;
  final bool isActive;
  final int? height;
  final int? weight;
  final String? gender;
  final String? birthday;
  final String? appearance;
  final String? voice;
  final double? voiceRate;
  final double? voicePitch;
  final int? timezoneOffset;
  final bool cognitiveLoopEnabled;

  AICharacter({
    required this.id,
    required this.name,
    this.personality,
    this.chatStyle,
    this.systemPrompt,
    this.greetingMessage,
    this.avatarUrl,
    this.bio,
    this.selfStatement,
    this.currentStatus,
    this.relationshipSummary,
    this.isActive = true,
    this.height,
    this.weight,
    this.gender,
    this.birthday,
    this.appearance,
    this.voice,
    this.voiceRate,
    this.voicePitch,
    this.timezoneOffset,
    this.cognitiveLoopEnabled = false,
  });

  factory AICharacter.fromJson(Map<String, dynamic> json) {
    return AICharacter(
      id: json['id'] as int,
      name: json['name'] as String,
      personality: json['personality'] as String?,
      chatStyle: json['chat_style'] as String?,
      systemPrompt: json['system_prompt'] as String?,
      greetingMessage: json['greeting_message'] as String?,
      avatarUrl: json['avatar_url'] as String?,
      bio: json['bio'] as String?,
      selfStatement: json['self_statement'] as String?,
      currentStatus: json['current_status'] as String?,
      relationshipSummary: json['relationship_summary'] as String?,
      isActive: json['is_active'] as bool? ?? true,
      height: json['height'] as int?,
      weight: json['weight'] as int?,
      gender: json['gender'] as String?,
      birthday: json['birthday'] as String?,
      appearance: json['appearance'] as String?,
      voice: json['voice'] as String?,
      voiceRate: (json['voice_rate'] as num?)?.toDouble(),
      voicePitch: (json['voice_pitch'] as num?)?.toDouble(),
      timezoneOffset: json['timezone_offset'] as int?,
      cognitiveLoopEnabled: json['cognitive_loop_enabled'] as bool? ?? false,
    );
  }

  Map<String, dynamic> toJson() => {
    'name': name,
    'personality': personality,
    'chat_style': chatStyle,
    'greeting_message': greetingMessage,
    'avatar_url': avatarUrl,
    'birthday': birthday,
    'voice': voice,
    'voice_rate': voiceRate,
    'voice_pitch': voicePitch,
    'timezone_offset': timezoneOffset,
    'cognitive_loop_enabled': cognitiveLoopEnabled,
  };
}