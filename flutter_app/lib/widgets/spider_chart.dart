import 'dart:math' as math;
import 'package:flutter/material.dart';
import "package:ai_companion/theme/tokens.dart";

/// 正八边形蛛网图（八维可视化状态共用组件：角色页 + 用户主页）
///
/// 支持单组（values）与对比（values + compareValues）两种模式：
/// - 对比模式标签显示「心情\n80/65」（主值/对比值），主组用主色、对比组用对比色，
///   两网颜色区分度高（默认蓝 vs 橙），不再叠加绘制重复标签。
class SpiderChart extends StatelessWidget {
  final List<double> values; // 8 个 0-100
  final List<String> labels;
  final List<Color> colors;
  final List<double>? compareValues; // 对比组（可选，与 values 同长度）
  final Color primaryColor; // 主组颜色（默认蓝）
  final Color compareColor; // 对比组颜色（默认橙）

  const SpiderChart({
    super.key,
    required this.values,
    required this.labels,
    required this.colors,
    this.compareValues,
    this.primaryColor = const Color(0xFF1E88E5),
    this.compareColor = AppColors.compareOrange,
  });

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _SpiderChartPainter(
        values: values,
        labels: labels,
        colors: colors,
        compareValues: compareValues,
        primaryColor: primaryColor,
        compareColor: compareColor,
      ),
      // Stack（StackFit.loose）中 CustomPaint 默认尺寸为 0，需撑满父级避免蛛网挤在左上角
      child: const SizedBox.expand(),
    );
  }
}

class _SpiderChartPainter extends CustomPainter {
  final List<double> values; // 8 个 0-100
  final List<String> labels;
  final List<Color> colors;
  final List<double>? compareValues;
  final Color primaryColor;
  final Color compareColor;

  _SpiderChartPainter({
    required this.values,
    required this.labels,
    required this.colors,
    this.compareValues,
    required this.primaryColor,
    required this.compareColor,
  });

  static const _n = 8;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = math.min(size.width, size.height) / 2 - 36;
    final gridColor = Colors.grey.withValues(alpha: 0.35);
    final axisColor = Colors.grey.withValues(alpha: 0.5);
    final hasCompare = compareValues != null && compareValues!.length == values.length;

    // 多层网格（25/50/75/100）
    for (var level = 1; level <= 4; level++) {
      final path = Path();
      for (var i = 0; i < _n; i++) {
        final p = _point(center, radius * level / 4, i);
        if (i == 0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      path.close();
      canvas.drawPath(path, Paint()..color = gridColor..style = PaintingStyle.stroke..strokeWidth = 1);
    }

    // 轴线 + 标签（对比模式每维显示 主值/对比值，避免两个网的数值重叠）
    for (var i = 0; i < _n; i++) {
      final p = _point(center, radius, i);
      canvas.drawLine(center, p, Paint()..color = axisColor..strokeWidth = 1);
      final label = labels.length > i ? labels[i] : '';
      final v1 = values.length > i ? values[i].round().toString() : '';
      final v2 = hasCompare ? compareValues![i].round().toString() : null;
      final valueText = v2 == null ? v1 : '$v1/$v2';
      final tp = TextPainter(
        text: TextSpan(
          text: '$label\n$valueText',
          style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: Colors.grey.shade800),
        ),
        textAlign: TextAlign.center,
        textDirection: TextDirection.ltr,
      )..layout();
      final labelPos = _point(center, radius + 18, i);
      final angle = -math.pi / 2 + i * 2 * math.pi / _n;
      final dx = math.cos(angle);
      final dy = math.sin(angle);
      double tx = labelPos.dx;
      double ty = labelPos.dy;
      if (dx.abs() < 0.35) {
        tx = labelPos.dx - tp.width / 2;
      } else if (dx < 0) {
        tx = labelPos.dx - tp.width;
      }
      if (dy.abs() < 0.35) {
        ty = labelPos.dy - tp.height / 2;
      } else if (dy > 0) {
        ty = labelPos.dy;
      } else {
        ty = labelPos.dy - tp.height;
      }
      tp.paint(canvas, Offset(tx, ty));
    }

    void drawPolygon(List<double> src, Color color, {bool top = false}) {
      final path = Path();
      for (var i = 0; i < _n; i++) {
        final v = (src.length > i ? src[i] : 0).clamp(0.0, 100.0);
        final pt = _point(center, radius * v / 100, i);
        if (i == 0) {
          path.moveTo(pt.dx, pt.dy);
        } else {
          path.lineTo(pt.dx, pt.dy);
        }
      }
      path.close();
      canvas.drawPath(path, Paint()..color = color.withValues(alpha: 0.28)..style = PaintingStyle.fill);
      canvas.drawPath(path, Paint()..color = color..style = PaintingStyle.stroke..strokeWidth = 2);
      for (var i = 0; i < _n; i++) {
        final v = (src.length > i ? src[i] : 0).clamp(0.0, 100.0);
        final pt = _point(center, radius * v / 100, i);
        canvas.drawCircle(pt, top ? 4 : 3, Paint()..color = color);
        if (!hasCompare && colors.length > i) {
          canvas.drawCircle(pt, 1.5, Paint()..color = colors[i]);
        }
      }
    }

    if (hasCompare) {
      drawPolygon(compareValues!, compareColor); // 对比组（较晚）先画
      drawPolygon(values, primaryColor, top: true); // 主组（较早）置顶
    } else {
      drawPolygon(values, primaryColor);
    }
  }

  Offset _point(Offset center, double r, int i) {
    final angle = -math.pi / 2 + i * 2 * math.pi / _n;
    return center + Offset(math.cos(angle) * r, math.sin(angle) * r);
  }

  @override
  bool shouldRepaint(covariant _SpiderChartPainter oldDelegate) {
    return oldDelegate.values != values || oldDelegate.compareValues != compareValues;
  }
}
