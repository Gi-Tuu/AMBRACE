/// 角色最小模型（跨端共享）。
///
/// 首单只承载闭环必需字段（id / name / avatar）；其余字段**容错忽略**，不新造
/// 手机端不存在的字段名。
class Character {
  final int id;
  final String name;
  final String? avatarUrl;

  const Character({required this.id, required this.name, this.avatarUrl});

  factory Character.fromJson(Map<String, dynamic> json) {
    return Character(
      id: _asInt(json['id']),
      name: (json['name'] as String?) ?? '',
      avatarUrl: json['avatar_url'] as String?,
    );
  }

  /// 容错整数：int / num / 纯数字串都接受，其余回 0（不抛异常）。
  static int _asInt(dynamic v) {
    if (v is int) return v;
    if (v is num) return v.toInt();
    if (v is String) return int.tryParse(v) ?? 0;
    return 0;
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        if (avatarUrl != null) 'avatar_url': avatarUrl,
      };
}
