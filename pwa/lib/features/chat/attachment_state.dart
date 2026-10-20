import 'package:ambrace_core/ambrace_core.dart';

/// 待发附件阶段（M1a-4）：明确区分「本地上传」与「等待服务器处理」，
/// 100% 只代表上传阶段结束，**不代表**服务器已处理完成或已收到可解析响应。
enum AttachPhase {
  /// 已选好、等待用户明确点「发送」
  ready,

  /// 真实上行中（progress 来自 xhr.upload）
  uploading,

  /// 上传阶段已结束、等待服务器处理/回复
  waiting,

  /// 已收到完整可解析响应并落 UI
  sent,

  /// 可能已上行/落库但结果未知（超时/断网/服务失败/取消）——**不**自动重发
  unconfirmed,

  /// 明确失败（未发请求的本地校验失败等）
  failed,

  /// 用户取消本地等待（服务器可能已写入）
  cancelled,
}

/// 待发附件（单文件；绑定发起时的角色 + 会话，切换会话即作废）。
class PendingAttachment {
  const PendingAttachment({
    required this.file,
    required this.characterId,
    required this.sessionId,
    this.phase = AttachPhase.ready,
    this.loaded = 0,
    this.total = 0,
  });

  final PickedTextFile file;
  final int characterId;

  /// 发起时锁定的会话 id（发送前用于归属校验。
  final int sessionId;

  final AttachPhase phase;

  /// 已发送字节（来自 wire 上行进度）。
  final int loaded;

  /// 总字节；`<= 0` ⇒ 未知（不定进度）。
  final int total;

  /// 是否处于「请求在飞」状态（上传中 / 等待处理）。
  bool get inFlight =>
      phase == AttachPhase.uploading || phase == AttachPhase.waiting;

  /// 进度比例；`total <= 0`（未知）⇒ null（**不**凭文件大小伪算）。
  double? get fraction {
    if (total <= 0) return null;
    final f = loaded / total;
    if (f.isNaN || f.isInfinite) return null;
    return f.clamp(0.0, 1.0);
  }

  PendingAttachment copyWith({AttachPhase? phase, int? loaded, int? total}) =>
      PendingAttachment(
        file: file,
        characterId: characterId,
        sessionId: sessionId,
        phase: phase ?? this.phase,
        loaded: loaded ?? this.loaded,
        total: total ?? this.total,
      );
}

/// 阶段 → 文案 key（AttachmentStrings）。
String attachPhaseKey(AttachPhase p) => switch (p) {
      AttachPhase.ready => 'phaseReady',
      AttachPhase.uploading => 'phaseUploading',
      AttachPhase.waiting => 'phaseWaiting',
      AttachPhase.sent => 'phaseSent',
      AttachPhase.unconfirmed => 'phaseUnconfirmed',
      AttachPhase.failed => 'phaseFailed',
      AttachPhase.cancelled => 'phaseCancelled',
    };

/// 人类可读字节数（用于待发卡的本地已知大小；历史消息的大小可能是展示字符串，不套用本函数）。
String humanSize(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
  return '${(bytes / 1024 / 1024).toStringAsFixed(1)} MB';
}
