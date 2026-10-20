import 'dart:typed_data';

/// 允许的文本文件扩展名（**仅** txt/md/csv；大小写不敏感）。
///
/// 浏览器 `accept` 只是过滤提示；运行时仍以本白名单为准（不许仅凭重命名扩展名
/// 宣称「识别真实文件格式」）。
const Set<String> kAllowedTextFileExts = {'.txt', '.md', '.csv'};

/// 单文本文件大小上限（与后端 `MAX_FILE_BYTES` 对齐：20MiB）。
const int kMaxTextFileBytes = 20 * 1024 * 1024;

/// 取 basename（去掉目录成分；`/` 与 `\` 都处理）。
String basenameOf(String path) {
  final i = path.lastIndexOf(RegExp(r'[/\\]'));
  return i >= 0 ? path.substring(i + 1) : path;
}

/// 取小写扩展名（含点；无扩展名/以点开头返回 ''）。
String extensionOf(String name) {
  final base = basenameOf(name);
  final i = base.lastIndexOf('.');
  return i <= 0 ? '' : base.substring(i).toLowerCase();
}

/// 扩展名是否在白名单（**只看扩展名**；不看 MIME）。
bool isAllowedTextFileName(String name) =>
    kAllowedTextFileExts.contains(extensionOf(name));

/// 前端本地拒绝原因（双语文案由壳给出；此处只给结构化原因）。
enum TextFileRejectReason {
  /// 扩展名不在白名单
  unsupportedType,

  /// 超过 20MiB 上限
  tooLarge,
}

/// 单文件选择结果。
///
/// * [reject] 非空 ⇒ 前端**本地拒绝**（0 网络请求）；
/// * [bytes] 仅在通过校验并成功读入时非空（字节只在内存，不落 localStorage/缓存/URL）。
class PickedTextFile {
  const PickedTextFile({
    required this.name,
    required this.size,
    this.bytes,
    this.reject,
  });

  /// basename（保留用户原名，不翻译、不改写）。
  final String name;

  /// 字节数（来自浏览器 `File.size`；拒绝时也保留，供提示「已超过上限」）。
  final int size;

  /// 读入的字节；仅 [accepted] 时非空。
  final Uint8List? bytes;

  /// 非空 ⇒ 被本地拒绝（[TextFileRejectReason]）。
  final TextFileRejectReason? reject;

  /// 是否通过本地校验（可发送）。
  bool get accepted => reject == null && bytes != null;
}

/// 单文件文本选择器接口（平台无关；Web 走 DOM `<input type=file>`，
/// VM/测试走 stub 或壳注入实现）。
abstract interface class TextFilePicker {
  /// 打开文件选择器。
  ///
  /// 用户取消 ⇒ 返回 `null`（**静默**，调用方不得因此发请求）。
  Future<PickedTextFile?> pick();
}
