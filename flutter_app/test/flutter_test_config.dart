import 'dart:async';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// 全局测试配置（`flutter test` 在加载 test/ 下每个用例文件前自动执行）。
///
/// P2-A：SettingsProvider 的 token 读写改走 flutter_secure_storage。但在
/// `flutter test` 环境里该插件的 MethodChannel 没有任何实现，调用**永远不会返回**
/// （实测不抛 MissingPluginException，而是 Future 永不完成）——任何
/// `await settings.load()` / `setAuth()` 的用例都会挂死到 10 分钟超时。
///
/// 这里全局装一个内存版测试平台，让安全存储调用立即可用（行为等价于各用例里
/// 既有的 `SharedPreferences.setMockInitialValues`）。个别用例若需要特定初始值，
/// 仍可在自己内部再次调用 `setMockInitialValues` 覆盖（后调用者生效）。
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  FlutterSecureStorage.setMockInitialValues(<String, String>{});
  await testMain();
}
