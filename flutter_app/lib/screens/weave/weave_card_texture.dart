// 织网 3D · 卡片纹理：离屏渲染文字卡片 + LRU 纹理池（2026-08-24，织网 3D P1）
//
// 职责拆分（尽量保持可单测）：
// - kWeavePalette / weaveNodeColor：节点调色盘（与 2.5D 画布一致），供卡片与小球共用。
// - weaveCardContentKey：卡片内容指纹（标题+摘要+情绪色+生活类型+热标签），内容变化即需重建纹理。
// - WeaveTextureCache<T>：泛型 LRU 缓存。只关心「按 nodeId 缓存 + contentKey 失效 + 上限淘汰」，
//   不依赖 flutter_scene 的 GPU 类型，故可用 T=String 等做纯逻辑单测。
// - WeaveCardTextureRenderer：用 ui.PictureRecorder 离屏画卡片 → ui.Image → Texture2D.fromImage，
//   真正触 GPU 纹理（真机/模拟器运行；flutter 测试环境无法跑 GPU，故只测 LRU/降级等纯逻辑）。
//
// 说明：flutter_scene 0.20.0 的 UnlitMaterial({TextureSource? colorTexture}) 支持 baseColorTexture，
// 传一张 Texture2D 即可把离屏卡片贴到节点小球（最终颜色 = baseColorFactor * baseColorTexture，
// 贴图时把 baseColorFactor 置为 1×1×1×1 白，让纹理原色透出）。
import 'dart:async';
import 'dart:ui' as ui;

import 'package:flutter/painting.dart'
    show
        Color,
        FontWeight,
        Offset,
        PaintingStyle,
        Radius,
        Rect,
        RRect,
        TextDirection,
        TextPainter,
        TextSpan,
        TextStyle;
import 'package:flutter_scene/scene.dart'
    show Texture2D, TextureContent, TextureSampling;

import 'package:ai_companion/screens/weave/weave_scene_controller.dart';
import 'package:ai_companion/screens/weave/weave_perf_monitor.dart' show WeaveRenderTier;
import 'package:ai_companion/theme/tokens.dart';

/// 织网节点调色盘（与 2.5D 画布一致，供 2D 画布 / 3D 小球 / 卡片情绪色共用）。
const List<Color> kWeavePalette = <Color>[
  AppColors.accent,
  AppColors.success,
  Color(0xFFFF9500),
  Color(0xFFFF2D55),
  Color(0xFFAF52DE),
  Color(0xFF00C7BE),
  Color(0xFFFFCC00),
  Color(0xFF5AC8FA),
];

/// 依据角色/生活类型求出节点颜色（与 2.5D 画布一致：生活=蓝/反思=紫/笔记=绿）。
Color weaveNodeColor(WeaveSceneNode n) {
  final base = kWeavePalette[n.characterId % kWeavePalette.length];
  if (n.lifeType == 'reflection') return const Color(0xFFAF52DE);
  if (n.lifeType == 'note') return AppColors.success;
  return base;
}

/// 卡片内容指纹：标题/摘要/情绪/生活类型/热标签任一变化都会生成新纹理键，
/// 使旧纹理在 LRU 中失效（下次访问重建）。id 仅用于调试，不参与是否复用的判定。
String weaveCardContentKey(WeaveSceneNode n) {
  return '${n.title}|${n.summary}|${n.mood}|${n.lifeType}|${n.hotTags.join(',')}';
}

/// 卡片纹理固定分辨率（逻辑像素；池内每张纹理内存一致，便于估计上限）。
///
/// 2026-08-24（织网 3D P2-P3，灰块修复）：由 NPOT 384×240 改为 2 的幂 256×256。
///
/// 依据（灰块最可能根因）：flutter_scene 0.20.0 的 `Texture2D.fromImage` 在创建时无条件生成
/// mip 链（texture2d.dart `fromPixels`→`generateMipChain`）；384×240 非 2 的幂，其上采样
/// 后接 mipmap，在部分移动 GPU/驱动按半精度/非规范级过滤时出现异常采样，表现为节点小球
/// 显示“灰块/黑块”。改 2 的幂后，各 mip 级尺寸保持整数比，规避这类采样异常。
///
/// 256×256=65536 px；RGBA8888=4 B/px → 256 KiB/张；80 张 ≈ 20 MiB（含 mip 链约 +33% ≈ 27 MiB）。
/// 备选 512×256（是 2 的幂）：0.5 MiB/张，80 张 ≈ 40 MiB，需把池上限降到 ~60；低端机优先紧凑，
/// 故取 256×256（内容为“标题+摘要”紧凑排版，正方形即可容纳，无需更宽）。
const int kWeaveCardTextureWidth = 256;
const int kWeaveCardTextureHeight = 256;

