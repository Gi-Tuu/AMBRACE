/// 会话工作台（M1a-3）PWA 侧文案表：zh/en 成对，模式与 ReaderStrings 一致。
/// 只承载**系统文案**；会话标题是用户内容，一律原文展示（空标题用 [untitledSession] 占位）。
class SessionStrings {
  const SessionStrings(this.lang);

  final String lang;

  static const Map<String, Map<String, String>> _t = {
    'zh': {
      'sessions': '会话',
      'newSession': '新建会话',
      'untitledSession': '未命名会话',
      'noSessions': '暂无会话',
      'selectSession': '选择一个会话',
      'loadingSessions': '加载会话中…',
      'loadSessionsFailed': '会话列表加载失败',
      'retry': '重试',
      'createdAt': '创建于',
      'loadEarlier': '加载更早',
      'loadingEarlier': '加载中…',
      'noEarlier': '没有更早的消息了',
      'loadEarlierFailed': '加载更早失败',
      'newSessionUnconfirmed': '新建会话结果未确认，已刷新列表请核对',
      'newSessionFailed': '新建会话失败',
      'sessionReady': '就绪',
    },
    'en': {
      'sessions': 'Sessions',
      'newSession': 'New session',
      'untitledSession': 'Untitled session',
      'noSessions': 'No sessions yet',
      'selectSession': 'Select a session',
      'loadingSessions': 'Loading sessions…',
      'loadSessionsFailed': 'Failed to load sessions',
      'retry': 'Retry',
      'createdAt': 'Created',
      'loadEarlier': 'Load earlier',
      'loadingEarlier': 'Loading…',
      'noEarlier': 'No earlier messages',
      'loadEarlierFailed': 'Failed to load earlier',
      'newSessionUnconfirmed': 'New session result unconfirmed; list refreshed, please verify',
      'newSessionFailed': 'Failed to create session',
      'sessionReady': 'Ready',
    },
  };

  String t(String key) {
    final m = _t[lang] ?? _t['en']!;
    return m[key] ?? _t['en']![key] ?? key;
  }

  /// 仅测试用：暴露成对表（断言 zh/en key 完全对齐）。
  static Map<String, Map<String, String>> get debugTables => _t;
}
