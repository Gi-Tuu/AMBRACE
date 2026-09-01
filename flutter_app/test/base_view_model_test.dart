import 'package:flutter_test/flutter_test.dart';

import 'package:ai_companion/core/mvvm/base_view_model.dart';

/// F7-a BaseViewModel 测试：guard 守卫（busy/error/静默）、重入拒绝、dispose 安全。
class _CounterVm extends BaseViewModel {
  int loads = 0;
  Future<String?> loadOk() => guard(() async {
        loads++;
        await Future<void>.delayed(const Duration(milliseconds: 5));
        return 'done';
      });
}

void main() {
  test('guard 成功：busy 两段翻转、error 保持空、返回结果', () async {
    final vm = _CounterVm();
    final states = <bool>[];
    vm.addListener(() => states.add(vm.busy));
    final out = await vm.loadOk();
    expect(out, 'done');
    expect(vm.loads, 1);
    expect(vm.busy, isFalse);
    expect(vm.error, isNull);
    expect(states, containsAllInOrder([true, false]));
    vm.dispose();
  });

  test('guard 失败：error 记录、返回 null、可 clearError', () async {
    final vm = BaseViewModel();
    final out = await vm.guard<String>(
      () async => throw StateError('boom'),
      errorPrefix: '加载失败',
    );
    expect(out, isNull);
    expect(vm.hasError, isTrue);
    expect(vm.error, contains('加载失败'));
    expect(vm.error, contains('boom'));
    vm.clearError();
    expect(vm.error, isNull);
    vm.dispose();
  });

  test('guard 重入拒绝：busy 期间再次 guard 直接返回 null 不执行', () async {
    final vm = _CounterVm();
    final first = vm.loadOk(); // 启动即同步执行 run：loads=1
    final second = await vm.loadOk(); // busy → 拒绝
    expect(second, isNull);
    await first;
    expect(vm.loads, 1, reason: '重入的第二个任务不应执行');
    vm.dispose();
  });

  test('guard silent：失败不写 error（静默降级）', () async {
    final vm = BaseViewModel();
    await vm.guard<String>(() async => throw Exception('x'), silent: true);
    expect(vm.error, isNull);
    expect(vm.busy, isFalse);
    vm.dispose();
  });

  test('dispose 后异步完成：不抛 notify 断言', () async {
    final vm = _CounterVm();
    final pending = vm.loadOk();
    vm.dispose();
    await expectLater(pending, completes);
  });
}
