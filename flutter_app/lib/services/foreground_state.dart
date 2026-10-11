/// 前后台判定的纯规则（A53，2026-10-11）。
///
/// 为什么要单独抽出来：判定原本只剩一个内存标志——后台 isolate 的 `_appInForeground` 初值 `true`，
/// 唯一翻成 `false` 的途径是主 isolate 在 `onHide` 里发的那**一条** invoke。`onHide` 触发时主
/// isolate 已经被系统冻结，消息可能根本没送到 ⇒ 标志永久停在 `true`，后台三条通知路径
/// （轮询私聊／群 @／WS 推送）全部 `!inForeground` 恒假，于是**前台不弹、后台也不弹**。
///
/// 新规则把「听推送」改成「问一次，没人回答就算后台」（fail-closed）：后台每 5 秒探测，
/// 只有拿到主 isolate **当时刻**回答的 `resumed` 状态、且回答没过期，才判在前台。
/// 生命周期事件仍然保留（它负责 0 延迟立刻切换），心跳只负责「漏了也能自愈」。
///
/// 抽成纯函数是为了能在 `flutter test` 里跑：「后台 isolate 被系统冻结」这件事本机测不到，
/// 但「回答一过期就必须判后台」这条判据必须可验，否则改完只是自欺。
library;

/// 回答的有效窗口：探测周期是 5s，这里给两倍，容忍一次调度抖动。
const int kForegroundAckWindowMs = 10000;

/// 生命周期事件说在前台（旧信号，仍然要，用来做到「回前台立刻停止后台弹窗」）。
/// 判定＝两票都过才算前台：**任一**信号说不在前台，就判不在前台。
bool foregroundFromSignals({
  required bool lifecycleSaysForeground,
  required int nowMs,
  required int lastResumedAckMs,
  int windowMs = kForegroundAckWindowMs,
}) {
  if (!lifecycleSaysForeground) return false;
  if (lastResumedAckMs <= 0) return false; // 从没回答过（开机自启那一侧）＝判后台
  return nowMs - lastResumedAckMs <= windowMs;
}
