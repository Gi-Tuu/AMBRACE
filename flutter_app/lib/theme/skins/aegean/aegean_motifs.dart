import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'aegean_palette.dart';

/// 「爱琴海典藏」希腊神话题材具象纹样：羽翼 / 石柱 / 苹果树 / 双耳瓶 / 里拉琴 / 翼环徽。
/// 全部为静态矢量、可随主题染色；只用于标题、空态、头卡、登录、结算等少量装饰位，
/// 严禁进入 ListView item、消息流、输入区（密度与帧率红线，见交接 §6.8）。
class AegeanMotif extends StatelessWidget {
  final double size;
  final Color? color;
  final double opacity;
  const AegeanMotif({super.key, required this.size, this.color, this.opacity = 1});
  static Color goldOf(BuildContext c) =>
      Theme.of(c).brightness == Brightness.dark
          ? AegeanPalette.goldPale
          : AegeanPalette.goldDeepLight;
  @override
  Widget build(BuildContext context) => const SizedBox.shrink(); // 基类不直接用
}

// ─────────────────────────────────────────────────────────────
// 1) 羽翼 AegeanWings：标题两侧 / 徽章背后 / 空态。both=true 双翼，false 单侧
// ─────────────────────────────────────────────────────────────
class AegeanWings extends StatelessWidget {
  final double size;
  final Color? color;
  final bool both;
  final double opacity;
  const AegeanWings({
    super.key,
    this.size = 24,
    this.color,
    this.both = true,
    this.opacity = 1,
  });

  @override
  Widget build(BuildContext context) {
    final c = color ?? AegeanMotif.goldOf(context);
    return CustomPaint(
      size: Size(size, size * 0.62),
      painter: _WingsPainter(c, both, opacity),
    );
  }
}

class _WingsPainter extends CustomPainter {
  final Color c;
  final bool both;
  final double op;
  _WingsPainter(this.c, this.both, this.op);

  @override
  void paint(Canvas canvas, Size s) {
    final h = s.height;
    final root = Offset(s.width * (both ? 0.5 : 0.9), h * 0.70);
    if (both) {
      canvas.save();
      canvas.translate(s.width, 0);
      canvas.scale(-1, 1); // 镜像出左翼
      _half(canvas, s, Offset(s.width * 0.5, h * 0.70));
      canvas.restore();
    }
    _half(canvas, s, root);
  }

  void _half(Canvas canvas, Size s, Offset root) {
    // 主飞羽：内侧上扬 → 外侧平展略垂、长度递增（参差下缘）
    final mains = <(double, double)>[
      (-.50, .36), (-.34, .45), (-.18, .53), (-.04, .60), (.10, .64), (.20, .58),
    ];
    for (var i = 0; i < mains.length; i++) {
      final (ang, len) = mains[i];
      final p = Paint()
        ..color = c.withValues(alpha: op * (0.46 + 0.09 * i))
        ..style = PaintingStyle.fill;
      _feather(canvas, root, ang, s.width * len, s.height * 0.145, p);
    }
    // 覆羽：沿一条上拱弧线排列，形成翼的圆拱上缘
    const n = 5;
    for (var i = 0; i < n; i++) {
      final t = i / (n - 1);
      final base = Offset(
        s.width * (0.5 + 0.33 * t),
        s.height * (0.70 - 0.27 * math.sin(t * math.pi)),
      );
      final p = Paint()
        ..color = c.withValues(alpha: op * (0.70 + 0.06 * i))
        ..style = PaintingStyle.fill;
      _feather(canvas, base, -0.42 + 0.34 * t, s.width * (0.22 - 0.07 * t),
          s.height * 0.10, p);
    }
  }

  /// 梭形羽片：o 出发，ang=方向（0 向右、负上扬），len 长，w 最宽
  void _feather(Canvas cv, Offset o, double ang, double len, double w, Paint p) {
    final dx = math.cos(ang), dy = math.sin(ang);
    final nx = -dy, ny = dx;
    final tip = o + Offset(dx * len, dy * len);
    final path = Path()
      ..moveTo(o.dx, o.dy)
      ..quadraticBezierTo(o.dx + dx * len * .55 + nx * w,
          o.dy + dy * len * .55 + ny * w, tip.dx, tip.dy)
      ..quadraticBezierTo(o.dx + dx * len * .55 - nx * w * .7,
          o.dy + dy * len * .55 - ny * w * .7, o.dx, o.dy)
      ..close();
    cv.drawPath(path, p);
  }

  @override
  bool shouldRepaint(covariant _WingsPainter old) =>
      old.c != c || old.both != both || old.op != op;
}

// 注：希腊石柱 AegeanColumn 已唯一化迁移到 aegean_architects.dart（公式三柱式，
// 自身坐标系、支持 doric/ionic/corinthian），此处不再保留旧版，避免同名冲突。

