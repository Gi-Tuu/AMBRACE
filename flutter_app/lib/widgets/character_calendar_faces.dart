import 'dart:math' as math;
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../theme/tokens.dart';
import '../../theme/aurora_tokens.dart';
import '../providers/settings_provider.dart';

/// 三面翻转容器：日记 / 状态 / 记忆库。
///
/// 使用 PageView + Transform 透视实现伪 3D 翻页（Cover Flow 风格）。
/// 2026-08-29：三面循环滑动（记忆库再向左滑回到日记），用「大初始页 + 无限 itemCount」
/// 实现，成本最低且手感连续。
class CharacterCalendarFaces extends StatefulWidget {
  final Widget diaryFace;
  final Widget statusFace;
  final Widget memoryFace;
  final ValueChanged<int>? onPageChanged;

  const CharacterCalendarFaces({
    super.key,
    required this.diaryFace,
    required this.statusFace,
    required this.memoryFace,
    this.onPageChanged,
  });

  @override
  State<CharacterCalendarFaces> createState() =>
      _CharacterCalendarFacesState();
}

class _CharacterCalendarFacesState extends State<CharacterCalendarFaces> {
  // 3000 % 3 == 0，初始停在「日记」面，且可向两侧无限滑动。
  static const int _loopStart = 3000;
  late final PageController _controller;
  double _page = _loopStart.toDouble();

  @override
  void initState() {
    super.initState();
    _controller = PageController(
      initialPage: _loopStart,
      viewportFraction: 0.92,
    );
    _controller.addListener(() {
      final p = _controller.page;
      if (p != null) setState(() => _page = p);
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// 当前面（0 日记 / 1 状态 / 2 记忆库），循环取模。
  int get _face => _page.round() % 3;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final pages = [widget.diaryFace, widget.statusFace, widget.memoryFace];
    final labels = [l10n.diary, l10n.status, l10n.calendarMemory];
    final reduceMotion = MediaQuery.disableAnimationsOf(context);

    return Column(
      children: [
        // 页面指示器
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            for (var i = 0; i < 3; i++)
              AnimatedContainer(
                duration: const Duration(milliseconds: 250),
                margin: const EdgeInsets.symmetric(horizontal: 4),
                width: i == _face ? 20 : 6,
                height: 6,
                decoration: BoxDecoration(
                  color: i == _face
                      ? scheme.primary
                      : AppColors.textTertiary.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(3),
                ),
              ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          labels[_face],
          style: const TextStyle(
            fontSize: 11,
            color: AppColors.textSecondary,
            fontWeight: FontWeight.w500,
          ),
        ),
        const SizedBox(height: 8),
        // 三面 PageView（无限循环）
        SizedBox(
          height: 300,
          child: PageView.builder(
            controller: _controller,
            itemCount: null, // 无限
            onPageChanged: (i) => widget.onPageChanged?.call(i % 3),
            itemBuilder: (context, index) {
              final faceIndex = ((index % 3) + 3) % 3;
              final delta = index - _page;
              final absDelta = delta.abs();
              final scale = 1.0 - (absDelta * 0.08).clamp(0.0, 0.15);
              final rotateY = reduceMotion ? 0.0 : delta * 0.08;

              return Transform(
                alignment: Alignment.center,
                transform: Matrix4.identity()
                  ..setEntry(3, 2, 0.001)
                  ..rotateY(rotateY)
                  ..scaleByDouble(scale, scale, scale, 1),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: pages[faceIndex],
                ),
              );
            },
          ),
        ),
      ],
    );
  }
}

/// 日记日历面
class DiaryCalendarFace extends StatefulWidget {
  final int characterId;
  final Future<List<String>> Function(int characterId, String month) fetchDates;

  /// 点击日期的外部回调（可选）；日记正文默认以内置弹窗展示。
  final void Function(DateTime date, String? diaryContent)? onDateTap;
  final Future<String?> Function(int characterId, String date)?
      fetchDiaryContent;

