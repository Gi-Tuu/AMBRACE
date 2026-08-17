import 'package:flutter/material.dart';
import 'app_picker_screen.dart';
import 'node_picker_screen.dart';

/// 工作流步骤公共定义 + 编辑对话框（2026-08-14 方案 B 提取，供列表/画布编辑器共用）
const Map<String, String> kStepLabels = {
  'click': '点击',
  'long_click': '长按',
  'scroll': '滚动',
  'set_text': '输入',
  'launch_app': '启动应用',
  'tap_xy': '坐标点击',
  'swipe': '滑动',
  'wait': '等待',
  'back': '返回',
  'go_home': '返回主页',
};

const Map<String, IconData> kStepIcons = {
  'click': Icons.touch_app_outlined,
  'long_click': Icons.gesture_outlined,
  'scroll': Icons.swipe_outlined,
  'set_text': Icons.keyboard_outlined,
  'launch_app': Icons.apps_outlined,
  'tap_xy': Icons.my_location_outlined,
  'swipe': Icons.swap_vert_outlined,
  'wait': Icons.hourglass_bottom_outlined,
  'back': Icons.arrow_back_outlined,
  'go_home': Icons.home_outlined,
};

String stepLabelOf(String action) => kStepLabels[action] ?? action;

IconData stepIconOf(String action) => kStepIcons[action] ?? Icons.help_outline;

String stepSummary(Map<String, dynamic> s) {
  final a = s['action'] as String? ?? 'click';
  switch (a) {
    case 'set_text':
      return '输入：${s['text'] as String? ?? ''}';
    case 'tap_xy':
      return '(${s['x'] ?? 0}, ${s['y'] ?? 0})';
    case 'swipe':
      return '(${s['x1'] ?? 0},${s['y1'] ?? 0})→(${s['x2'] ?? 0},${s['y2'] ?? 0})';
    case 'wait':
      return '${s['ms'] ?? 800} 毫秒';
    case 'launch_app':
      return '启动 ${s['target'] as String? ?? ''}';
    case 'back':
      return '返回上一页';
    case 'go_home':
      return '返回手机主页';
    default:
      return (s['target'] as String? ?? '');
  }
}

