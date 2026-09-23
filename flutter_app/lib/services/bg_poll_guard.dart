/// 后台会话骨架的两条纯逻辑守卫（不 import 任何插件，便于单测）。
///
/// 对应 X7 本体重估 §1 里会话层仅剩的说不清处：
/// - **S5 轮询重入保护**：`Timer.periodic` 回调既不 await 也没有在途标志，
///   单轮耗时超过 15s（弱网下多个未读的 5s/10s 超时叠加）就会两轮重叠，
///   交错改写 `_sessionMap` / `_lastCounts` 等共享状态。
/// - **S7 长连接跟随**：WS 的服务器地址与 token 只在 `onStart` 捕获一次，
///   此后换账号 / 换服务器（`server_url` 变更）WS 仍连着旧地址旧 token，
///   只能靠杀进程重开。
/// - **S6 退避抖动与心跳探活**：通知 WS 的指数退避原来完全确定（多端同时断线会
///   同时重连），且半开连接只能等 TCP 超时才发现。这里给出两条纯逻辑：退避抖动
///   （±20%，随机源可注入）与心跳探活判定（服务端支持 `{"type":"ping"}` →
///   `{"type":"pong"}`），由 `background_ws_client.dart` 调用。
library;

import "dart:math" as math;

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

// ── S6：退避抖动 + 心跳探活（2026-09-22 P8）──────────────────────────────

/// 退避抖动比例：±20%（多端/多账号错峰重连，避免同时打回服务端）。
const double kBackoffJitterRatio = 0.2;

/// 心跳间隔：连接建立后每 30s 发一次 `{"type":"ping"}`（服务端回 `{"type":"pong"}`）。
const Duration kHeartbeatInterval = Duration(seconds: 30);

/// 连续多少个心跳周期（每周期都发过 ping）收不到任何数据即判定半开连接。
/// 2 × 30s = 60s，比等 TCP 超时快得多。
const int kHeartbeatMissLimit = 2;

/// 把退避基数 [base] 按 ±[ratio] 抖动，[unit] 是 `[0,1)` 的随机数（纯函数，可测）：
/// `unit = 0` → 下限 `base × (1 - ratio)`；`unit = 1` → 上限 `base × (1 + ratio)`。
///
/// 越界的 [unit] 一律钳到边界（`+∞` → 上限、负值/`-∞`/`NaN` → 下限），非正或非有限的
/// [ratio] 按 0 处理：结果一定落在 `[base × (1 - ratio), base × (1 + ratio)]` 内，且不会为负。
Duration jitterBackoff(Duration base, double unit, {double ratio = kBackoffJitterRatio}) {
  final r = (ratio.isFinite && ratio > 0) ? (ratio > 1 ? 1.0 : ratio) : 0.0;
  final u = unit.isNaN ? 0.0 : (unit <= 0 ? 0.0 : (unit >= 1 ? 1.0 : unit));
  final factor = 1 + r * (2 * u - 1);
  final ms = (base.inMilliseconds * factor).round();
  return Duration(milliseconds: ms < 0 ? 0 : ms);
}

/// 退避抖动器：默认取进程级 `math.Random`；测试可注入固定随机源（返回 `[0,1)`）。
class BackoffJitter {
  BackoffJitter({this.ratio = kBackoffJitterRatio, double Function()? random})
      : _random = random ?? _defaultRandom;

  final double ratio;
  final double Function() _random;

  /// 抖动后的退避时长；[ratio] ≤ 0 时原样返回（不消费随机数）。
  Duration apply(Duration base) =>
      ratio <= 0 ? base : jitterBackoff(base, _random(), ratio: ratio);

  /// 无抖动实例（需要精确时序的调用方用）。
  static BackoffJitter get none => BackoffJitter(ratio: 0);
}

final math.Random _defaultRandomSource = math.Random();

double _defaultRandom() => _defaultRandomSource.nextDouble();

/// 心跳周期是否该发 ping 探活：本周期一条数据都没收到（含服务端 pong）就该探。
bool shouldPingOnHeartbeatTick({required int dataSinceLastTick}) => dataSinceLastTick <= 0;

/// 半开连接判定：连续 [missCount] 个周期都发过 ping 且仍无任何数据 → 交给上层重连。
bool isHeartbeatHalfOpen({required int missCount, int missLimit = kHeartbeatMissLimit}) =>
    missLimit > 0 && missCount >= missLimit;
