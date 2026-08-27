/// 皮肤字体体系（v1 仅枚举三大类，供设置页/主题使用）。
class SkinTypography {
  const SkinTypography._(this.name);

  /// 字体体系名称（可用于 l10n/font 选择）
  final String name;

  /// 系统默认字体
  static const SkinTypography system = SkinTypography._('system');

  /// 衬线体（纸艺/复古）
  static const SkinTypography serif = SkinTypography._('serif');

  /// 圆润体（温柔陪伴）
  static const SkinTypography rounded = SkinTypography._('rounded');
}
