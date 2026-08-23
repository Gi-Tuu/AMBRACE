/// AI 间私聊记录（Phase 1 只读展示）
class AIChat {
  final int id;
  final int characterAId;
  final String characterAName;
  final int characterBId;
  final String characterBName;
  final int speakerId;
  final String speakerName;
  final int roundSeq;
  final String content;
  final DateTime createdAt;

  const AIChat({
    required this.id,
    required this.characterAId,
    required this.characterAName,
    required this.characterBId,
    required this.characterBName,
    required this.speakerId,
    required this.speakerName,
    required this.roundSeq,
    required this.content,
    required this.createdAt,
  });

  factory AIChat.fromJson(Map<String, dynamic> json) {
    return AIChat(
      id: (json['id'] as num).toInt(),
      characterAId: (json['character_a_id'] as num).toInt(),
      characterAName: json['character_a_name'] as String? ?? '',
      characterBId: (json['character_b_id'] as num).toInt(),
      characterBName: json['character_b_name'] as String? ?? '',
      speakerId: (json['speaker_id'] as num).toInt(),
      speakerName: json['speaker_name'] as String? ?? '',
      roundSeq: (json['round_seq'] as num).toInt(),
      content: json['content'] as String? ?? '',
      createdAt:
          DateTime.tryParse(json['created_at'] as String? ?? '')?.toLocal() ??
              DateTime.now(),
    );
  }
}
