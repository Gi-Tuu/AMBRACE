/// 家庭群聊（多角色同群）的共享只读/发送客户端。
///
/// 契约以 `backend/app/api/chat_groups.py` 与 `backend/app/application/chat_groups.py`
/// 为准：
/// - GET  `/api/v1/chat-groups` → `{items,total}`；
/// - GET  `/api/v1/chat-groups/{id}/messages?limit=...` → `{items,total}`；
/// - POST `/api/v1/chat-groups/{id}/messages` `{content}` → `{user_message,replies}`；
/// - 群回复是同步生成，单独放宽 90s receive timeout，不改变全局文字聊天预算。
library;

import 'package:dio/dio.dart';

import '../api/amb_api_client.dart';
import '../api/api_exception.dart';
import '../platform/text_file_picker_types.dart';

class GroupMember {
  const GroupMember({required this.id, required this.name, this.muted = false});

  final int id;
  final String name;
  final bool muted;

  factory GroupMember.fromJson(Map<String, dynamic> j) => GroupMember(
        id: _int(j['id']),
        name: _str(j['name']),
        muted: j['muted'] == true,
      );
}

class ChatGroupSummary {
  const ChatGroupSummary({
    required this.id,
    required this.name,
    required this.members,
    this.createdAt = '',
  });

  final int id;
  final String name;
  final List<GroupMember> members;
  final String createdAt;

  factory ChatGroupSummary.fromJson(Map<String, dynamic> j) => ChatGroupSummary(
        id: _int(j['id']),
        name: _str(j['name']),
        members: [
          for (final m in (j['members'] is List ? j['members'] as List : const []))
            GroupMember.fromJson(_map(m)),
        ],
        createdAt: _str(j['created_at']),
      );
}

class GroupChatMessage {
  const GroupChatMessage({
    required this.id,
    required this.senderType,
    required this.senderName,
    required this.content,
    this.characterId,
    this.senderAvatar,
    this.fileUrl,
    this.fileName,
    this.fileSize,
    this.createdAt = '',
    this.notifyUser = false,
  });

  final int id;
  final String senderType;
  final int? characterId;
  final String senderName;
  final String? senderAvatar;
  final String? fileUrl;
  final String? fileName;
  final int? fileSize;
  final String content;
  final String createdAt;
  final bool notifyUser;

  bool get isUser => senderType == 'user';

  factory GroupChatMessage.fromJson(Map<String, dynamic> j) => GroupChatMessage(
        id: _int(j['id']),
        senderType: _str(j['sender_type']),
        characterId: _intN(j['character_id']),
        senderName: _str(j['sender_name']),
        senderAvatar: _strN(j['sender_avatar']),
        fileUrl: _strN(j['file_url']),
        fileName: _strN(j['file_name']),
        fileSize: _intN(j['file_size']),
        content: _str(j['content']),
        createdAt: _str(j['created_at']),
        notifyUser: j['notify_user'] == 1 || j['notify_user'] == true,
      );
}

class GroupSendResult {
  const GroupSendResult({required this.userMessage, required this.replies});

  final GroupChatMessage userMessage;
  final List<GroupChatMessage> replies;
}

class GroupMention {
  const GroupMention({
    required this.id,
    required this.groupId,
    required this.groupName,
    required this.senderName,
    required this.content,
    this.characterId,
    this.createdAt = '',
  });

  final int id;
  final int groupId;
  final String groupName;
  final int? characterId;
  final String senderName;
  final String content;
  final String createdAt;

  factory GroupMention.fromJson(Map<String, dynamic> j) => GroupMention(
        id: _int(j['id']),
        groupId: _int(j['group_id']),
        groupName: _str(j['group_name']),
        characterId: _intN(j['character_id']),
        senderName: _str(j['sender_name']),
        content: _str(j['content']),
        createdAt: _str(j['created_at']),
      );
}

class GroupChatApi {
  GroupChatApi(this.client);

  final AmbApiClient client;

