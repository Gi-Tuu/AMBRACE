class Pet {
  final int id;
  final String name;
  final String species;
  final String speciesLabel;
  final String? avatarUrl;
  final int level;
  final int exp;
  final int hunger;
  final int mood;
  final int energy;
  final int cleanliness;
  final String statusText;
  final bool needAttention;
  final bool isSpecial;
  final String createdAt;

  const Pet({
    required this.id,
    required this.name,
    required this.species,
    required this.speciesLabel,
    this.avatarUrl,
    required this.level,
    required this.exp,
    required this.hunger,
    required this.mood,
    required this.energy,
    required this.cleanliness,
    required this.statusText,
    required this.needAttention,
    required this.isSpecial,
    required this.createdAt,
  });

  factory Pet.fromJson(Map<String, dynamic> json) {
    final species = json['species'] as String? ?? "";
    return Pet(
      id: json['id'] as int,
      name: json['name'] as String? ?? "",
      species: species,
      speciesLabel: json['species_label'] as String? ?? species,
      avatarUrl: json['avatar_url'] as String?,
      level: json['level'] as int? ?? 1,
      exp: json['exp'] as int? ?? 0,
      hunger: json['hunger'] as int? ?? 80,
      mood: json['mood'] as int? ?? 80,
      energy: json['energy'] as int? ?? 80,
      cleanliness: json['cleanliness'] as int? ?? 80,
      statusText: json['status_text'] as String? ?? "",
      needAttention: json['need_attention'] as bool? ?? false,
      isSpecial: json['is_special'] as bool? ?? false,
      createdAt: json['created_at'] as String? ?? "",
    );
  }
}
