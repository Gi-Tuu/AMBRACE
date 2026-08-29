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

  /// 获取某月有日记的日期列表（轻量，只返回日期字符串）
  Future<List<String>> getDiaryDates(int characterId, String month) async {
    final r = await dio.get(
      "/api/v1/diary/$characterId/dates",
      queryParameters: {"month": month},
    );
    final data = r.data as Map<String, dynamic>;
    return (data["dates"] as List? ?? const [])
        .map((e) => e.toString())
        .toList();
  }
}
