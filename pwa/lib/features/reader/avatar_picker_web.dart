import 'dart:async';
import 'dart:js_interop';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

/// 浏览器：弹出文件选择框，返回所选图片的原始字节（取消/失败返回 null）。
/// 说明：网页版不做客户端裁剪，服务端按原图保存；显示时由圆形头像裁切。
Future<Uint8List?> pickAvatarBytes() async {
  final input = web.HTMLInputElement()
    ..type = 'file'
    ..accept = 'image/*'
    ..style.display = 'none';
  web.document.body?.append(input);
  final completer = Completer<Uint8List?>();

  void finish(Uint8List? bytes) {
    input.remove();
    if (!completer.isCompleted) completer.complete(bytes);
  }

  input.onchange = ((web.Event event) {
    final file = input.files?.item(0);
    if (file == null) {
      finish(null);
      return;
    }
    file.arrayBuffer().toDart.then((buffer) {
      finish(buffer.toDart.asUint8List());
    }).catchError((_) => finish(null));
  }).toJS;

  input.click();
  return completer.future;
}