/// 单张卡片纹理的显存估算（字节）：RGBA8888，宽×高×4。
/// 供单元测试与注释引用，避免魔数。
int kWeaveCardTextureBytesPerTexture() =>
    kWeaveCardTextureWidth * kWeaveCardTextureHeight * 4;

/// 是否为 2 的幂（NPOT 判断；灰块根因修复后应恒真）。
bool isPowerOfTwo(int v) => v > 0 && (v & (v - 1)) == 0;

/// LRU 纹理池上限（张）。节点数量级 100-300，超过上限后按 LRU 淘汰，
/// 被淘汰的节点回退为纯色圆点（与 2.5D 聚类泡语义一致）。
const int kWeaveTextureMaxCache = 80;

/// 可见节点数超过此值时：不再为节点生成/保留卡片纹理，直接全部纯色圆点（低端机兜底）。
const int kWeaveTextureDegradeAbove = 150;

/// 是否应降级为纯色圆点（节点数超阈值）。
bool weaveShouldDegradeToDots(int visibleNodeCount) =>
    visibleNodeCount > kWeaveTextureDegradeAbove;

/// 是否需要为节点预热卡片纹理（供视图 _scheduleWarm 判定）。
///
/// - light 档（[WeaveRenderTier.light]）：不预热（纯色圆点+低细分球），并清空缓存；
/// - full 档：节点数超 [kWeaveTextureDegradeAbove]（[weaveShouldDegradeToDots]）也不预热（纯色圆点）。
bool weaveShouldWarmTextures(int visibleNodeCount, WeaveRenderTier tier) =>
    tier == WeaveRenderTier.full && !weaveShouldDegradeToDots(visibleNodeCount);

/// 每批预热生成的纹理张数（分批节流：每帧只生成 2-3 张，避免一次性全量生成
/// 的 CPU/GPU 峰值误触发低端机帧率降级）。低端机取小值更稳。
const int kWeaveWarmBatchSize = 3;

/// 把待预热节点 id 列表按批次切分（每批 [batchSize] 个），用于分批渐进生成纹理。
///
/// 纯函数、不依赖 GPU，供单测与视图分批预热共用。前一批生成完才能上屏，故此处返回
/// 的是「批的划分」，实际逐批推进由视图以 Timer/Ticker 节流驱动（见 weave_scene_view.dart）。
/// 缺省 [batchSize]<=1 时按 1 个一批（最保守）。
List<List<int>> planWeaveTextureBatches(List<int> nodeIds, int batchSize) {
  final safe = batchSize < 1 ? 1 : batchSize;
  final out = <List<int>>[];
  for (var i = 0; i < nodeIds.length; i += safe) {
    final end = (i + safe) < nodeIds.length ? (i + safe) : nodeIds.length;
    out.add(nodeIds.sublist(i, end));
  }
  return out;
}

/// 泛型 LRU 纹理缓存。
///
/// 语义：以 [WeaveSceneNode.id] 为键缓存一个「纹理」值；每次访问触发 MRU 提升；
/// 超过 [maxEntries] 自动淘汰最久未用的条目。条目内容用 [weaveCardContentKey] 校验，
/// 内容变化即视为失效（下一次访问重建）。`T` 在真实视图里是 `TextureSource`
/// （flutter_scene 的 GPU 纹理源），在单测里可以是任意可比较的宿主类型。
///
/// `build` 为异步构建器（真实场景为「离屏渲染卡片 → Texture2D.fromImage」）；
/// 该缓存本身不依赖 GPU，构建器由调用方注入，从而可在无 GPU 的测试里用假构建器验证。
class WeaveTextureCache<T> {
  WeaveTextureCache({
    required this.build,
    int maxEntries = kWeaveTextureMaxCache,
  }) : _maxEntries = maxEntries;

