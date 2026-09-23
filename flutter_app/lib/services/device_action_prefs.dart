import "package:shared_preferences/shared_preferences.dart";

/// X7-M4c-5 行动确认策略三档（方案 §7 决策④ 的完整版；M4b 只落了推荐档＝中档）。
enum ActionConfirmPolicy {
  /// 轻：只在首次授权时确认一次，之后永久放行（落 prefs）
  onceEver,

  /// 中：每类动作本次会话首次确认，之后放行（＝M4b 现状，缺省档）
  firstPerType,

  /// 重：每次执行都确认（高风险动作建议用这档）
  everyTime,
}

/// 键值字面量（与派单口径一致，落 prefs 的就是这三个字符串）。
const String policyNameOnceEver = "once_ever";
const String policyNameFirstPerType = "first_per_type";
const String policyNameEveryTime = "every_time";

/// 策略 → 键值。
String policyName(ActionConfirmPolicy policy) {
  switch (policy) {
    case ActionConfirmPolicy.onceEver:
      return policyNameOnceEver;
    case ActionConfirmPolicy.firstPerType:
      return policyNameFirstPerType;
    case ActionConfirmPolicy.everyTime:
      return policyNameEveryTime;
  }
}

/// 键值 → 策略：未知/空/异常名一律回落中档（＝不放宽也不加码，回到 M4b 现状语义）。
ActionConfirmPolicy policyFromName(String? raw) {
  switch (raw) {
    case policyNameOnceEver:
      return ActionConfirmPolicy.onceEver;
    case policyNameEveryTime:
      return ActionConfirmPolicy.everyTime;
    case policyNameFirstPerType:
      return ActionConfirmPolicy.firstPerType;
    default:
      return ActionConfirmPolicy.firstPerType;
  }
}

/// 行动确认策略的本机持久化：只存**策略名**与**已永久放行的能力名**，绝不存动作内容/文本。
///
/// 所有方法都 try/catch 折成安全默认值（读失败＝中档；永久放行标记读失败＝没放行过），
/// 不向 UI 抛异常—— prefs 不可用时链路仍按 M4b 现状跑。
class DeviceActionPrefs {
  static const String policyKeyPrefix = "device_action_policy_";
  static const String onceEverKeyPrefix = "device_action_once_ever_";

  /// X7-M4d：工作流是否改走行动端口（内核裁决 + 审计 + 执行器）的客户端总开关
  static const String workflowBridgeKey = "device_action_workflow_bridge";

  /// 缺省档＝中档（每类动作本次会话首次确认）
  static const ActionConfirmPolicy defaultPolicy = ActionConfirmPolicy.firstPerType;

  static String policyKey(String capability) => "$policyKeyPrefix$capability";

  static String onceEverKey(String capability) => "$onceEverKeyPrefix$capability";

  /// 该能力的确认档位；没配过 / prefs 读不到 → 中档。
  static Future<ActionConfirmPolicy> policyFor(String capability) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return policyFromName(prefs.getString(policyKey(capability)));
    } catch (_) {
      return defaultPolicy;
    }
  }

  /// 写入某能力的档位。返回 true ＝ 已落盘；失败不抛异常（下次仍按原档跑）。
  ///
  /// **换档即重置「永久放行」标记**（M4c-5 收尾，2026-09-23）：轻档的永久放行只对轻档成立，
  /// 若不清，则「轻 → 重 → 轻」会直接沿用旧标记、不再询问；改成非轻档时一并删键。
  static Future<bool> setPolicy(String capability, ActionConfirmPolicy policy) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final ok = await prefs.setString(policyKey(capability), policyName(policy));
      if (policy != ActionConfirmPolicy.onceEver) {
        await prefs.remove(onceEverKey(capability));
      }
      return ok;
    } catch (_) {
      return false;
    }
  }

  /// 该能力是否已「永久放行」过（轻档首次授权后落的标记）。读不到＝没确认过（fail-closed）。
  static Future<bool> isOnceEverConfirmed(String capability) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getBool(onceEverKey(capability)) ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 标记该能力已永久放行（轻档确认通过后调用）。失败不抛异常：下次仍会再问一次。
  static Future<bool> markOnceEverConfirmed(String capability) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return await prefs.setBool(onceEverKey(capability), true);
    } catch (_) {
      return false;
    }
  }

  /// M4d：工作流桥开关。**缺省 / 读失败＝关**（＝整条走旧路径，不开新端口）。
  static Future<bool> isWorkflowBridgeEnabled() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getBool(workflowBridgeKey) ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 写入工作流桥开关。返回 true ＝ 已落盘；失败不抛异常（下次仍按缺省＝关处理）。
  static Future<bool> setWorkflowBridgeEnabled(bool enabled) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return await prefs.setBool(workflowBridgeKey, enabled);
    } catch (_) {
      return false;
    }
  }
}
