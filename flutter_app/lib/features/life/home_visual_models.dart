// F7-c-3（2026-08-31）自 screens/life/home_visual_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';

/// Aurora P4：全局「降低动效」读取（未包裹 Provider 兜底 false）。
bool homeMaybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion ||
        MediaQuery.disableAnimationsOf(context);
  } catch (_) {
    return MediaQuery.disableAnimationsOf(context);
  }
}

/// 小家 v3.2 家具自由摆放几何（纯函数，便于单测）：
/// 逻辑画布 640x480（16x12 格，每格 40px），家具坐标为浮点格坐标（可小数，不吸附格子）。
class HomeLayoutMath {
  static const double cell = 40;
  static const double canvasW = 640;
  static const double canvasH = 480;
  static const double gridCols = 16;
  static const double gridRows = 12;

  /// 家具命中检测（浮点）：logic 为画布内逻辑像素坐标；gx/gy/gw/gh 为格坐标
  static bool hitTestRect(Offset logic,
      {required double gx,
      required double gy,
      required double gw,
      required double gh}) {
    final rect = Rect.fromLTWH(gx * cell, gy * cell, gw * cell, gh * cell);
    return rect.contains(logic);
  }

  /// 逻辑像素坐标 → 格坐标
  static Offset logicToGrid(Offset logic) =>
      Offset(logic.dx / cell, logic.dy / cell);

  /// 拖动落位钳制：家具完整保持在画布内（左上角 0-16 / 0-12 格，自由小数）
  static Offset clampGrid(Offset g, double gw, double gh) => Offset(
        g.dx.clamp(0.0, gridCols - gw),
        g.dy.clamp(0.0, gridRows - gh),
      );

  // ── v3.3 家具朝向（rotation 字段，后端 0-7；0=前 1=后 2=左 3=右，斜向 4-7 预留）──
  static const int rotationMax = 7;

  /// 旋转循环切换：本轮先 4 方向（0-3），斜向素材就绪后改用 rotationMax 循环
  static int nextRotation(int rotation) => (rotation + 1) % 4;

  /// 后端透传钳制 0-7（防脏数据）
  static int clampRotation(int rotation) => rotation.clamp(0, rotationMax);
}

/// 生活可视·小家（v3.1.0+）：多房间像素家居 + 宠物素材 + 宠物互动
/// 触屏点地面移动、点家具/宠物弹居中弹窗交互；底部虚拟方向键 + 交互按钮（保留点屏移动）。
/// v3.2：长按家具 300ms 进入拖动态 → 浮点自由坐标（像素级）→ 松手落位自动保存。
class Furniture {
  final String key;
  final String name;
  final double gx, gy, gw, gh;
  final int rotation;
  final String? action;
  const Furniture(this.key, this.name, this.gx, this.gy, this.gw, this.gh,
      [this.rotation = 0, this.action]);
  factory Furniture.fromMap(Map<String, dynamic> m) => Furniture(
        m['key'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['gx'] as num?) ?? 0).toDouble(),
        ((m['gy'] as num?) ?? 0).toDouble(),
        ((m['gw'] as num?) ?? 1).toDouble(),
        ((m['gh'] as num?) ?? 1).toDouble(),
        HomeLayoutMath.clampRotation(((m['rotation'] as num?) ?? 0).toInt()),
        m['action'] as String?,
      );
}

class Room {
  final String id;
  final String name;
  final List<Furniture> furniture;
  const Room(this.id, this.name, this.furniture);
  factory Room.fromMap(Map<String, dynamic> m) => Room(
        m['id'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['furniture'] as List?) ?? const [])
            .map((e) => Furniture.fromMap(Map<String, dynamic>.from(e as Map)))
            .toList(),
      );
}

