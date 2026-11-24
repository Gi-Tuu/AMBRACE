// 只读工作台（M1a-2）共享五态视图：loading 转圈 / error 本地化错误+重试 /
// empty 空态 / data 业务内容。错误文案走 core 的 localizedError（zh/en 不回退中文）。
import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import 'pwa_shimmer.dart';

import '../features/reader/reader_state.dart';
import '../l10n/reader_strings.dart';

class ReaderAsyncView<T> extends StatelessWidget {
  const ReaderAsyncView({
    super.key,
    required this.loader,
    required this.rs,
    required this.cs,
    required this.onRetry,
    required this.dataBuilder,
    this.skeleton,
  });

  final ReaderLoader<T> loader;
  final ReaderStrings rs;
  final CoreStrings cs;
  final VoidCallback onRetry;
  final Widget Function(T data) dataBuilder;

  /// 加载骨架（缺省＝列表形态）。详情/宫格页可按页形传入 `ReaderSkeletons.detail/grid`。
  final Widget? skeleton;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: loader,
      builder: (context, _) {
        switch (loader.status) {
          case ReaderStatus.loading:
            return skeleton ?? ReaderSkeletons.list();
          case ReaderStatus.error:
            return Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Text(localizedError(loader.error, cs),
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error)),
                const SizedBox(height: 8),
                OutlinedButton(onPressed: onRetry, child: Text(rs.t('retry'))),
              ]),
            );
          case ReaderStatus.empty:
            return Center(child: Text(rs.t('empty')));
          case ReaderStatus.data:
            final d = loader.data;
            if (d == null) return Center(child: Text(rs.t('empty')));
            return dataBuilder(d);
        }
      },
    );
  }
}
