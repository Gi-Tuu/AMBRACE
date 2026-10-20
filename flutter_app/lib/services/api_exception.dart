// 共享异常（PWA M1a-0 返工）：转发 core 的权威实现，不再保留第二份。
//
// 与 `models/message.dart` 同理：旧 import 路径、类型名、`fromDio`/`messageOf`/`kind`
// 等调用面逐一保持不变，只是实现唯一了。守卫见 `pwa_core_compat_test.dart` 的 Type 断言。
export 'package:ambrace_core/ambrace_core.dart' show ApiException;
