import 'dart:typed_data';

/// 非浏览器环境（测试/VM）兜底：不支持选图。
Future<Uint8List?> pickAvatarBytes() async => null;
