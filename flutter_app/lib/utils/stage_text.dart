/// 非对话文本（动作/神态/细节）解析器 —— 私聊沉浸感 v2.0.0。
///
/// 规则（2026-08-08 拍板）：
/// - 识别全角「（）」与半角「()」，成对、内部无嵌套、长度 <= 80 字 → 动作/神态块；
/// - 开头块 → 气泡上方小字；结尾块 → 气泡下方小字；中间/多段 → 合并到上方；
/// - 正文只保留剥离后的对话文本；聊天记录箱仍存含括号原文（本工具仅用于展示层）。
/// - 2026-08-14：识别【状态更新：…】/【CAL_NOTE】…/【MEMO】… 标记行 → markers 小字（气泡下方）。
class StageText {
  /// 剥离后的对话正文（展示用）
  final String text;

  /// 上方小字段（前导 + 中间，按原文顺序，含括号）
  final List<String> above;

  /// 下方小字段（收尾，含括号）
  final List<String> below;

  /// 标记行小字（2026-08-14：状态更新/日历备注/备忘，从正文剥离）
  final List<String> markers;

  const StageText({
    required this.text,
    this.above = const [],
    this.below = const [],
    this.markers = const [],
  });

  bool get hasStage => above.isNotEmpty || below.isNotEmpty;

  /// 合并展示文本（多段按原文顺序拼接，无额外空格）
  String get aboveLine => above.join('');
  String get belowLine => below.join('');

  static const int maxLen = 80;

  static StageText parse(String raw) {
    if (raw.isEmpty) return const StageText(text: '');
    // 标记行剥离（2026-08-14）：状态更新/CAL_NOTE/MEMO → markers，正文移除
    final markers = _scanMarkers(raw);
    var src = raw;
    if (markers.isNotEmpty) {
      final sb = StringBuffer();
      var pos = 0;
      for (final it in markers) {
        sb.write(src.substring(pos, it.start));
        pos = it.end;
      }
      sb.write(src.substring(pos));
      src = sb.toString();
    }
    final markerTexts = markers.map((m) => m.text).toList();

    final blocks = _scan(src);
    if (blocks.isEmpty) {
      var text = src.replaceAll(RegExp(r'\s{2,}'), ' ').trim();
      return StageText(text: text, markers: markerTexts);
    }

    final leadEnd = _leadingEnd(src, blocks);      // 前导组结束（blocks 下标，不含）
    final trailStart = _trailingStart(src, blocks); // 收尾组开始（blocks 下标）

    final above = <String>[];
    final below = <String>[];
    for (var i = 0; i < blocks.length; i++) {
      final seg = src.substring(blocks[i].start, blocks[i].end);
      if (i >= trailStart && i >= leadEnd) {
        below.add(seg);
      } else {
        above.add(seg); // 前导 + 中间
      }
    }

    // 正文：去掉所有块后拼接，压缩多余空白
    final sb = StringBuffer();
    var pos = 0;
    for (final b in blocks) {
      sb.write(src.substring(pos, b.start));
      pos = b.end;
    }
    sb.write(src.substring(pos));
    var text = sb.toString().replaceAll(RegExp(r'\s{2,}'), ' ').trim();

    return StageText(text: text, above: above, below: below, markers: markerTexts);
  }

  /// 剥离后正文的摘录（引用用，<=100 字）
  static String excerpt(String raw, {int max = 100}) {
    final t = parse(raw).text;
    return t.length <= max ? t : t.substring(0, max);
  }

  static bool _onlySpaces(String s) => s.trim().isEmpty;

  /// 标记行扫描：状态更新/CAL_NOTE/MEMO（兼容中文括号、闭合标签可省略）
  static final RegExp _suMark =
      RegExp(r'[\[【]\s*状态更新\s*[：:]([^\n\]】]*?)[\]】]');
  static final RegExp _calMark = RegExp(
    r'[\[【]\s*CAL_NOTE\s*[\]】]\s*([^\n]*?)(?:[\[【]\s*/CAL_NOTE\s*[\]】]|$)',
    multiLine: true,
  );
  static final RegExp _memoMark = RegExp(
    r'[\[【]\s*MEMO\s*[\]】]\s*([^\n]*?)(?:[\[【]\s*/MEMO\s*[\]】]|$)',
    multiLine: true,
  );

  static List<_StripItem> _scanMarkers(String raw) {
    final items = <_StripItem>[];
    void collect(RegExp re, String label) {
      for (final m in re.allMatches(raw)) {
        final content = m.group(1)?.trim() ?? '';
        if (content.isEmpty) continue;
        items.add(_StripItem(start: m.start, end: m.end, text: '$label$content'));
      }
    }

    collect(_suMark, '状态更新：');
    collect(_calMark, '日历：');
    collect(_memoMark, '备忘：');
    items.sort((a, b) => a.start.compareTo(b.start));
    // 去除重叠（同一行不可能，防意外）
    final merged = <_StripItem>[];
    for (final it in items) {
      if (merged.isNotEmpty && it.start < merged.last.end) continue;
      merged.add(it);
    }
    return merged;
  }

  /// 前导组结束下标：第一个块必须在开头（前面只有空白），且块间只隔空白
  static int _leadingEnd(String raw, List<_Block> blocks) {
    final first = blocks.first;
    if (!_onlySpaces(raw.substring(0, first.start))) return 0;
    var end = 1;
    while (end < blocks.length) {
      if (!_onlySpaces(raw.substring(blocks[end - 1].end, blocks[end].start))) break;
      end++;
    }
    return end;
  }

  /// 收尾组开始下标：最后一个块必须在结尾（后面只有空白），且块间只隔空白
  static int _trailingStart(String raw, List<_Block> blocks) {
    final last = blocks.last;
    if (!_onlySpaces(raw.substring(last.end))) return blocks.length;
    var start = blocks.length - 1;
    while (start > 0) {
      if (!_onlySpaces(raw.substring(blocks[start - 1].end, blocks[start].start))) break;
      start--;
    }
    return start;
  }

  static List<_Block> _scan(String raw) {
    final blocks = <_Block>[];
    var i = 0;
    while (i < raw.length) {
      final ch = raw[i];
      if (ch != '（' && ch != '(') {
        i++;
        continue;
      }
      final closeCh = ch == '（' ? '）' : ')';
      final closeIdx = raw.indexOf(closeCh, i + 1);
      if (closeIdx == -1) {
        i++;
        continue;
      }
      final inner = raw.substring(i + 1, closeIdx);
      // 合法：非空、长度上限、内部无任何括号（拒绝嵌套）
      final hasNested =
          inner.contains('（') || inner.contains('）') || inner.contains('(') || inner.contains(')');
      if (inner.isNotEmpty && inner.length <= maxLen && !hasNested) {
        blocks.add(_Block(start: i, end: closeIdx + 1));
        i = closeIdx + 1;
      } else {
        i++;
      }
    }
    return blocks;
  }
}

class _StripItem {
  final int start;
  final int end;
  final String text;
  const _StripItem({required this.start, required this.end, required this.text});
}

class _Block {
  final int start;
  final int end;
  const _Block({required this.start, required this.end});
}
