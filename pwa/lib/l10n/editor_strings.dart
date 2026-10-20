/// 个人内容编辑（M1a-3）PWA 侧文案表：zh/en 成对。
/// 只承载**系统文案**；备忘标题/日记正文是用户内容，一律原文展示、不翻译。
class EditorStrings {
  const EditorStrings(this.lang);

  final String lang;

  static const Map<String, Map<String, String>> _t = {
    'zh': {
      'newMemo': '新建备忘',
      'editMemo': '编辑备忘',
      'newDiary': '新建日记',
      'editDiary': '编辑日记',
      'new': '新建',
      'edit': '编辑',
      'delete': '删除',
      'save': '保存',
      'saving': '保存中…',
      'cancel': '取消',
      'title': '标题',
      'titleHint': '标题（可留空）',
      'content': '正文',
      'contentHint': '写点什么…',
      'date': '日期',
      'dateHint': 'YYYY-MM-DD',
      'contentRequired': '正文不能为空',
      'titleTooLong': '标题过长（最多 100 字）',
      'memoTooLong': '正文过长（最多 2000 字）',
      'diaryTooLong': '正文过长（最多 5000 字）',
      'badDate': '日期格式无效（需 YYYY-MM-DD）',
      'deleteTitle': '确认删除？',
      'deleteMemoBody': '删除后不可恢复，确定要删除这条备忘吗？',
      'deleteDiaryBody': '删除后不可恢复，确定要删除这天的日记吗？',
      'discardTitle': '放弃未保存的修改？',
      'discardBody': '未保存的内容将丢失。',
      'discard': '放弃',
      'keepEditing': '继续编辑',
      'saveFailed': '保存失败，内容已保留，可直接重试',
      'deleteFailed': '删除失败，请重试',
      'onlyMineEditable': '家庭共享内容 · 仅本人条目可编辑',
      'identityNotReady': '身份未就绪，暂不可编辑',
      'existingMineWillEdit': '本人当天已有日记，保存将改为编辑该条',
      'unsaved': '有未保存的修改',
    },
    'en': {
      'newMemo': 'New memo',
      'editMemo': 'Edit memo',
      'newDiary': 'New diary',
      'editDiary': 'Edit diary',
      'new': 'New',
      'edit': 'Edit',
      'delete': 'Delete',
      'save': 'Save',
      'saving': 'Saving…',
      'cancel': 'Cancel',
      'title': 'Title',
      'titleHint': 'Title (optional)',
      'content': 'Content',
      'contentHint': 'Write something…',
      'date': 'Date',
      'dateHint': 'YYYY-MM-DD',
      'contentRequired': 'Content must not be empty',
      'titleTooLong': 'Title too long (max 100)',
      'memoTooLong': 'Content too long (max 2000)',
      'diaryTooLong': 'Content too long (max 5000)',
      'badDate': 'Invalid date (need YYYY-MM-DD)',
      'deleteTitle': 'Delete?',
      'deleteMemoBody': 'This cannot be undone. Delete this memo?',
      'deleteDiaryBody': 'This cannot be undone. Delete this diary entry?',
      'discardTitle': 'Discard unsaved changes?',
      'discardBody': 'Unsaved content will be lost.',
      'discard': 'Discard',
      'keepEditing': 'Keep editing',
      'saveFailed': 'Save failed; your text is preserved, you can retry',
      'deleteFailed': 'Delete failed; please retry',
      'onlyMineEditable': 'Shared content · only your own entries are editable',
      'identityNotReady': 'Identity not ready; editing disabled',
      'existingMineWillEdit': 'You already have a diary for that date; saving will **edit it**',
      'unsaved': 'Unsaved changes',
    },
  };

  String t(String key) {
    final m = _t[lang] ?? _t['en']!;
    return m[key] ?? _t['en']![key] ?? key;
  }

  /// 仅测试用：暴露成对表（断言 zh/en key 完全对齐）。
  static Map<String, Map<String, String>> get debugTables => _t;
}
