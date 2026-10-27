/// RC2 G2：记忆派生关系（只读）DTO。
///
/// 契约见后端 `GET /api/v1/memories/{id}/derivations`：
/// - 两方向 `parents`/`children` 各自独立分页（`has_more`/`next_cursor`）与完整性状态；
/// - 条目只含 id/content/title/memory_type/status/is_archived；
/// - 严格容器/必需字段：坏形状抛 [FormatException]（UI 走失败态，不降级为"无关联"）；
/// - `has_more`/`data_incomplete` 必需 bool；`next_cursor` 允许 null；未知字段向前兼容。
class DerivationItem {
  final int id;
  final String content;
  final String? title;
  final String memoryType;
  final String status;
  final bool isArchived;

  const DerivationItem({
    required this.id,
    required this.content,
    required this.title,
    required this.memoryType,
    required this.status,
    required this.isArchived,
  });

  factory DerivationItem.fromJson(Map<String, dynamic> json) {
    final id = json['id'];
    final content = json['content'];
    final memoryType = json['memory_type'];
    final status = json['status'];
    final archived = json['is_archived'];
    if (id is! int || content is! String || memoryType is! String ||
        status is! String || archived is! bool) {
      throw const FormatException('derivation item: required fields missing or invalid');
    }
    return DerivationItem(
      id: id,
      content: content,
      title: json['title'] as String?,
      memoryType: memoryType,
      status: status,
      isArchived: archived,
    );
  }

  static List<DerivationItem> listFromJson(Object? raw) {
    if (raw is! List) {
      throw const FormatException('derivation direction: items is not a list');
    }
    return raw
        .map((e) => DerivationItem.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList(growable: false);
  }
}

class DerivationDirection {
  final List<DerivationItem> items;
  final bool hasMore;
  final String? nextCursor;
  final bool dataIncomplete;

  const DerivationDirection({
    required this.items,
    required this.hasMore,
    required this.nextCursor,
    required this.dataIncomplete,
  });

  factory DerivationDirection.fromJson(Object? raw) {
    if (raw is! Map) {
      throw const FormatException('derivation direction: not an object');
    }
    final map = Map<String, dynamic>.from(raw);
    final hasMore = map['has_more'];
    final incomplete = map['data_incomplete'];
    final cursor = map['next_cursor'];
    if (hasMore is! bool || incomplete is! bool) {
      throw const FormatException('derivation direction: has_more/data_incomplete invalid');
    }
    if (cursor != null && cursor is! String) {
      throw const FormatException('derivation direction: next_cursor invalid');
    }
    if (hasMore) {
      // R5：有后续必须有可用的前进游标（ASCII 十进制正整数且不超 SQLite int64）。
      final text = cursor is String ? cursor : '';
      final usable = text.isNotEmpty &&
          RegExp(r'^[0-9]+$').hasMatch(text) &&
          text.length <= 19 &&
          (int.tryParse(text) ?? 0) > 0;
      if (!usable) {
        throw const FormatException('derivation direction: has_more requires usable next_cursor');
      }
    }
    return DerivationDirection(
      items: DerivationItem.listFromJson(map['items']),
      hasMore: hasMore,
      nextCursor: cursor as String?,
      dataIncomplete: incomplete,
    );
  }
}

class MemoryDerivations {
  final int memoryId;
  final DerivationDirection parents;
  final DerivationDirection children;

  const MemoryDerivations({
    required this.memoryId,
    required this.parents,
    required this.children,
  });

  factory MemoryDerivations.fromJson(Map<String, dynamic> json) {
    final memoryId = json['memory_id'];
    if (memoryId is! int) {
      throw const FormatException('memory derivations: memory_id missing or invalid');
    }
    return MemoryDerivations(
      memoryId: memoryId,
      parents: DerivationDirection.fromJson(json['parents']),
      children: DerivationDirection.fromJson(json['children']),
    );
  }
}
