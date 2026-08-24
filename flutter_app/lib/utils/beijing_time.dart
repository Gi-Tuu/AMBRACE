/// 后端时间统一存 UTC（naive，如 2026-08-04T12:47:29.123 或空格分隔）。
/// 展示统一转北京时间（UTC+8），跨日自动进位。
String formatBeijingTime(String isoTime) => formatInTz(isoTime, offset: 8);

/// 北京时间 HH:mm 短格式（消息气泡左下角时间），跨日自动进位。
String formatTimeOnly(String isoTime) {
  if (isoTime.isEmpty || isoTime.length < 19) return isoTime;
  try {
    final dt = DateTime.parse(isoTime.substring(0, 19));
    final shifted = dt.add(const Duration(hours: 8));
    final hh = shifted.hour.toString().padLeft(2, "0");
    final mm = shifted.minute.toString().padLeft(2, "0");
    return "$hh:$mm";
  } catch (_) {
    return isoTime;
  }
}

/// 按指定时区偏移（小时）格式化 UTC naive 时间字符串，返回 `YYYY-MM-DD HH:mm`。
/// 用于朋友圈等按"作者所在地区"显示时间的场景。
String formatInTz(String isoTime, {int offset = 8}) {
  if (isoTime.isEmpty || isoTime.length < 19) return isoTime;
  try {
    final dt = DateTime.parse(isoTime.substring(0, 19));
    final shifted = dt.add(Duration(hours: offset));
    final y = shifted.year.toString().padLeft(4, "0");
    final mo = shifted.month.toString().padLeft(2, "0");
    final d = shifted.day.toString().padLeft(2, "0");
    final hh = shifted.hour.toString().padLeft(2, "0");
    final mm = shifted.minute.toString().padLeft(2, "0");
    return "$y-$mo-$d $hh:$mm";
  } catch (_) {
    return isoTime;
  }
}
