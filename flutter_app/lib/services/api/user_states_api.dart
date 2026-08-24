import '../../models/user_state.dart';
import '../api_client.dart';

/// UserStatesApi：用户可视化状态（用户主页蛛网图，手动滑动调整）
extension UserStatesApi on ApiClient {
  Future<UserState> getUserStates() async {
    final r = await dio.get("/api/v1/users/states");
    return UserState.fromJson(r.data as Map<String, dynamic>);
  }

  Future<UserState> updateUserStates(Map<String, dynamic> values) async {
    final r = await dio.put("/api/v1/users/states", data: values);
    return UserState.fromJson(r.data as Map<String, dynamic>);
  }
}
