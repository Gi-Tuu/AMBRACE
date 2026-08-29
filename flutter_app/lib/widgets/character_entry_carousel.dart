import 'package:flutter/material.dart';

import '../theme/aurora_tokens.dart';
import '../theme/tokens.dart';

/// 头像入口「翻转轮播」。
///
/// 交互与 CharacterCalendarFaces 保持一致：PageView + 透视 Transform
/// （Cover Flow 风格），替代旧的「手动横向拖拽 + 速度判定」圆环，避免与
/// 头像的点击/长按手势冲突，翻页手感与下方日历三面统一。
///
/// 页面排布：`entries.first | center | entries[1] | entries[2] ...`
/// 默认停在 center（initialPage=1），左右可翻到各入口；点击入口卡片触发
/// 对应回调，返回后自动吸附回 center。center（通常是头像）自身的点击/长按
/// 由调用方在外层包裹，PageView 只接管水平滑动，二者不冲突。
class CharacterEntryCarousel extends StatefulWidget {
  /// 居中页（一般为头像），其自身 onTap/onLongPress 由传入组件保留。
  final Widget center;

  /// 入口列表：第 1 个排在 center 左侧，其余依次排在右侧。
  final List<EntryCarouselItem> entries;

  /// 轮播视口高度（头像/入口卡片的外接高度）。
  final double height;

  /// 视口分数（越小相邻页露出越多）。
  final double viewportFraction;

  const CharacterEntryCarousel({
    super.key,
    required this.center,
    required this.entries,
    this.height = 116,
    this.viewportFraction = 0.62,
  });

  @override
  State<CharacterEntryCarousel> createState() =>
      _CharacterEntryCarouselState();
}

/// 一个侧边入口（设置 / AI 内心世界 / AI 生活 …）。
class EntryCarouselItem {
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  const EntryCarouselItem({
    required this.icon,
    required this.label,
    required this.onTap,
  });
}

class _CharacterEntryCarouselState extends State<CharacterEntryCarousel> {
  late final PageController _controller;
  late final int _initialPage;
  double _page = 0;

  /// 一个循环单元长度：entries.first | center | entries[1..]
  int get _cycle => widget.entries.length + 1;

  /// center（头像）在循环单元中的位置。
  static const int _centerInCycle = 1;

  @override
  void initState() {
    super.initState();
    // 选一个足够大、且落在头像页（%cycle==1）的初始页，两侧都可无限滑。
    const base = 3000;
    final n = widget.entries.length + 1;
    _initialPage = base - (base % n) + _centerInCycle;
    _page = _initialPage.toDouble();
    _controller = PageController(
      initialPage: _initialPage,
      viewportFraction: widget.viewportFraction,
    );
    _controller.addListener(() {
      final pv = _controller.page;
      if (pv != null) setState(() => _page = pv);
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// 入口点击：先触发业务回调（通常是 push 新页），稍后吸附回最近的头像页。
  Future<void> _onEntryTap(EntryCarouselItem item) async {
    item.onTap();
    await Future<void>.delayed(const Duration(milliseconds: 80));
    if (!mounted || !_controller.hasClients) return;
    final cur = _page.round();
    final n = _cycle;
    // 最近的、循环位置为头像（%n==1）的页
    final target = cur - (cur % n) + _centerInCycle;
    _controller.animateToPage(
      target,
      duration: AppMotion.normal,
      curve: AppMotion.emphasized,
    );
  }

  // pages: [entries.first, center, entries[1..]]
  List<Widget> get _pages {
    final list = <Widget>[];
    if (widget.entries.isNotEmpty) list.add(_buildEntryCard(widget.entries.first));
    list.add(Center(child: widget.center));
    for (var i = 1; i < widget.entries.length; i++) {
      list.add(_buildEntryCard(widget.entries[i]));
    }
    return list;
  }

  Widget _buildEntryCard(EntryCarouselItem item) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: GestureDetector(
        onTap: () => _onEntryTap(item),
        child: Container(
          width: 132,
          height: widget.height - 8,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: scheme.surface,
            borderRadius: BorderRadius.circular(AppRadius.xl),
            border: Border.all(
              color: scheme.outlineVariant.withValues(alpha: 0.6),
              width: 0.5,
            ),
            boxShadow: AppShadow.light,
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [
                      scheme.primary.withValues(alpha: 0.18),
                      scheme.primary.withValues(alpha: 0.08),
                    ],
                  ),
                ),
                child: Icon(item.icon, color: scheme.primary, size: 22),
              ),
              const SizedBox(height: 6),
              Text(
                item.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: scheme.onSurface,
                ),
              ),
              const SizedBox(height: 2),
              Icon(Icons.chevron_right,
                  size: 14, color: scheme.onSurfaceVariant),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final pages = _pages;
    final n = pages.length;
    final reduceMotion = MediaQuery.disableAnimationsOf(context);
    final current = ((_page.round() % n) + n) % n;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          height: widget.height,
          child: PageView.builder(
            controller: _controller,
            itemCount: null, // 无限循环
            physics: const BouncingScrollPhysics(),
            itemBuilder: (context, index) {
              final face = ((index % n) + n) % n;
              final delta = index - _page;
              final absDelta = delta.abs();
              final scale = 1.0 - (absDelta * 0.10).clamp(0.0, 0.18);
              final rotateY = reduceMotion ? 0.0 : delta * 0.08;
              return Transform(
                alignment: Alignment.center,
                transform: Matrix4.identity()
                  ..setEntry(3, 2, 0.001)
                  ..rotateY(rotateY)
                  ..scaleByDouble(scale, scale, scale, 1),
                child: pages[face],
              );
            },
          ),
        ),
        const SizedBox(height: 8),
        // 圆点指示器（与日历三面一致）
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            for (var i = 0; i < n; i++)
              AnimatedContainer(
                duration: AppMotion.fast,
                margin: const EdgeInsets.symmetric(horizontal: 4),
                width: i == current ? 18 : 6,
                height: 6,
                decoration: BoxDecoration(
                  color: i == current
                      ? scheme.primary
                      : scheme.onSurfaceVariant.withValues(alpha: 0.4),
                  borderRadius: BorderRadius.circular(3),
                ),
              ),
          ],
        ),
      ],
    );
  }
}
