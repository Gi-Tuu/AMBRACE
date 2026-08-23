import 'package:flutter/material.dart';

/// 全局 Navigator key：顶部横幅/通知跳转使用
final GlobalKey<NavigatorState> appNavigatorKey = GlobalKey<NavigatorState>();

/// 全局路由观察器：页面切换/压栈弹栈时上报当前活动页面（用于消息弹窗抑制）
final RouteObserver<ModalRoute<void>> appRouteObserver = RouteObserver<ModalRoute<void>>();
