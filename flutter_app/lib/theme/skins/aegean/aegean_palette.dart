import 'package:flutter/material.dart';

/// 「爱琴海典藏 / Aegean Codex」皮肤色板。
/// 与官网 gh-pages「爱琴海手稿」CSS 变量同源；白昼=羊皮纸，夜阑=星夜手稿。
class AegeanPalette {
  AegeanPalette._();

  // ── 白昼（羊皮纸） ──
  static const Color paperLight = Color(0xFFF3ECDD);
  static const Color paper2Light = Color(0xFFECE2CD);
  static const Color marbleLight = Color(0xFFFAF5EA);
  static const Color inkLight = Color(0xFF2B251B);
  static const Color goldLight = Color(0xFFA9823F);
  static const Color goldDeepLight = Color(0xFF836428);
  static const Color goldPale = Color(0xFFC9AE72); // 两版共用的金发丝
  static const Color terraLight = Color(0xFFB0543A);
  static const Color terraDeepLight = Color(0xFF90402B);
  static const Color olive = Color(0xFF6C7850);
  static const Color aegeanBlue = Color(0xFF35586B);

  // ── 夜阑（星夜手稿） ──
  static const Color paperDark = Color(0xFF131A29);
  static const Color paper2Dark = Color(0xFF1C2740);
  static const Color marbleDark = Color(0xFF202C46);
  static const Color inkDark = Color(0xFFECE3CB);
  static const Color goldDark = Color(0xFFD8BC7E);
  static const Color goldDeepDark = Color(0xFFC9AE72);
  static const Color terraDark = Color(0xFFC96E50);
  static const Color terraDeepDark = Color(0xFFB5573C);

  // ── §6.2 语义常量（追加） ──
  static const Color cream = Color(0xFFF7EEDD);      // 赤陶面上的米白字 / 高光
  static const Color inkBlockLight = Color(0xFF2B251B); // SnackBar 墨块（白昼）
  static const Color inkBlockDark = Color(0xFF0E1424);  // SnackBar 墨块（夜阑）
  // 状态语义（不引入新彩色，统一收进橄榄/赤陶/金体系）
  static const Color ok = Color(0xFF6C7850);         // 成功=橄榄
  static const Color warn = Color(0xFFB0543A);       // 警示=赤陶
  static const Color goldHair = Color(0x73C9AE72);   // 0.45 金发丝

  // ── 公式几何重做追加（纯增量，不改任何现有成员）：背景顶部暖光晕中心色 ──
  // 把原 AegeanPaperPainter 内联的夜晕色 0xFF223050 提为常量；白昼用更暖的米金。
  static const Color glowTopLight = Color(0xFFFBF6EC);
  static const Color glowTopDark = Color(0xFF223050);
  static Color glowTop(Brightness b) => darkOf(b) ? glowTopDark : glowTopLight;

  /// 逻辑字体名：默认走系统衬线（零资源）。若按第 9 节打包了 Cormorant/EB Garamond，
  /// 把 [displayFont] 改为 'Cormorant Garamond'、[bodyLatinFont] 改为 'EB Garamond' 即可。
  static const String displayFont = 'serif';
  static const String bodyLatinFont = 'serif';

  /// 中文衬线回退链（按平台命中系统宋体 / 思源宋体；都没有再回退默认）。
  static const List<String> cnSerifFallback = [
    'Songti SC',
    'Noto Serif SC',
    'Source Han Serif SC',
    'STSong',
    'SimSun',
    'serif',
  ];

  static bool darkOf(Brightness b) => b == Brightness.dark;

  static Color paper(Brightness b) => darkOf(b) ? paperDark : paperLight;
  static Color paper2(Brightness b) => darkOf(b) ? paper2Dark : paper2Light;
  static Color marble(Brightness b) => darkOf(b) ? marbleDark : marbleLight;
  static Color ink(Brightness b) => darkOf(b) ? inkDark : inkLight;
  static Color gold(Brightness b) => darkOf(b) ? goldDark : goldLight;
  static Color goldDeep(Brightness b) => darkOf(b) ? goldDeepDark : goldDeepLight;
  static Color terra(Brightness b) => darkOf(b) ? terraDark : terraLight;
  static Color terraDeep(Brightness b) => darkOf(b) ? terraDeepDark : terraDeepLight;

  /// 金发丝描边（SkinDecoration 无 brightness 入参，故用两版都可见的 goldPale）。
  static const Color frameHair = Color(0x8CC9AE72); // goldPale @ 55%
}
