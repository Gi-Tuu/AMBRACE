// 共享模型（PWA M1a-0 返工）：本文件**不再自带一份实现**，改为转发 core 的权威实现。
//
// 为什么这样改（主审 §1）：只在 pubspec 加 path 依赖、却让手机端继续跑自己的 `ChatMessage`
// 副本，等于「复制」而不是「共享」。改成 re-export 后，全仓手机代码 `import '../models/message.dart'`
// 拿到的就是 `ambrace_core` 里那**同一个类**——单一真相成立，且旧 import 路径与类型名完全不变
// （无需改任何业务码，也不影响原生上传/认证语义）。
//
// 守卫：`flutter_app/test/pwa_core_compat_test.dart` 断言 `ChatMessage` 与
// `core.ChatMessage` 是**同一个 Type**；一旦有人把实现复制回来，该断言立刻失败。
export 'package:ambrace_core/ambrace_core.dart' show ChatMessage;
