import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'aegean_palette.dart';
import 'aegean_motifs.dart';
import 'aegean_geometry.dart';   // 新增

// ════════════════════════════════════════════════════════════
// 1) 全局背景：羊皮纸 / 星夜 + 金粉 + 回纹带（挂在 AppBackground）
// ════════════════════════════════════════════════════════════
class AegeanPaperPainter extends CustomPainter {
  final bool dark;
  const AegeanPaperPainter({required this.dark});

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;

    // 1.1 暖晕 / 夜晕：中心略亮、四周略沉，营造纸面光照
    final glow = Paint()
      ..shader = RadialGradient(
        center: const Alignment(0, -0.35),
        radius: 1.15,
        colors: dark
            ? [AegeanPalette.glowTopDark, AegeanPalette.paperDark]
            : [AegeanPalette.glowTopLight, AegeanPalette.paperLight],
        stops: const [0.0, 1.0],
      ).createShader(rect);
    canvas.drawRect(rect, glow);

    final rnd = math.Random(20260907); // 固定种子：重绘不闪烁、滚动不抖

    // 1.2 纸面纤维（极细、低透明）
    final fiber = Paint()
      ..color = (dark ? Colors.white : const Color(0xFF8A6A45))
          .withValues(alpha: dark ? 0.045 : 0.05)
      ..strokeWidth = 0.6;
    final fibers = (size.width * size.height / 8200).clamp(40, 240).toInt();
    for (var i = 0; i < fibers; i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      final len = 2 + rnd.nextDouble() * 5;
      final ang = rnd.nextDouble() * math.pi;
      canvas.drawLine(Offset(x, y),
          Offset(x + math.cos(ang) * len, y + math.sin(ang) * len), fiber);
    }

    // 1.3 金粉星点（少量亮金 + 个别十字微光）；夜阑版更密更亮
    final speckN = dark ? 90 : 56;
    final goldSpeck = Paint()
      ..color = (dark ? AegeanPalette.goldPale : AegeanPalette.goldLight)
          .withValues(alpha: dark ? 0.5 : 0.32);
    for (var i = 0; i < speckN; i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      final r = rnd.nextDouble() * (dark ? 1.3 : 1.0) + 0.3;
      canvas.drawCircle(Offset(x, y), r, goldSpeck);
    }
    final cross = Paint()
      ..color = (dark ? AegeanPalette.goldPale : AegeanPalette.goldDeepLight)
          .withValues(alpha: 0.5)
      ..strokeWidth = 0.7;
    for (var i = 0; i < (dark ? 14 : 8); i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      const s = 3.2;
      canvas.drawLine(Offset(x - s, y), Offset(x + s, y), cross);
      canvas.drawLine(Offset(x, y - s), Offset(x, y + s), cross);
    }

    // 1.4 顶部 / 底部回纹带：统一走 AegeanGeometry.meander（等距、按安全区 18 收口、
    //     不画半个单元），与书眉/门廊同源；透明度低，不抢内容。
    final band = Paint()
      ..color = AegeanPalette.goldPale.withValues(alpha: dark ? 0.16 : 0.22)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;
    canvas.drawPath(
      AegeanGeometry.meander(Rect.fromLTWH(18, 10, size.width - 36, 12), 3.8), band);
    canvas.drawPath(
      AegeanGeometry.meander(
          Rect.fromLTWH(18, size.height - 22, size.width - 36, 12), 3.8),
      band);

    // 1.5 四角暗角（暖棕，极轻），把视线收向中心
    final vignette = Paint()
      ..shader = RadialGradient(
        radius: 1.05,
        colors: [
          Colors.transparent,
          (dark ? Colors.black : const Color(0xFF6B4F2A))
              .withValues(alpha: dark ? 0.28 : 0.06),
        ],
        stops: const [0.72, 1.0],
      ).createShader(rect);
    canvas.drawRect(rect, vignette);
  }

  @override
  bool shouldRepaint(covariant AegeanPaperPainter old) => old.dark != dark;
}

/// 水平回纹分隔：「◆ —— 回纹带 —— ◆」，用于区块 / 卡片标题之间。
class AegeanMeanderDivider extends StatelessWidget {
  final double height;
  final Color? color;
  const AegeanMeanderDivider({super.key, this.height = 12, this.color});

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final c = color ??
        (dark ? AegeanPalette.goldPale.withValues(alpha: 0.6)
              : AegeanPalette.goldDeepLight.withValues(alpha: 0.6));
    return SizedBox(
      height: height + 6,
      child: CustomPaint(
        painter: _MeanderLinePainter(c),
        size: Size.infinite,
      ),
    );
  }
}

