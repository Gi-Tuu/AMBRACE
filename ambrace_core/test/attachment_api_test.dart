// 共享附件 API / DTO / 选择器契约测试（M1a-4，core 长期守卫）。
//
// 契约权威＝backend/app/api/chat.py::upload_chat_file：
// - POST /api/v1/chat/sessions/{sid}/file，multipart 字段名 `file`，语言头 `lang`（**非** X-Lang）；
// - 返回普通 JSON {file_message, chunks}（不是 SSE 上传通道）；
// - 响应 sid 必须等于发起 sid，否则拒绝落 UI；坏封套不得当发送成功。

import 'dart:convert';
import 'dart:typed_data';

import 'package:ambrace_core/ambrace_core.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.body, {this.contentType = Headers.jsonContentType});
  final String body;
  final String contentType;
  RequestOptions? last;
  int calls = 0;
  int statusCode = 200;

  @override
  Future<ResponseBody> fetch(RequestOptions o, Stream<Uint8List>? b, Future<void>? c) async {
    calls++;
    last = o;
    return ResponseBody.fromString(body, statusCode, headers: {
      Headers.contentTypeHeader: [contentType],
    });
  }

  @override
  void close({bool force = false}) {}
}

const String _okJson = '{"file_message":{"id":41,"session_id":7,"sender_type":"user",'
    '"content":"报告.txt","file_url":"/uploads/files/7/abc.txt",'
    '"created_at":"2026-10-17 03:00:00"},'
    '"chunks":[{"id":42,"session_id":7,"sender_type":"ai","content":"收到",'
    '"created_at":"2026-10-17 03:00:01","extra_meta":"{\\"tools\\":[\\"文档问答\\"]}"}]}';

AmbApiClient _client(_FakeAdapter adapter) {
  final c = AmbApiClient(dio: Dio(BaseOptions(baseUrl: 'http://srv.local')));
  c.dio.httpClientAdapter = adapter;
  return c;
}

PickedTextFile _validFile([String name = 'note.txt']) => PickedTextFile(
      name: name,
      size: 4,
      bytes: Uint8List.fromList([104, 105, 33, 10]),
    );

