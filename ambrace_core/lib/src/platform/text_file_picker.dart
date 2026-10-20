/// 文本文件选择器（条件平台分支入口）。
///
/// * Web（`dart.library.js_interop`）⇒ 真 DOM `<input type=file>` 实现；
/// * 其它平台 ⇒ stub（无可选能力，返回 null；测试可注入自定义实现）。
///
/// 共享类型（[TextFilePicker] / [PickedTextFile] / 白名单与上限）在同目录
/// `text_file_picker_types.dart`，本入口一并导出，调用方只 import 这一个文件。
library;

export 'text_file_picker_stub.dart'
    if (dart.library.js_interop) 'text_file_picker_web.dart';
export 'text_file_picker_types.dart';