  /// 为给定节点异步生成一件纹理（null 表示应回退纯色圆点）。
  final Future<T?> Function(WeaveSceneNode node) build;
  final int _maxEntries;

  /// 键为 nodeId；Map 的插入顺序即 LRU 顺序（最早插入=最久未用，位于首部）。
  final Map<int, _CacheEntry<T>> _map = <int, _CacheEntry<T>>{};

  /// 正在异步生成的 nodeId（避免重复构建）。
  final Set<int> _building = <int>{};

  /// 当前缓存条目数。
  int get size => _map.length;

  /// 缓存上限。
  int get maxEntries => _maxEntries;

  /// 同步 MRU 查询：命中且内容一致时把条目移到最新并返回，否则返回 null。
  T? lookup(int nodeId, String contentKey) {
    final entry = _map.remove(nodeId);
    if (entry == null) return null;
    if (entry.contentKey != contentKey) {
      // 内容已失效：丢弃，等下次访问/预热重建。
      return null;
    }
    _map[nodeId] = entry; // 移到 LRU 尾部（最新）
    return entry.value;
  }

  /// 是否需要为 nodeId 构建（已缓存且内容一致，或已在构建中）。
  bool contains(int nodeId, String contentKey) {
    final entry = _map[nodeId];
    if (entry != null && entry.contentKey == contentKey) return true;
    return _building.contains(nodeId);
  }

  /// 开始（若尚未）异步构建一件纹理并返回其 Future；已构建/内容一致则直接返回 null。
  /// [degrade] 为 true 时不做任何构建（调用方用纯色圆点）。
  Future<void>? ensure(WeaveSceneNode node, {required bool degrade}) {
    if (degrade) return null;
    final id = node.id;
    final key = weaveCardContentKey(node);
    if (contains(id, key)) return null;
    if (!_building.add(id)) return null;
    return build(node).then((value) {
      _building.remove(id);
      if (value != null) _store(id, key, value);
    }).catchError((Object _) {
      // 构建失败：移除标记，下次访问/预热再试；本次回退纯色圆点即可。
      _building.remove(id);
    });
  }

  /// 预热一批节点（幂等）：为缺失/失效的条目发起构建，等待全部完成。
  /// [degrade] 为 true 时清空并跳过（纯色圆点）。
  ///
  /// 注意：flutter_scene 的 `Texture2D` 无显式 dispose API（纹理内存按引用计数由引擎管理），
  /// 淘汰/清空只需移除引用，待 GC/引擎回收即可；[clear] 丢弃全部引用即释放纹理显存。
  Future<void> warm(List<WeaveSceneNode> nodes, {required bool degrade}) async {
    if (degrade) {
      clear();
      return;
    }
    final futures = <Future<void>>[];
    for (final n in nodes) {
      final f = ensure(n, degrade: false);
      if (f != null) futures.add(f);
    }
    if (futures.isNotEmpty) await Future.wait(futures);
  }

  void _store(int id, String key, T value) {
    _map.remove(id);
    _map[id] = _CacheEntry<T>(key, value);
    while (_map.length > _maxEntries) {
      _map.remove(_map.keys.first); // 淘汰最久未用
    }
  }

  /// 清空（降级 / 节点集变化 / dispose）。
  void clear() {
    _map.clear();
    _building.clear();
  }
}

class _CacheEntry<T> {
  _CacheEntry(this.contentKey, this.value);
  final String contentKey;
  final T value;
}

/// 离屏渲染节点卡片 → flutter_scene 纹理。
///
/// 绘制：圆角卡片（标题 + 摘要 + 情绪色底/描边 + 热标签圆点），分辨率固定为
/// [kWeaveCardTextureWidth]×[kWeaveCardTextureHeight]。
class WeaveCardTextureRenderer {
  const WeaveCardTextureRenderer();

