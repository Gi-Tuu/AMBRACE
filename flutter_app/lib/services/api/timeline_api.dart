import '../../models/timeline.dart';
import '../api_client.dart';

/// TimelineApi：领域 API 方法（extension 挂到 ApiClient）
extension TimelineApi on ApiClient {

  Future<TimelineData> getTimeline(int characterId) async {
    final r = await dio.get("/api/v1/timeline/$characterId");
    return TimelineData.fromJson(r.data as Map<String, dynamic>);
  }

  Future<Map<String, dynamic>> createMilestones(int characterId) async {
    final r = await dio.post("/api/v1/timeline/$characterId/milestones");
    return r.data as Map<String, dynamic>;
  }
}
