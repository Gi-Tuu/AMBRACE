// 个人内容编辑器（M1a-3）· 备忘与个人日记**共用**的模态编辑表单。
//
// 纪律：
// - 提交**由本表单自己做**（调用方传 [onSubmit]）：失败**不关**、原文保留、可直接重试；
// - 提交中防重复（`_saving` 期间禁二次提交、按钮显示「保存中…」且**三个字段全部禁用**，
//   不接受到「提交快照之后」的新输入再被成功关掉丢弃）。为什么用「禁用」而不是「只读」：
//   Flutter Web 的语义 textfield 在编辑中**不会**把只读配置重新下发到 DOM，只读挡不住平台输入
//   （`EditableText._shouldCreateInputConnection` 在 web 恒为 true）；`enabled:false` 会把语义
//   元素置为 `disabled`，浏览器层面真正拒绝输入。失败后恢复可编辑、原文保留；
// - 未保存时离开给「放弃 / 继续编辑」明确选择：`PopScope(canPop:false)` 让**系统/应用内
//   返回**与「取消」按钮走同一决策（一次只弹一个确认框）；barrier/拖拽关闭被禁用，
//   避免点空白处静默丢失；被 401/退出/换号强制作废时（壳走 popUntil）不弹确认、直接清；
// - 表单**不**决定后端路径与归属：只把三个字段交给 [onSubmit]，写路径由 EditorApi 负责；
// - 日期是**纯字符串** `YYYY-MM-DD`，零 UTC 换算（不经任何时间轴转换挪天）。
import 'package:flutter/material.dart';

import '../../l10n/editor_strings.dart';

/// 提交回调：返回 null 表示成功；返回非空字符串为**本地化错误文案**（表单保持打开）。
typedef EditorSubmit = Future<String?> Function(
    String title, String content, String date);

class PersonalEditorSheet extends StatefulWidget {
  const PersonalEditorSheet({
    super.key,
    required this.es,
    required this.title,
    required this.kind, // 'memo' | 'diary'
    required this.onSubmit,
    this.initialTitle = '',
    this.initialContent = '',
    this.initialDate = '',
    this.hintForDate,
  });

  final EditorStrings es;
  final String title; // 表单标题（新建备忘/编辑备忘/…）
  final String kind;
  final EditorSubmit onSubmit;
  final String initialTitle;
  final String initialContent;
  final String initialDate; // diary：YYYY-MM-DD；memo：空
  /// 按**当前输入日期**动态给出的明确提示（如「本人当天已有日记，保存将改为编辑该条」）。
  final String? Function(String date)? hintForDate;

  @override
  State<PersonalEditorSheet> createState() => _PersonalEditorSheetState();
}

class _PersonalEditorSheetState extends State<PersonalEditorSheet> {
  late final TextEditingController _title =
      TextEditingController(text: widget.initialTitle);
  late final TextEditingController _content =
      TextEditingController(text: widget.initialContent);
  late final TextEditingController _date =
      TextEditingController(text: widget.initialDate);

  bool _saving = false;
  String _error = '';

  bool get _isDiary => widget.kind == 'diary';
  int get _maxContent => _isDiary ? 5000 : 2000;

  bool get _dirty =>
      _title.text != widget.initialTitle ||
      _content.text != widget.initialContent ||
      _date.text != widget.initialDate;

  @override
  void dispose() {
    _title.dispose();
    _content.dispose();
    _date.dispose();
    super.dispose();
  }

  static bool _validDate(String s) {
    if (s.length != 10) return false;
    final p = s.split('-');
    if (p.length != 3) return false;
    final y = int.tryParse(p[0]);
    final m = int.tryParse(p[1]);
    final d = int.tryParse(p[2]);
    if (y == null || m == null || d == null) return false;
    if (m < 1 || m > 12 || d < 1 || d > 31) return false;
    return DateTime(y, m, d).month == m && DateTime(y, m, d).day == d;
  }

  /// 前端校验（**不**让后端静默截断替代提示）。
  String? _validate() {
    final es = widget.es;
    if (_content.text.trim().isEmpty) return es.t('contentRequired');
    if (_title.text.length > 100) return es.t('titleTooLong');
    if (_content.text.length > _maxContent) {
      return es.t(_isDiary ? 'diaryTooLong' : 'memoTooLong');
    }
    if (_isDiary && !_validDate(_date.text.trim())) return es.t('badDate');
    return null;
  }

