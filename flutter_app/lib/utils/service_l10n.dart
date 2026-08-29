/// 服务层本地化辅助：无 BuildContext 的 Service/Provider 使用
///
/// 用法：
///   final l10n = ServiceL10n(await appLang());
///   l10n.notifChannelMessages;
///
/// 注意：仅用于 Service/Provider 等无法获取 BuildContext 的场景。
/// UI 页面仍应使用 AppLocalizations.of(context)!。
library;

class ServiceL10n {
  final String lang; // 'zh' | 'en'
  const ServiceL10n(this.lang);

  bool get isEn => lang == 'en';
  String get t => isEn ? 'en' : 'zh';

  // ===== 通知渠道 =====

  String get notifChannelBackground =>
      isEn ? 'AMBRACE Background Service' : '拥爱后台服务';

  String get notifChannelBackgroundDesc => isEn
      ? 'Persistent notification for background message polling'
      : '后台轮询 AI 好友新消息的常驻通知';

  String get notifRunningTitle => isEn ? 'AMBRACE Running' : '拥爱运行中';

  String get notifRunningDesc => isEn
      ? 'Listening for new messages in the background'
      : '正在后台监听 AI 好友的新消息';

  String get notifChannelMessages => isEn ? 'Chat Messages' : '聊天消息';

  String get notifChannelMessagesDesc =>
      isEn ? 'New message notifications from AI friends' : 'AI 好友的新消息通知';

  String get notifChannelAlert => isEn ? 'Important Alerts' : '重要提醒';

  String get notifChannelAlertDesc =>
      isEn ? 'Important notifications like check-ins' : '查岗等重要通知';

  // ===== Chat / Provider 错误 =====

  String get streamSendFailed =>
      isEn ? 'Stream send failed' : '流式发送失败';

  String get imageSendFailed => isEn ? 'Image send failed' : '图片发送失败';

  String get emojiSendFailed => isEn ? 'Emoji send failed' : '表情发送失败';

  String get noResponseFallback =>
      isEn ? 'They did not respond for now' : 'TA 暂时没有回应你';

  String get charNotFoundOrDeleted =>
      isEn ? 'This character does not exist or has been deleted' : '该角色不存在或已被删除';

  String get connFailedRetry =>
      isEn ? 'Connection failed, please check your network and try again' : '连接失败，请检查网络后重试';

  String get scopeAbility => isEn ? 'Ability' : '能力';

  String get actionTarget => isEn ? 'Target' : '目标';

  String get gameTimeoutMsg => isEn
      ? 'Loading timed out, check your network and try again'
      : '加载超时，请检查网络后再试';

  String get emotionWave => isEn ? 'Emotional Wave' : '情绪波动';

  String get defaultNickname => isEn ? 'User' : '用户';

  String get cooldown => isEn ? 'Cooldown' : '冷却';

  String get unknownDetail => isEn ? 'Unknown' : '不详';

  String get appTitle => isEn ? 'AMBRACE' : '拥爱';

  String get aiFriend => isEn ? 'AI Friend' : 'AI 好友';

  // 带参数的错误消息
  String streamSendFailedErr(dynamic e) =>
      isEn ? 'Stream send failed: $e' : '流式发送失败: $e';

  String imageSendFailedErr(dynamic e) =>
      isEn ? 'Image send failed: $e' : '图片发送失败: $e';

  String fileSendFailedErr(dynamic e) =>
      isEn ? 'File send failed: $e' : '文件发送失败: $e';

  String voiceSendFailedErr(dynamic e) =>
      isEn ? 'Voice send failed: $e' : '语音发送失败: $e';

  String emojiSendFailedErr(dynamic e) =>
      isEn ? 'Emoji send failed: $e' : '表情发送失败: $e';

  String deleteFailedErr(dynamic e) =>
      isEn ? 'Delete failed: $e' : '删除失败: $e';

  String connectFailedPrefix([dynamic e]) =>
      isEn ? 'Connection failed${e != null ? ': $e' : ''}' : '连接失败${e != null ? ': $e' : ''}';
}