class _MeanderLinePainter extends CustomPainter {
  final Color color;
  _MeanderLinePainter(this.color);
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;
    final mid = size.height / 2;
    // 左右直线
    canvas.drawLine(Offset(0, mid), Offset(size.width * 0.16, mid), paint);
    canvas.drawLine(Offset(size.width * 0.84, mid),
        Offset(size.width, mid), paint);
    // 中央回纹（统一公式：单元宽 4g，令 4g≈band 高）
    final band = Rect.fromLTWH(size.width * 0.16, mid - 5, size.width * 0.68, 10);
    canvas.drawPath(AegeanGeometry.meander(band, band.height / 4), paint);
    // 两端菱形
    final dia = Path()
      ..moveTo(size.width * 0.16, mid)
      ..relativeLineTo(4, -4)..relativeLineTo(4, 4)..relativeLineTo(-4, 4)
      ..close();
    canvas.drawPath(dia, Paint()..color = color);
    final dia2 = dia.shift(Offset(size.width * 0.68 - 8, 0));
    canvas.drawPath(dia2, Paint()..color = color);
  }

  @override
  bool shouldRepaint(covariant _MeanderLinePainter old) => old.color != color;
}

// ════════════════════════════════════════════════════════════
// 5) 首字下沉（§6.4）：长文首段首字，赤陶花体、占两行
// ════════════════════════════════════════════════════════════
// ⚠ 落地注意：AegeanDropCap 仅是“首字块”组件，真正的首字下沉需在长文首段用
// Text.rich（WidgetSpan）或 Row 把首字嵌入段落首行（阶段 2 实现时处理）。
class AegeanDropCap extends StatelessWidget {
  final String char;
  const AegeanDropCap({super.key, required this.char});
  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final c = dark ? AegeanPalette.terraDark : AegeanPalette.terraDeepLight;
    return Padding(
      padding: const EdgeInsets.only(right: 6, top: 2),
      child: Text(
        char,
        style: TextStyle(
          fontFamily: AegeanPalette.displayFont,
          fontSize: 30, height: 0.95, color: c, fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

// ════════════════════════════════════════════════════════════
// 6) 赤陶圆印（§6.4）：结算/认证/核心标记（朱底 + 米白花体/图标 + 金发丝圈）
// ════════════════════════════════════════════════════════════
class AegeanSeal extends StatelessWidget {
  final String label;
  final double size;
  const AegeanSeal({super.key, required this.label, this.size = 36});
  @override
  Widget build(BuildContext context) {
    return Container(
      width: size, height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: AegeanPalette.terraDeepLight,
        border: Border.all(color: AegeanPalette.goldPale, width: 1),
        boxShadow: [BoxShadow(color: AegeanPalette.goldDeepLight.withValues(alpha: .25), blurRadius: 6)],
      ),
      child: Text(label, textAlign: TextAlign.center,
        style: TextStyle(fontFamily: AegeanPalette.displayFont, color: AegeanPalette.cream,
          fontSize: size * 0.32, fontWeight: FontWeight.w700, height: 1.05)),
    );
  }
}

// ════════════════════════════════════════════════════════════
// 7) AppBar 标题（§6.6）：双翼 + 花体标题 —— 仅 aegean 启用，封装成小工具复用
// ════════════════════════════════════════════════════════════
class AegeanTitleBar extends StatelessWidget {
  final String title;
  final double wingSize;
  const AegeanTitleBar({super.key, required this.title, this.wingSize = 18});
  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final c = dark ? AegeanPalette.goldPale : AegeanPalette.goldDeepLight;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Transform.flip(
          flipX: true,
          child: AegeanWings(size: wingSize, color: c, both: false),
        ),
        const SizedBox(width: 10),
        Flexible(
          child: Text(
            title,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontFamily: AegeanPalette.displayFont,
              fontFamilyFallback: AegeanPalette.cnSerifFallback,
              fontSize: 19,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
              color: c,
            ),
          ),
        ),
        const SizedBox(width: 10),
        AegeanWings(size: wingSize, color: c, both: false),
      ],
    );
  }
}