  bool _leaving = false; // 只允许一个「放弃/继续编辑」确认框，不反复递归 pop

  Future<void> _save() async {
    if (_saving) return;
    final bad = _validate();
    if (bad != null) {
      setState(() => _error = bad);
      return;
    }
    // 打开前先拿住本表单所在的 Navigator / Route：**晚到的**提交结果只有在本 route
    // 仍是当前页时才允许 pop / 弹提示——否则被 401/退出/换号强制作废后，会误关
    // 新账号的 route 或在新账号上弹旧内容提示。
    final nav = Navigator.of(context);
    final route = ModalRoute.of(context);
    setState(() {
      _saving = true;
      _error = '';
      _leaving = false;
    });
    try {
      final err = await widget.onSubmit(
          _title.text.trim(), _content.text.trim(), _date.text.trim());
      if (!mounted || !(route?.isCurrent ?? false)) return;
      if (err == null) {
        nav.pop(true); // 成功才关
      } else {
        setState(() {
          _error = err;
          _saving = false;
        });
      }
    } on Object catch (e) {
      if (!mounted || !(route?.isCurrent ?? false)) return;
      setState(() {
        _error = '$e';
        _saving = false;
      });
    }
  }

  /// 统一的「离开」决策：**干净**表单直接关；**dirty** 必须给「放弃 / 继续编辑」；
  /// **保存中**不接受离开（等本次保存结果）。系统返回与「取消」按钮共用本入口。
  Future<void> _requestLeave() async {
    if (_saving || _leaving) return;
    if (!_dirty) {
      Navigator.of(context).pop(false);
      return;
    }
    _leaving = true;
    final es = widget.es;
    try {
      final keep = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(es.t('discardTitle')),
          content: Text(es.t('discardBody')),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(ctx).pop(true),
                child: Text(es.t('keepEditing'))),
            FilledButton(
                onPressed: () => Navigator.of(ctx).pop(false),
                child: Text(es.t('discard'))),
          ],
        ),
      );
      if (!mounted) return;
      if (keep == false) Navigator.of(context).pop(false); // 放弃并关闭
    } finally {
      _leaving = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final es = widget.es;
    final editable = !_saving; // 保存中：标题/日期/正文**全部禁用**（不接受「提交快照后」的新输入再被关掉丢弃）
    return PopScope<bool>(
      // 平台/应用内返回不再绕过：系统返回走统一「离开」决策（干净直接关、dirty 弹
      // 「放弃/继续编辑」、保存中忽略）；PopScope 只影响 maybePop，**不影响**壳在
      // 401/退出/换号时的 popUntil 强制作废（那走 Navigator.pop 直连）。
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        _requestLeave();
      },
      child: SafeArea(
        child: Padding(
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 16,
            bottom: MediaQuery.viewInsetsOf(context).bottom + 16,
          ),
          child: ListView(shrinkWrap: true, children: [
            Text(widget.title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            if (!_isDiary) ...[
              TextField(
                controller: _title,
                enabled: editable,
                maxLength: 100,
                decoration: InputDecoration(
                    labelText: es.t('title'), hintText: es.t('titleHint')),
              ),
              const SizedBox(height: 8),
            ] else ...[
              TextField(
                controller: _date,
                enabled: editable,
                onChanged: (_) => setState(() {}), // 提示随日期实时更新
                decoration: InputDecoration(
                    labelText: es.t('date'), hintText: es.t('dateHint')),
              ),
              if (widget.hintForDate?.call(_date.text.trim()) != null)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(widget.hintForDate!.call(_date.text.trim())!,
                      style: TextStyle(
                          fontSize: 12,
                          color: Theme.of(context).colorScheme.primary)),
                ),
              const SizedBox(height: 8),
            ],
            TextField(
              controller: _content,
              enabled: editable,
              maxLines: 8,
              minLines: 4,
              decoration: InputDecoration(
                  labelText: es.t('content'), hintText: es.t('contentHint')),
            ),
            if (_error.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(_error,
                    style: TextStyle(color: Theme.of(context).colorScheme.error)),
              ),
            const SizedBox(height: 12),
            Row(mainAxisAlignment: MainAxisAlignment.end, children: [
              TextButton(
                  onPressed: _saving ? null : _requestLeave,
                  child: Text(es.t('cancel'))),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: _saving ? null : _save,
                child: Text(_saving ? es.t('saving') : es.t('save')),
              ),
            ]),
          ]),
        ),
      ),
    );
  }
}
