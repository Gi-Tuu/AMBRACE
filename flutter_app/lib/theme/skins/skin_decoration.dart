import 'package:flutter/material.dart';

/// 底部导航栏样式（皮肤装饰参数之一）。
enum NavigationBarStyle {
  /// 全宽普通导航栏
  fullWidth,

  /// 悬浮胶囊导航栏
  floatingCapsule,
}

/// 皮肤装饰参数：圆角 / 阴影 / 导航栏样式 / 描边等（枚举/常量，零逻辑）。
class SkinDecoration {
  const SkinDecoration({
    this.cardRadius = 12.0,
    this.buttonRadius = 12.0,
    this.inputRadius = 12.0,
    this.navBarStyle = NavigationBarStyle.fullWidth,
    this.appBarBlur = false,
    this.cardShadow = const [],
    this.cardBorder = false,
    this.borderColor,
    this.borderWidth = 0,
  });

  /// 卡片圆角
  final double cardRadius;
  /// 按钮圆角
  final double buttonRadius;
  /// 输入框圆角
  final double inputRadius;
  /// 底部导航栏样式
  final NavigationBarStyle navBarStyle;
  /// AppBar 是否毛玻璃
  final bool appBarBlur;
  /// 卡片阴影列表
  final List<BoxShadow> cardShadow;
  /// 卡片是否描边
  final bool cardBorder;
  /// 描边颜色
  final Color? borderColor;
  /// 描边宽度
  final double borderWidth;
}
