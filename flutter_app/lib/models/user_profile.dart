class UserProfile {
  int id;
  String username;
  String nickname;
  String? birthday;
  String? gender;
  double? height;
  double? weight;
  String? bio;
  String? avatarUrl;
  String? createdAt;

  UserProfile({
    required this.id,
    required this.username,
    required this.nickname,
    this.birthday,
    this.gender,
    this.height,
    this.weight,
    this.bio,
    this.avatarUrl,
    this.createdAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      id: json["id"] as int,
      username: json["username"] as String,
      nickname: json["nickname"] as String,
      birthday: json["birthday"] as String?,
      gender: json["gender"] as String?,
      height: (json["height"] as num?)?.toDouble(),
      weight: (json["weight"] as num?)?.toDouble(),
      bio: json["bio"] as String?,
      avatarUrl: json["avatar_url"] as String?,
      createdAt: json["created_at"] as String?,
    );
  }

  Map<String, dynamic> toUpdateJson() {
    final map = <String, dynamic>{};
    if (nickname.isNotEmpty) map["nickname"] = nickname;
    if (birthday != null) map["birthday"] = birthday;
    if (gender != null) map["gender"] = gender;
    if (height != null) map["height"] = height;
    if (weight != null) map["weight"] = weight;
    if (bio != null) map["bio"] = bio;
    return map;
  }
}
