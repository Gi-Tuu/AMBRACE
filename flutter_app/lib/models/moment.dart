import 'moment_comment.dart';

class Moment {
  final int id;
  final int characterId;
  final String characterName;
  final int userId;
  final String senderType;
  final String? avatarUrl;
  final String content;
  final String? imageUrl;
  final String? imageDesc;
  final int likesCount;
  final List<String> likers;
  final bool isActive;
  final String createdAt;
  final int authorTzOffset;
  final bool likedByMe;
  final List<MomentComment> comments;

  Moment({
    required this.id, required this.characterId, required this.characterName,
    required this.userId, required this.senderType, this.avatarUrl, required this.content,
    this.imageUrl, this.imageDesc,
    required this.likesCount, this.likers = const [], required this.isActive, required this.createdAt,
    this.authorTzOffset = 8,
    required this.likedByMe,
    this.comments = const [],
  });

  factory Moment.fromJson(Map<String, dynamic> json) {
    return Moment(
      id: json['id'] as int,
      characterId: json['character_id'] as int? ?? 0,
      characterName: json['character_name'] as String? ?? "",
      userId: json['user_id'] as int? ?? 0,
      senderType: json['sender_type'] as String? ?? "ai",
      avatarUrl: json['avatar_url'] as String?,
      content: json['content'] as String,
      imageUrl: json['image_url'] as String?,
      imageDesc: json['image_desc'] as String?,
      likesCount: json['likes_count'] as int? ?? 0,
      likers: (json['likers'] as List?)?.map((e) => e.toString()).toList() ?? [],
      isActive: json['is_active'] as bool? ?? true,
      createdAt: json['created_at'] as String? ?? "",
      authorTzOffset: json['author_tz_offset'] as int? ?? 8,
      likedByMe: json['liked_by_me'] as bool? ?? false,
      comments: (json['comments'] as List<dynamic>?)
              ?.map((c) => MomentComment.fromJson(c as Map<String, dynamic>))
              .toList() ?? [],
    );
  }

  Moment copyWith({int? likesCount, List<String>? likers, bool? likedByMe, List<MomentComment>? comments}) {
    return Moment(
      id: id, characterId: characterId, characterName: characterName,
      userId: userId, senderType: senderType, avatarUrl: avatarUrl, content: content,
      imageUrl: imageUrl, imageDesc: imageDesc,
      likesCount: likesCount ?? this.likesCount, likers: likers ?? this.likers, isActive: isActive,
      createdAt: createdAt, authorTzOffset: authorTzOffset, likedByMe: likedByMe ?? this.likedByMe, comments: comments ?? this.comments,
    );
  }
}