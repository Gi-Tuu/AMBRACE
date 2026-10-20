import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

import 'text_file_picker_types.dart';

/// Web：真浏览器单文件选择器（`<input type=file>`）。
///
/// 纪律：
/// * 只接受单文件，`accept` 只是过滤提示，**运行时**仍按 [kAllowedTextFileNames] 白名单校验；
/// * 越限/类型不符 ⇒ 本地拒绝（[PickedTextFile.reject]），**0 网络请求**，字节不读入；
/// * 用户取消 ⇒ 返回 null。取消有**两条**通道（返工 R2 补齐）：
///   ① 明确的 `cancel` 事件（现代浏览器在对话框被关闭时于 input 上触发）；
///   ② 缺失 `cancel` 时的兜底：窗口重获焦点后宽限仍无 `change` ⇒ 视为取消。
/// * 收尾清理：input 从 DOM 移除、`change`/`cancel`/`focus` 监听全部摘除、
///   字节读完即交由调用方（不落 localStorage/缓存/URL）。
TextFilePicker createPlatformTextFilePicker() => _WebTextFilePicker();

/// 供 accept 属性的扩展名清单（与白名单同源）。
final String kAllowedTextFileNames = kAllowedTextFileExts.join(',');

class _WebTextFilePicker implements TextFilePicker {
  @override
  Future<PickedTextFile?> pick() {
    final completer = Completer<PickedTextFile?>();
    final input = web.HTMLInputElement()
      ..type = 'file'
      ..multiple = false
      ..accept = kAllowedTextFileNames;
    // 离屏但仍在 DOM 内（部分浏览器要求 input 在文档中才能打开选择器）。
    input.setAttribute('style',
        'position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0;');

    var changeSeen = false;
    var cancelSeen = false;
    JSFunction? focusHandler;

    void cleanup() {
      try {
        input.onchange = null;
        input.oncancel = null; // 返工 R2：取消监听一并摘除（不留悬挂句柄）
      } catch (_) {/* 忽略 */}
      if (focusHandler != null) {
        try {
          web.window.removeEventListener('focus', focusHandler);
        } catch (_) {/* 忽略 */}
        focusHandler = null;
      }
      try {
        input.remove();
      } catch (_) {/* 忽略 */}
    }

    void complete(PickedTextFile? result) {
      if (completer.isCompleted) return;
      completer.complete(result);
      cleanup();
    }

    input.onchange = ((web.Event _) {
      changeSeen = true;
      final files = input.files;
      final f = (files == null || files.length == 0) ? null : files.item(0);
      if (f == null) {
        complete(null);
        return;
      }
      final name = basenameOf(f.name);
      final size = f.size;
      // 先校验类型/体积，**再**读字节（越限 ⇒ 0 读取、0 网络）。
      if (!isAllowedTextFileName(name)) {
        complete(PickedTextFile(
            name: name, size: size, reject: TextFileRejectReason.unsupportedType));
        return;
      }
      if (size > kMaxTextFileBytes) {
        complete(PickedTextFile(
            name: name, size: size, reject: TextFileRejectReason.tooLarge));
        return;
      }
      f.arrayBuffer().toDart.then((JSArrayBuffer buf) {
        final bytes = Uint8List.view(buf.toDart);
        complete(PickedTextFile(name: name, size: bytes.length, bytes: bytes));
      }, onError: (Object _) {
        complete(null);
      });
    }).toJS;

    // 返工 R2：明确的取消事件优先（对话框被关闭 ⇒ 立即静默返回，不等焦点兜底）。
    input.oncancel = ((web.Event _) {
      cancelSeen = true;
      complete(null);
    }).toJS;

    // 取消兜底：选择器关闭后窗口重获焦点；宽限后仍无 change ⇒ 视为取消。
    focusHandler = ((web.Event _) {
      Future<void>.delayed(const Duration(milliseconds: 500), () {
        if (!changeSeen && !cancelSeen) complete(null);
      });
    }).toJS;
    web.window.addEventListener('focus', focusHandler);

    web.document.body?.appendChild(input);
    input.click();
    return completer.future;
  }
}