  const DiaryCalendarFace({
    super.key,
    required this.characterId,
    required this.fetchDates,
    this.onDateTap,
    this.fetchDiaryContent,
  });

  @override
  State<DiaryCalendarFace> createState() => _DiaryCalendarFaceState();
}

class _DiaryCalendarFaceState extends State<DiaryCalendarFace> {
  late DateTime _displayMonth;
  Set<String> _markedDates = {};
  String? _selectedDate;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _displayMonth = DateTime(now.year, now.month);
    _loadDates();
  }

  String get _monthKey =>
      '${_displayMonth.year}-${_displayMonth.month.toString().padLeft(2, '0')}';

  Future<void> _loadDates() async {
    try {
      final dates = await widget.fetchDates(widget.characterId, _monthKey);
      if (mounted) setState(() => _markedDates = dates.toSet());
    } catch (_) {
      if (mounted) setState(() => _markedDates = {});
    }
  }

  void _prevMonth() {
    setState(() {
      _displayMonth = DateTime(_displayMonth.year, _displayMonth.month - 1);
      _selectedDate = null;
    });
    _loadDates();
  }

  void _nextMonth() {
    setState(() {
      _displayMonth = DateTime(_displayMonth.year, _displayMonth.month + 1);
      _selectedDate = null;
    });
    _loadDates();
  }

  Future<void> _onDayTap(DateTime date) async {
    final dateStr =
        '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
    setState(() => _selectedDate = dateStr);

    String? content;
    if (widget.fetchDiaryContent != null) {
      try {
        content =
            await widget.fetchDiaryContent!(widget.characterId, dateStr);
      } catch (_) {
        content = null;
      }
    }
    widget.onDateTap?.call(date, content);
    if (!mounted) return;
    // 以独立弹窗查看日记（不再在功能面内挤一块，且有关闭按钮）
    _showDiaryDialog(date, content);
  }