  Future<List<ChatGroupSummary>> listGroups({CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/chat-groups', cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed group list');
    }
    return [
      for (final item in data['items'] as List) ChatGroupSummary.fromJson(_map(item)),
    ];
  }

  Future<List<GroupChatMessage>> listMessages(int groupId,
      {int limit = 100, CancelToken? cancelToken}) async {
    final r = await client.dio.get(
      '/api/v1/chat-groups/$groupId/messages',
      queryParameters: {'limit': limit},
      cancelToken: cancelToken,
    );
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed group messages');
    }
    return [
      for (final item in data['items'] as List) GroupChatMessage.fromJson(_map(item)),
    ];
  }

  Future<GroupSendResult> sendMessage(int groupId, String content,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post(
      '/api/v1/chat-groups/$groupId/messages',
      data: {'content': content},
      options: Options(receiveTimeout: const Duration(seconds: 90)),
      cancelToken: cancelToken,
    );
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed group send response');
    }
    final user = data['user_message'];
    if (user is! Map) {
      throw const ApiException(kind: 'unknown', message: 'group response has no user_message');
    }
    if (data['replies'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'group response has no replies');
    }
    final replies = <GroupChatMessage>[
      for (final item in data['replies'] as List) GroupChatMessage.fromJson(_map(item)),
    ];
    return GroupSendResult(
      userMessage: GroupChatMessage.fromJson(user.cast<String, dynamic>()),
      replies: replies,
    );
  }

  Future<void> deleteGroup(int groupId, {CancelToken? cancelToken}) async {
    await client.dio.delete('/api/v1/chat-groups/$groupId',
        cancelToken: cancelToken);
  }

  Future<void> addMembers(int groupId, List<int> characterIds,
      {CancelToken? cancelToken}) async {
    await client.dio.post('/api/v1/chat-groups/$groupId/members',
        data: {'character_ids': characterIds}, cancelToken: cancelToken);
  }

  Future<void> removeMember(int groupId, int characterId,
      {CancelToken? cancelToken}) async {
    await client.dio.delete(
        '/api/v1/chat-groups/$groupId/members/$characterId',
        cancelToken: cancelToken);
  }

  Future<void> setMemberMuted(int groupId, int characterId, bool muted,
      {CancelToken? cancelToken}) async {
    await client.dio.put('/api/v1/chat-groups/$groupId/members/$characterId',
        data: {'muted': muted}, cancelToken: cancelToken);
  }

  Future<GroupChatMessage> uploadFile(int groupId, PickedTextFile file,
      {String lang = 'zh', CancelToken? cancelToken}) async {
    final bytes = file.bytes;
    if (!file.accepted || bytes == null) {
      throw const ApiException(kind: 'unknown', message: 'file not accepted locally');
    }
    final r = await client.dio.post(
      '/api/v1/chat-groups/$groupId/files',
      data: FormData.fromMap({
        'file': MultipartFile.fromBytes(bytes, filename: file.name),
      }),
      options: Options(
        headers: {'lang': lang},
        receiveTimeout: const Duration(seconds: 90),
        sendTimeout: const Duration(seconds: 90),
      ),
      cancelToken: cancelToken,
    );
    final data = r.data;
    if (data is! Map || data['message'] is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed group file response');
    }
    return GroupChatMessage.fromJson(_map(data['message']));
  }

  Future<List<GroupMention>> listMentions({int afterId = 0, CancelToken? cancelToken}) async {
    final r = await client.dio.get('/api/v1/chat-groups/mentions',
        queryParameters: {'after_id': afterId}, cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map || data['items'] is! List) {
      throw const ApiException(kind: 'unknown', message: 'malformed group mentions');
    }
    return [
      for (final item in data['items'] as List) GroupMention.fromJson(_map(item)),
    ];
  }

  Future<ChatGroupSummary> createGroup(String name, List<int> characterIds,
      {CancelToken? cancelToken}) async {
    final r = await client.dio.post('/api/v1/chat-groups',
        data: {'name': name, 'character_ids': characterIds},
        cancelToken: cancelToken);
    final data = r.data;
    if (data is! Map) {
      throw const ApiException(kind: 'unknown', message: 'malformed group create response');
    }
    return ChatGroupSummary.fromJson(data.cast<String, dynamic>());
  }
}

Map<String, dynamic> _map(dynamic v) =>
    v is Map ? v.cast<String, dynamic>() : const {};

int _int(dynamic v) => v is num ? v.toInt() : int.tryParse('$v') ?? 0;
int? _intN(dynamic v) => v == null ? null : _int(v);
String _str(dynamic v) => v == null ? '' : '$v';
String? _strN(dynamic v) => v == null ? null : '$v';
