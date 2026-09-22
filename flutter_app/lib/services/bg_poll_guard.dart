/// 后台会话骨架的两条纯逻辑守卫（不 import 任何插件，便于单测）。
///
/// 对应 X7 本体重估 §1 里会话层仅剩的说不清处：
/// - **S5 轮询重入保护**：`Timer.periodic` 回调既不 await 也没有在途标志，
///   单轮耗时超过 15s（弱网下多个未读的 5s/10s 超时叠加）就会两轮重叠，
///   交错改写 `_sessionMap` / `_lastCounts` 等共享状态。
/// - **S7 长连接跟随**：WS 的服务器地址与 token 只在 `onStart` 捕获一次，
///   此后换账号 / 换服务器（`server_url` 变更）WS 仍连着旧地址旧 token，
///   只能靠杀进程重开。
library;

/// S5：本轮定时轮询是否应当跳过。
///
/// 上一轮尚未结束时（[inFlight] 为 `true`）跳过本轮，保证任意时刻只有一轮
/// `_pollOnce` 在跑；调用方负责在置位 / 复位之间 `await` 整轮。
bool shouldSkipPoll({required bool inFlight}) => inFlight;

/// S7：长连接是否需要按「当前生效」的地址 / token 重建。
///
/// 入参 [curServerUrl] / [curToken] 是本轮轮询刚读出的新值，
/// [wsServerUrl] / [wsToken] 是 WS 当前实际使用的值，
/// [wsActive] 表示 WS 客户端是否已存在。
///
/// - 当前未启动：只要新地址与新 token 都非空就该启动（此前可能因缺 token /
///   地址被守卫拦下）；任一为空则维持不启动。
/// - 当前已启动：地址或 token 与新值不一致就重建；token 变空（登出）也算不一致，
///   调用方会 `dispose` 旧连接并按既有守卫不再新建。
bool needWsRestart({
  required String curServerUrl,
  required String curToken,
  required String wsServerUrl,
  required String wsToken,
  required bool wsActive,
}) {
  if (!wsActive) return curServerUrl.isNotEmpty && curToken.isNotEmpty;
  return curServerUrl != wsServerUrl || curToken != wsToken;
}
