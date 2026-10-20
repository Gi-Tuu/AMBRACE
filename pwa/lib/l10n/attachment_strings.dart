/// 文本附件（M1a-4）PWA 侧文案表：zh/en 成对，模式与 SessionStrings / EditorStrings 一致。
/// 只承载**系统文案**；文件名/文件内容属用户内容，一律原文展示（不翻译）。
class AttachmentStrings {
  const AttachmentStrings(this.lang);

  final String lang;

  static const Map<String, Map<String, String>> _t = {
    'zh': {
      'pick': '选择文本文件',
      'onlyText': '仅支持 .txt / .md / .csv（≤20MB）',
      'rejectedType': '不支持的文件类型（仅 .txt / .md / .csv）',
      'rejectedSize': '文件超过 20MB 上限',
      'remove': '移除',
      'send': '发送附件',
      'phaseReady': '待发送',
      'phaseUploading': '上传中',
      'phaseWaiting': '等待服务器处理…',
      'phaseSent': '已发送',
      'phaseUnconfirmed': '结果未确认，请核对原会话历史',
      'phaseFailed': '附件发送失败',
      'phaseCancelled': '已取消本地等待（服务器可能已收到）',
      'cancelWait': '取消等待',
      'verifyHistory': '核对历史',
      'sizeUnknown': '大小未知',
      'fileUnavailable': '附件已失效或不可用',
      'fileUnknownMeta': '附件信息不完整',
      'needSession': '请先选择会话再发送附件',
      'goBackVerify': '请回到该会话后点「核对历史」',
      'retrySend': '重新选择后再发送',
    },
    'en': {
      'pick': 'Choose text file',
      'onlyText': 'Only .txt / .md / .csv (≤20MB)',
      'rejectedType': 'Unsupported file type (only .txt / .md / .csv)',
      'rejectedSize': 'File exceeds the 20MB limit',
      'remove': 'Remove',
      'send': 'Send attachment',
      'phaseReady': 'Ready to send',
      'phaseUploading': 'Uploading',
      'phaseWaiting': 'Waiting for server…',
      'phaseSent': 'Sent',
      'phaseUnconfirmed': 'Result unconfirmed; verify the original session history',
      'phaseFailed': 'Failed to send attachment',
      'phaseCancelled': 'Local wait cancelled (server may have received it)',
      'cancelWait': 'Cancel wait',
      'verifyHistory': 'Verify history',
      'sizeUnknown': 'Unknown size',
      'fileUnavailable': 'File unavailable or expired',
      'fileUnknownMeta': 'Incomplete attachment info',
      'needSession': 'Select a session before attaching',
      'goBackVerify': 'Return to that session, then tap "Verify history"',
      'retrySend': 'Pick the file again to resend',
    },
  };

  String t(String key) {
    final m = _t[lang] ?? _t['en']!;
    return m[key] ?? _t['en']![key] ?? key;
  }

  /// 仅测试用：暴露成对表（断言 zh/en key 完全对齐）。
  static Map<String, Map<String, String>> get debugTables => _t;
}
