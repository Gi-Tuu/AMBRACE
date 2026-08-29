import 'dart:io';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/settings_provider.dart';
import '../theme/app_theme.dart';
import '../theme/skins/skin_colors.dart';

/// 全局背景层：挂在 `MaterialApp.builder` 最底层，所有页面（含登录页）共享同一背景。
///
/// - 非 glass 皮肤：纯色背景（零开销）。
/// - glass 皮肤：aurora 渐变，或用自定义背景图（静态模糊 + 压暗遮罩）。
class AppBackground extends StatelessWidget {
  const AppBackground({super.key});

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    final theme = Theme.of(context);
    final skinColors = theme.extension<SkinColors>();
    final isGlass = settings.skinId == 'glass';

    // 非 glass 皮肤：纯色背景（纸艺皮肤叠加程序化纤维纹理）
    if (!isGlass) {
      final solid =
          SizedBox.expand(child: Container(color: theme.scaffoldBackgroundColor));
      if (settings.skinId == 'paper') {
        final isDark = theme.brightness == Brightness.dark;
        return Stack(fit: StackFit.expand, children: [
          solid,
          RepaintBoundary(
            child: CustomPaint(painter: _PaperFiberPainter(isDark: isDark)),
          ),
        ]);
      }
      return solid;
    }

    final path = settings.glassBackgroundPath;
    final useImage = path != null && path.isNotEmpty && File(path).existsSync();
    final seedColor = AppTheme.seedColorAt(settings.seedColorIndex);

    final content = useImage
        ? _imageBackground(context, settings, skinColors, seedColor)
        : _gradientBackground(context, settings, skinColors, seedColor);

    // 从 glass 切到其他皮肤，或渐变 ↔ 图片切换，均用 AnimatedSwitcher 过渡避免闪烁
    return SizedBox.expand(
      child: AnimatedSwitcher(
        duration: const Duration(milliseconds: 300),
        child: KeyedSubtree(
          key: ValueKey('$isGlass-$useImage'),
          child: content,
        ),
      ),
    );
  }

  /// aurora 渐变背景 —— 被 RepaintBoundary 包裹，避免滚动重绘。
  Widget _gradientBackground(
    BuildContext context,
    SettingsProvider settings,
    SkinColors? skinColors,
    Color seedColor,
  ) {
    final aurora1 = settings.glassAuroraColor1 != null
        ? Color(settings.glassAuroraColor1!)
        : (skinColors?.auroraColor1 ?? seedColor.withValues(alpha: 0.15));
    final aurora2 = settings.glassAuroraColor2 != null
        ? Color(settings.glassAuroraColor2!)
        : (skinColors?.auroraColor2 ?? seedColor.withValues(alpha: 0.05));
    final surface = Theme.of(context).colorScheme.surface;

    return RepaintBoundary(
      child: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [aurora1, aurora2, surface],
          ),
        ),
      ),
    );
  }

  /// 自定义背景图：渐变兜底 + ImageFiltered 静态模糊（只模糊一次，性能好）+ 压暗遮罩。
  Widget _imageBackground(
    BuildContext context,
    SettingsProvider settings,
    SkinColors? skinColors,
    Color seedColor,
  ) {
    final blur = settings.glassBlur;
    final dim = settings.glassDim;
    final width = MediaQuery.of(context).size.width;

    return Stack(
      fit: StackFit.expand,
      children: [
        // 图片解码前先显示渐变兜底
        _gradientBackground(context, settings, skinColors, seedColor),
        // 图片 + 静态模糊；解码完成后 AnimatedOpacity 淡入（300ms）
        RepaintBoundary(
          child: ImageFiltered(
            imageFilter: ui.ImageFilter.blur(sigmaX: blur, sigmaY: blur),
            child: Image.file(
              File(settings.glassBackgroundPath!),
              fit: BoxFit.cover,
              cacheWidth: width.round(),
              frameBuilder: (context, child, frame, wasSynchronouslyLoaded) {
                if (wasSynchronouslyLoaded) return child;
                return AnimatedOpacity(
                  opacity: frame == null ? 0 : 1,
                  duration: const Duration(milliseconds: 300),
                  child: child,
                );
              },
              errorBuilder: (context, error, stack) => const SizedBox.shrink(),
            ),
          ),
        ),
        // 压暗遮罩，保证文字对比度 ≥ 4.5:1
        Container(color: Colors.black.withValues(alpha: dim)),
      ],
    );
  }
}


/// 纸艺手账皮肤的程序化纸质纹理：固定种子的细纤维 + 微点，暖棕低透明，零资源。
class _PaperFiberPainter extends CustomPainter {
  final bool isDark;
  const _PaperFiberPainter({required this.isDark});

  @override
  void paint(Canvas canvas, Size size) {
    final rnd = math.Random(20260829); // 固定种子，重绘不闪烁
    final fiber = Paint()
      ..color = (isDark ? Colors.white : const Color(0xFF8A6A45))
          .withValues(alpha: isDark ? 0.05 : 0.06)
      ..strokeWidth = 0.6;
    final dot = Paint()
      ..color = (isDark ? Colors.white : const Color(0xFF7A5A38))
          .withValues(alpha: isDark ? 0.04 : 0.05);

    final fibers = (size.width * size.height / 9000).clamp(40, 220).toInt();
    for (var i = 0; i < fibers; i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      final len = 2 + rnd.nextDouble() * 5;
      final ang = rnd.nextDouble() * math.pi;
      canvas.drawLine(
        Offset(x, y),
        Offset(x + math.cos(ang) * len, y + math.sin(ang) * len),
        fiber,
      );
    }
    final dots = fibers ~/ 2;
    for (var i = 0; i < dots; i++) {
      canvas.drawCircle(
        Offset(rnd.nextDouble() * size.width, rnd.nextDouble() * size.height),
        0.7,
        dot,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _PaperFiberPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}
