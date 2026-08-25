import '../../models/ai_chat.dart';
import '../api_client.dart';

/// AiChatsApi：AI 间私聊只读接口（Phase 1）
extension AiChatsApi on ApiClient {
  Future<List<AIChat>> getAiChats({int limit = 100, int? charA, int? charB}) async {
    final r = await dio.get('/api/v1/ai-chats', queryParameters: {
      'limit': limit,
      if (charA != null) 'char_a': charA,
      if (charB != null) 'char_b': charB,
    });
    final data = r.data as Map<String, dynamic>;
    return (data['items'] as List)
        .map((j) => AIChat.fromJson(j as Map<String, dynamic>))
        .toList();
  }
}
