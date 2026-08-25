import '../../models/diary_entry.dart';
import '../api_client.dart';

/// DiaryApi：领域 API 方法（extension 挂到 ApiClient）
extension DiaryApi on ApiClient {

  Future<List<DiaryEntry>> getDiary(int characterId) async {
    final r = await dio.get("/api/v1/diary/$characterId");
    final data = r.data as Map<String, dynamic>;
    return (data["entries"] as List)
        .map((j) => DiaryEntry.fromJson(j as Map<String, dynamic>))
        .toList();
  }
}
