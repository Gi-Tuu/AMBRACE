/// 皮肤动效体系（v1 仅枚举三大档位，供页面转场/交互动效选择）。
class SkinAnimation {
  const SkinAnimation._(this.name);

  /// 动效档位名称
  final String name;

  /// 标准动效
  static const SkinAnimation standard = SkinAnimation._('standard');

  /// 弹性动效（温柔陪伴）
  static const SkinAnimation elastic = SkinAnimation._('elastic');

  /// 敏捷动效（暗夜霓虹）
  static const SkinAnimation snappy = SkinAnimation._('snappy');
}
