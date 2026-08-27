import 'package:flutter/material.dart';

/// 皮肤颜色扩展：作为 `ThemeExtension` 注入 ThemeData，供聊天气泡/输入栏/玻璃等读取。
/// 字段均持默认 null，未覆盖的皮肤走 Material3 ColorScheme / 全局兜底。
class SkinColors extends ThemeExtension<SkinColors> {
  const SkinColors({
    this.bubbleUser,
    this.bubbleAi,
    this.bubbleUserText,
    this.bubbleAiText,
    this.glassBackground,
    this.glassBorder,
    this.glassBlur = 0.0,
    this.cardElevated,
    this.quoteBarBg,
    this.inputBarBg,
    this.bgGradientStart,
    this.bgGradientEnd,
  });

  /// 用户气泡背景
  final Color? bubbleUser;
  /// AI 气泡背景
  final Color? bubbleAi;
  /// 用户气泡文字
  final Color? bubbleUserText;
  /// AI 气泡文字
  final Color? bubbleAiText;
  /// 毛玻璃背景
  final Color? glassBackground;
  /// 毛玻璃描边
  final Color? glassBorder;
  /// 毛玻璃模糊强度
  final double glassBlur;
  /// 抬升卡片背景
  final Color? cardElevated;
  /// 引用条背景
  final Color? quoteBarBg;
  /// 输入栏背景
  final Color? inputBarBg;
  /// 底盘渐变色（起）
  final Color? bgGradientStart;
  /// 底盘渐变色（终）
  final Color? bgGradientEnd;

  @override
  SkinColors copyWith({
    Color? bubbleUser,
    Color? bubbleAi,
    Color? bubbleUserText,
    Color? bubbleAiText,
    Color? glassBackground,
    Color? glassBorder,
    double? glassBlur,
    Color? cardElevated,
    Color? quoteBarBg,
    Color? inputBarBg,
    Color? bgGradientStart,
    Color? bgGradientEnd,
  }) {
    return SkinColors(
      bubbleUser: bubbleUser ?? this.bubbleUser,
      bubbleAi: bubbleAi ?? this.bubbleAi,
      bubbleUserText: bubbleUserText ?? this.bubbleUserText,
      bubbleAiText: bubbleAiText ?? this.bubbleAiText,
      glassBackground: glassBackground ?? this.glassBackground,
      glassBorder: glassBorder ?? this.glassBorder,
      glassBlur: glassBlur ?? this.glassBlur,
      cardElevated: cardElevated ?? this.cardElevated,
      quoteBarBg: quoteBarBg ?? this.quoteBarBg,
      inputBarBg: inputBarBg ?? this.inputBarBg,
      bgGradientStart: bgGradientStart ?? this.bgGradientStart,
      bgGradientEnd: bgGradientEnd ?? this.bgGradientEnd,
    );
  }

  @override
  SkinColors lerp(ThemeExtension<SkinColors>? other, double t) {
    if (other is! SkinColors) return this;
    return SkinColors(
      bubbleUser: Color.lerp(bubbleUser, other.bubbleUser, t),
      bubbleAi: Color.lerp(bubbleAi, other.bubbleAi, t),
      bubbleUserText: Color.lerp(bubbleUserText, other.bubbleUserText, t),
      bubbleAiText: Color.lerp(bubbleAiText, other.bubbleAiText, t),
      glassBackground: Color.lerp(glassBackground, other.glassBackground, t),
      glassBorder: Color.lerp(glassBorder, other.glassBorder, t),
      glassBlur: glassBlur + (other.glassBlur - glassBlur) * t,
      cardElevated: Color.lerp(cardElevated, other.cardElevated, t),
      quoteBarBg: Color.lerp(quoteBarBg, other.quoteBarBg, t),
      inputBarBg: Color.lerp(inputBarBg, other.inputBarBg, t),
      bgGradientStart: Color.lerp(bgGradientStart, other.bgGradientStart, t),
      bgGradientEnd: Color.lerp(bgGradientEnd, other.bgGradientEnd, t),
    );
  }
}