// ─────────────────────────────────────────────────────────────
// 3) 苹果树 AegeanAppleTree：空态 / 小家 / AI 生活（金苹果 = 生命与收获）
// ─────────────────────────────────────────────────────────────
class AegeanAppleTree extends StatelessWidget {
  final double size;
  final Color? color;
  const AegeanAppleTree({super.key, this.size = 96, this.color});
  @override
  Widget build(BuildContext context) {
    final gold = color ?? AegeanMotif.goldOf(context);
    return CustomPaint(size: Size.square(size), painter: _TreePainter(gold));
  }
}

class _TreePainter extends CustomPainter {
  final Color gold;
  _TreePainter(this.gold);
  @override
  void paint(Canvas canvas, Size s) {
    final w = s.width, h = s.height;
    // 地面缓坡
    final ground = Paint()
      ..style = PaintingStyle.stroke
      ..color = gold.withValues(alpha: .35);
    canvas.drawArc(Rect.fromLTWH(w * .08, h * .9, w * .84, h * .16), math.pi,
        math.pi, false, ground..strokeWidth = 1.1);
    // 树干 + 枝
    final bark = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = w * .05
      ..color = gold.withValues(alpha: .85);
    final trunk = Path()
      ..moveTo(w * .5, h * .95)
      ..cubicTo(w * .46, h * .74, w * .56, h * .6, w * .5, h * .42);
    canvas.drawPath(trunk, bark);
    final branch = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeWidth = w * .026
      ..color = gold.withValues(alpha: .8);
    canvas.drawPath(
        Path()
          ..moveTo(w * .5, h * .58)
          ..cubicTo(w * .4, h * .5, w * .32, h * .46, w * .28, h * .38),
        branch);
    canvas.drawPath(
        Path()
          ..moveTo(w * .5, h * .52)
          ..cubicTo(w * .62, h * .46, w * .68, h * .4, w * .72, h * .32),
        branch);
    // 叶冠（交叠圆，低透橄榄 + 金边）
    final crownFill = Paint()..color = AegeanPalette.olive.withValues(alpha: .14);
    final crownEdge = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.1
      ..color = gold.withValues(alpha: .7);
    final crowns = [
      (.50, .27, .20), (.31, .37, .14), (.69, .34, .15),
      (.40, .18, .13), (.62, .17, .12),
    ];
    for (final (cx, cy, r) in crowns) {
      final rect = Rect.fromCircle(
          center: Offset(cx * w, cy * h), radius: r * w);
      canvas.drawOval(rect, crownFill);
      canvas.drawOval(rect, crownEdge);
    }
    // 金苹果（赤陶实心 + 米白高光）
    const apples = [(.42, .30), (.58, .26), (.34, .40), (.66, .38), (.50, .38)];
    final apple = Paint()..color = AegeanPalette.terraLight;
    final hi = Paint()..color = AegeanPalette.cream.withValues(alpha: .8);
    for (final (ax, ay) in apples) {
      final c = Offset(ax * w, ay * h);
      canvas.drawCircle(c, w * .028, apple);
      canvas.drawCircle(c.translate(-w * .008, -h * .008), w * .008, hi);
    }
  }

  @override
  bool shouldRepaint(covariant _TreePainter old) => old.gold != gold;
}

// ─────────────────────────────────────────────────────────────
// 4) 双耳瓶 AegeanAmphora：空态 / 记忆 / 归档（瓶身一道回纹腰线）
// ─────────────────────────────────────────────────────────────
class AegeanAmphora extends StatelessWidget {
  final double size;
  final Color? color;
  const AegeanAmphora({super.key, this.size = 64, this.color});
  @override
  Widget build(BuildContext context) {
    final c = color ?? AegeanMotif.goldOf(context);
    return CustomPaint(size: Size(size * .72, size), painter: _AmphoraPainter(c));
  }
}

class _AmphoraPainter extends CustomPainter {
  final Color c;
  _AmphoraPainter(this.c);
  @override
  void paint(Canvas canvas, Size s) {
    final w = s.width, h = s.height;
    final fill = Paint()..color = c.withValues(alpha: .08);
    final p = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.3
      ..color = c.withValues(alpha: .88);
    // 饱满瓶身：窄颈、宽肩、圆腹、小台足
    final body = Path()
      ..moveTo(w * .43, h * .04)
      ..lineTo(w * .57, h * .04)
      ..lineTo(w * .54, h * .13)
      ..cubicTo(w * .82, h * .16, w * .92, h * .30, w * .74, h * .42)
      ..cubicTo(w * .60, h * .54, w * .72, h * .66, w * .62, h * .82)
      ..lineTo(w * .38, h * .82)
      ..cubicTo(w * .28, h * .66, w * .40, h * .54, w * .26, h * .42)
      ..cubicTo(w * .08, h * .30, w * .18, h * .16, w * .46, h * .13)
      ..close();
    canvas.drawPath(body, fill);
    canvas.drawPath(body, p);
    // 台足
    canvas.drawLine(Offset(w * .36, h * .82), Offset(w * .64, h * .82), p);
    canvas.drawLine(Offset(w * .40, h * .86), Offset(w * .60, h * .86), p);
    // 大弯把手
    final handle = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.1
      ..color = c.withValues(alpha: .8);
    canvas.drawPath(
        Path()
          ..moveTo(w * .46, h * .14)
          ..cubicTo(w * .06, h * .20, w * .04, h * .40, w * .28, h * .44),
        handle);
    canvas.drawPath(
        Path()
          ..moveTo(w * .54, h * .14)
          ..cubicTo(w * .94, h * .20, w * .96, h * .40, w * .72, h * .44),
        handle);
    // 口沿
    canvas.drawLine(Offset(w * .41, h * .04), Offset(w * .59, h * .04),
        p..strokeWidth = 1.6);
    // 瓶身回纹腰线
    final band = Paint()
      ..color = c.withValues(alpha: .6)
      ..strokeWidth = .8
      ..style = PaintingStyle.stroke;
    canvas.drawLine(Offset(w * .32, h * .52), Offset(w * .68, h * .52), band);
    canvas.drawLine(Offset(w * .34, h * .57), Offset(w * .66, h * .57), band);
    for (var i = 0; i < 6; i++) {
      canvas.drawRect(
          Rect.fromLTWH(w * (.37 + i * .045), h * .528, w * .024, h * .026),
          band);
    }
  }

