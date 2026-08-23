import 'package:dio/dio.dart';
import '../api_client.dart';

/// ChatGroupApi：家庭群聊（多角色同群聊天 + 用户插话）
extension ChatGroupApi on ApiClient {
  /// 创建家庭群聊
  Future<Map<String, dynamic>> createChatGroup(String name, List<int> characterIds) async {
    final r = await dio.post('/api/v1/chat-groups', data: {
      'name': name,
      'character_ids': characterIds,
    });
    return r.data as Map<String, dynamic>;
  }

  /// 群列表（含成员）
  Future<List<Map<String, dynamic>>> getChatGroups() async {
    final r = await dio.get('/api/v1/chat-groups');
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 删除群
  Future<void> deleteChatGroup(int groupId) async {
    await dio.delete('/api/v1/chat-groups/$groupId');
  }

  /// 拉群：添加角色进群
  Future<Map<String, dynamic>> addChatGroupMembers(
      int groupId, List<int> characterIds) async {
    final r = await dio.post('/api/v1/chat-groups/$groupId/members',
        data: {'character_ids': characterIds});
    return r.data as Map<String, dynamic>;
  }

  /// 移除角色
  Future<Map<String, dynamic>> removeChatGroupMember(
      int groupId, int characterId) async {
    final r = await dio.delete('/api/v1/chat-groups/$groupId/members/$characterId');
    return r.data as Map<String, dynamic>;
  }

  /// 群消息（时间正序）
  Future<List<Map<String, dynamic>>> getChatGroupMessages(int groupId, {int limit = 100}) async {
    final r = await dio.get('/api/v1/chat-groups/$groupId/messages', queryParameters: {'limit': limit});
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  /// 用户发言 → 返回 {user_message, replies}
  /// 群回复需同步等 LLM 生成，单独放宽超时（全局 10s 会误报发送失败）
  Future<Map<String, dynamic>> sendChatGroupMessage(int groupId, String content) async {
    final r = await dio.post(
      '/api/v1/chat-groups/$groupId/messages',
      data: {'content': content},
      options: Options(receiveTimeout: const Duration(seconds: 90)),
    );
    return r.data as Map<String, dynamic>;
  }
}