  /// 生成节点卡片的 `ui.Image`（离屏 PictureRecorder）。调用方负责 dispose。
  Future<ui.Image> render(WeaveSceneNode node) async {
    final recorder = ui.PictureRecorder();
    final canvas = ui.Canvas(recorder);
    _drawCard(
      canvas,
      node,
      ui.Size(
        kWeaveCardTextureWidth.toDouble(),
        kWeaveCardTextureHeight.toDouble(),
      ),
    );
    final picture = recorder.endRecording();
    try {
      return await picture.toImage(
        kWeaveCardTextureWidth,
        kWeaveCardTextureHeight,
      );
    } finally {
      picture.dispose();
    }
  }

  /// 生成并上传为 flutter_scene `Texture2D`（贴到节点小球）。
  Future<Texture2D> buildTexture(WeaveSceneNode node) async {
    final image = await render(node);
    try {
      // 采样取舍（依据）：
      // - 尺寸已是 2 的幂（256×256），故保留 mipmaps=true 的三线性+各向异性采样，规避
      //   NPOT+mipmap 采样异常（灰块根因）的同时保留小节点球上的文字细节；
      //   mipmaps=false 可省约 33% 显存并削减生成峰值，但会损失小球的抗锯齿，低端机优先观感取 true。
      // - addressMode=repeat：卡片四周均有 14px 背景留白，球面经度接缝（u=0/1）两侧均为背景色，
      //   沿球面包裹无需 clamp 也不会出现“包裹瑕疵”（clampToEdge 需引用 flutter_scene 内部
      //   gpu.SamplerAddressMode，属 implementation_imports，避免引入内部依赖）。
      return await Texture2D.fromImage(
        image,
        content: TextureContent.color,
        sampling: const TextureSampling(),
      );
    } finally {
      image.dispose();
    }
  }

  /// 绘制卡片内容（纯 Canvas 绘制；供 [render] 使用）。
  void _drawCard(ui.Canvas canvas, WeaveSceneNode node, ui.Size size) {
    final w = size.width;
    final h = size.height;
    final mood = weaveNodeColor(node);
    final isHot = node.hotTags.isNotEmpty;

    // 卡片主体（留出边距）
    const pad = 14.0;
    final card = RRect.fromRectAndRadius(
      Rect.fromLTWH(pad, pad, w - pad * 2, h - pad * 2),
      const Radius.circular(18),
    );

    // 背景：白底
    canvas.drawRRect(
      card,
      ui.Paint()..color = const ui.Color(0xFFF6F3FC),
    );
    // 左侧情绪色条
    final bar = RRect.fromRectAndRadius(
      Rect.fromLTWH(pad, pad, 8, h - pad * 2),
      const Radius.circular(4),
    );
    canvas.drawRRect(bar, ui.Paint()..color = mood);

    // 标题（单行，最多 7 字符，超出省略号）
    final title = node.title.trim();
    final titleText = title.length > 7 ? '${title.substring(0, 7)}…' : title;
    final tpTitle = TextPainter(
      text: TextSpan(
        text: titleText,
        style: TextStyle(
          fontSize: 17.0,
          fontWeight: FontWeight.w700,
          color: const ui.Color(0xFF1C1B20),
          height: 1.2,
        ),
      ),
      maxLines: 1,
      ellipsis: '…',
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: w - pad * 2 - 26);

    // 摘要（最多 2 行，超出省略号）
    final tpSummary = TextPainter(
      text: TextSpan(
        text: node.summary.trim(),
        style: TextStyle(
          fontSize: 11.5,
          fontWeight: FontWeight.w400,
          color: const ui.Color(0xFF6B6676),
          height: 1.3,
        ),
      ),
      maxLines: 2,
      ellipsis: '…',
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: w - pad * 2 - 26);

    final leftX = pad + 20;
    var y = pad + 26;
    tpTitle.paint(canvas, Offset(leftX, y));
    y += tpTitle.height + 10;
    tpSummary.paint(canvas, Offset(leftX, y));

    // 情绪色描边 + 热标签圆点（右下角）
    canvas.drawRRect(
      card,
      ui.Paint()
        ..color = mood.withValues(alpha: 0.9)
        ..style = PaintingStyle.stroke
        ..strokeWidth = (isHot ? 4.0 : 2.0),
    );
    if (isHot) {
      canvas.drawCircle(
        Offset(w - pad - 18, h - pad - 18),
        9.0,
        ui.Paint()..color = const ui.Color(0xFFFF3B30),
      );
    }
  }
}