  @override
  bool shouldRepaint(covariant _AmphoraPainter old) => old.c != c;
}

// ─────────────────────────────────────────────────────────────
// 5) 里拉琴 AegeanLyre：语音 / 音乐 / 诗意空态（阿波罗之琴）
// ─────────────────────────────────────────────────────────────
class AegeanLyre extends StatelessWidget {
  final double size;
  final Color? color;
  const AegeanLyre({super.key, this.size = 64, this.color});
  @override
  Widget build(BuildContext context) {
    final c = color ?? AegeanMotif.goldOf(context);
    return CustomPaint(size: Size(size * .7, size), painter: _LyrePainter(c));
  }
}

class _LyrePainter extends CustomPainter {
  final Color c;
  _LyrePainter(this.c);
  @override
  void paint(Canvas canvas, Size s) {
    final w = s.width, h = s.height;
    final p = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.3
      ..strokeCap = StrokeCap.round
      ..color = c.withValues(alpha: .9);
    // 龟壳共鸣箱（底部横椭圆）
    final box = Paint()..color = c.withValues(alpha: .10);
    final body = Rect.fromCenter(
        center: Offset(w * .5, h * .85), width: w * .52, height: h * .18);
    canvas.drawOval(body, box);
    canvas.drawOval(body, p);
    // 两臂：先外张再内收
    final arm = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.3
      ..strokeCap = StrokeCap.round
      ..color = c.withValues(alpha: .9);
    canvas.drawPath(
        Path()
          ..moveTo(w * .33, h * .80)
          ..cubicTo(w * .16, h * .62, w * .16, h * .36, w * .40, h * .14),
        arm);
    canvas.drawPath(
        Path()
          ..moveTo(w * .67, h * .80)
          ..cubicTo(w * .84, h * .62, w * .84, h * .36, w * .60, h * .14),
        arm);
    // 横梁 + 两端卷
    canvas.drawLine(Offset(w * .38, h * .13), Offset(w * .62, h * .13),
        p..strokeWidth = 1.8);
    final knob = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = c.withValues(alpha: .9);
    canvas.drawCircle(Offset(w * .38, h * .13), w * .03, knob);
    canvas.drawCircle(Offset(w * .62, h * .13), w * .03, knob);
    // 垂直三弦
    final string = Paint()
      ..color = c.withValues(alpha: .6)
      ..strokeWidth = .7;
    for (final sx in [.44, .5, .56]) {
      canvas.drawLine(
          Offset(w * sx, h * .15), Offset(w * sx, h * .77), string);
    }
  }

  @override
  bool shouldRepaint(covariant _LyrePainter old) => old.c != c;
}

// ─────────────────────────────────────────────────────────────
// 6) 翼环徽 AegeanWingedOrb：头像背后 / 加载印 / 结算徽章（双翼 + 金环，中心可放 child）
// ─────────────────────────────────────────────────────────────
class AegeanWingedOrb extends StatelessWidget {
  final double size;
  final Widget? child;
  final Color? color;
  const AegeanWingedOrb({super.key, required this.size, this.child, this.color});
  @override
  Widget build(BuildContext context) {
    final c = color ?? AegeanMotif.goldOf(context);
    return SizedBox(
      width: size,
      height: size * 0.8,
      child: Stack(alignment: Alignment.center, children: [
        Transform.translate(
          offset: Offset(0, -size * 0.04),
          child: AegeanWings(size: size, color: c, both: true, opacity: .9),
        ),
        Container(
          width: size * .5,
          height: size * .5,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: AegeanPalette.marble(Theme.of(context).brightness),
            border: Border.all(color: c, width: 1.4),
            boxShadow: [
              BoxShadow(color: c.withValues(alpha: .25), blurRadius: 8),
            ],
          ),
          child: SizedBox(width: size * .42, child: child),
        ),
      ]),
    );
  }
}
