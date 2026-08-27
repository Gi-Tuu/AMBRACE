import 'package:flutter/material.dart';
import 'skin_colors.dart';
import 'skin_decoration.dart';
import 'skin_typography.dart';
import 'skin_animation.dart';

/// 皮肤接口（可换肤架构 v3）：每种皮肤定义配色/装饰/字体/动效，并提供 ThemeData。
abstract class Skin {
  /// 皮肤唯一 ID
  String get id;

  /// 展示名（设置页）
  String get displayName;

  /// 设置页预览色
  Color get previewColor;

  /// 是否支持 seed color（强调色跟随）
  bool get supportsSeedColor;

  /// 是否支持深色模式
  bool get supportsDarkMode;

  /// 构建完整 ThemeData
  ThemeData buildThemeData({required Brightness brightness, required Color seedColor});

  /// 构建该皮肤专属的颜色扩展（气泡/输入栏/玻璃等）
  SkinColors buildSkinColors({required Brightness brightness, required Color seedColor});

  /// 装饰参数（圆角/阴影/导航栏样式等）
  SkinDecoration get decoration;

  /// 字体体系
  SkinTypography get typography;

  /// 动效体系
  SkinAnimation get animation;
}
