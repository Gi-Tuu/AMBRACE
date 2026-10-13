/// 前后台心跳的**载荷契约**（A53，2026-10-13 修订）。
///
/// 为什么单独抽出来：发送侧（主 isolate `main.dart`）原先把 `lifecycleState?.name` 直接塞进
/// `{'state': ...}`，收到 `.name` 拿到的是短名 `"resumed"`；接收侧（后台 isolate）却拿
/// 长名 `"AppLifecycleState.resumed"` 比较 ⇒ 两端**永远匹配不上**，`_lastResumedAckMs` 恒为 0，
/// 心跳这条 fail-closed 信号一次也没生效过（前台时后台 isolate 照样弹系统通知）。
///
/// 字符串枚举名跨 isolate 传本来就是多余的中间层：后台只想知道「此刻是不是 resumed」这一个 bool。
/// 于是契约收敛成 `{'resumed': bool}`，编解码都放进这个纯 helper，两端调同一个函数，
/// 名字再也不会各写各的。判定规则本体仍在 `foreground_state.dart`（10s 窗口、双信号），此处不改。
library;

import 'dart:ui';

/// 后台 → 主 isolate 的探测事件名。
const String kForegroundProbeEvent = 'fgProbe';

/// 主 isolate → 后台 的回答事件名。
const String kForegroundAckEvent = 'fgAck';

/// 契约载荷里唯一的键：主 isolate **当时刻**是否处于 `resumed`。
const String kForegroundResumedKey = 'resumed';

/// 发送侧编码：只有 `AppLifecycleState.resumed` 才写 `true`；
/// inactive/hidden/paused/detached/null 全部写 `false`（不新增事件字符串协议）。
Map<String, bool> foregroundAckPayload(AppLifecycleState? state) =>
    {kForegroundResumedKey: state == AppLifecycleState.resumed};

/// 接收侧解析：**只认 bool true**。
/// `false`、缺键、载荷不是 Map、值是字符串 `"true"`、值是数字 `1` —— 一律返回 false，
/// 保持「答不上来就算后台」的 fail-closed 语义（宽松接受等于把 bug 换个形状留下）。
bool isResumedAck(Object? payload) {
  if (payload is! Map) return false;
  return payload[kForegroundResumedKey] == true;
}