void main() {
  group('附件响应严格解析（attachmentUploadResultFromJson）', () {
    test('happy path：file_message → ChatMessage，fileMeta 正确合并；chunks 解析', () {
      final r = attachmentUploadResultFromJson(
        jsonDecode(_okJson) as Map<String, dynamic>,
        requestedSessionId: 7,
      );
      expect(r.fileMessage.id, 41);
      expect(r.fileMessage.sessionId, 7);
      expect(r.fileMessage.content, '报告.txt');
      expect(r.fileMessage.fileMeta?['url'], '/uploads/files/7/abc.txt');
      expect(r.fileMessage.fileMeta?['name'], '报告.txt');
      expect(r.chunks.length, 1);
      expect(r.chunks.single.id, 42);
      expect(r.chunks.single.senderType, 'ai');
      expect(r.chunks.single.tools, contains('文档问答'));
    });

    test('空 chunks 合法（不虚构回复）', () {
      final r = attachmentUploadResultFromJson(
        {
          'file_message': {
            'id': 1,
            'session_id': 7,
            'sender_type': 'user',
            'content': 'a.md',
            'file_url': '/uploads/files/7/a.md',
            'created_at': '2026-10-17 03:00:00',
          },
          'chunks': <dynamic>[],
        },
        requestedSessionId: 7,
      );
      expect(r.chunks, isEmpty);
    });

    test('file_message.session_id 不等于请求 sid ⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson({
          'file_message': {
            'id': 1,
            'session_id': 99,
            'sender_type': 'user',
            'content': 'a.txt',
            'created_at': '2026-10-17 03:00:00',
          },
          'chunks': <dynamic>[],
        }, requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('chunk.session_id 不等于请求 sid ⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson({
          'file_message': {
            'id': 1,
            'session_id': 7,
            'sender_type': 'user',
            'content': 'a.txt',
            'file_url': '/uploads/files/7/a.txt',
            'created_at': '2026-10-17 03:00:00',
          },
          'chunks': [
            {
              'id': 2,
              'session_id': 99,
              'sender_type': 'ai',
              'content': 'x',
              'created_at': '2026-10-17 03:00:01',
            }
          ],
        }, requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('缺 file_message ⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson({'chunks': <dynamic>[]}, requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('chunks 缺失/非列表 ⇒ FormatException（坏封套不当成功）', () {
      final fm = {
        'id': 1,
        'session_id': 7,
        'sender_type': 'user',
        'content': 'a.txt',
        'file_url': '/uploads/files/7/a.txt',
        'created_at': '2026-10-17 03:00:00',
      };
      expect(() => attachmentUploadResultFromJson({'file_message': fm}, requestedSessionId: 7),
          throwsFormatException);
      expect(
          () => attachmentUploadResultFromJson({'file_message': fm, 'chunks': 'oops'},
              requestedSessionId: 7),
          throwsFormatException);
    });

    test('chunk 非对象 ⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson({
          'file_message': {
            'id': 1,
            'session_id': 7,
            'sender_type': 'user',
            'content': 'a.txt',
            'created_at': '2026-10-17 03:00:00',
          },
          'chunks': ['nope'],
        }, requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('file_message 缺必填字段 id ⇒ FormatException（不伪装成功）', () {
      expect(
        () => attachmentUploadResultFromJson({
          'file_message': {
            'session_id': 7,
            'sender_type': 'user',
            'content': 'a.txt',
            'created_at': '2026-10-17 03:00:00',
          },
          'chunks': <dynamic>[],
        }, requestedSessionId: 7),
        throwsFormatException,
      );
    });
  });

  group('上传成功封套的文件身份（返工 R3；**仅上传专用**，历史 DTO 不适用）', () {
    Map<String, dynamic> fm({
      String senderType = 'user',
      String content = 'note.txt',
      Object? fileUrl = '/uploads/files/7/note.txt',
      String? extraFileUrl,
    }) =>
        {
          'id': 9,
          'session_id': 7,
          'sender_type': senderType,
          'content': content,
          if (fileUrl != null) 'file_url': fileUrl,
          if (extraFileUrl != null)
            'extra_meta': jsonEncode({
              'file': {'url': extraFileUrl, 'name': content, 'size': 4, 'type': 'txt'}
            }),
          'created_at': '2026-10-17 03:00:00',
        };

    test('正对照：有 file_url ⇒ 解析成功且 fileMeta 可用', () {
      final r = attachmentUploadResultFromJson(
          {'file_message': fm(), 'chunks': <dynamic>[]},
          requestedSessionId: 7);
      expect(r.fileMessage.fileMeta?['url'], '/uploads/files/7/note.txt');
      expect(r.chunks, isEmpty, reason: '合法空 chunks 不得被拒');
    });

    test('正对照：兼容既有合法 extra_meta.file.url（无 file_url）', () {
      final r = attachmentUploadResultFromJson({
        'file_message': fm(fileUrl: null, extraFileUrl: '/uploads/files/7/note.txt'),
        'chunks': <dynamic>[],
      }, requestedSessionId: 7);
      expect(r.fileMessage.fileMeta?['url'], '/uploads/files/7/note.txt');
      expect(r.fileMessage.fileMeta?['size'], 4, reason: '既有合法元数据不丢');
    });

    test('反例：缺 file_url 且无文件元数据 ⇒ FormatException（不得当成功）', () {
      expect(
        () => attachmentUploadResultFromJson(
            {'file_message': fm(fileUrl: null), 'chunks': <dynamic>[]},
            requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('反例：file_url 为空串 ⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson(
            {'file_message': fm(fileUrl: ''), 'chunks': <dynamic>[]},
            requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('反例：file_url 非字符串（畸形）⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson(
            {'file_message': fm(fileUrl: 123), 'chunks': <dynamic>[]},
            requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('反例：sender_type 非 user ⇒ FormatException（不是用户文件消息）', () {
      expect(
        () => attachmentUploadResultFromJson(
            {'file_message': fm(senderType: 'ai'), 'chunks': <dynamic>[]},
            requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('反例：content 空（无原名）⇒ FormatException', () {
      expect(
        () => attachmentUploadResultFromJson(
            {'file_message': fm(content: ''), 'chunks': <dynamic>[]},
            requestedSessionId: 7),
        throwsFormatException,
      );
    });

    test('反例：chunks 会话不一致仍拒（文件身份齐全也拦）', () {
      expect(
        () => attachmentUploadResultFromJson({
          'file_message': fm(),
          'chunks': [
            {
              'id': 10,
              'session_id': 99,
              'sender_type': 'ai',
              'content': 'x',
              'created_at': '2026-10-17 03:00:01',
            }
          ],
        }, requestedSessionId: 7),
        throwsFormatException,
      );
    });
  });

  group('AmbAttachmentApi 契约（路径 / 字段 / lang / progress）', () {
    test('POST 路径 + multipart 字段 file + lang 头 + wire 进度键', () async {
      final adapter = _FakeAdapter(_okJson);
      final api = AmbAttachmentApi(_client(adapter));
      final progress = <AmbUploadProgress>[];
      final r = await api.uploadTextFile(
        sessionId: 7,
        file: _validFile(),
        lang: 'en',
        onUploadProgress: progress.add,
      );
      expect(adapter.calls, 1);
      expect(adapter.last!.method, 'POST');
      expect(adapter.last!.path, '/api/v1/chat/sessions/7/file');
      expect(adapter.last!.headers['lang'], 'en');
      expect(adapter.last!.headers.containsKey('X-Lang'), isFalse,
          reason: '契约语言头是 lang，不是 X-Lang');
      expect(adapter.last!.extra.containsKey(kAmbWireUploadProgressExtraKey), isTrue,
          reason: '进度必须经命名键传入适配器（真实 wire 上行）');
      final form = adapter.last!.data as FormData;
      expect(form.files.length, 1);
      expect(form.files.first.key, 'file');
      expect(form.files.first.value.filename, 'note.txt');
      expect(r.fileMessage.id, 41);
      expect(r.chunks.single.id, 42);
    });

    test('未通过本地校验（拒绝/无字节）⇒ ApiException，0 请求', () async {
      final adapter = _FakeAdapter(_okJson);
      final api = AmbAttachmentApi(_client(adapter));
      await expectLater(
        api.uploadTextFile(
          sessionId: 7,
          file: const PickedTextFile(
              name: 'bad.exe', size: 3, reject: TextFileRejectReason.unsupportedType),
        ),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
      expect(adapter.calls, 0, reason: '本地已拒绝，不得发请求');
    });

    test('响应 sid 不等 ⇒ ApiException(unknown)，不当发送成功', () async {
      final adapter = _FakeAdapter(
        '{"file_message":{"id":1,"session_id":99,"sender_type":"user","content":"a.txt",'
        '"created_at":"2026-10-17 03:00:00"},"chunks":[]}',
      );
      final api = AmbAttachmentApi(_client(adapter));
      await expectLater(
        api.uploadTextFile(sessionId: 7, file: _validFile()),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });

    test('响应非对象 ⇒ ApiException(unknown)', () async {
      final adapter = _FakeAdapter('plain text', contentType: 'text/plain');
      final api = AmbAttachmentApi(_client(adapter));
      await expectLater(
        api.uploadTextFile(sessionId: 7, file: _validFile()),
        throwsA(isA<ApiException>().having((e) => e.kind, 'kind', 'unknown')),
      );
    });
  });

  group('选择器类型工具（白名单/上限/basename）', () {
    test('basenameOf 去目录（/ 与 \\）', () {
      expect(basenameOf('/tmp/a/b.txt'), 'b.txt');
      expect(basenameOf(r'C:\x\y\report.md'), 'report.md');
      expect(basenameOf('plain.csv'), 'plain.csv');
    });

    test('extensionOf 小写、含点；无扩展名 ⇒ 空', () {
      expect(extensionOf('A.TXT'), '.txt');
      expect(extensionOf('noext'), '');
      expect(extensionOf('.hidden'), '');
    });

    test('isAllowedTextFileName 只认 .txt/.md/.csv（不看 MIME）', () {
      expect(isAllowedTextFileName('a.txt'), isTrue);
      expect(isAllowedTextFileName('a.MD'), isTrue);
      expect(isAllowedTextFileName('a.csv'), isTrue);
      expect(isAllowedTextFileName('a.exe'), isFalse);
      expect(isAllowedTextFileName('a.txt.exe'), isFalse);
      expect(isAllowedTextFileName('a'), isFalse);
    });

    test('PickedTextFile.accepted 语义', () {
      expect(_validFile().accepted, isTrue);
      expect(
        const PickedTextFile(name: 'a.txt', size: 1, reject: TextFileRejectReason.tooLarge)
            .accepted,
        isFalse,
      );
      expect(const PickedTextFile(name: 'a.txt', size: 1).accepted, isFalse,
          reason: '无字节 = 未通过（不可发送）');
    });
  });
}
