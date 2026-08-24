class MomentComment {
  final int id;
  final int momentId;
  final int? parentId;
  final String senderType;
  final int senderId;
  final String senderName;
  final String content;
  final String createdAt;
  final List<MomentComment> replies;

  MomentComment({
    required this.id, required this.momentId, this.parentId,
    required this.senderType, required this.senderId, required this.senderName,
    required this.content, required this.createdAt, this.replies = const [],
  });

  factory MomentComment.fromJson(Map<String, dynamic> json) {
    return MomentComment(
      id: json['id'] as int,
      momentId: json['moment_id'] as int,
      parentId: json['parent_id'] as int?,
      senderType: json['sender_type'] as String? ?? "user",
      senderId: json['sender_id'] as int? ?? 0,
      senderName: json['sender_name'] as String? ?? "",
      content: json['content'] as String? ?? "",
      createdAt: json['created_at'] as String? ?? "",
      replies: (json['replies'] as List<dynamic>?)
              ?.map((r) => MomentComment.fromJson(r as Map<String, dynamic>))
              .toList() ?? [],
    );
  }
}
