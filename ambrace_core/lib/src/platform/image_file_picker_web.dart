import 'package:web/web.dart' as web;
import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

import 'image_file_picker_types.dart';

const String kAllowedImageNames = '.jpg,.jpeg,.png,.webp,.gif';

class _WebImageFilePicker implements ImageFilePicker {
  @override
  Future<PickedImageFile?> pick() {
    final completer = Completer<PickedImageFile?>();
    final input = web.HTMLInputElement()
      ..type = 'file'
      ..multiple = false
      ..accept = kAllowedImageNames;
    input.setAttribute('style',
        'position:fixed;left:-10000px;top:0;width:1px;height:1px;opacity:0;');
    var changeSeen = false;
    var cancelSeen = false;
    JSFunction? focusHandler;

    void cleanup() {
      try {
        input.onchange = null;
        input.oncancel = null;
      } catch (_) {}
      if (focusHandler != null) {
        try {
          web.window.removeEventListener('focus', focusHandler);
        } catch (_) {}
        focusHandler = null;
      }
      try {
        input.remove();
      } catch (_) {}
    }

    void complete(PickedImageFile? result) {
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
      final name = imageBasename(f.name);
      final size = f.size;
      if (!isAllowedImageName(name)) {
        complete(PickedImageFile(
            name: name, size: size, reject: ImageRejectReason.unsupportedType));
        return;
      }
      if (size > kMaxImageBytes) {
        complete(PickedImageFile(
            name: name, size: size, reject: ImageRejectReason.tooLarge));
        return;
      }
      f.arrayBuffer().toDart.then((JSArrayBuffer buf) {
        final bytes = Uint8List.view(buf.toDart);
        complete(PickedImageFile(name: name, size: bytes.length, bytes: bytes));
      }, onError: (Object _) => complete(null));
    }).toJS;

    input.oncancel = ((web.Event _) {
      cancelSeen = true;
      complete(null);
    }).toJS;
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

ImageFilePicker createPlatformImageFilePicker() => _WebImageFilePicker();
