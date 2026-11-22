import 'dart:typed_data';

const Set<String> kAllowedImageExts = {'.jpg', '.jpeg', '.png', '.webp', '.gif'};
const int kMaxImageBytes = 20 * 1024 * 1024;

String imageBasename(String path) {
  final i = path.lastIndexOf(RegExp(r'[/\\]'));
  return i >= 0 ? path.substring(i + 1) : path;
}

String imageExtension(String name) {
  final base = imageBasename(name);
  final i = base.lastIndexOf('.');
  return i <= 0 ? '' : base.substring(i).toLowerCase();
}

bool isAllowedImageName(String name) => kAllowedImageExts.contains(imageExtension(name));

enum ImageRejectReason { unsupportedType, tooLarge }

class PickedImageFile {
  const PickedImageFile({
    required this.name,
    required this.size,
    this.bytes,
    this.reject,
  });

  final String name;
  final int size;
  final Uint8List? bytes;
  final ImageRejectReason? reject;
  bool get accepted => reject == null && bytes != null;
}

abstract interface class ImageFilePicker {
  Future<PickedImageFile?> pick();
}
