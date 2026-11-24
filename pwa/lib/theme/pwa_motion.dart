import 'package:flutter/material.dart';

/// 动效令牌（PWA 对齐 App 的 `AppMotion`）。
///
/// 约束（同 App 侧）：组件内的动画时长/曲线只从这里取，禁止散落硬编码毫秒/Curve；
/// 系统「减少动态效果」开启时，装饰动画一律停在静止帧（见 `PwaBackdrop`）。
class PwaMotion {
  PwaMotion._();

  /// 按压 / 微交互（150-200ms 级）。
  static const Duration fast = Duration(milliseconds: 200);

  /// 通用转场 / 面板切换。
  static const Duration normal = Duration(milliseconds: 350);

  /// 列表项 / 气泡入场（对齐 App 的 280ms，不拖沓）。
  static const Duration enter = Duration(milliseconds: 280);

  /// 骨架屏流光一轮（对齐 App 的 1400ms）。
  static const Duration shimmer = Duration(milliseconds: 1400);

  /// 大区域 / 慢速过渡。
  static const Duration slow = Duration(milliseconds: 600);

  /// 持续循环装饰动效（背景漂流/呼吸光晕）的整周期。
  static const Duration drift = Duration(seconds: 18);

  /// 弹性曲线（卡片弹入 / 高亮入场）。
  static const Curve spring = Curves.easeOutBack;

  /// 强调曲线（展开收起 / 面板过渡，先快后慢）。
  static const Curve emphasized = Curves.easeOutCubic;
}
