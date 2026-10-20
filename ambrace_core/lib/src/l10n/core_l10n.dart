/// 共享核心的最小文案表（zh / en **成对**）。
///
/// 为什么不用 ARB：core 是纯共享包，ARB 生成物属于各壳的构建流程；首单把闭环必需
/// 文案放在这里成对维护（zh/en 一一对应），壳可再叠加自己的 l10n。避免为几条文案
/// 引入全量重生成噪音。
class CoreStrings {
  final String lang; // 'zh' | 'en'

  const CoreStrings(this.lang);

  static const Map<String, Map<String, String>> _t = {
    'zh': {
      'appTitle': '拥爱 · 网页版',
      'serverAddress': '服务器地址',
      'login': '登录',
      'logout': '退出登录',
      'username': '账号',
      'password': '密码',
      'characters': '角色',
      'history': '会话历史',
      'send': '发送',
      'messageHint': '输入消息…',
      'emptyCharacters': '暂无角色',
      'emptyMessages': '还没有消息，说点什么吧',
      'connecting': '连接中…',
      'offline': '离线：无法连接服务器',
      'unauthorized': '登录已失效，请重新登录',
      'identityMismatch': '服务器身份校验不通过，已拒绝该响应',
      'sending': '发送中…',
      'cancelled': '已取消',
      'switchAccount': '切换账号',
      'pairingCode': '配对码',
      'pair': '配对',
      'pairFailed': '配对失败',
      'pairOk': '已配对',
      'tabCharacters': '角色',
      'tabChat': '聊天',
      'errorNetwork': '网络连接失败，请检查服务器地址与网络',
      'errorTimeout': '请求超时，请稍后重试',
      'errorServer': '服务器暂时不可用，请稍后重试',
      'errorClient': '请求被拒绝',
      'errorUnknown': '请求失败，请稍后重试',
      'offlineShell': '当前离线：只能显示已缓存的界面，联网后请刷新',
    },
    'en': {
      'appTitle': 'AMBRACE · Web',
      'serverAddress': 'Server address',
      'login': 'Sign in',
      'logout': 'Sign out',
      'username': 'Account',
      'password': 'Password',
      'characters': 'Characters',
      'history': 'History',
      'send': 'Send',
      'messageHint': 'Type a message…',
      'emptyCharacters': 'No characters',
      'emptyMessages': 'No messages yet',
      'connecting': 'Connecting…',
      'offline': 'Offline: cannot reach the server',
      'unauthorized': 'Session expired, please sign in again',
      'identityMismatch': 'Server identity check failed; response rejected',
      'sending': 'Sending…',
      'cancelled': 'Cancelled',
      'switchAccount': 'Switch account',
      'pairingCode': 'Pairing code',
      'pair': 'Pair',
      'pairFailed': 'Pairing failed',
      'pairOk': 'Paired',
      'tabCharacters': 'Characters',
      'tabChat': 'Chat',
      'errorNetwork': 'Network unreachable; check the server address and your connection',
      'errorTimeout': 'Request timed out; please retry',
      'errorServer': 'Server temporarily unavailable; please retry',
      'errorClient': 'Request rejected',
      'errorUnknown': 'Request failed; please retry',
      'offlineShell': 'Offline: showing the cached shell only. Refresh once you are back online.',
    },
  };

  String t(String key) => _t[lang]?[key] ?? _t['en']?[key] ?? key;

  /// 错误 kind → **本地化**文案（英文界面不得显示中文原文）。
  ///
  /// 为什么按 kind 映射而不是直接显示 `ApiException.message`：后者恒为中文硬编码，
  /// 在 en 界面就是中英混排（主审 §5 点名的正是这一处）。
  String errorForKind(String? kind) {
    switch (kind) {
      case 'unauthorized':
        return t('unauthorized');
      case 'network':
        return t('offline');
      case 'timeout':
        return t('errorTimeout');
      case 'server':
        return t('errorServer');
      case 'client':
        return t('errorClient');
      case 'cancelled':
        return t('cancelled');
      case 'identity':
        return t('identityMismatch');
      default:
        return t('errorUnknown');
    }
  }

  /// 配对失败原因码 → **本地化**文案（绝不把内部 failure 码回显给用户）。
  String pairFailureText(String? failure) {
    switch (failure) {
      case 'network':
        return t('errorNetwork');
      case 'no_code':
      case 'rejected':
        return t('errorClient');
      case 'unsupported':
      case 'fp_mismatch':
        return t('identityMismatch');
      default:
        return t('errorUnknown');
    }
  }

  static const List<String> supported = ['zh', 'en'];
}
