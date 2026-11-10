// F7-c-3（2026-08-31）自 features/life/home_visual_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:math' as math;
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

  // ── v3.3 家具朝向（rotation 字段，后端 0-7）──
  // 2026-11-10：8 向素材就位（`furn_<key>_<rotation>.png`），语义＝素材罗盘映射：
  // 0=前(S) 1=后(N) 2=左(W) 3=右(E) 4=前左(SW) 5=前右(SE) 6=后左(NW) 7=后右(NE)。
  // 用户拍板：家具面向玩家的那一面＝「前」＝ S 视图。
  static const int rotationMax = 7;

  /// 旋转循环：0→1→…→7→0（8 向）
  static int nextRotation(int rotation) => (rotation + 1) % (rotationMax + 1);

  /// 一个家具槽位在某方向下的素材键；无对应 8 向图时由调用方回落到旧单图
  /// （`furn_<key>`），再回落 painter 的矢量兜底。
  static String furnitureAssetKey(String key, int rotation) =>
      'furn_${key}_${rotation.clamp(0, rotationMax)}';

  /// 小家现有家具 key（后端 life-home 布局口径）
  static const List<String> furnitureKeys = [
    'bed', 'sofa', 'tv', 'stove', 'fridge',
    'table', 'desk', 'bookshelf', 'shower', 'bathtub',
    'wardrobe', 'nightstand', 'coffee', 'chair',
    'speaker', 'game', 'plant', 'petbed', 'sink',
    'rug', 'painting', 'lamp', 'shelf', 'bin', 'clock',
    // 2026-11-10 第二批（WorkBuddy 8 向素材）：单人沙发/柜/微波炉/镜/边几/桌面台灯/马桶/梳妆台/洗衣机
    'armchair', 'cabinet', 'micro', 'mirror', 'side',
    'tablelamp', 'toilet', 'vanity', 'washer',
  ];

  /// 8 向素材比旧单图大（如床 113×160 vs 80×96）：按旧单图占位等比缩进，
  /// 既保持既有小家观感/脚底对齐，也不让新图压到相邻格子。无旧图时返回 1.0（原尺寸）。
  static double furnitureFitScale({
    required double srcW,
    required double srcH,
    required double boxW,
    required double boxH,
  }) {
    if (srcW <= 0 || srcH <= 0 || boxW <= 0 || boxH <= 0) return 1.0;
    final s = math.min(boxW / srcW, boxH / srcH);
    return s <= 0 ? 1.0 : s;
  }

  /// 预加载清单：旧单图（回落用）＋ 每个家具 8 个方向图
  static List<String> furnitureAssetKeys() => [
        for (final k in furnitureKeys) ...[
          'furn_$k',
          for (var r = 0; r <= rotationMax; r++) furnitureAssetKey(k, r),
        ],
      ];

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

