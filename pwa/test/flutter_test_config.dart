// 全局测试配置：默认关闭系统动效（等价于用户开了「减少动态效果」）。
//
// 装饰层 PwaBackdrop 的漂流/呼吸是**无限循环**动画；若测试环境按默认（动效开）跑，
// 任何 `pumpAndSettle` 都会等到超时。这里把平台无障碍开关设成 disableAnimations=true，
// 让所有既有用例保持原有的「瞬时切换」语义；需要验动效的用例在 MediaQuery 里显式覆盖。
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  final binding = TestWidgetsFlutterBinding.ensureInitialized();
  binding.platformDispatcher.accessibilityFeaturesTestValue =
      const FakeAccessibilityFeatures(disableAnimations: true);
  await testMain();
}
