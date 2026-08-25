import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:ai_companion/providers/settings_provider.dart';

/// Onboarding 触发时机与持久化回归（2026-08-24）。
/// 核心规则：`needsOnboarding = !onboardingDone && serverUrl.isEmpty`。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('全新设备：serverUrl 为空且未完成引导 → needsOnboarding true', () async {
    final s = SettingsProvider();
    await s.load();
    expect(s.onboardingDone, isFalse);
    expect(s.serverUrl, isEmpty);
    expect(s.needsOnboarding, isTrue);
  });

  test('已配置服务器（即使未完成引导）→ 不打扰，needsOnboarding false', () async {
    final s = SettingsProvider();
    await s.load();
    await s.setServerUrl('http://192.168.1.100:8000');
    expect(s.needsOnboarding, isFalse);
  });

  test('完成引导（onboarding_done=true）→ 无论是否有服务器都不再显示', () async {
    final s = SettingsProvider();
    await s.load();
    await s.setOnboardingDone(true);
    expect(s.onboardingDone, isTrue);
    expect(s.needsOnboarding, isFalse);
    await s.setServerUrl('http://192.168.1.100:8000');
    expect(s.needsOnboarding, isFalse);
  });

  test('onboarding_done 持久化 round-trip（重启后仍为已完成）', () async {
    final a = SettingsProvider();
    await a.load();
    await a.setOnboardingDone(true);

    final b = SettingsProvider();
    await b.load();
    expect(b.onboardingDone, isTrue);
    expect(b.needsOnboarding, isFalse);
  });

  test('已持久化 onboarding_done + 空 serverUrl 的存量用户 → 不再进引导', () async {
    SharedPreferences.setMockInitialValues({'onboarding_done': true});
    final s = SettingsProvider();
    await s.load();
    expect(s.onboardingDone, isTrue);
    expect(s.needsOnboarding, isFalse);
  });
}
