import "package:flutter/material.dart";

/// 通用页面切换路由：淡入 + 轻微缩放（0.94→1.0），时长 ≤0.3s。
///
/// 用于主要页面（聊天 / 记忆 / 宠物 / 设置 等）的 Navigator.push，
/// 取代默认 MaterialPageRoute（Cupertino 侧滑），给页面切换以柔和、克制的过渡。
class AppPageRoute<T> extends PageRouteBuilder<T> {
  AppPageRoute({
    required WidgetBuilder builder,
    super.settings,
    this.duration = const Duration(milliseconds: 260),
  }) : super(
          transitionDuration: duration,
          reverseTransitionDuration: const Duration(milliseconds: 220),
          pageBuilder: (context, animation, secondaryAnimation) =>
              builder(context),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            final curved = CurvedAnimation(
              parent: animation,
              curve: Curves.easeOutCubic,
              reverseCurve: Curves.easeInCubic,
            );
            final opacity = Tween<double>(begin: 0, end: 1).animate(curved);
            final scale = Tween<double>(begin: 0.94, end: 1.0).animate(curved);
            return FadeTransition(
              opacity: opacity,
              child: ScaleTransition(scale: scale, child: child),
            );
          },
        );

  final Duration duration;
}

/// 便捷方法：`AppNavigator.push(context, () => const SomeScreen())`
/// 等价于 `Navigator.push(context, AppPageRoute(builder: ...))`。
class AppNavigator {
  AppNavigator._();

  static Future<T?> push<T>(BuildContext context, WidgetBuilder builder) {
    return Navigator.push<T>(context, AppPageRoute<T>(builder: builder));
  }
}
