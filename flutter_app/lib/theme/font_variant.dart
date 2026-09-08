import 'dart:io' show Platform;
import 'package:flutter/foundation.dart' show kIsWeb;

/// 全局正文字体档位（R8，工具轨迹治理批次三）：
/// system = 跟随系统默认（现状，fontFamily=null）；serif / rounded 走系统逻辑字体。
/// 不打包任何字体文件，零包体增量、零版权风险。
enum FontVariant { system, serif, rounded }

/// 解析全局正文字体族。null = 跟随系统默认（与治理前行为完全一致）。
String? resolveFontFamily(FontVariant v) {
  switch (v) {
    case FontVariant.system:
      return null;
    case FontVariant.serif:
      // Android→系统衬线（Noto Serif）、iOS→Times/宋体兜底，跨平台逻辑字体
      return 'serif';
    case FontVariant.rounded:
      if (!kIsWeb && Platform.isIOS) {
        // iOS 圆体没有稳定的 Flutter 逻辑族名；取不到时让系统兜底（null）最稳
        return null;
      }
      // Android AOSP 提供 sans-serif-rounded（部分厂商覆盖不一，失败自动回退默认 sans）
      return 'sans-serif-rounded';
  }
}