/// 步骤编辑对话框；返回 null 表示取消。
/// 普通用户易用性：click/long_click/scroll 可从当前屏幕点选目标，
/// launch_app 可从已安装应用列表选择；[图标] 目标自动切换为坐标点击。
Future<Map<String, dynamic>?> showStepDialog(
  BuildContext context, {
  Map<String, dynamic>? step,
}) async {
  String action = step?['action'] as String? ?? 'click';
  final targetCtrl = TextEditingController(text: step?['target'] as String? ?? '');
  final textCtrl = TextEditingController(text: step?['text'] as String? ?? '');
  final xCtrl = TextEditingController(text: (step?['x'] as num? ?? 0).toString());
  final yCtrl = TextEditingController(text: (step?['y'] as num? ?? 0).toString());
  final x1Ctrl = TextEditingController(text: (step?['x1'] as num? ?? 0).toString());
  final y1Ctrl = TextEditingController(text: (step?['y1'] as num? ?? 0).toString());
  final x2Ctrl = TextEditingController(text: (step?['x2'] as num? ?? 0).toString());
  final y2Ctrl = TextEditingController(text: (step?['y2'] as num? ?? 0).toString());
  final msCtrl = TextEditingController(text: (step?['ms'] as num? ?? 800).toString());
  bool confirm = step?['confirm'] == true;

  final result = await showDialog<Map<String, dynamic>>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setLocal) => AlertDialog(
        title: const Text('编辑步骤'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              DropdownButtonFormField<String>(
                value: action,
                decoration: const InputDecoration(labelText: '动作'),
                items: kStepLabels.entries
                    .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
                    .toList(),
                onChanged: (v) {
                  if (v != null) setLocal(() => action = v);
                },
              ),
              const SizedBox(height: 8),
              if (action == 'set_text')
                TextField(
                  controller: textCtrl,
                  maxLines: 2,
                  decoration: const InputDecoration(labelText: '输入内容（≤50 字）'),
                )
              else if (action == 'tap_xy')
                Row(children: [
                  Expanded(child: TextField(controller: xCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'x'))),
                  const SizedBox(width: 8),
                  Expanded(child: TextField(controller: yCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: 'y'))),
                ])
              else if (action == 'swipe')
                Column(children: [
                  Row(children: [
                    Expanded(child: TextField(controller: x1Ctrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: '起 x'))),
                    const SizedBox(width: 8),
                    Expanded(child: TextField(controller: y1Ctrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: '起 y'))),
                  ]),
                  const SizedBox(height: 8),
                  Row(children: [
                    Expanded(child: TextField(controller: x2Ctrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: '终 x'))),
                    const SizedBox(width: 8),
                    Expanded(child: TextField(controller: y2Ctrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: '终 y'))),
                  ]),
                  const SizedBox(height: 8),
                  TextField(controller: msCtrl, keyboardType: TextInputType.number, decoration: const InputDecoration(labelText: '时长 ms')),
                ])
              else if (action == 'wait')
                TextField(
                  controller: msCtrl,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: '等待毫秒（100-10000）'),
                )
              else if (action == 'back' || action == 'go_home')
                Text(action == 'go_home' ? '返回手机主页（无需参数）' : '返回上一页（无需参数）', style: const TextStyle(color: Colors.grey)),
              if (action == 'click' || action == 'long_click' || action == 'scroll' || action == 'launch_app') ...[
                TextField(
                  controller: targetCtrl,
                  decoration: InputDecoration(
                    labelText: action == 'launch_app' ? '应用' : '目标',
                    hintText: action == 'launch_app'
                        ? '点右侧图标从已安装应用选择'
                        : '点右侧图标从当前屏幕点选，或手输节点文本',
                    suffixIcon: IconButton(
                      icon: const Icon(Icons.my_location_outlined, size: 20),
                      tooltip: action == 'launch_app' ? '从应用列表选择' : '从当前屏幕点选',
                      onPressed: () async {
                        if (action == 'launch_app') {
                          final pkg = await Navigator.of(ctx).push<String>(
                            MaterialPageRoute(builder: (_) => const AppPickerScreen())
                          );
                          if (pkg != null) targetCtrl.text = pkg;
                        } else {
                          final picked = await Navigator.of(ctx).push<Map<dynamic, dynamic>>(
                            MaterialPageRoute(builder: (_) => const NodePickerScreen())
                          );
                          if (picked != null) {
                            final label = picked['label'] as String? ?? '';
                            if (label == '[图标]') {
                              setLocal(() {
                                action = 'tap_xy';
                                xCtrl.text = (picked['x'] ?? 0).toString();
                                yCtrl.text = (picked['y'] ?? 0).toString();
                              });
                            } else {
                              targetCtrl.text = label;
                            }
                          }
                        }
                      },
                    ),
                  ),
                ),
              ],
              SwitchListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: const Text('此步需再次确认', style: TextStyle(fontSize: 13)),
                value: confirm,
                onChanged: (v) => setLocal(() => confirm = v),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          FilledButton(
            onPressed: () {
              final m = <String, dynamic>{'action': action};
              if (action == 'set_text') {
                m['text'] = textCtrl.text.trim();
              } else if (action == 'tap_xy') {
                m['x'] = int.tryParse(xCtrl.text) ?? 0;
                m['y'] = int.tryParse(yCtrl.text) ?? 0;
              } else if (action == 'swipe') {
                m['x1'] = int.tryParse(x1Ctrl.text) ?? 0;
                m['y1'] = int.tryParse(y1Ctrl.text) ?? 0;
                m['x2'] = int.tryParse(x2Ctrl.text) ?? 0;
                m['y2'] = int.tryParse(y2Ctrl.text) ?? 0;
                m['ms'] = int.tryParse(msCtrl.text) ?? 300;
              } else if (action == 'wait') {
                m['ms'] = int.tryParse(msCtrl.text) ?? 800;
              } else if (action == 'back' || action == 'go_home') {
                // 无参数
              } else {
                m['target'] = targetCtrl.text.trim();
              }
              m['confirm'] = confirm;
              Navigator.pop(ctx, m);
            },
            child: const Text('确定'),
          ),
        ],
      ),
    ),
  );
  return result;
}
