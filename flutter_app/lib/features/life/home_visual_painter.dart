// F7-c-3（2026-08-31）自 features/life/home_visual_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:math' as math;
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import '../../theme/aurora_tokens.dart';
import "package:ai_companion/theme/tokens.dart";

/// Aurora P4：全局「降低动效」读取（未包裹 Provider 兜底 false）。
import 'home_visual_models.dart';
class RoomPainter extends CustomPainter {
  final String roomId;
  final List<Furniture> furniture;
  final Offset userPos;
  final String aiStatus;
  final String? selected;
  final String? dragging;
  final String? editing;
  final String? bubble;
  final Map<String, ui.Image> images;
  final bool imagesReady;

  RoomPainter({
    required this.roomId,
    required this.furniture,
    required this.userPos,
    required this.aiStatus,
    required this.selected,
    required this.dragging,
    required this.editing,
    required this.bubble,
    required this.images,
    required this.imagesReady,
  });

  (Color, Color, Color) _theme() {
    switch (roomId) {
      case 'bedroom':
        return (const Color(0xFFE3D6C4), const Color(0xFFB8A080), const Color(0xFF7B6B55));
      case 'kitchen':
        return (const Color(0xFFE8E4DA), const Color(0xFFD0D0C0), const Color(0xFF888878));
      case 'bathroom':
        return (const Color(0xFFD6E8EC), const Color(0xFFA8C8D8), const Color(0xFF7898A8));
      default:
        return (const Color(0xFFE8DCC8), const Color(0xFFD9B98A), const Color(0xFF8B7355));
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final scale = math.min(size.width / 640.0, size.height / 480.0);
    canvas.save();
    canvas.translate(
      (size.width - 640 * scale) / 2,
      (size.height - 480 * scale) / 2,
    );
    canvas.scale(scale);

    final (wall, floor, wallDark) = _theme();
    // 地板平铺（40px 网格，480-60=420 高 -> 10.5 格，取 11 行覆盖）
    final floorImg = images['floor_$roomId'];
    final wallImg = images['wall_$roomId'];
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 11; j++) {
        if (floorImg != null) {
          canvas.drawImage(floorImg, Offset(i * 40.0, 60 + j * 40.0), Paint());
        }
      }
    }
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 2; j++) {
        if (wallImg != null) {
          canvas.drawImage(wallImg, Offset(i * 40.0, j * 40.0), Paint());
        }
      }
    }
    // 兜底：素材未加载时用纯色 + 网格
    if (wallImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 0, 640, 60), Paint()..color = wall);
    }
    if (floorImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 60, 640, 420), Paint()..color = floor);
      final grid = Paint()
        ..color = wallDark.withValues(alpha: 0.35)
        ..strokeWidth = 1;
      for (var i = 1; i < 16; i++) {
        canvas.drawLine(Offset(i * 40.0, 60), Offset(i * 40.0, 480), grid);
      }
      for (var j = 1; j < 11; j++) {
        canvas.drawLine(Offset(0, 60 + j * 40.0), Offset(640, 60 + j * 40.0), grid);
      }
    }
    // 窗户（叠在墙上）
    canvas.drawRect(
      const Rect.fromLTWH(430, 10, 120, 45),
      Paint()..color = const Color(0xFFBFE3FF),
    );
    canvas.drawLine(const Offset(490, 10), const Offset(490, 55),
        Paint()..color = AppColors.white..strokeWidth = 3);
    canvas.drawLine(const Offset(430, 32), const Offset(550, 32),
        Paint()..color = AppColors.white..strokeWidth = 3);

    // Aurora P4：柔和光影——光从窗户照入的径向渐变光斑（中心≈窗户中心，软边暖白，
    // 纯静态绘制，在家具/角色之前、不参与命中）
    canvas.drawRect(
      const Rect.fromLTWH(0, 0, 640, 480),
      Paint()
        ..shader = RadialGradient(
          colors: [
            const Color(0xFFFFF6DE).withValues(alpha: 0.12),
            const Color(0xFFFFF6DE).withValues(alpha: 0.0),
          ],
        ).createShader(Rect.fromCircle(center: const Offset(490, 32), radius: 280)),
    );

    // 按前后（脚底 Y）排序绘制：靠后的先画，实现前后遮挡
    final draws = <(double, void Function())>[];
    for (final f in furniture) {
      final bottomY = (f.gy + f.gh) * 40;
      draws.add((bottomY, () => _paintFurniture(canvas, f)));
    }
    draws.add((320, () => _paintCharacter(canvas, const Offset(220, 320), 'char_ai')));
    draws.add((userPos.dy, () => _paintCharacter(canvas, userPos, 'char_user')));
    draws.sort((a, b) => a.$1.compareTo(b.$1));
    for (final d in draws) {
      d.$2();
    }

    if (bubble != null && bubble!.isNotEmpty) {
      _paintBubble(canvas, userPos + const Offset(0, -46), bubble!);
    }

    canvas.restore();
  }

  void _paintFurniture(Canvas canvas, Furniture f) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    final isDragging = dragging == f.key;
    if (isDragging) {
      // 拖动中：青色描边 + 半透明遮罩（视觉反馈）
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5
          ..color = const Color(0xFF00BCD4),
      );
    }
    if (selected == f.key) {
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFFFFC107),
      );
    }
    if (editing == f.key) {
      // 被编辑：橙色描边（与拖动态青色、选中弹窗琥珀色区分）
      canvas.drawRect(
        rect.inflate(4),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 4
          ..color = const Color(0xFFFF6D00),
      );
    }
    final wood = Paint()..color = const Color(0xFF9B7E5E);
    final img = images['furn_${f.key}'];
    if (img != null) {
      // 素材脚底对齐到格子底部（高度包含向上突出的部分）
      canvas.drawImage(
        img,
        Offset(f.gx * 40, (f.gy + f.gh) * 40 - img.height.toDouble()),
        Paint(),
      );
    } else {
      _paintFurnitureFallback(canvas, f, wood);
    }
    if (isDragging) {
      canvas.drawRect(rect, Paint()..color = const Color(0x59FFFFFF));
    }
    final tp = TextPainter(
      text: TextSpan(
        text: f.name,
        style: const TextStyle(fontSize: 11, color: Color(0xFF5A4632)),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(f.gx * 40, f.gy * 40 - 14));
  }

  void _paintFurnitureFallback(Canvas canvas, Furniture f, Paint wood) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    switch (f.key) {
      case 'bed':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(6)),
          Paint()..color = const Color(0xFF7FA8C9),
        );
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 6, 30, 22),
          Paint()..color = AppColors.white,
        );
        break;
      case 'sofa':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(8)),
          Paint()..color = const Color(0xFF8B6FA8),
        );
        break;
      case 'tv':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF222831));
        canvas.drawRect(rect.deflate(8), Paint()..color = const Color(0xFF9BE8FF));
        break;
      case 'stove':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 2; i++) {
          canvas.drawCircle(
            Offset(f.gx * 40 + 26 + i * 30, f.gy * 40 + 20),
            11,
            Paint()..color = AppColors.textStrong,
          );
        }
        break;
      case 'fridge':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF9AA5B1));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 36),
          Paint()..color = const Color(0xFF6B7684)..strokeWidth = 2,
        );
        break;
      case 'table':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'desk':
        canvas.drawRect(rect, wood);
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 10, f.gy * 40 + 8, 20, 14),
          Paint()..color = const Color(0xFFE8E2D8),
        );
        break;
      case 'bookshelf':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 3; i++) {
          canvas.drawLine(
            Offset(f.gx * 40 + 2, f.gy * 40 + 14 + i * 14),
            Offset(f.gx * 40 + 38, f.gy * 40 + 14 + i * 14),
            Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
          );
        }
        break;
      case 'shower':
        canvas.drawRect(rect, Paint()..color = const Color(0xFF9DD8F0));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 12),
          8,
          Paint()..color = AppColors.accentBlue,
        );
        break;
      case 'petbed':
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 14, 28, 18),
          Paint()..color = const Color(0xFFE8A0A0),
        );
        break;
      case 'wardrobe':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFFA8825F));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 72),
          Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
        );
        break;
      case 'nightstand':
      case 'coffee':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'chair':
        canvas.drawRect(rect.deflate(6), Paint()..color = const Color(0xFFA8825F));
        break;
      case 'speaker':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF4A4E69));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 18),
          7,
          Paint()..color = const Color(0xFF22223B),
        );
        break;
      case 'game':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF2A2A3A));
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 8, f.gy * 40 + 10, 24, 16),
          Paint()..color = const Color(0xFF7BE0FF),
        );
        break;
      case 'bathtub':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(3), const Radius.circular(10)),
          Paint()..color = AppColors.white,
        );
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(10), const Radius.circular(6)),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'sink':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFE8E2D8));
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 12, f.gy * 40 + 10, 16, 10),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'plant':
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 14, f.gy * 40 + 24, 12, 10),
          Paint()..color = const Color(0xFFB07A3E),
        );
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 14),
          8,
          Paint()..color = const Color(0xFF3E9B4F),
        );
        break;
      default:
        canvas.drawRect(rect, wood);
    }
  }

  void _paintCharacter(Canvas canvas, Offset p, String imgKey) {
    final img = images[imgKey];
    if (img != null) {
      // 精灵底部对齐到脚点 p
      canvas.drawImage(
        img,
        Offset(p.dx - img.width / 2, p.dy - img.height.toDouble()),
        Paint(),
      );
      return;
    }
    final color = imgKey == 'char_ai'
        ? const Color(0xFFE05A5A)
        : AppColors.accentBlue;
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy - 26, 16, 14),
        Paint()..color = const Color(0xFFF2C48D));
    canvas.drawRect(Rect.fromLTWH(p.dx - 10, p.dy - 12, 20, 16), Paint()..color = color);
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
    canvas.drawRect(Rect.fromLTWH(p.dx + 1, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
  }

  void _paintBubble(Canvas canvas, Offset p, String text) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 12, color: AppColors.textStrong),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final rect = Rect.fromLTWH(
      p.dx - tp.width / 2 - 8,
      p.dy - 24,
      tp.width + 16,
      tp.height + 10,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(8)),
      Paint()..color = AppColors.white,
    );
    tp.paint(canvas, Offset(p.dx - tp.width / 2, p.dy - 20));
  }

  @override
  bool shouldRepaint(covariant RoomPainter old) =>
      old.roomId != roomId ||
      old.userPos != userPos ||
      old.selected != selected ||
      old.dragging != dragging ||
      old.editing != editing ||
      old.bubble != bubble ||
      old.furniture != furniture ||
      old.imagesReady != imagesReady ||
      old.images != images;
}


/// Aurora P4：家具点击波纹——一次扩散圆（scale 1→2.2 + opacity 0.45→0，
/// AppMotion.fast + emphasized）。仅在家具命中且未开启 reduceMotion 时由宿主挂载。
class FurnitureRipple extends StatelessWidget {
  const FurnitureRipple({super.key});

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.primary;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0.0, end: 1.0),
      duration: AppMotion.fast,
      curve: AppMotion.emphasized,
      builder: (context, t, _) {
        return Transform.scale(
          scale: 1.0 + 1.2 * t,
          child: Container(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(
                color: color.withValues(alpha: 0.45 * (1 - t)),
                width: 2,
              ),
            ),
          ),
        );
      },
    );
  }
}
