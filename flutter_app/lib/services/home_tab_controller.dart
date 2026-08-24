import 'package:flutter/foundation.dart';

/// 全局首页 Tab 控制器：供手机感知动作序列在自家 app 内切换页面
/// （0=好友列表 / 1=朋友圈 / 2=宠物），由 HomeScreen 监听并切换。
class HomeTabController {
  static final ValueNotifier<int> index = ValueNotifier<int>(0);
  static void switchTo(int i) => index.value = i;
}
