import '../../models/user_content.dart';
import '../api_client.dart';

/// UserContentApi：用户备忘录 + 用户日记（领域 API，extension 挂到 ApiClient）
extension UserContentApi on ApiClient {

  Future<List<UserMemo>> getMemos() async {
    final r = await dio.get('/api/v1/user/memos');
    final data = r.data as Map<String, dynamic>;
    return (data['memos'] as List)
        .map((j) => UserMemo.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<UserMemo> createMemo({String? title, required String content}) async {
    final r = await dio.post('/api/v1/user/memos',
        data: {'title': title ?? '', 'content': content});
    return UserMemo.fromJson(r.data as Map<String, dynamic>);
  }

  Future<UserMemo> updateMemo(int id, {String? title, String? content}) async {
    final r = await dio.put('/api/v1/user/memos/$id',
        data: {'title': title ?? '', 'content': content ?? ''});
    return UserMemo.fromJson(r.data as Map<String, dynamic>);
  }

  Future<void> deleteMemo(int id) async {
    await dio.delete('/api/v1/user/memos/$id');
  }

  Future<List<UserDiaryEntry>> getDiaries() async {
    final r = await dio.get('/api/v1/user/diaries');
    final data = r.data as Map<String, dynamic>;
    return (data['diaries'] as List)
        .map((j) => UserDiaryEntry.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<UserDiaryEntry> upsertDiary(String diaryDate, String content) async {
    final r = await dio.post('/api/v1/user/diaries',
        data: {'diary_date': diaryDate, 'content': content});
    return UserDiaryEntry.fromJson(r.data as Map<String, dynamic>);
  }

  Future<void> deleteDiary(int id) async {
    await dio.delete('/api/v1/user/diaries/$id');
  }
}