  void _showDiaryDialog(DateTime date, String? content) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    final title =
        '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
    showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => Dialog(
        backgroundColor: scheme.surface,
        insetPadding:
            const EdgeInsets.symmetric(horizontal: 32, vertical: 48),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 16, 10, 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      title,
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                        color: scheme.onSurface,
                      ),
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 20),
                    tooltip: l10n.close,
                    onPressed: () => Navigator.of(ctx).pop(),
                  ),
                ],
              ),
              Flexible(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.only(right: 8, bottom: 8),
                  child: Text(
                    (content != null && content.isNotEmpty)
                        ? content
                        : l10n.diaryNoEntry,
                    style: TextStyle(
                      fontSize: 14,
                      height: 1.55,
                      color: (content != null && content.isNotEmpty)
                          ? scheme.onSurface.withValues(alpha: 0.85)
                          : AppColors.textTertiary,
                    ),
                  ),
                ),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: () => Navigator.of(ctx).pop(),
                  child: Text(l10n.close),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    const hPad = 10.0; // 星期表头与日期网格使用相同水平内边距 → 列严格对齐
    return Container(
      decoration: BoxDecoration(
        color: scheme.surface,
        borderRadius: BorderRadius.circular(AppRadius.lg),
        boxShadow: AppShadow.light,
      ),
      child: Column(
        children: [
          // 月份导航
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
            child: Row(
              children: [
                IconButton(
                  icon: const Icon(Icons.chevron_left, size: 20),
                  onPressed: _prevMonth,
                  visualDensity: VisualDensity.compact,
                ),
                Expanded(
                  child: Text(
                    l10n.calendarTitle(_displayMonth.year, _displayMonth.month),
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                      color: scheme.onSurface,
                    ),
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.chevron_right, size: 20),
                  onPressed: _nextMonth,
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
          ),
          // 星期标题：7 等分，和下方网格列宽一致
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: hPad),
            child: Row(
              children: [
                l10n.weekday1,
                l10n.weekday2,
                l10n.weekday3,
                l10n.weekday4,
                l10n.weekday5,
                l10n.weekday6,
                l10n.weekday7,
              ]
                  .map((d) => Expanded(
                        child: Center(
                          child: Text(
                            d,
                            style: const TextStyle(
                              fontSize: 10,
                              color: AppColors.textTertiary,
                            ),
                          ),
                        ),
                      ))
                  .toList(),
            ),
          ),
          const SizedBox(height: 4),
          // 日期网格：等分列 + 固定尺寸圆，避免边缘数字贴边/占位不均
          Expanded(child: _buildGrid(scheme, hPad)),
          const SizedBox(height: 6),
        ],
      ),
    );
  }

  Widget _buildGrid(ColorScheme scheme, double hPad) {
    final firstDay = DateTime(_displayMonth.year, _displayMonth.month, 1);
    final leadingBlanks = firstDay.weekday - 1; // 周一为一周第一天
    final daysInMonth =
        DateTime(_displayMonth.year, _displayMonth.month + 1, 0).day;
    final total = leadingBlanks + daysInMonth;
    final rowCount = (total / 7).ceil();
    final today = DateTime.now();
    final isCurrentMonth = today.year == _displayMonth.year &&
        today.month == _displayMonth.month;

    Widget dayCell(int d, double size) {
      final date = DateTime(_displayMonth.year, _displayMonth.month, d);
      final dateStr =
          '${date.year}-${date.month.toString().padLeft(2, '0')}-${d.toString().padLeft(2, '0')}';
      final hasDiary = _markedDates.contains(dateStr);
      final isToday = isCurrentMonth && today.day == d;
      final isSelected = _selectedDate == dateStr;
      return GestureDetector(
        onTap: () => _onDayTap(date),
        child: Center(
          child: Container(
            width: size,
            height: size,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: isSelected
                  ? scheme.primary
                  : isToday
                      ? scheme.primaryContainer
                      : null,
            ),
            child: Stack(
              alignment: Alignment.center,
              children: [
                Text(
                  '$d',
                  style: TextStyle(
                    fontSize: 12,
                    color: isSelected
                        ? scheme.onPrimary
                        : isToday
                            ? scheme.onPrimaryContainer
                            : scheme.onSurface.withValues(alpha: 0.7),
                    fontWeight: (isToday || isSelected)
                        ? FontWeight.w600
                        : FontWeight.normal,
                  ),
                ),
                if (hasDiary && !isSelected)
                  Positioned(
                    bottom: size * 0.10, // 比例定位，圆再小也不会被裁
                    child: Container(
                      width: 4,
                      height: 4,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: scheme.primary,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      );
    }

    return LayoutBuilder(builder: (context, constraints) {
      final colW = (constraints.maxWidth - 2 * hPad) / 7;
      final rowH = constraints.maxHeight / rowCount;
      // 圆直径取列宽/行高的较小者 ×0.90 → 行间、列间留白均匀，底行不贴边
      final size = (colW < rowH ? colW : rowH) * 0.90;
      final rows = <Widget>[];
      var slot = 0;
      for (var r = 0; r < rowCount; r++) {
        final cells = <Widget>[];
        for (var c = 0; c < 7; c++) {
          if (slot < leadingBlanks || slot >= total) {
            cells.add(const Expanded(child: SizedBox.shrink()));
          } else {
            final d = slot - leadingBlanks + 1;
            cells.add(Expanded(child: dayCell(d, size)));
          }
          slot++;
        }
        rows.add(Expanded(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: cells,
          ),
        ));
      }
      return Padding(
        padding: EdgeInsets.symmetric(horizontal: hPad),
        child: Column(children: rows),
      );
    });
  }
}

/// 状态面：顶部状态描述 + 八维竖条（描述在上，2026-08-29）
class StatusFace extends StatelessWidget {
  final List<int> values; // 8 个 0-100 的值
  final List<String> labels; // 8 个标签
  final List<Color> colors; // 8 个颜色
  final String statusText;
  final VoidCallback? onTap;

  const StatusFace({
    super.key,
    required this.values,
    required this.labels,
    required this.colors,
    required this.statusText,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    // #6 状态卡模糊走全局换算（不硬编码 sigma）
    final sigma = AppGlass.effectiveBlur(AppGlass.blurLight,
        reduceBlur: _maybeReduceBlur(context));

    return GestureDetector(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surface,
          borderRadius: BorderRadius.circular(AppRadius.lg),
          boxShadow: AppShadow.light,
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(AppRadius.lg),
          child: Column(
            children: [
              // ① 状态描述置顶（毛玻璃小卡）
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(AppRadius.sm),
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 8),
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                          colors: [
                            scheme.primaryContainer.withValues(alpha: 0.18),
                            scheme.primaryContainer.withValues(alpha: 0.06),
                          ],
                        ),
                        borderRadius: BorderRadius.circular(AppRadius.sm),
                      ),
                      child: Text(
                        statusText,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 12,
                          height: 1.4,
                          color: scheme.onSurface.withValues(alpha: 0.85),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              // ② 八维竖条占满剩余空间、底部对齐
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      for (var i = 0; i < 8; i++)
                        _buildBar(
                          values[i].clamp(0, 100),
                          colors[i].withValues(alpha: 0.4),
                          labels[i],
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildBar(int value, Color color, String label) {
    // 可用高度按 8 行网格估算（父级 Expanded 内），最高约 150
    final barHeight = 150.0 * (value / 100);
    return Expanded(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          Container(
            width: 14,
            height: barHeight < 4 ? 4 : barHeight,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(7),
            ),
          ),
          const SizedBox(height: 4),
          FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 9,
                color: AppColors.textTertiary,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 记忆库面：默认收起（中心一颗呼吸球），点击功能面 → 大小不一的气泡喷入并微微浮动；
/// 再点一次空白处 → 收回。点具体气泡进入对应入口（2026-08-29）。
class MemoryFace extends StatefulWidget {
  final List<MemoryBubbleItem> items;

  const MemoryFace({super.key, required this.items});

  @override
  State<MemoryFace> createState() => _MemoryFaceState();
}

class MemoryBubbleItem {
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  const MemoryBubbleItem({
    required this.icon,
    required this.label,
    required this.onTap,
  });
}

class _MemoryFaceState extends State<MemoryFace>
    with TickerProviderStateMixin {
  late final AnimationController _sprayController;
  late final AnimationController _floatController;
  late final AnimationController _hintBreath;
  bool _expanded = false;

  // 当次展开的气泡尺寸：每次展开都重新随机生成，展开期间保持不变（不抖动）
  late List<double> _bubbleSizes = _genSizes(widget.items.length);
  final math.Random _rnd = math.Random();

  List<double> _genSizes(int n) {
    const base = [64.0, 70.0, 76.0, 82.0];
    final list = List<double>.generate(n, (i) {
      final b = base[i % base.length];
      final jitter = _rnd.nextDouble() * 16 - 8; // ±8
      return (b + jitter).clamp(58.0, 88.0).toDouble();
    });
    list.shuffle(_rnd);
    return list;
  }

  @override
  void initState() {
    super.initState();
    _sprayController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    _floatController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 3200),
    ); // 仅展开时 repeat，收起时停（减少离屏/常驻动画开销）
    _hintBreath = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2200),
    )..repeat(reverse: true); // 收起态中心呼吸球
  }

  @override
  void dispose() {
    _sprayController.dispose();
    _floatController.dispose();
    _hintBreath.dispose();
    super.dispose();
  }

  void _toggle() {
    final willOpen = !_expanded;
    setState(() => _expanded = willOpen);
    if (willOpen) {
      _bubbleSizes = _genSizes(widget.items.length); // 每次打开大小都不同
      _sprayController.forward(from: 0);
      _hintBreath.stop();
      if (!_floatController.isAnimating) _floatController.repeat(reverse: true);
    } else {
      _sprayController.reverse();
      _floatController.stop();
      _hintBreath.repeat(reverse: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context)!;
    final count = widget.items.length;

    return GestureDetector(
      // 点功能面空白处展开/收回；点气泡时气泡自己的 GestureDetector 优先，不会触发这里
      onTap: _toggle,
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surface,
          borderRadius: BorderRadius.circular(AppRadius.lg),
          boxShadow: AppShadow.light,
        ),
        child: AnimatedBuilder(
          animation:
              Listenable.merge([_sprayController, _floatController, _hintBreath]),
          builder: (context, _) {
            return LayoutBuilder(
              builder: (context, constraints) {
                final centerX = constraints.maxWidth / 2;
                final centerY = constraints.maxHeight / 2;
                return Stack(
                  children: [
                    // 收起态：中心呼吸球 + 提示
                    if (!_expanded) _buildHint(scheme, l10n.calendarMemory),
                    for (var i = 0; i < count; i++)
                      _buildBubble(
                          i, count, scheme, centerX, centerY),
                  ],
                );
              },
            );
          },
        ),
      ),
    );
  }

  Widget _buildHint(ColorScheme scheme, String label) {
    final s = 1.0 + _hintBreath.value * 0.06;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Transform.scale(
            scale: s,
            child: Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [
                    scheme.primaryContainer.withValues(alpha: 0.55),
                    scheme.primaryContainer.withValues(alpha: 0.2),
                  ],
                ),
                border: Border.all(
                    color: scheme.primary.withValues(alpha: 0.3), width: 1),
              ),
              child: Icon(Icons.bubble_chart_outlined,
                  color: scheme.primary, size: 28),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            label,
            style: TextStyle(
              fontSize: 11,
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBubble(int index, int count, ColorScheme scheme,
      double centerX, double centerY) {
    // 终点位置：围绕中心的环形布局
    final angle = -math.pi / 2 + (2 * math.pi / count) * index;
    final radius = math.min(centerX, centerY) * 0.62;

    final stagger = index * 0.12;
    final t = Curves.easeOutBack
        .transform((_sprayController.value - stagger).clamp(0.0, 1.0));

    final endX = centerX + math.cos(angle) * radius;
    final endY = centerY + math.sin(angle) * radius;
    final dx = centerX + (endX - centerX) * t;
    final dy = centerY + (endY - centerY) * t;

    // 微微浮动（每颗相位/幅度不同，不阻碍点击）
    final floatY =
        math.sin(_floatController.value * math.pi * 2 + index * 1.3) * 3;

    final size = _bubbleSizes[index];
    final item = widget.items[index];

    // 收起态（动画值≈0）直接不构建气泡，避免隐藏节点仍可被命中/检索
    if (t <= 0.001) return const SizedBox.shrink();

    return Positioned(
      left: dx - size / 2,
      top: dy - size / 2 + floatY,
      child: Opacity(
        opacity: t.clamp(0.0, 1.0),
        child: GestureDetector(
          onTap: t >= 1.0
              ? () {
                  item.onTap();
                }
              : null,
          child: Container(
            width: size,
            height: size,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(
                colors: [
                  scheme.primaryContainer.withValues(alpha: 0.6),
                  scheme.primaryContainer.withValues(alpha: 0.25),
                ],
              ),
              border: Border.all(
                color: scheme.primary.withValues(alpha: 0.3),
                width: 1,
              ),
              boxShadow: [
                BoxShadow(
                  color: scheme.primary.withValues(alpha: 0.1),
                  blurRadius: 8,
                  spreadRadius: 1,
                ),
              ],
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(item.icon, size: size * 0.3, color: scheme.primary),
                const SizedBox(height: 4),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  child: Text(
                    item.label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 10,
                      color: scheme.onSurface.withValues(alpha: 0.8),
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 读取全局「降低模糊」开关，未包裹 Provider（如部分 widget 测试）时按不降级（false）兜底。
bool _maybeReduceBlur(BuildContext context) {
  return Provider.of<SettingsProvider?>(context, listen: false)?.reduceBlur ??
      false;
}
