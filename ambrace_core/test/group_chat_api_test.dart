import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _Adapter implements HttpClientAdapter {
  _Adapter(this.body);
  final String body;
  RequestOptions? last;
  int calls = 0;

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b, Future<void>? c) async {
    calls++;
    last = o;
    return ResponseBody.fromString(body, 200,
        headers: {Headers.contentTypeHeader: [Headers.jsonContentType]});
  }

  @override
  void close({bool force = false}) {}
}

AmbApiClient _client(_Adapter adapter) {
  final c = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  c.dio.httpClientAdapter = adapter;
  return c;
}

void main() {
  test('listGroups 解析成员/静音', () async {
    final adapter = _Adapter(jsonEncode({
      'items': [
        {
          'id': 1,
          'name': '测试群',
          'members': [
            {'id': 11, 'name': '小阳', 'muted': false},
            {'id': 12, 'name': '小冰', 'muted': true},
          ],
          'created_at': '2026-11-22 10:00:00',
        }
      ],
      'total': 1,
    }));
    final groups = await GroupChatApi(_client(adapter)).listGroups();
    expect(groups.single.name, '测试群');
    expect(groups.single.members.map((m) => m.name), ['小阳', '小冰']);
    expect(groups.single.members[1].muted, isTrue);
  });

  test('listMessages 解析用户/角色消息', () async {
    final adapter = _Adapter(jsonEncode({
      'items': [
        {'id': 1, 'sender_type': 'user', 'sender_name': 'test', 'content': '在吗'},
        {'id': 2, 'sender_type': 'ai', 'character_id': 11, 'sender_name': '小阳', 'content': '在'},
      ],
      'total': 2,
    }));
    final items = await GroupChatApi(_client(adapter)).listMessages(1);
    expect(items.map((m) => m.content), ['在吗', '在']);
    expect(items[1].isUser, isFalse);
    expect(items[1].characterId, 11);
  });

  test('sendMessage 发到群消息端点并放宽 receiveTimeout', () async {
    final adapter = _Adapter(jsonEncode({
      'user_message': {'id': 3, 'sender_type': 'user', 'sender_name': 'test', 'content': '大家好'},
      'replies': [
        {'id': 4, 'sender_type': 'ai', 'character_id': 11, 'sender_name': '小阳', 'content': '好'}
      ],
    }));
    final result = await GroupChatApi(_client(adapter)).sendMessage(1, '大家好');
    expect(adapter.last!.path, '/api/v1/chat-groups/1/messages');
    expect(adapter.last!.data, {'content': '大家好'});
    expect(adapter.last!.receiveTimeout, const Duration(seconds: 90));
    expect(result.userMessage.content, '大家好');
    expect(result.replies.single.content, '好');
  });

  test('群消息坏封套拒绝为 ApiException', () async {
    final adapter = _Adapter(jsonEncode({'items': 'oops'}));
    expect(
      () => GroupChatApi(_client(adapter)).listMessages(1),
      throwsA(isA<ApiException>()),
    );
  });

  test('群管理：删除/加成员/移成员/静音路径与载荷', () async {
    final a = _Adapter('{}');
    await GroupChatApi(_client(a)).deleteGroup(7);
    expect(a.last!.method, 'DELETE');
    expect(a.last!.path, '/api/v1/chat-groups/7');

    final b = _Adapter('{}');
    await GroupChatApi(_client(b)).addMembers(7, [11, 12]);
    expect(b.last!.method, 'POST');
    expect(b.last!.path, '/api/v1/chat-groups/7/members');
    expect(b.last!.data, {'character_ids': [11, 12]});

    final c = _Adapter('{}');
    await GroupChatApi(_client(c)).removeMember(7, 12);
    expect(c.last!.method, 'DELETE');
    expect(c.last!.path, '/api/v1/chat-groups/7/members/12');

    final d = _Adapter('{}');
    await GroupChatApi(_client(d)).setMemberMuted(7, 11, true);
    expect(d.last!.method, 'PUT');
    expect(d.last!.path, '/api/v1/chat-groups/7/members/11');
    expect(d.last!.data, {'muted': true});
  });

  test('listMentions 解析群/角色/内容', () async {
    final adapter = _Adapter(jsonEncode({
      'items': [
        {
          'id': 9,
          'group_id': 1,
          'group_name': '测试群',
          'character_id': 11,
          'sender_name': '小阳',
          'content': '收到',
          'created_at': '2026-11-22 10:00:00',
        }
      ],
      'total': 1,
    }));
    final m = await GroupChatApi(_client(adapter)).listMentions(afterId: 3);
    expect(adapter.last!.queryParameters['after_id'], 3);
    expect(m.single.groupName, '测试群');
    expect(m.single.senderName, '小阳');
  });

  test('uploadFile 走群文件端点，保留文件身份', () async {
    final adapter = _Adapter(jsonEncode({
      'message': {
        'id': 10,
        'group_id': 1,
        'sender_type': 'user',
        'sender_name': '你',
        'content': 'note.txt',
        'file_url': '/uploads/files/groups/1/note.txt',
        'file_name': 'note.txt',
        'file_size': 4,
        'created_at': '2026-11-22 10:01:00',
      }
    }));
    final api = GroupChatApi(_client(adapter));
    final m = await api.uploadFile(
      1,
      PickedTextFile(name: 'note.txt', size: 4, bytes: Uint8List.fromList([1, 2, 3, 4])),
      lang: 'zh',
    );
    expect(adapter.last!.path, '/api/v1/chat-groups/1/files');
    expect(adapter.last!.headers['lang'], 'zh');
    expect((adapter.last!.data as FormData).files.single.key, 'file');
    expect(m.fileName, 'note.txt');
    expect(m.fileUrl, '/uploads/files/groups/1/note.txt');
  });
}
