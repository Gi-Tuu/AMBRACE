import 'text_file_picker_types.dart';

/// 非 Web 平台：无浏览器文件选择能力（返回 null，不伪造选择结果）。
///
/// 手机壳/VM 若需选文件，应由壳注入自己的 [TextFilePicker] 实现；
/// 本 stub 只保证「不引入原生依赖」且不制造假成功。
TextFilePicker createPlatformTextFilePicker() => const _UnsupportedTextFilePicker();

class _UnsupportedTextFilePicker implements TextFilePicker {
  const _UnsupportedTextFilePicker();

  @override
  Future<PickedTextFile?> pick() async => null;
}
