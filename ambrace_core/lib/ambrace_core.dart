/// AMBRACE 共享核心包（跨端：手机 App / PWA 薄壳）。
///
/// 单一真相纪律：模型、API 传输、身份（验签）判定的**唯一权威实现**都在本包；
/// 手机壳与 Web 壳都通过 path 依赖引用本包，不各自复制一份业务实现。
///
/// 本包**不得** import 任何移动/桌面原生能力（dart:io、path_provider、文件选择、
/// 录音、Shizuku、FFI、后台服务、flutter_secure_storage…）。平台相关的存取一律走
/// 接口 + 壳注入（见 src/platform/）。这样 Web 构建的真实依赖图里不会拖进原生实现。
library;

export 'src/api/amb_api_client.dart';
export 'src/api/api_exception.dart';
export 'src/api/identity.dart';
export 'src/attachment/attachment_api.dart';
export 'src/attachment/upload_progress.dart';
export 'src/character_management/character_editor_api.dart';
export 'src/character_management/character_editor_models.dart';
export 'src/chat/chat_transport.dart';
export 'src/chat/session_models.dart';
export 'src/editor/editor_api.dart';
export 'src/l10n/core_l10n.dart';
export 'src/l10n/error_l10n.dart';
export 'src/models/character.dart';
export 'src/models/chat_message.dart';
export 'src/platform/key_value_store.dart';
export 'src/platform/text_file_picker.dart';
export 'src/reader/reader_api.dart';
export 'src/reader/reader_models.dart';
