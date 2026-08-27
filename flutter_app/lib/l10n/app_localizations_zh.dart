// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Chinese (`zh`).
class AppLocalizationsZh extends AppLocalizations {
  AppLocalizationsZh([String locale = 'zh']) : super(locale);

  @override
  String get apiTabLlm => 'LLM';

  @override
  String get apiTabSpeech => '语音';

  @override
  String get apiTabVision => '识图';

  @override
  String get apiTabImage => '生图';

  @override
  String get apiTabTask => '任务';

  @override
  String get newLlmConfig => '新建 LLM';

  @override
  String get editLlmConfig => '编辑 LLM';

  @override
  String get llmConfigName => '配置名';

  @override
  String get llmConfigNameRequired => '请填写配置名';

  @override
  String get setDefault => '设为默认';

  @override
  String get sharedWithSubs => '可共享给子账号';

  @override
  String get sharedConfigList => '主账号共享';

  @override
  String get defaultBadge => '默认';

  @override
  String get sharedBadge => '共享';

  @override
  String get modelDefaultBind => '默认（不绑定）';

  @override
  String get emptyLlmConfigs => '还没有 LLM 配置';

  @override
  String get addLlmConfig => '新增配置';

  @override
  String get llmSharedReadonly => '共享配置仅可查看';

  @override
  String get llmConfigHint => '为角色选择要使用的模型配置';

  @override
  String get appName => '拥爱';

  @override
  String get onboardingTitle => '欢迎使用拥爱';

  @override
  String get onboardingSubtitle => '几步即可开始与你的 AI 伙伴聊天';

  @override
  String get onboardingStepServer => '连接服务器';

  @override
  String get onboardingStepAccount => '账号';

  @override
  String get onboardingStepCharacter => '创建角色';

  @override
  String get onboardingStepApiKey => 'API Key';

  @override
  String get onboardingServerTitle => '连接你的服务器';

  @override
  String get onboardingServerDesc => '输入服务器地址并检测连接，这是所有功能的前提。';

  @override
  String get onboardingAccountTitle => '登录或注册账号';

  @override
  String get onboardingAccountDesc => '登录后即可保存你的对话与记忆。';

  @override
  String get onboardingAccountDone => '登录成功';

  @override
  String get onboardingCharacterTitle => '创建你的第一个 AI 角色';

  @override
  String get onboardingCharacterDesc => '给 AI 伙伴起个名字，一句话描述它的性格。';

  @override
  String get onboardingCharacterPersonalityLabel => '一句话性格';

  @override
  String get onboardingCharacterPersonalityHint => '例：温柔体贴，喜欢讲冷笑话';

  @override
  String get onboardingCharacterCreate => '创建角色';

  @override
  String get onboardingCharacterCreated => '角色已创建';

  @override
  String get onboardingCharacterSkip => '跳过（稍后创建）';

  @override
  String get onboardingApiKeyTitle => '配置 LLM API Key';

  @override
  String get onboardingApiKeyHint => '配置后 AI 才能回复你；也可以稍后在设置中配置。';

  @override
  String get onboardingApiKeyPreset => '供应商预设';

  @override
  String get onboardingApiKeySaveDone => '保存并完成';

  @override
  String get onboardingApiKeySkip => '跳过（稍后设置）';

  @override
  String get onboardingApiKeySkipTip => '跳过将引导你到设置页，稍后在「设置 → API 配置」中配置。';

  @override
  String get onboardingApiKeySaved => 'API Key 已保存';

  @override
  String get onboardingApiKeyEmpty => '请填写 Base URL 和 API Key';

  @override
  String get onboardingApiTestOk => '配置有效，连接成功';

  @override
  String get onboardingApiTestFail => '连接失败，请检查配置';

  @override
  String get onboardingFirstMessage => '你好';

  @override
  String get onboardingWarningUsername => '请输入用户名和密码';

  @override
  String get onboardingNext => '下一步';

  @override
  String get onboardingReRun => '重新运行首次引导';

  @override
  String get onboardingReRunConfirm => '重新运行首次引导？已完成的信息不会丢失。';

  @override
  String get abandon => '遗弃';

  @override
  String get abandonConfirm => '遗弃后宠物会被送走（删除），AI 伙伴们会记得这件事。确定要遗弃吗？';

  @override
  String get abandonFailed => '遗弃失败';

  @override
  String abandonTitle(Object name) {
    return '遗弃$name？';
  }

  @override
  String abandoned(Object name) {
    return '已遗弃$name';
  }

  @override
  String get about => '关于';

  @override
  String get actionCook => '做饭';

  @override
  String get actionDone => '完成';

  @override
  String get actionEat => '吃饭';

  @override
  String get actionExercise => '运动';

  @override
  String get actionGame => '玩游戏';

  @override
  String actionInProgress(Object action) {
    return '在$action…';
  }

  @override
  String get actionMusic => '听音乐';

  @override
  String get actionRead => '读书';

  @override
  String get actionShower => '洗澡';

  @override
  String get actionSleep => '睡觉';

  @override
  String actionSucceeded(Object label) {
    return '$label成功';
  }

  @override
  String get actionTv => '看电视';

  @override
  String get actionWork => '工作';

  @override
  String get activeImageGen => '主动生图';

  @override
  String get activeImageGenHint => 'AI会在合适的时机主动生成图片发给你（如分享画面、表达心情）';

  @override
  String get agentMind => 'AI 内心世界';

  @override
  String get agentMindEmpty => '暂无记录';

  @override
  String get agentMindReflection => '最近复盘';

  @override
  String get agentMindTasks => '任务记录';

  @override
  String get agentMindToolLogs => '工具轨迹';

  @override
  String agentMindToolSummary(
      Object blocked, Object fail, Object ok, Object rate) {
    return '成功率 $rate%（完成 $ok / 失败 $fail · 拦截 $blocked）';
  }

  @override
  String get agentMindMemorySearch => '记忆召回';

  @override
  String agentMindHitSummary(Object hit, Object miss, Object ms) {
    return '命中 $hit / 未命中 $miss · 平均 ${ms}ms';
  }

  @override
  String get agentMindSearchEmpty => '暂无检索记录';

  @override
  String get agentMindRunningNotes => '运行笔记';

  @override
  String get agentMindIdentity => '身份画像';

  @override
  String get agentMindPinned => '置顶摘要';

  @override
  String get agentMindNoteEmpty => '暂无运行笔记';

  @override
  String get activityBrowse => '浏览';

  @override
  String get activityLearn => '学习';

  @override
  String get activityLog => '互动记录';

  @override
  String get add => '添加';

  @override
  String get addCommentHint => '发表评论...';

  @override
  String get addFailed => '添加失败';

  @override
  String get addMember => '添加角色';

  @override
  String get adopt => '领养';

  @override
  String get adoptFailed => '领养失败';

  @override
  String get adoptFailedRetry => '领养失败，请稍后重试';

  @override
  String adoptForChar(Object name) {
    return '已帮 $name 领养成功';
  }

  @override
  String get adoptForTa => '帮 TA 领养';

  @override
  String get adoptHeading => '领养一只小动物';

  @override
  String get adoptNewPet => '领养新宠物';

  @override
  String adoptPetFor(Object name) {
    return '帮 $name 领养宠物';
  }

  @override
  String adoptSpecies(Object label) {
    return '领养$label';
  }

  @override
  String get adoptSubtitle => '折纸风小宠物会成为家里的一员，AI 伙伴们也会记得它';

  @override
  String get aiBrowseHistory => 'AI 浏览记录';

  @override
  String get aiDiary => 'AI日记';

  @override
  String get aiDiaryHint => 'AI每天会撰写日记记录当天聊天';

  @override
  String get aiFriendFallback => 'AI 好友';

  @override
  String get aiGenerated => 'AI 生成';

  @override
  String get aiLife => 'AI 生活';

  @override
  String get aiLifeHint => 'TA的生活点滴/兴趣/产物';

  @override
  String get aiOfflineLife => 'AI 离线生活';

  @override
  String get aiOfflineLifeHint => '离线时角色会真实度过时间：状态变化、休息、反思、整理记忆（默认开启）';

  @override
  String get aiPetsSubtitle =>
      '拜访 TA 的宠物可以喂食 / 玩耍 / 清洁；TA 还没有宠物的话，也可以帮 TA 领养一只。';

  @override
  String get aiPetsTitle => '角色们的宠物';

  @override
  String get aiPrivateChat => 'AI 间私聊';

  @override
  String get aiPrivateChatHint => '开启后你的 AI 角色之间会偶尔私下聊天';

  @override
  String get aiPromised => 'AI 承诺';

  @override
  String get aiSchedule => 'AI 的日程';

  @override
  String get aiWantsToCall => 'TA 想调用';

  @override
  String get albumTitle => '相册';

  @override
  String get allow => '允许';

  @override
  String get featureFlagsTitle => '服务器功能管理';

  @override
  String get featureFlagsHint => '即时生效，无需重启服务器';

  @override
  String get featureFlagsAdminOnly => '仅主账号可管理服务器功能';

  @override
  String get flagLightReply => '群聊精简回复模式';

  @override
  String get flagLightReplyHint => '群聊/抖音回复用精简上下文，更省更快';

  @override
  String get flagGroupRuntime => '群聊统一智能回复';

  @override
  String get flagGroupRuntimeHint => '群聊回复接入角色记忆，各角色互不知晓彼此私事';

  @override
  String get flagDouyinRuntime => '平台动态统一回复';

  @override
  String get flagDouyinRuntimeHint => '外部平台动态回复接入角色记忆';

  @override
  String get flagAdvanced => '高级开关';

  @override
  String get flagAdvancedHint => '高级功能开关，请谨慎调整';

  @override
  String get flagSaved => '已保存';

  @override
  String get flagError => '切换失败，请重试';

  @override
  String get flagWeave3D => '织网 3D（实验）';

  @override
  String get flagWeave3DHint => '织库画布切换为 3D 球视图（实验功能，低端机可关）';

  @override
  String get apiConfig => 'API 配置';

  @override
  String get apiConfigHint => 'LLM / 生图服务（BYOK 与服务器级）';

  @override
  String get appAlbum => '相册';

  @override
  String get appBrowser => '浏览器';

  @override
  String get appCalendar => '日历';

  @override
  String get appChat => '畅聊';

  @override
  String get appDescAlbum => 'AI 生成图片 + 我的上传';

  @override
  String get appDescBrowser => '浏览器扩展附属 · 搜索历史保留 7 天';

  @override
  String get appDescCalendar => '查看 / 写备注，AI 可见';

  @override
  String get appDescChat => '与角色畅聊';

  @override
  String get appDescMarket => '恢复误删的应用';

  @override
  String get appDescMemo => '便签，AI 也会主动记录';

  @override
  String get appDescSettings => '虚拟手机（占位）';

  @override
  String get appDescTheme => '壁纸等手机美化';

  @override
  String get appMarket => '应用市场';

  @override
  String get appMemo => '备忘录';

  @override
  String get appPets => '宠物';

  @override
  String get appSettings => '设置';

  @override
  String get appTheme => '主题';

  @override
  String get appearance => '样貌';

  @override
  String get appearanceHint => '例如：长发飘飘，戴着眼镜，看起来很温柔';

  @override
  String get appearanceTitle => '应用容貌';

  @override
  String get archiveBox => '聊天记录箱';

  @override
  String archiveTitle(Object a, Object b) {
    return '$a · $b 聊天记录箱';
  }

  @override
  String get arrangement => '安排';

  @override
  String get artifactImage => '作品';

  @override
  String get artifactNote => '笔记';

  @override
  String get artifactText => '创作';

  @override
  String get artifactsTab => '产物库';

  @override
  String artifactsTitle(Object name) {
    return '$name的产物库';
  }

  @override
  String get avatarUpdateFailed => '头像更新失败';

  @override
  String get avatarUpdated => '头像已更新';

  @override
  String get back => '返回';

  @override
  String get backgroundInfo => '背景信息';

  @override
  String get basic => '基本';

  @override
  String get birthday => '生日';

  @override
  String get browserTitle => '浏览器';

  @override
  String get browsingHint => '开启「AI 离线生活」并授权浏览器后，TA 会真实浏览网页';

  @override
  String browsingTitle(Object name) {
    return '$name · 浏览记录';
  }

  @override
  String get calendarHint => '点击日期可查看 / 添加备注（AI 会在聊天中感知近期备注）';

  @override
  String calendarTitle(Object y, Object m) {
    return '$y年$m月';
  }

  @override
  String get wfDefaultName => '工作流';

  @override
  String wfImportConfirm(Object name) {
    return '导入「$name」？';
  }

  @override
  String get wfImportNoTemplates => '暂无 workflow 型插件的模板';

  @override
  String get wfImportSuccess => '工作流导入成功';

  @override
  String get wfImportTemplates => '从插件模板导入';

  @override
  String chatRunWf(Object name) {
    return '执行「$name」';
  }

  @override
  String chatWfSteps(Object count) {
    return '共 $count 步，按顺序操作手机（敏感步骤需你确认）';
  }

  @override
  String chatWfDone(Object summary) {
    return '工作流执行完成：$summary';
  }

  @override
  String chatWfInterrupted(Object summary) {
    return '工作流中断：$summary';
  }

  @override
  String chatWfStep(Object mark, Object msg, Object step) {
    return '第$step步$mark $msg';
  }

  @override
  String get chatNoAccessibility => '未开启读屏（无障碍），无法执行操作';

  @override
  String chatSeqInterrupted(Object summary) {
    return '序列中断：$summary';
  }

  @override
  String chatSeqDone(Object summary) {
    return '序列执行完成：$summary';
  }

  @override
  String get chatPickTarget => 'AI 想操作当前屏幕，选一个目标';

  @override
  String get nodeClickable => '可点击';

  @override
  String get nodeInput => '输入框';

  @override
  String get chatNoNodes => '当前屏幕暂无可操作节点';

  @override
  String get chatOpDone => '执行完成';

  @override
  String get seqReply => '回复消息';

  @override
  String get seqPublish => '发布朋友圈';

  @override
  String get seqLike => '点赞';

  @override
  String get seqPlay => '播放/切歌';

  @override
  String get seqCombo => '组合操作';

  @override
  String seqInputLine(Object text) {
    return '· 输入“$text”';
  }

  @override
  String get seqClick => '点击';

  @override
  String get seqLongClick => '长按';

  @override
  String seqClickLine(Object target, Object verb) {
    return '· $verb“$target”';
  }

  @override
  String get chatSeqTitle => 'AI 想按序列操作手机';

  @override
  String chatSeqDesc(Object autoNote, Object steps, Object type) {
    return '场景：$type\n$steps\n\n$autoNote干涉档位：轻度干涉（默认）——序列内不自动跳转 app，切换页面由你手动完成；密码/银行/支付类节点自动拒绝。';
  }

  @override
  String get chatSeqAutoNote => '将自动切换到朋友圈页执行（自家 app 内导航，不跳转其他 app）。\n';

  @override
  String get reject => '拒绝';

  @override
  String get allowOnce => '允许本次';

  @override
  String get allowMinute => '允许1分钟';

  @override
  String get chatOpDefault => '操作';

  @override
  String chatOpTarget(Object target) {
    return '点击/长按“$target”';
  }

  @override
  String get chatOpInput => '输入文本';

  @override
  String get chatOpTitle => 'AI 想操作你的手机';

  @override
  String chatOpDesc(Object op) {
    return '操作：$op\n仅作用于当前可见页面，不跨应用跳转；密码/银行/支付类节点已自动拒绝。';
  }

  @override
  String get chatInputTitle => 'AI 想帮你输入';

  @override
  String get chatInputHint => '输入内容（≤50字）';

  @override
  String chatInputHintTarget(Object target) {
    return '输入到“$target”（≤50字）';
  }

  @override
  String get input => '输入';

  @override
  String chatFileSendFail(Object err) {
    return '文件发送失败: $err';
  }

  @override
  String get chatContinuous => '连续发送';

  @override
  String get chatVoiceSend => '语音发送';

  @override
  String get chatEmoji => '表情';

  @override
  String get chatSendImage => '发送图片';

  @override
  String get chatImageCaption => '给图片配一句话（可选）...';

  @override
  String get send => '发送';

  @override
  String chatEmojiDownloaded(Object name) {
    return '已下载「$name」';
  }

  @override
  String get chatEmojiAdd => '添加';

  @override
  String chatEmojiHint(Object desc) {
    return '$desc（点击表情自动下载并发送）';
  }

  @override
  String get emojiMarketTab => '市场';

  @override
  String emojiMarketEmojiCount(Object count) {
    return '$count 个表情';
  }

  @override
  String get emojiMarketDownloading => '下载中…';

  @override
  String get emojiMarketDownloadFail => '下载失败，请重试';

  @override
  String get emojiMarketUninstall => '卸载';

  @override
  String get emojiMarketUninstalled => '已卸载';

  @override
  String get emojiMarketEmpty => '市场上还没有表情包';

  @override
  String get emojiMarketUnavailable => '表情市场暂不可用，请稍后再试';

  @override
  String get chatMicPermission => '需要麦克风权限才能发送语音';

  @override
  String get tzDefault => '默认（北京时间 UTC+8）';

  @override
  String get tzBeijing => '北京 UTC+8';

  @override
  String get tzTokyo => '东京 UTC+9';

  @override
  String get tzDubai => '迪拜 UTC+4';

  @override
  String get tzMoscow => '莫斯科 UTC+3';

  @override
  String get tzParis => '巴黎 UTC+1';

  @override
  String get tzLondon => '伦敦 UTC+0';

  @override
  String get tzNewYork => '纽约 UTC-5';

  @override
  String get tzLosAngeles => '洛杉矶 UTC-8';

  @override
  String get tzSydney => '悉尼 UTC+10';

  @override
  String get voiceXiaoxiao => '晓晓 · 自然女声';

  @override
  String get voiceXiaoyi => '晓伊 · 年轻女声';

  @override
  String get voiceXiaobei => '晓北 · 东北女声';

  @override
  String get voiceXiaoni => '晓妮 · 陕西女声';

  @override
  String get voiceXiaojia => '曉佳 · 粤语女声';

  @override
  String get voiceXiaoman => '曉曼 · 粤语女声';

  @override
  String get voiceXiaozhen => '曉臻 · 台湾女声';

  @override
  String get voiceYunxi => '云希 · 青年男声';

  @override
  String get voiceYunjian => '云健 · 磁性男声';

  @override
  String get voiceYunyang => '云扬 · 新闻男声';

  @override
  String get voiceYunfeng => '云枫 · 成熟男声';

  @override
  String get voiceYunlong => '雲龍 · 粤语男声';

  @override
  String get voicePreviewFailConfig => '试听失败：语音合成不可用，请检查服务器语音配置';

  @override
  String get voicePreviewFailNet => '试听失败，请检查网络';

  @override
  String get avatarUploadFail => '头像上传失败';

  @override
  String get voiceRate => '语速';

  @override
  String get voicePitch => '语调';

  @override
  String get pitchNormal => '正常';

  @override
  String get saveFail => '保存失败';

  @override
  String get createFriend => '创建好友';

  @override
  String get editFriend => '编辑好友';

  @override
  String get save => '保存';

  @override
  String get tapToPickAvatar => '点击选择头像（可选）';

  @override
  String get basicInfo => '基础信息';

  @override
  String get name => '名字';

  @override
  String get nameRequired => '名字不能为空';

  @override
  String get heightCm => '身高(cm)';

  @override
  String get weightKg => '体重(kg)';

  @override
  String get birthdayHint => 'YYYY-MM-DD（例如 1998-05-20）';

  @override
  String get gender => '性别';

  @override
  String get genderOther => '其他';

  @override
  String get genderFemale => '女';

  @override
  String get genderMale => '男';

  @override
  String get backgroundInfoHint => '例如：出身、经历、性格成因，越详细 AI 越有立体感';

  @override
  String get timezone => '所在时区';

  @override
  String get timezoneHelper => 'TA 所在地区的时区；朋友圈动态时间按此显示（默认=北京时间）';

  @override
  String get personalityGroup => '性格';

  @override
  String get personality => '人格';

  @override
  String get personalityHint => '例如：温柔体贴，善解人意';

  @override
  String get chatStyleHint => '例如：说话轻柔，喜欢用表情符号';

  @override
  String get talkativeness => '话痨度';

  @override
  String get talkativenessLocked => '锁定';

  @override
  String get talkativenessLockedHint => '锁定后 AI 不会自动调整话痨度';

  @override
  String get generateGreetingAsk => '是否生成问候语？';

  @override
  String get generateGreetingDesc => '用 LLM 为 TA 生成一句符合人设的开场白？';

  @override
  String get generateGreetingDo => '生成';

  @override
  String get generateGreetingSkip => '跳过';

  @override
  String get generateGreetingDone => '问候语已生成';

  @override
  String get generateGreetingFail => '生成失败，可稍后再试';

  @override
  String get voiceGroup => '声音';

  @override
  String get voiceLabel => '声音';

  @override
  String get voiceHelper => '语音对话时使用的音色（默认=按性别）';

  @override
  String get voiceDefault => '默认（按性别）';

  @override
  String get previewing => '试听中…';

  @override
  String get previewVoice => '试听当前音色';

  @override
  String get previewHint => '固定文案合成，即时预览音色与语速语调';

  @override
  String get deleteFriend => '删除好友';

  @override
  String get confirmDelete => '确认删除';

  @override
  String deleteFriendConfirm(Object name) {
    return '确定要删除「$name」吗？\n聊天记录和记忆将一并删除，不可恢复。';
  }

  @override
  String get delete => '删除';

  @override
  String get deleteFail => '删除失败';

  @override
  String get usageStats => '用量统计';

  @override
  String get myLlm => '我的 LLM（BYOK）';

  @override
  String get myLlmHint => '仅聊天主链路生效：开启后你自己的 OpenAI 兼容端点优先于服务器配置';

  @override
  String get enable => '启用';

  @override
  String get apiKeyConfigured => 'API Key 已配置';

  @override
  String get apiKeyNotConfigured => 'API Key 未配置，请填写下方 Key';

  @override
  String get apiKeyNotConfiguredShort => 'API Key 未配置';

  @override
  String get apiKeyKeep => 'API Key（留空保持不变）';

  @override
  String get apiKeyHintReplace => '已配置，输入新 Key 可替换（多个逗号分隔轮换）';

  @override
  String get testConnection => '检测连接';

  @override
  String get saveMyConfig => '保存我的配置';

  @override
  String get srvLlm => '服务器级 LLM（全局）';

  @override
  String get srvLlmHint => '仅主账号可管理：影响所有未配置 BYOK 的调用（日记/朋友圈/记忆等）';

  @override
  String get llmPresets => 'LLM 供应商预设';

  @override
  String get apiKeyRotateHint => '多个 Key 用逗号分隔自动轮换';

  @override
  String get saveSrvLlm => '保存服务器级 LLM';

  @override
  String get srvSpeech => '服务器级语音大模型';

  @override
  String get srvSpeechHint => '语音转写当前走本地 faster-whisper；云端 ASR 配置先落库，调用链路后续接入';

  @override
  String get speechPresets => '语音供应商预设';

  @override
  String get saveSrvSpeech => '保存服务器级语音';

  @override
  String get srvVlm => '服务器级识图（图片理解）';

  @override
  String get srvVlmHint =>
      '聊天/手机感知读图用：填 API Key 优先走云端视觉 API，不填则用本地 OCR（可选本地 VLM）';

  @override
  String get vlmPresets => '识图供应商预设';

  @override
  String get saveSrvVlm => '保存服务器级识图';

  @override
  String get srvImageGen => '服务器级生图（全局）';

  @override
  String get srvImageGenHint =>
      '聊天内 AI 发图使用；provider: dashscope=通义千问 / openai=OpenAI 兼容';

  @override
  String get imagePresets => '生图供应商预设';

  @override
  String get dailyLimit => '每日限额（张）';

  @override
  String get saveSrvImageGen => '保存服务器级生图';

  @override
  String get srvMultimodal => '服务器级全模态大模型';

  @override
  String get srvMultimodalHint => '多模态理解（图片/音频/视频进 LLM）；配置先落库，调用链路后续接入';

  @override
  String get multimodalPresets => '全模态供应商预设';

  @override
  String get saveSrvMultimodal => '保存服务器级全模态';

  @override
  String get srvTask => '服务器级任务模型（按用途指定）';

  @override
  String get srvTaskHint =>
      '记忆/卡片/情绪/状态/复习/主动消息/日记/时光可分别指定模型；API Key 多个逗号分隔自动轮换；留空回退服务器级 LLM';

  @override
  String get task => '任务';

  @override
  String get taskHint => '选择要指定模型的任务';

  @override
  String get saveTaskConfig => '保存任务配置';

  @override
  String get srvAdminOnly => '服务器级配置仅主账号（user_id=1）可管理，请联系部署者配置。';

  @override
  String saveSuccessEnabled(Object enabled) {
    return '保存成功（enabled=$enabled）';
  }

  @override
  String saveFailedErr(Object err) {
    return '保存失败: $err';
  }

  @override
  String loadConfigFailed(Object err) {
    return '加载配置失败：$err';
  }

  @override
  String connSuccess(Object latency, Object model, Object tail) {
    return '连接成功：$model（${latency}ms，Key尾号 $tail）';
  }

  @override
  String connFailed(Object err) {
    return '连接失败：$err';
  }

  @override
  String testRequestFailed(Object err) {
    return '测试请求失败：$err';
  }

  @override
  String get presetSelectHint => '选择后自动填入，Key 仍需手动填';

  @override
  String get model => '模型';

  @override
  String get provider => '供应商';

  @override
  String get setQuotaTotal => '设置免费额度总量';

  @override
  String get quotaHint => '单位：tokens（如 1000000）';

  @override
  String get quotaCleared => '已清除总额设置';

  @override
  String get quotaUpdated => '总额已更新';

  @override
  String get saveFailed => '保存失败';

  @override
  String unitYi(Object n) {
    return '$n亿';
  }

  @override
  String unitWan(Object n) {
    return '$n万';
  }

  @override
  String get llmUsageStats => 'LLM 用量统计';

  @override
  String get loadFailedCheckServer => '加载失败，请检查服务器连接';

  @override
  String get usedTotal => '已用 / 总额';

  @override
  String get setQuota => '设置总额';

  @override
  String totalTokensNoQuota(Object total) {
    return '$total tokens（未设置总额）';
  }

  @override
  String remainingTokens(Object remaining) {
    return '剩余约 $remaining tokens';
  }

  @override
  String get today => '今天';

  @override
  String get last7Days => '近 7 天';

  @override
  String get thisMonth => '本月';

  @override
  String get byModelUsage => '按模型用量';

  @override
  String get unknown => '未知';

  @override
  String etcModels(Object count) {
    return '等 $count 个模型';
  }

  @override
  String get usageNote => '用量由本 App 每次 LLM 调用自动累计（近似值，非官方数据）；总额请按百炼控制台免费额度手动填写';

  @override
  String get ppEnabledOn => '手机感知已开启，请按需打开下方采集项';

  @override
  String get ppEnabledOff => '手机感知已关闭';

  @override
  String get ppOpenAccessibility => '请在系统无障碍设置中开启“拥爱手机感知”';

  @override
  String get ppUsageNotGranted => '仍未检测到“使用情况访问”授权，请到系统设置确认后重试';

  @override
  String ppUsageGrantedWith(Object content) {
    return '授权成功，已开启应用使用时长：$content';
  }

  @override
  String get ppUsageGrantedEmpty => '授权成功，已开启应用使用时长（暂无数据，稍后自动上报）';

  @override
  String get ppUsageOpenSettings => '请在系统“使用情况访问”中允许“拥爱”，返回后将自动生效';

  @override
  String ppUsageEnabledWith(Object content) {
    return '已开启应用使用时长：$content';
  }

  @override
  String get ppUsageEnabledEmpty => '应用使用时长已开启（暂无数据，稍后自动上报）';

  @override
  String get ppUsageDisabled => '已关闭应用使用时长';

  @override
  String get ppOpenNotification => '请在系统“通知使用权”中开启“拥爱通知感知”';

  @override
  String get ppMediaDenied => '未获得相册权限，请到系统设置中允许访问照片';

  @override
  String get ppMediaFilesDenied => '未获得视频/音频权限，请到系统设置中允许';

  @override
  String ppCollectedWith(Object preview) {
    return '已采集：$preview';
  }

  @override
  String get ppCollectDisabled => '手机感知未开启，请先开启总开关';

  @override
  String get ppCollectNoSources => '未选择采集项，请先勾选下方采集项';

  @override
  String get ppCollectEmpty => '暂无可采集的信息（可能不在本 app 页面）';

  @override
  String get ppCollectNetworkError => '上传失败，请检查网络后重试';

  @override
  String get ppCollectDone => '已完成采集';

  @override
  String get ppClearedAll => '已清除全部手机感知快照';

  @override
  String get ppClearFailed => '清除失败，请稍后重试';

  @override
  String ppShizukuUploadFailed(Object text) {
    return '$text（上报失败）';
  }

  @override
  String get ppLocEnabledOn => '位置信息已开启，AI 可感知你所在城市';

  @override
  String get ppLocEnabledOff => '位置信息已关闭';

  @override
  String ppLocGpsEnabledWith(Object loc) {
    return '已开启获取地理位置：$loc';
  }

  @override
  String get ppLocGpsDisabled => '已关闭获取地理位置';

  @override
  String get ppLocServiceOff => '手机定位服务未开启，请到系统设置中打开';

  @override
  String get ppLocDeniedForever => '定位权限被永久拒绝，请到系统设置中手动授权';

  @override
  String get ppLocNoPermission => '未获得定位权限，无法获取地理位置';

  @override
  String ppLocFailed(Object err) {
    return '定位失败：$err。可到窗边/室外重试，或在系统定位服务里开启“提高精确度”（Wi-Fi/蓝牙扫描）';
  }

  @override
  String ppLocCityLocated(Object city) {
    return '$city（已定位，不可自定义）';
  }

  @override
  String ppLocCoordsLocated(Object lat, Object lng) {
    return '$lat,$lng（已定位）';
  }

  @override
  String get ppLocLocating => '定位中…（获取地理位置已开启）';

  @override
  String get ppLocUnset => '未设置，点击填写';

  @override
  String ppLocFollowUser(Object loc) {
    return '与用户位置相同：$loc';
  }

  @override
  String get ppLocNotSet => '未设置';

  @override
  String ppLocUser(Object loc) {
    return '用户：$loc';
  }

  @override
  String get ppLocFollow => 'AI 跟随用户';

  @override
  String get ppLocGpsOn => '定位已开启';

  @override
  String get ppLocUnsetExpand => '未设置位置，展开配置';

  @override
  String get ppLocSetUser => '设置用户位置';

  @override
  String get ppLocSetAi => '设置 AI 位置';

  @override
  String get ppLocHint => '如：广州 / 北京 / Tokyo';

  @override
  String get ppSourceScreen => '屏幕';

  @override
  String get ppSourceClipboard => '剪贴板';

  @override
  String get ppSourceMedia => '相册';

  @override
  String get ppSourceNotification => '通知';

  @override
  String get ppClipboard => '剪贴板';

  @override
  String get ppSubtitleOn => '开启后 AI 好友可在你允许时了解手机状态';

  @override
  String get ppSubtitleOff => '默认关闭，开启后请选择下方采集项';

  @override
  String get ppGroupSources => '采集项';

  @override
  String get ppScreenTitle => '读屏（无障碍）';

  @override
  String get ppScreenRunning => '服务运行中，会缓存最近非本 app 页面文字';

  @override
  String get ppScreenOff => '未开启，点击后请到系统无障碍设置中开启';

  @override
  String get ppClipboardSub => '读取你最近复制的内容（仅聊天时前台读取）';

  @override
  String get ppMediaTitle => '相册最近图片';

  @override
  String get ppMediaSub => '读取最近 8 张图片的文件名与时间（不读取图片内容）';

  @override
  String get ppMediaFilesTitle => '媒体文件（视频/音频/文档）';

  @override
  String get ppMediaFilesSub => '读取最近视频/音频/文档的文件名与时间（仅元数据；文档需“所有文件访问”权限）';

  @override
  String get ppUsageStatsTitle => '应用使用时长';

  @override
  String get ppUsageStatsGranted => '最近 24h 各应用使用时长，每 30 分钟自动上报给 AI';

  @override
  String get ppUsageStatsNotGranted => '未授权：开启后请到系统“使用情况访问”中允许';

  @override
  String get ppActionsTitle => '模拟操作';

  @override
  String get ppActionsOn => 'AI 可在你确认后点击/长按/滑动/输入（仅当前屏幕节点，敏感页面拒绝，默认关）';

  @override
  String get ppActionsOff => '默认关闭：AI 在获得你单次确认后帮你操作手机';

  @override
  String get ppWorkflowTitle => '自定义工作流';

  @override
  String get ppWorkflowSub => '自建多步操作序列，对 AI 说“帮我执行 XX”即可触发（系统级操作需 Shizuku 授权）';

  @override
  String get ppNotificationTitle => '通知读取';

  @override
  String get ppNotifRunning => '服务运行中，会缓存最近收到的 app 通知文字';

  @override
  String get ppNotifOff => '未开启，点击后请到系统“通知使用权”中开启';

  @override
  String get ppAutoNotifyTitle => 'AI 主动提及通知';

  @override
  String get ppAutoNotifySub => 'AI 约每 5 分钟检查一次，主动提起你手机收到的新通知（默认关）';

  @override
  String get ppWhitelistTitle => '通知白名单';

  @override
  String get ppWhitelistSub => '勾选后只感知指定 app 的通知（默认全部）';

  @override
  String get ppShizukuTitle => 'Shizuku 权限';

  @override
  String get ppShizukuSub => '系统级能力（应用列表/系统设置/模拟操作前置）：状态、授权与 Shell 测试';

  @override
  String get ppShizukuServer => 'Shizuku 服务';

  @override
  String get ppShizukuGranted => '本应用授权';

  @override
  String get ppReady => '已就绪';

  @override
  String get ppNotReady => '未就绪';

  @override
  String get ppCollecting => '采集中…';

  @override
  String get ppCollectShizuku => '采集系统状态并告诉 AI';

  @override
  String get ppGroupLocation => '位置';

  @override
  String get ppLocationTitle => '位置信息';

  @override
  String get ppLocSubtitleOn => 'AI 可感知你所在城市，提供更自然的时间感知';

  @override
  String get ppLocSubtitleOff => '默认关闭：开启后 AI 才知道你在哪里';

  @override
  String get ppLocGpsTitle => '获取地理位置';

  @override
  String get ppLocGpsOnSub => '已开启：用户位置由定位获取，不可自定义';

  @override
  String get ppLocGpsOffSub => '开启后自动获取你所在位置';

  @override
  String get ppLocUserTitle => '用户位置';

  @override
  String get ppLocAiTitle => 'AI 位置';

  @override
  String get ppLocFollowTitle => '位置跟随';

  @override
  String get ppLocFollowOnSub => 'AI 位置与用户相同，不可自定义';

  @override
  String get ppLocFollowOffSub => '开启后 AI 位置跟随用户位置';

  @override
  String get ppGroupPrivacy => '隐私说明';

  @override
  String get ppPrivacyNote =>
      '· 全部能力默认关闭，逐项授权，关闭立即生效\n· 仅读取文本与图片元数据；图片如需理解，走本地 OCR/VLM 转文字，绝不上传云端模型\n· 密码框、银行支付类页面自动跳过\n· 数据只发送到你自己的服务器，快照 30 分钟过期、最多保留 20 条';

  @override
  String get ppGroupActions => '操作与记录';

  @override
  String get ppCollectNowTitle => '立即采集一次';

  @override
  String get ppCollectNowSub => '采集当前屏幕/剪贴板/相册并告诉 AI';

  @override
  String get ppHistoryTitle => '历史记录';

  @override
  String get ppNoSnapshots => '暂无快照';

  @override
  String ppRecentCount(Object count) {
    return '最近 $count 条';
  }

  @override
  String get ppLockContentName => '手机';

  @override
  String get ppClearAll => '清除全部快照';

  @override
  String ppLocAi(Object loc) {
    return 'AI：$loc';
  }

  @override
  String get providerLocalHint => 'openai / dashscope / 本地';

  @override
  String get worldGroup => '角色设定';

  @override
  String get lorebookTitle => '设定条目（Lorebook）';

  @override
  String get lorebookHint => '关键词触发注入的既定设定，对话提到即生效';

  @override
  String get lorebookAdd => '新增条目';

  @override
  String get lorebookEdit => '编辑条目';

  @override
  String get lorebookTitleField => '标题';

  @override
  String get lorebookContentField => '内容';

  @override
  String get lorebookKeywords => '关键词（≥2 字，逗号分隔）';

  @override
  String get lorebookKeywordsHint => '对话出现任一关键词即注入，如：我养的猫';

  @override
  String get lorebookExclude => '排除词（出现则不触发）';

  @override
  String get lorebookExcludeHint => '防止误触发，如：猫屎咖啡';

  @override
  String get lorebookEmpty => '暂无设定条目';

  @override
  String get lorebookActive => '启用';

  @override
  String get lorebookStyleHint => '用第三人称简洁描述设定内容（如：用户养了一只叫团团的橘猫）';

  @override
  String get lorebookRegex => '正则匹配';

  @override
  String get lorebookRegexHint => '把关键词当作正则表达式匹配（默认关闭，按子串匹配）';

  @override
  String get lorebookProbability => '触发概率';

  @override
  String get lorebookProbabilityHint => '关键词命中时注入的概率（100=必注入，0=不注入）';

  @override
  String get lorebookGroup => '包含组';

  @override
  String get lorebookGroupHint => '同一组的条目同轮只注入一条（留空=不分组）';

  @override
  String get lorebookSticky => '粘性轮数';

  @override
  String get lorebookStickyHint => '触发后持续注入的轮数（0=不持续）';

  @override
  String get lorebookCooldown => '冷却轮数';

  @override
  String get lorebookCooldownHint => '触发后 N 轮内不再注入（0=关闭）';

  @override
  String get worldFactsTitle => '世界设定';

  @override
  String get worldFactsHint => '你定义的不可动摇事实，AI 推断不能覆盖';

  @override
  String get worldFactsEmpty => '暂无世界设定';

  @override
  String get worldFactAdd => '添加设定';

  @override
  String get worldFactContentHint => '如：我住在杭州 / 我养了一只叫团团的猫';

  @override
  String get cancel => '取消';

  @override
  String get cancelled => '已取消';

  @override
  String get changeFailed => '修改失败';

  @override
  String get changePassword => '修改密码';

  @override
  String get changePasswordHint => '长度≥8，需同时包含字母和数字';

  @override
  String get charLife => '角色生活';

  @override
  String charPetTitle(Object char, Object pet) {
    return '$char 的 $pet';
  }

  @override
  String get charSettings => '角色设置';

  @override
  String get chatArchive => '聊天记录箱';

  @override
  String get chatArchiveHint => '历史聊天记录';

  @override
  String chatOf(Object name) {
    return '$name的畅聊';
  }

  @override
  String get chatStyle => '聊天风格';

  @override
  String get checkIn => '查岗';

  @override
  String get checkInHint => '开启后 AI 主动找你时，能自然知道你正在用什么软件';

  @override
  String get checking => '正在检测...';

  @override
  String get chooseFriendFirst => '请先在好友页面选择一位AI好友';

  @override
  String get chooseSpecies => '选择种类';

  @override
  String get clean => '清洁';

  @override
  String get cleanliness => '清洁度';

  @override
  String get close => '关闭';

  @override
  String get cognitiveLoop => '认知循环';

  @override
  String get cognitiveLoopHint => '开启后 AI 会带入当前状态、进行中话题与关系温度，对话与主动消息更懂你（默认关闭）';

  @override
  String get coldWar => '冷战断联';

  @override
  String get coldWarHint => '生气冷战期不回复消息，直到你哄好TA';

  @override
  String get collapse => '收起';

  @override
  String get comingSoon => '即将上线';

  @override
  String comingSoonTemplate(Object feature) {
    return '$feature 功能即将上线';
  }

  @override
  String get commentHint => '输入评论...';

  @override
  String get completed => '已完成';

  @override
  String get confirm => '确认';

  @override
  String get connectFail => '连接失败，请检查地址';

  @override
  String get connectFailed => '连接失败';

  @override
  String get connectSuccess => '连接成功';

  @override
  String get connected => '已连接';

  @override
  String get connectionStatus => '连接状态';

  @override
  String get content => '内容';

  @override
  String get contentAiHint => '内容（AI 好友会在聊天中读到）';

  @override
  String get contentRequired => '内容不能为空';

  @override
  String get control => '管制';

  @override
  String get controlComingSoon => '「管制」功能待设计，敬请期待';

  @override
  String get controlHint => '查岗的子功能（待设计中）';

  @override
  String get copied => '已复制';

  @override
  String get copy => '复制';

  @override
  String get create => '创建';

  @override
  String get createGroup => '创建群聊';

  @override
  String get createGroupDialog => '创建家庭群聊';

  @override
  String get createRoleHint =>
      '先去创建一个 AI 角色吧\n每个角色都会有一台自己的小手机\n多角色家庭群聊开发中，敬请期待';

  @override
  String get creationGroup => '创作';

  @override
  String currentPreview(Object mode, Object color) {
    return '当前：$mode · $color';
  }

  @override
  String get currentUser => '当前用户';

  @override
  String get dailyGroup => '日常';

  @override
  String get dark => '深色';

  @override
  String get date => '日期';

  @override
  String get dateArchive => '日期归档';

  @override
  String get dateFormatHint => '日期格式应为 YYYY-MM-DD';

  @override
  String dateFull(Object year, Object month, Object day) {
    return '$year年$month月$day日';
  }

  @override
  String get dateLinePattern => 'M月d日 EEEE';

  @override
  String dateMonthDay(Object month, Object day) {
    return '$month月$day日';
  }

  @override
  String dateNotes(Object date) {
    return '$date 的备注';
  }

  @override
  String dayLabel(Object m, Object d) {
    return '$m月$d日';
  }

  @override
  String daysCount(Object n) {
    return '$n 天';
  }

  @override
  String daysKnown(Object name, Object days) {
    return '认识 $name 第 $days 天';
  }

  @override
  String get deepThinking => '深度思考';

  @override
  String get deleteCountdown => '删除倒计时 · 3 天内自动清除';

  @override
  String deleteDiaryConfirm(Object date) {
    return '确定删除 $date 的日记？';
  }

  @override
  String get deleteDiaryTitle => '删除日记';

  @override
  String get deleteEmojiConfirm => '确定删除这个表情吗？';

  @override
  String get deleteEmojiTitle => '删除表情';

  @override
  String get deleteFailed => '删除失败，请重试';

  @override
  String deleteFailedErr(Object err) {
    return '删除失败: $err';
  }

  @override
  String get deleteGroup => '删除群聊';

  @override
  String get deleteGroupConfirm => '将删除群及全部消息，确定吗？';

  @override
  String deleteInDays(Object days) {
    return '剩余 $days 天后删除';
  }

  @override
  String deleteInHours(Object hours) {
    return '剩余 $hours 小时后删除';
  }

  @override
  String get deleteMemoConfirm => '删除后 AI 好友将不再看到这条备忘录，确定删除？';

  @override
  String get deleteMemoTitle => '删除备忘录';

  @override
  String get deleteMemoryConfirm => '确定要删除这条记忆吗？删除后不可恢复。';

  @override
  String get deleteMemoryTitle => '删除记忆';

  @override
  String get deleteMessageConfirm => '删除后无法恢复，确定删除这条消息吗？';

  @override
  String get deleteMessageTitle => '删除消息';

  @override
  String get deleteMoment => '删除动态';

  @override
  String get deleteMomentConfirm => '确定删除这条朋友圈吗？它的评论和点赞将一并删除。';

  @override
  String get deletePhoto => '删除照片';

  @override
  String get deletePhotoConfirm => '删除后无法恢复，确定删除这张照片吗？';

  @override
  String get deleteSoon => '即将删除';

  @override
  String get deleteTimerTooltip => '删除这个计时';

  @override
  String get deleted => '已删除';

  @override
  String get deny => '拒绝';

  @override
  String get detailTitle => '详情';

  @override
  String get diary => '日记';

  @override
  String diaryCount(Object count) {
    return '$count 篇';
  }

  @override
  String get diaryHint => 'TA每天写的日记';

  @override
  String diaryTitle(Object name) {
    return '$name的日记';
  }

  @override
  String get disconnected => '未连接';

  @override
  String get dnd => '免打扰';

  @override
  String get dndHint => '设置免打扰时段';

  @override
  String get dndOff => '关闭时沿用默认（凌晨 0-7 点静默）';

  @override
  String dndOn(Object start, Object end) {
    return '$start - $end 不发送主动消息';
  }

  @override
  String get dndPeriod => '免打扰时段';

  @override
  String get doSomething => '去做某事';

  @override
  String get done => '完成';

  @override
  String get download => '下载';

  @override
  String get downloadPack => '下载表情包';

  @override
  String get dragEditHint => '拖动图标换位 · 点 ✕ 删除';

  @override
  String durationMin(Object min) {
    return '$min 分钟';
  }

  @override
  String durationSec(Object sec) {
    return '$sec 秒';
  }

  @override
  String get edit => '编辑';

  @override
  String get editDiary => '编辑日记';

  @override
  String get editMemo => '编辑备忘录';

  @override
  String get emojiAdded => '表情已添加';

  @override
  String get emojiPack => '表情包';

  @override
  String get emotionAll => '全部';

  @override
  String get emotionFilter => '情绪';

  @override
  String emotionMemoryTitle(Object name) {
    return '$name · 情绪记忆';
  }

  @override
  String get end => '结束';

  @override
  String get energy => '精力';

  @override
  String get english => 'English';

  @override
  String get eventClockEmpty => '暂无进行中的计时';

  @override
  String get eventClockHint => '进行中的计时（到点会提醒）';

  @override
  String get eventClockTitle => '事件时钟';

  @override
  String get expand => '展开';

  @override
  String get extensions => '扩展';

  @override
  String get extensionsHint => '服务器端插件（Hook 扩展 AI 能力）';

  @override
  String get feed => '喂食';

  @override
  String get file => '文件';

  @override
  String get fileTooLarge => '文件不能超过 20MB';

  @override
  String get followSystem => '跟随系统';

  @override
  String get fontIconFuture => '字体 / 图标（未来开放）';

  @override
  String get fontIconHint => '目前提供壁纸更换；字体与图标美化后续版本开放。';

  @override
  String get furnitureInactive => '这个家具暂时不能互动';

  @override
  String get goal => '目标';

  @override
  String get goalActive => '进行中';

  @override
  String get goalCompleted => '已完成';

  @override
  String get goalFailed => '未完成';

  @override
  String get goalTypeCreative => '创造';

  @override
  String get goalTypeExplore => '探索';

  @override
  String get goalTypeGrowth => '成长';

  @override
  String get goalTypeRelationship => '关系';

  @override
  String get goalTypeSkill => '技能';

  @override
  String get groupAddFail => '添加失败';

  @override
  String get groupChatEmpty => '群聊还没有消息，说点什么吧';

  @override
  String get groupInputHint => '对群里的角色们说点什么…';

  @override
  String get groupMemberEmpty => '群成员为空';

  @override
  String get groupMembers => '群成员';

  @override
  String get groupNameLabel => '群名称';

  @override
  String get groupMuteFail => '操作失败';

  @override
  String get groupRemoveFail => '移除失败';

  @override
  String get mute => '静音';

  @override
  String get unmute => '取消静音';

  @override
  String get groupReplying => '角色们正在回复…';

  @override
  String get groupTitle => '家庭群聊';

  @override
  String get height => '身高';

  @override
  String get high => '高';

  @override
  String get highFreq => '高频';

  @override
  String get holdToTalk => '按住下方按钮说话';

  @override
  String get homeTitle => '小家';

  @override
  String homeTitleMine(Object nickname) {
    return '$nickname的小家';
  }

  @override
  String homeTitleWithLover(Object lover, Object nickname) {
    return '$nickname与$lover的小家';
  }

  @override
  String get homeLayoutDragHint => '长按家具可拖动摆放';

  @override
  String get homeLayoutSaveFailed => '布局保存失败，已还原';

  @override
  String get homeLayoutSaved => '布局已保存';

  @override
  String get homeWorldMap => '小家地图';

  @override
  String get homeExit => '出口';

  @override
  String get homeGoOut => '出门';

  @override
  String get furnitureEdit => '家具编辑';

  @override
  String get furnitureEditHint => '拖动或点选家具进行编辑';

  @override
  String get furnitureRevert => '回退';

  @override
  String get furnitureRotate => '旋转';

  @override
  String get furnitureConfirm => '确定';

  @override
  String get hunger => '饱食度';

  @override
  String get image => '图片';

  @override
  String get imageGen => '生图';

  @override
  String get imageGenHint => '允许AI生成图片并发送给你（需服务器已配置生图服务）';

  @override
  String get imageSelected => '已选择 1 张图片';

  @override
  String get importanceHigh => '很重要';

  @override
  String get importanceLow => '一般';

  @override
  String get importanceMax => '极其重要';

  @override
  String get importanceMedium => '重要';

  @override
  String get importanceTitle => '重要性';

  @override
  String get importanceVeryHigh => '非常重要';

  @override
  String get inProgress => '进行中';

  @override
  String get inputHint => '输入消息...';

  @override
  String get inputHintBatch => '连续发送中，输入并收集消息...';

  @override
  String get installed => '已安装';

  @override
  String get interact => '互动';

  @override
  String get interactFailed => '互动失败，请稍后重试';

  @override
  String get interactHintBase => '点击宠物玩耍 · 点击食物喂食';

  @override
  String get interactHintClean => ' · 点击💩清洁';

  @override
  String get interests => '兴趣';

  @override
  String get interestsGoalsTab => '兴趣与目标';

  @override
  String interestsGoalsTitle(Object name) {
    return '$name的兴趣与目标';
  }

  @override
  String get journeyDesc => '从第一句“你好”到现在，我们一起经历的事';

  @override
  String get language => '语言';

  @override
  String lifeHomeTitle(Object name) {
    return '$name · AI 生活';
  }

  @override
  String get lifeIntensity => '离线生活强度';

  @override
  String get lifeIntensityHint => '越高角色生活越活跃（tick 更频繁、token 消耗更高）';

  @override
  String get lifeShare => 'AI 生活分享';

  @override
  String get lifeShareHint => '角色会自然提起自己的生活点滴（信任越高越常提起）';

  @override
  String get lifeTypeGoal => '目标';

  @override
  String get lifeTypeInterest => '兴趣';

  @override
  String get lifeTypeLife => '生活';

  @override
  String get lifeTypeNote => '笔记';

  @override
  String get lifeTypeReflection => '反思';

  @override
  String get light => '浅色';

  @override
  String get like => '点赞';

  @override
  String likersText1(Object names) {
    return '$names 觉得很赞';
  }

  @override
  String likersTextMany(Object names, Object count) {
    return '$names 等 $count 人觉得很赞';
  }

  @override
  String get listMode => '列表模式';

  @override
  String get loadFailed => '加载失败';

  @override
  String loadFailedErr(Object err) {
    return '加载失败: $err';
  }

  @override
  String loadHomeFailed(Object err) {
    return '加载小家失败：$err';
  }

  @override
  String loadOriginalFailed(Object err) {
    return '加载原文失败: $err';
  }

  @override
  String get loadPetFailed => '加载角色宠物失败，请稍后重试';

  @override
  String get lockMemory => '锁定记忆（不遗忘）';

  @override
  String get lockedFrozen => '已锁定：强度与重要性冻结，不再遗忘';

  @override
  String get lockedNoDecay => '已锁定：不衰减 · 不删除';

  @override
  String get login => '登录';

  @override
  String get loginFailed => '登录失败';

  @override
  String get logout => '退出登录';

  @override
  String get longPressAbandon => '长按顶部宠物名片可遗弃';

  @override
  String get low => '低';

  @override
  String get lowFreq => '低频';

  @override
  String get manualMoment => '手动发送角色朋友圈';

  @override
  String get manualMomentHint => '让TA现在发一条动态';

  @override
  String get marketDetailHooks => 'Hook 挂载点';

  @override
  String get marketDetailPermissions => '权限声明';

  @override
  String get marketHint => '误删的应用可以在这里下回来';

  @override
  String get marketInstall => '安装';

  @override
  String get marketInstallFailed => '安装失败';

  @override
  String get marketInstallSuccess => '安装成功，可在「扩展」中启用';

  @override
  String get marketInstalled => '已安装';

  @override
  String get marketNoResult => '没有找到匹配的插件';

  @override
  String get marketRiskTip => '第三方插件与服务器同权限，请确认来源可信后再安装';

  @override
  String get marketTrustTip => '⚠️ 仅安装可信插件：插件与后端进程同权限（无沙箱）。请只安装来源可信的插件。';

  @override
  String get marketSearchHint => '搜索插件名称或描述';

  @override
  String get marketSourceBuiltin => '内置';

  @override
  String get marketTitle => '应用市场';

  @override
  String get marketplace => '插件市场';

  @override
  String get marketplaceHint => '发现并一键安装插件（内置市场）';

  @override
  String get marketSourceRemote => '远程';

  @override
  String get marketRemoteUpdate => '更新';

  @override
  String get marketRemoteUpToDate => '已是最新';

  @override
  String get marketRemoteInstallTip => '该插件来自远程第三方市场，与服务器同权限。请确认来源可信后再安装？';

  @override
  String get marketRemoteConfig => '远程市场';

  @override
  String get marketRemoteConfigHint => '配置远程市场地址，从第三方仓库发现并安装插件';

  @override
  String get marketRemoteEnabled => '启用远程市场';

  @override
  String get marketRemoteUrls => '市场地址（每行一个，https）';

  @override
  String get marketRemoteUrlsHint =>
      '例如：https://raw.githubusercontent.com/AMBRACE-plugin/index.json';

  @override
  String get marketRemoteRefreshInterval => '自动刷新间隔（小时）';

  @override
  String get marketRemoteAllowedHosts => '域名白名单（留空=不限 https）';

  @override
  String get marketRemoteAllowedHostsHint => '每行一个域名，例如 market.example.com';

  @override
  String get marketRemoteMaxZip => '安装包大小上限（MB）';

  @override
  String get marketRemoteSave => '保存配置';

  @override
  String get marketRemoteRefreshNow => '立即刷新';

  @override
  String get marketRemoteRefreshing => '刷新中…';

  @override
  String get marketRemoteSaved => '配置已保存';

  @override
  String marketRemoteRefreshed(Object ok) {
    return '刷新完成：$ok 个市场更新成功';
  }

  @override
  String get marketRemoteNotReady => '未启用远程市场或未配置地址';

  @override
  String marketRemoteLastRefresh(Object time) {
    return '上次刷新：$time';
  }

  @override
  String get marketRemoteNever => '从未刷新';

  @override
  String get marketRemoteAdd => '添加市场地址';

  @override
  String get marketRemoteConfirmDelete => '删除该市场地址？';

  @override
  String get me => '我';

  @override
  String get medium => '中';

  @override
  String get memoHint => '记点什么...（AI 也会主动记录）';

  @override
  String get memoTitle => '备忘录';

  @override
  String get memoTitleHint => '标题（可选）';

  @override
  String get memoryAll => '全部';

  @override
  String get memoryBook => '记忆本';

  @override
  String get memoryBookHint => '与TA的共同记忆';

  @override
  String memoryBookTitle(Object name) {
    return '$name 的记忆本';
  }

  @override
  String memoryCount(Object count) {
    return '$count 条记忆';
  }

  @override
  String get memoryDetailTitle => '记忆详情';

  @override
  String get memoryChainTitle => '记忆链条';

  @override
  String get memoryChainChildren => '关联记忆';

  @override
  String get memoryChainEmpty => '暂无关联记忆';

  @override
  String get memoryEditContent => '修改内容';

  @override
  String get memoryEditContentHint => '输入新的记忆内容';

  @override
  String get memorySaveEdit => '保存';

  @override
  String get memoryDeleteCascadeTitle => '级联删除';

  @override
  String get memoryDeleteCascadeConfirm => '将删除本条及其所有关联记忆，确定？';

  @override
  String get memoryUpdatedOk => '记忆已更新';

  @override
  String get memoryEvent => '事件';

  @override
  String get memoryImpression => '印象';

  @override
  String get memoryInsight => '洞察';

  @override
  String get memoryPreference => '偏好';

  @override
  String get memorySourceCharacter => '角色';

  @override
  String get memorySourceUser => '用户';

  @override
  String get memoryReview => '记忆复习';

  @override
  String get memoryReviewHint => 'AI会顺着聊天自然地提起记得的事（记得你的陪伴感）';

  @override
  String memoryStrength(Object pct) {
    return '记忆强度 $pct%';
  }

  @override
  String get menu => '菜单';

  @override
  String get mine => '我的';

  @override
  String get minutesLater => ' 分钟后';

  @override
  String momentDateFull(Object year, Object month, Object day, Object time) {
    return '$year年$month月$day日 $time';
  }

  @override
  String get momentHint => '说点什么...';

  @override
  String get momentLimit => '今日已达发布上限';

  @override
  String momentPublishFailed(Object msg) {
    return '发布失败: $msg';
  }

  @override
  String momentPublished(Object content) {
    return '朋友圈已发布: $content';
  }

  @override
  String get moments => '朋友圈';

  @override
  String get momentsComment => '朋友圈评论、回复';

  @override
  String get momentsCommentHint => 'AI会评论动态并回复用户的评论';

  @override
  String momentsCount(Object count) {
    return '$count条';
  }

  @override
  String get momentsHint => 'AI会发布朋友圈动态';

  @override
  String get month1 => '一月';

  @override
  String get month10 => '十月';

  @override
  String get month11 => '十一月';

  @override
  String get month12 => '十二月';

  @override
  String get month2 => '二月';

  @override
  String get month3 => '三月';

  @override
  String get month4 => '四月';

  @override
  String get month5 => '五月';

  @override
  String get month6 => '六月';

  @override
  String get month7 => '七月';

  @override
  String get month8 => '八月';

  @override
  String get month9 => '九月';

  @override
  String get monthApr => '四月';

  @override
  String get monthAug => '八月';

  @override
  String get monthDec => '十二月';

  @override
  String get monthFeb => '二月';

  @override
  String get monthJan => '一月';

  @override
  String get monthJul => '七月';

  @override
  String get monthJun => '六月';

  @override
  String get monthMar => '三月';

  @override
  String get monthMay => '五月';

  @override
  String get monthNov => '十一月';

  @override
  String monthNumFallback(Object num) {
    return '$num月';
  }

  @override
  String monthNumeric(Object month) {
    return '$month月';
  }

  @override
  String get monthOct => '十月';

  @override
  String get monthSep => '九月';

  @override
  String get mood => '心情';

  @override
  String get moodBadge => '聊天页心情标识';

  @override
  String get moodBadgeHint => '聊天页角色名字旁显示当前心情表情（纯展示，独立开关）';

  @override
  String get moodGood => '心情不错';

  @override
  String get moodGreat => '心情好';

  @override
  String get moodLow => '有点低落';

  @override
  String get moodOk => '心情一般';

  @override
  String get moreFunctions => '更多功能';

  @override
  String msgCount(Object n) {
    return '$n 条消息';
  }

  @override
  String msgCountShort(Object n) {
    return '$n 条';
  }

  @override
  String get myDiary => '我的日记';

  @override
  String get myEmoji => '我的表情';

  @override
  String get myMemos => '我的备忘录';

  @override
  String get myPhone => '我的手机';

  @override
  String get myPhoneComingSoon => '我的手机 · 敬请期待（未来将映射你的手机，让 AI 可以了解你的使用）';

  @override
  String get myUploads => '我的上传';

  @override
  String get nameTooLong => '名字最多 5 个字';

  @override
  String get needTwoChars => '至少需要 2 个角色才能创建家庭群聊';

  @override
  String get newMemo => '新增备忘录';

  @override
  String get newPasswordHint => '新密码（≥8位，字母+数字）';

  @override
  String get nickname => '昵称';

  @override
  String get nicknameOptional => '昵称（可选）';

  @override
  String get noAccountRegister => '没有账号？注册';

  @override
  String get noActivities => '还没有互动记录，摸摸宠物吧～';

  @override
  String get noAddableChars => '没有可添加的角色';

  @override
  String get noAiImages => '暂无 AI 生成图片';

  @override
  String get noArchive => '暂无聊天记录';

  @override
  String get noArtifacts => '还没有产物，等 TA 离线时创作吧';

  @override
  String get noBrowsingRecords => '还没有真实浏览记录';

  @override
  String get noCharacters => '还没有角色，先去创建 AI 角色吧';

  @override
  String get noChars => '暂无角色';

  @override
  String get noChatRecords => 'TA 还没有聊天记录';

  @override
  String get noChats => '暂无聊天';

  @override
  String get noDetails => '（无详情）';

  @override
  String get noDiary => '暂无日记';

  @override
  String get noDiaryHint => '还没有日记，点右下角写一篇吧（AI 好友会看到）';

  @override
  String get noEmoji => '暂无表情';

  @override
  String get noEmotionRecords => '还没有情绪记录\n多和 TA 聊聊，情绪波动会自动记录在这里';

  @override
  String get noGoals => '还没有目标';

  @override
  String get noGroups => '还没有家庭群聊';

  @override
  String get noInterests => '还没有兴趣记录';

  @override
  String get noLifeRecords => '还没有生活记录';

  @override
  String get noMemories => '暂无比记忆';

  @override
  String get noMemoriesInCategory => '该分类下无比记忆';

  @override
  String get noMemos => '暂无备忘录';

  @override
  String get noMemosHint => '还没有备忘录，点右下角 + 添加一条吧';

  @override
  String get noMilestones => '还没有值得纪念的时刻，多聊聊就有了';

  @override
  String get noMoments => '暂无动态';

  @override
  String get noNearbyFurniture => '附近没有可互动的家具';

  @override
  String noPetForChar(Object name) {
    return '$name 还没有宠物';
  }

  @override
  String get noPets => '暂无宠物';

  @override
  String get noResultsHint => '未找到相关结果，换个关键词试试';

  @override
  String get noSelfStatement => 'TA 还没有自述，聊一聊之后会慢慢形成';

  @override
  String get noUploadsHint => '暂无上传图片，点右上角上传';

  @override
  String get notCompleted => '未完成';

  @override
  String get noteHint => '写一条备注（AI 也会看到）...';

  @override
  String get notifyWhitelist => '通知白名单';

  @override
  String get notifyWhitelistEmpty => '暂无通知记录：先让几个 app 发通知，再回来勾选白名单';

  @override
  String get notifyWhitelistHint =>
      '未勾选任何 app = 全部允许；勾选后只感知勾选的通知。\n下方列表来自最近感知到的通知，勾选的 app 通知才会被 AI 看到。';

  @override
  String get off => '关闭';

  @override
  String get offlineLifeHint => '开启「AI 离线生活」后，角色会在离线时真实度过时间';

  @override
  String get oldPassword => '旧密码';

  @override
  String opFailedErr(Object err) {
    return '操作失败: $err';
  }

  @override
  String get opFailedRetry => '操作失败，请重试';

  @override
  String openFailed(Object url) {
    return '打开失败：$url';
  }

  @override
  String get originalUnavailable => '无法加载原文';

  @override
  String get password => '密码';

  @override
  String get passwordChanged => '密码已修改，下次请用新密码登录';

  @override
  String get petClean => '清洁';

  @override
  String get petEntry => '宠物入口';

  @override
  String get petFullClean => '宠物已经很干净啦';

  @override
  String get petFullHunger => '宠物已经吃饱啦';

  @override
  String get petFullPlay => '宠物已经玩得很尽兴啦';

  @override
  String get petHunger => '饥饿';

  @override
  String get petLimit3 => '最多只能养 3 只宠物';

  @override
  String get petNameHint => '给宠物起个名字（最多5个字）';

  @override
  String get petNameLabel => '宠物名字（最多5个字）';

  @override
  String get petNameRequired => '请给宠物起个名字';

  @override
  String get petting => '抚摸';

  @override
  String get phaseAfternoon => '在过下午';

  @override
  String get phaseEvening => '在过晚上';

  @override
  String get phaseLiving => '在生活';

  @override
  String get phaseMorning => '在过上午';

  @override
  String get phaseSleep => '在睡觉';

  @override
  String phoneOf(Object name) {
    return '$name 的小手机';
  }

  @override
  String get phonePerception => '手机感知';

  @override
  String get phonePerceptionHint => '让 AI 好友了解你的手机状态（读屏/剪贴板/相册）';

  @override
  String phonePetCareHint(Object name) {
    return '$name 会亲自照顾它；想帮忙的话，去主页宠物页拜访 TA 吧';
  }

  @override
  String get phoneShort => '小手机';

  @override
  String get pickOne => '选一张';

  @override
  String get pinEmotion => '收藏这段情绪';

  @override
  String get pinned => '已收藏';

  @override
  String get pinnedEmotion => '已收藏这段情绪';

  @override
  String pinnedSummary(Object type) {
    return '$type · 置顶摘要';
  }

  @override
  String get play => '玩耍';

  @override
  String get pluginAll => '全部';

  @override
  String get pluginAuthor => '作者';

  @override
  String get pluginBridgeError => '桥调用失败';

  @override
  String get pluginChatInputHint => '输入消息…';

  @override
  String get pluginChatSendFail => '发送失败';

  @override
  String get pluginClose => '关闭';

  @override
  String get pluginConfig => '配置';

  @override
  String get pluginConfigChatName => '名称';

  @override
  String get pluginConfigGreeting => '开场白';

  @override
  String get pluginConfigPersona => '人设';

  @override
  String get pluginConfigSaved => '配置已保存';

  @override
  String get pluginConfigSystemPrompt => '技能提示词（systemPrompt）';

  @override
  String get pluginConfigTriggers => '触发词（逗号分隔）';

  @override
  String get pluginCopied => '已复制';

  @override
  String get pluginDisabled => '已禁用';

  @override
  String get pluginDisabledToast => '已禁用';

  @override
  String get pluginEnabled => '已启用';

  @override
  String get pluginEnabledToast => '已启用';

  @override
  String get pluginExternalLink => '已在浏览器中打开';

  @override
  String get pluginHooks => '挂载点';

  @override
  String get pluginInstallFail => '安装失败';

  @override
  String get pluginInstallSuccess => '插件安装成功';

  @override
  String get pluginInstallZip => '安装插件 zip';

  @override
  String get pluginMcp => '插件协议';

  @override
  String get pluginNavBlocked => '已拦截非插件页跳转';

  @override
  String get pluginNeedZip => '所选文件不是 .zip';

  @override
  String get pluginNoPlugins => '暂无插件';

  @override
  String get pluginNormal => '普通';

  @override
  String get pluginNotWritable => '只读（仅主账号可修改）';

  @override
  String get pluginOnlyAdmin => '仅主账号可管理插件';

  @override
  String get pluginOpen => '打开';

  @override
  String get pluginOpenChat => '打开聊天';

  @override
  String get pluginOpenPage => '打开页面';

  @override
  String get pluginPageLoadFailed => '页面加载失败';

  @override
  String get pluginRiskHint => '插件在服务器上执行，第三方插件与服务器同权限，请只安装可信来源的插件。';

  @override
  String get pluginRiskTitle => '风险提示';

  @override
  String get pluginSaveConfig => '保存配置';

  @override
  String get pluginSelectZip => '请选择 zip 文件';

  @override
  String get pluginTypeChat => '互动对话';

  @override
  String get pluginTypeHttp => '常规';

  @override
  String get pluginTypeHybrid => '页面插件';

  @override
  String get pluginTypePrompt => 'Prompt 技能';

  @override
  String get pluginTypeWorkflow => '工作流模板';

  @override
  String get pluginUninstall => '卸载';

  @override
  String get pluginUninstallConfirm => '确定卸载该插件？其存储数据将一并清除。';

  @override
  String get pluginUninstallFail => '卸载失败';

  @override
  String get pluginUninstallSuccess => '插件已卸载';

  @override
  String get pluginVersion => '版本';

  @override
  String get pluginZeroCodeConfig => '零代码配置';

  @override
  String get portraitGroup => '形象';

  @override
  String presentLine(Object doing, Object moodText) {
    return '此刻：$doing · $moodText';
  }

  @override
  String get privacy => '隐私';

  @override
  String get privacyGroup => '隐私';

  @override
  String get privacyHint => '隐私上锁与聊天细节展示';

  @override
  String get privacyLock => '隐私上锁';

  @override
  String get privacyLockHint => '日记和小手机平常上锁，查看需向TA申请';

  @override
  String get proactiveChat => '主动交流';

  @override
  String get proactiveChatHint => '闲置时AI会主动找您聊天';

  @override
  String get proactiveFrequency => '主动频率';

  @override
  String get proactiveFrequencyHint => 'AI主动找您聊天的频率';

  @override
  String get publish => '发布';

  @override
  String get publishFailed => '发布失败，请重试';

  @override
  String get publishMoment => '发布动态';

  @override
  String get quote => '引用';

  @override
  String get quotePrefix => '引用';

  @override
  String get readOnly => '（只读）';

  @override
  String get reasoningLevel => '思考过程';

  @override
  String get reasoningLevelHint => '气泡顶部显示TA回复前的推理内容';

  @override
  String recordCount(Object count) {
    return '$count 条记录';
  }

  @override
  String get recordTime => '记录时间';

  @override
  String get recordingPrefix => '录音中';

  @override
  String get recordingSuffix => '秒，松开发送 · 上滑取消';

  @override
  String get refresh => '刷新';

  @override
  String get register => '注册';

  @override
  String get registerFailed => '注册失败';

  @override
  String get releaseToCancel => '松开取消';

  @override
  String get remove => '移除';

  @override
  String get removeImage => '移除图片';

  @override
  String get rename => '改名';

  @override
  String get renameFailed => '改名失败';

  @override
  String get renameHint => '新名字（最多5个字）';

  @override
  String get reply => '回复';

  @override
  String get replying => '回复中';

  @override
  String get restoredToDesktop => '已恢复到桌面';

  @override
  String get retry => '重试';

  @override
  String get roleFallback => '角色';

  @override
  String get routine => '作息';

  @override
  String get saveNote => '保存备注';

  @override
  String get savedToAlbum => '已保存到我的相册';

  @override
  String get searchFail => '搜索失败';

  @override
  String searchFailDetail(Object e) {
    return '搜索失败：$e';
  }

  @override
  String get searchHint => '输入关键词搜索，历史保留 7 天';

  @override
  String get searchHistory => '搜索历史（保留 7 天）';

  @override
  String get searchPlaceholder => '搜索内容...';

  @override
  String get searching => '正在搜索…首次可能需要十几秒';

  @override
  String get selectFriend => '选择一位好友';

  @override
  String get selectMembersHint => '选择要添加的角色：';

  @override
  String get selectMinTwo => '选择成员（至少 2 个）：';

  @override
  String get selfStatement => '自述';

  @override
  String get sendChat => '发送';

  @override
  String get sendDoc => '发送文档';

  @override
  String get sendFail => '发送失败';

  @override
  String get sendImage => '发送图片';

  @override
  String get sending => '发送中...';

  @override
  String get serverAddress => '服务器地址';

  @override
  String get serverAddressHint => '请输入电脑上显示的服务器地址（http://IP:8000）';

  @override
  String get setStarToKeep => '设置星级可以消除倒计时保留记忆';

  @override
  String get settingsTitle => '设置';

  @override
  String get showTools => '调用能力';

  @override
  String get showToolsHint => '气泡内显示本次回复使用的能力（识图/生图/语音/扩展）';

  @override
  String get simpleThinking => '简单思考';

  @override
  String get simplifiedChinese => '简体中文';

  @override
  String get socialGroup => '社交';

  @override
  String get sourceBio => '自述';

  @override
  String get sourceChat => '聊天';

  @override
  String get sourceDiary => '日记';

  @override
  String get sourceEmotion => '对话评估';

  @override
  String get sourceExtracted => '提取';

  @override
  String sourceFrom(Object url) {
    return '来源: $url';
  }

  @override
  String get sourceInfo => '来源信息';

  @override
  String get sourceLabel => '来源';

  @override
  String get sourceMoment => '朋友圈';

  @override
  String sourcePrefix(Object source) {
    return '来源：$source';
  }

  @override
  String get sourceRelationship => '关系';

  @override
  String get sourceStatus => '状态';

  @override
  String get sourceStory => '剧情线';

  @override
  String get sourceTrigger => '状态触发';

  @override
  String get speciesCat => '猫';

  @override
  String get speciesDog => '狗';

  @override
  String get speciesGecko => '守宫';

  @override
  String get speciesHamster => '仓鼠';

  @override
  String get speciesParrot => '鹦鹉';

  @override
  String speciesPrefix(Object species) {
    return '种类：$species';
  }

  @override
  String get speciesRabbit => '兔子';

  @override
  String get speciesSnake => '蛇';

  @override
  String get stamina => '体力';

  @override
  String get standard => '标准';

  @override
  String get start => '开始';

  @override
  String get stateAnger => '怒气值';

  @override
  String get stateComfort => '舒适感';

  @override
  String get stateDesire => '性欲';

  @override
  String get stateEmotionMemory => '状态情绪记忆';

  @override
  String get stateEmotionMemoryHint => '回看 TA 最近的情绪波动、触发与剧情记录';

  @override
  String get stateFatigue => '疲惫感';

  @override
  String get statePossessiveness => '占有欲';

  @override
  String get stateSensitivity => '敏感度';

  @override
  String get stateTemp => '体温';

  @override
  String get stateTrend => '状态趋势';

  @override
  String get stateTrendHint => '回看八维状态变化曲线，对比不同时间点的状态';

  @override
  String get stateTrigger => '状态触发';

  @override
  String get stateTriggerHint => '心情/怒气等状态达阈值时AI会主动表达（发消息/朋友圈）';

  @override
  String stateUpdatedHint(Object time) {
    return '最近评估于 $time · 数值随时间自然变化（箭头=趋势）';
  }

  @override
  String get status => '状态';

  @override
  String get statusGroup => '状态';

  @override
  String get statusHint => '状态达阈值时的主动表达与冷战行为';

  @override
  String get storyCount => '剧情';

  @override
  String get storyFilter => '剧情';

  @override
  String get subCategory => '子分类';

  @override
  String get summaryGenFailed => '生成失败，请稍后再试';

  @override
  String get summaryRegenFailed => '重新生成失败，请检查服务器';

  @override
  String get summaryRegenerated => '置顶摘要已重新生成';

  @override
  String get supportAuthor => '支持作者';

  @override
  String get supportAuthorHint => '自愿支持用于维护/更新';

  @override
  String get switchMode => '切换';

  @override
  String get switchSaveFail => '开关保存失败，请稍后重试';

  @override
  String get ta => 'TA';

  @override
  String get taCareEmpty => 'TA 还没开始照顾记录，正在和宠物培养感情～';

  @override
  String get taCareLog => 'TA 的照顾记录';

  @override
  String taNoPet(Object name) {
    return '$name 还没有养宠物';
  }

  @override
  String get taNoPetHint => 'TA 会自己决定领养一只小动物；想帮 TA 的话，可以去主页宠物页「拜访」';

  @override
  String get tabAiInteraction => '小手机';

  @override
  String get tabFriends => '好友';

  @override
  String get tabMoments => '朋友圈';

  @override
  String get tabPets => '宠物';

  @override
  String get tapAvatarToChange => '点击头像可裁剪更换';

  @override
  String get tapToTest => '点击检测连接';

  @override
  String get tapToViewOriginal => '点击查看原文内容';

  @override
  String get themeAurora => '极光';

  @override
  String get themeCherry => '樱花';

  @override
  String get themeCoffee => '暖咖';

  @override
  String get themeColor => '主题色';

  @override
  String get skinTitle => '皮肤';

  @override
  String get skinNameIos => '原生 iOS';

  @override
  String get skinNameWarm => '温柔陪伴';

  @override
  String get skinNameMaterial => 'Material You';

  @override
  String get skinNamePaper => '纸艺手账';

  @override
  String get skinNameNeon => '暗夜霓虹';

  @override
  String get themeMode => '主题模式';

  @override
  String get themeOcean => '海洋';

  @override
  String get themeStarryNight => '经典·星夜';

  @override
  String get themeSunset => '日落';

  @override
  String get themeTitle => '主题';

  @override
  String get thinkAgain => '再想想';

  @override
  String get timeline => '时光';

  @override
  String get timelineHint => 'TA的成长时间线';

  @override
  String get timelineLoadFailed => '时光加载失败';

  @override
  String timelineTitle(Object name) {
    return '时光 · $name';
  }

  @override
  String get timerDeleted => '已删除该计时';

  @override
  String get todo => '待办';

  @override
  String totalCount(Object count) {
    return '共 $count 条';
  }

  @override
  String get triggerFilter => '触发';

  @override
  String get typing => '输入中...';

  @override
  String get unlockMemory => '解锁记忆';

  @override
  String get unlockedResume => '已解锁：恢复自然遗忘';

  @override
  String get unnamed => '未命名';

  @override
  String get unpinned => '已取消收藏';

  @override
  String updateFailedErr(Object err) {
    return '更新失败: $err';
  }

  @override
  String updatedAt(Object time) {
    return '更新于 $time';
  }

  @override
  String get upload => '上传';

  @override
  String get uploadFail => '上传失败';

  @override
  String get uploadWallpaper => '上传壁纸';

  @override
  String get uploadedToAlbum => '已上传到相册';

  @override
  String get userId => '用户ID';

  @override
  String get userPromised => '你承诺';

  @override
  String get username => '用户名';

  @override
  String get version => '拥爱 v3.2.2';

  @override
  String viewAllComments(Object count) {
    return '查看全部$count条评论';
  }

  @override
  String get virtualPhone => '虚拟手机（开发中）';

  @override
  String get virtualPhoneDesc =>
      '这里未来将承载小手机的完整可操作性：声音、通知、存储管理、应用权限、勿扰模式等，让每个 AI 角色拥有一台真正可操作的虚拟手机。目前仅占位，敬请期待。';

  @override
  String get visit => '拜访';

  @override
  String visualStateTitle(Object name) {
    return '$name · 可视化状态';
  }

  @override
  String get visualize => '可视化';

  @override
  String get voiceRetryMsg => '网络好像不太稳定，要重试发送吗？（录音已保留）';

  @override
  String get voiceSendFailed => '语音发送失败';

  @override
  String get wallpaper => '壁纸';

  @override
  String get wallpaperChanged => '壁纸已更换';

  @override
  String get weaveFullInject => '全注入对话';

  @override
  String get weaveFullInjectHint => '开启后每次对话注入织库卡片，记忆更完整（token 消耗更高）';

  @override
  String get weekOverview => '近 7 天概览';

  @override
  String get weekday1 => '一';

  @override
  String get weekday2 => '二';

  @override
  String get weekday3 => '三';

  @override
  String get weekday4 => '四';

  @override
  String get weekday5 => '五';

  @override
  String get weekday6 => '六';

  @override
  String get weekday7 => '日';

  @override
  String get weekdayFri => '星期五';

  @override
  String get weekdayMon => '星期一';

  @override
  String get weekdaySat => '星期六';

  @override
  String get weekdaySun => '星期日';

  @override
  String get weekdayThu => '星期四';

  @override
  String get weekdayTue => '星期二';

  @override
  String get weekdayWed => '星期三';

  @override
  String get weight => '体重';

  @override
  String whyMatters(Object why) {
    return '意义：$why';
  }

  @override
  String get writeTodayDiary => '写今天的日记';

  @override
  String yearCountTotal(Object count) {
    return '共 $count 条记录';
  }

  @override
  String yearLabel(Object year) {
    return '$year年';
  }

  @override
  String get yesterday => '昨天';

  @override
  String get you => '你';

  @override
  String get invalidLink => '链接无效';

  @override
  String get openFailedManual => '打开失败，可复制链接手动打开';

  @override
  String get supportIntro => '如果这个应用给你带来了陪伴，欢迎请作者喝杯咖啡 ☕';

  @override
  String get wechatReward => '微信赞赏';

  @override
  String get donateSupport => '打赏支持';

  @override
  String get donateOpenPage => '打开主页支持作者';

  @override
  String get donateNotOpen => '作者暂未开启打赏渠道';

  @override
  String get goSupport => '去支持';

  @override
  String get notOpened => '未开启';

  @override
  String get followDouyin => '关注抖音';

  @override
  String douyinId(Object id) {
    return '抖音号：$id';
  }

  @override
  String get joinQQGroup => '加入 QQ 群';

  @override
  String qqGroup(Object id) {
    return '群号：$id';
  }

  @override
  String get supportFooter => '打赏与关注纯属自愿，感谢你的支持 ❤️';

  @override
  String get dndSaved => '已保存免打扰设置';

  @override
  String get dndSettings => '免打扰设置';

  @override
  String get notificationSection => '通知';

  @override
  String get messageNotifications => '消息通知';

  @override
  String get msgNotifOnSubtitle => 'AI好友新消息将弹横幅与系统通知';

  @override
  String get msgNotifOffSubtitle => '关闭后横幅与系统通知都不弹（红点仍更新）';

  @override
  String get enableDnd => '启用免打扰';

  @override
  String get dndOnSubtitle => '在设定时段内不推送通知';

  @override
  String get dndOffSubtitle => '通知将正常推送';

  @override
  String get dndStartLabel => '开始时间';

  @override
  String get dndEndLabel => '结束时间';

  @override
  String get dndStartAction => '开始';

  @override
  String get dndEndAction => '结束';

  @override
  String get dndNote => '免打扰时段内，AI好友将不会推送新消息通知。\n例如: 22:00 ~ 08:00 适合夜间休息时段。';

  @override
  String get dyMemoryTitle => '抖音记忆收紧';

  @override
  String get dyMemoryOnSave => '已开启：排除关系类私密记忆';

  @override
  String get dyMemoryOffSave => '已关闭：按现状筛选记忆';

  @override
  String dyMemorySaveFailed(Object err) {
    return '保存失败：$err';
  }

  @override
  String get dyMemorySection => '公开记忆注入';

  @override
  String get dyMemorySwitchTitle => '收紧私密记忆';

  @override
  String get dyMemorySwitchSubtitle =>
      '开启后，抖音图文创作与评论回复不再注入「关系类」记忆（表白/金钱等无姓名但私密的内容）';

  @override
  String get dyMemoryNote =>
      '说明：无论开关状态，抖音都永不注入「身份画像」与含用户姓名的记忆。开启「收紧」后，关系类记忆（如表白、亲密互动、金钱往来）也会被排除，适合对外更谨慎的场景。';

  @override
  String get updateAnnouncement => '更新公告';

  @override
  String get noUpdates => '暂无更新记录';

  @override
  String get updateNoDetail => '（无明细）';

  @override
  String updateReason(Object reason) {
    return '原因：$reason';
  }

  @override
  String copiedText(Object text) {
    return '已复制：$text';
  }

  @override
  String get permTitle => 'AI 能力权限';

  @override
  String get permGlobalDefault => '全局默认';

  @override
  String get permGlobalDefaultHint => '所有能力的默认档位；未单独设置的能力跟随全局默认';

  @override
  String get permScopes => '各能力';

  @override
  String get permAskNote => '「每次询问」：AI 调用该能力前会先征求你的同意（目前生图支持询问交互，其余能力询问时暂不执行）。';

  @override
  String get permSaveFailed => '保存失败，请重试';

  @override
  String get permLevelAllow => '允许';

  @override
  String get permLevelAsk => '每次询问';

  @override
  String get permLevelForbid => '禁止';

  @override
  String get permScopeImgTitle => '生图';

  @override
  String get permScopeImgDesc => 'AI 生成图片发给你（聊天内发图/主动生图）';

  @override
  String get permScopeImgUnderstandTitle => '识图';

  @override
  String get permScopeImgUnderstandDesc => 'AI 理解你发来的图片内容（本地识图）';

  @override
  String get permScopeTtsTitle => '语音回复';

  @override
  String get permScopeTtsDesc => 'AI 用语音回复你（TTS 合成）';

  @override
  String get permScopeAsrTitle => '语音转写';

  @override
  String get permScopeAsrDesc => '转写你的语音消息（ASR 识别）';

  @override
  String get permScopeBrowserTitle => '浏览器';

  @override
  String get permScopeBrowserDesc => '浏览器扩展：AI 搜索网页、读取页面';

  @override
  String get permScopeDouyinTitle => '抖音';

  @override
  String get permScopeDouyinDesc => '抖音扩展：发布图文、回复评论';

  @override
  String get permScopeExtensionTitle => '扩展';

  @override
  String get permScopeExtensionDesc => '其他扩展/插件的能力调用';

  @override
  String get dyApprovalsTitle => '抖音批准请求';

  @override
  String get dyApprovalsAiCreate => 'AI 创作';

  @override
  String get dyApprovalsEmpty => '暂无待批准的抖音内容';

  @override
  String get dyApprovalsEmptyDraft => '暂无待批准的草稿';

  @override
  String get dyApprovalsMemorySection => '记忆';

  @override
  String get dyApprovalsRestrictHint => '公开平台记忆注入时排除关系类私密记忆';

  @override
  String dyApprovalsRestrictFailed(Object err) {
    return '记忆收紧设置失败：$err';
  }

  @override
  String get dyApprovalsPromptHint => '写点灵感或提示词（可留空，AI 会以自己的想法创作）';

  @override
  String get dyApprovalsPromptExample => '例如：发一条表达你最近想法的图文…';

  @override
  String get dyApprovalsGenPost => '生成图文';

  @override
  String get dyApprovalsGenReply => '生成回复';

  @override
  String get dyApprovalsDraftCreated => '已生成草稿';

  @override
  String dyApprovalsGenFailed(Object err) {
    return '生成失败: $err';
  }

  @override
  String get dyApprovalsConfirmed => '已确认';

  @override
  String dyApprovalsConfirmFailed(Object err) {
    return '确认失败: $err';
  }

  @override
  String get dyApprovalsRejected => '已拒绝';

  @override
  String dyApprovalsRejectFailed(Object err) {
    return '拒绝失败: $err';
  }

  @override
  String get dyApprovalsImageUploaded => '图片已上传';

  @override
  String dyApprovalsUploadFailed(Object err) {
    return '上传失败: $err';
  }

  @override
  String dyApprovalsCountdown(Object count) {
    return '发布倒计时（$count）';
  }

  @override
  String get dyApprovalsCountdownHint => '已确认，将在随机时间发布/回复，避开深夜静默';

  @override
  String get dyKindImage => '图文';

  @override
  String get dyKindReply => '回复';

  @override
  String dyApprovalsReplyTo(Object commenter, Object content) {
    return '回复 $commenter：$content';
  }

  @override
  String get dyApprovalsPublishing => '正在发布…';

  @override
  String get dyApprovalsSoon => '即将发布';

  @override
  String dyApprovalsHourMin(Object h, Object m) {
    return '$h 小时 $m 分';
  }

  @override
  String dyApprovalsMinSec(Object m, Object s) {
    return '$m 分 $s 秒';
  }

  @override
  String dyApprovalsSec(Object s) {
    return '$s 秒';
  }

  @override
  String get dyApprovalsKindPost => '图文发布';

  @override
  String get dyApprovalsKindReplyComment => '回复评论';

  @override
  String get dyApprovalsFan => '粉丝';

  @override
  String get dyApprovalsNotFan => '非粉丝';

  @override
  String get dyApprovalsNoImage => '未配图（发布时抖音自动生成配图）';

  @override
  String dyApprovalsImageCount(Object n) {
    return '图片 $n 张';
  }

  @override
  String get dyApprovalsChooseImage => '选择图片';

  @override
  String get dyApprovalsConfirmBtn => '确认（随机时间发布）';

  @override
  String get dyApprovalsRejectBtn => '拒绝';

  @override
  String get aiFriendTitle => 'AI 好友';

  @override
  String get searchAiFriend => '搜索 AI 好友';

  @override
  String get familyGroupChat => '家庭群聊';

  @override
  String get familyGroupHint => '和你的 AI 角色们一起聊天';

  @override
  String get noMatchingFriend => '没有匹配的好友';

  @override
  String get noAiFriend => '还没有AI好友，点击右上角创建';

  @override
  String get charListLoadFailed => '加载失败: ';

  @override
  String charArchiveTitle(Object name) {
    return '$name的聊天记录';
  }

  @override
  String get noChatHistory => '暂无聊天记录';

  @override
  String archiveMsgCount(Object count) {
    return '$count 条消息';
  }

  @override
  String archiveCount(Object count) {
    return '$count 条';
  }

  @override
  String get privacyApproved => 'TA 同意了';

  @override
  String get privacyLater => '稍后再看';

  @override
  String get privacyView => '查看';

  @override
  String get privacyRejected => 'TA 拒绝了';

  @override
  String get privacyGotIt => '知道了';

  @override
  String get privacyTooFrequent => '申请太频繁啦，2 分钟后再试试';

  @override
  String get privacyApplyFailed => '申请失败，请稍后再试';

  @override
  String privacyLockedBy(Object content) {
    return 'TA 把$content锁起来了';
  }

  @override
  String get privacyApplyHint => '想看看就向 TA 申请吧';

  @override
  String privacyCooldown(Object seconds) {
    return '申请冷却中 $seconds 秒';
  }

  @override
  String get privacyApplying => '申请中…';

  @override
  String get privacyApplyButton => '向 TA 申请查看';

  @override
  String get privacyRefreshStatus => '刷新状态';

  @override
  String get msgFileExpired => '文件已过期（保留 5 天后自动清理）';

  @override
  String msgFileSizeExpired(Object size) {
    return '$size · 已过期';
  }

  @override
  String get voice => '语音';

  @override
  String get voiceReply => '语音回复';

  @override
  String get thinkingProcess => '思考过程';

  @override
  String get calledAbility => '调用能力';

  @override
  String get imageLoadFailed => '图片加载失败';

  @override
  String get continueLabel => '继续';

  @override
  String get quoteDeleted => '原消息已删除';

  @override
  String get playFailed => '播放失败';

  @override
  String msgQuoteLine(Object content, Object sender) {
    return '$sender：$content';
  }

  @override
  String get weaveLoadFail => '画布加载失败，请重试';

  @override
  String get weaveDetailLoadFail => '详情加载失败';

  @override
  String get weaveFallback2D => '已自动切换到 2.5D 视图以提升流畅度';

  @override
  String get weaveFallback2DRenderError => '已自动切换到 2.5D（3D 渲染异常，已反馈定位）';

  @override
  String get weaveFallback2DLowFps => '已自动切换到 2.5D（检测到持续低帧率）';

  @override
  String get weaveFallback2DNodeLimit => '已自动切换到 2.5D（当前节点数较多）';

  @override
  String get weaveSwitchedToLight => '已切换轻量模式（3D 简化渲染）';

  @override
  String get weaveCanvasTitle => '织库画布';

  @override
  String get weaveModeAuto => '全自动';

  @override
  String get weaveModeFull3D => '3D 全量';

  @override
  String get weaveModeLight3D => '3D 轻量';

  @override
  String get weaveMode2D => '2.5D';

  @override
  String get weaveNoCards => '还没有卡片，先去列表页整理生成吧';

  @override
  String get weaveNear7Days => '近7天';

  @override
  String get weaveNear30Days => '近30天';

  @override
  String get weaveAllCharacters => '全部角色';

  @override
  String get weaveAllMoods => '全部心情';

  @override
  String get weaveAllTypes => '全部类型';

  @override
  String get weaveCardsLoadFail => '加载失败，请重试';

  @override
  String weaveDone(int created) {
    return '已织好 $created 张卡片';
  }

  @override
  String get weaveNoNewMemory => '没有新的可整理记忆';

  @override
  String get weaveGenerateFail => '整理失败，请稍后重试';

  @override
  String weaveNetworkFail(String type) {
    return '网络请求失败（$type）';
  }

  @override
  String get weaveNoDuplicates => '未发现重复卡片';

  @override
  String weaveDedupCheckFail(String err) {
    return '查重失败：$err';
  }

  @override
  String get weaveDedup => '去重';

  @override
  String get weaveDedupConfirm =>
      '每组重复卡片将保留信息最全的一张，其余删除（参与记忆会合并，原始记忆不受影响）。确定执行吗？';

  @override
  String get weaveExecuteDedup => '执行去重';

  @override
  String weaveDedupMerged(int groups, int removed) {
    return '已合并 $groups 组，删除 $removed 张重复卡片';
  }

  @override
  String weaveDedupFail(String err) {
    return '去重失败：$err';
  }

  @override
  String get weaveDeleteCard => '删除卡片';

  @override
  String get weaveDeleteCardConfirm => '仅删除织库卡片，不影响原始记忆。确定删除吗？';

  @override
  String get weaveLibraryTitle => '织库';

  @override
  String get weaveOrganizeGenerate => '整理生成';

  @override
  String get weaveCanvas => '画布';

  @override
  String get weaveAllDomain => '全·织库';

  @override
  String get weavePrivateDomain => '私·织库';

  @override
  String get weaveCheckDup => '查重';

  @override
  String weaveCardCount(int count) {
    return '$count 张卡片';
  }

  @override
  String get weaveNoMemoryCards => '还没有织好的记忆卡片';

  @override
  String get weaveTapTopRightGenerate => '点右上角 ✨ 整理生成';

  @override
  String weaveDedupResult(int groups, int total) {
    return '查重结果：$groups 组重复，将合并 $total 张';
  }

  @override
  String get weaveDedupResultDesc => '每组保留信息最全的一张，重复卡片合并后删除（原始记忆不受影响）';

  @override
  String weaveKeepTitle(String title, int count) {
    return '保留：$title（$count 条记忆）';
  }

  @override
  String weaveMergeTitle(String title, int count) {
    return '合并：$title（$count 条记忆）';
  }

  @override
  String get sheetTime => '时间';

  @override
  String get sheetWeather => '天气';

  @override
  String get sheetLocation => '地点';

  @override
  String get sheetDetails => '细节';

  @override
  String get sheetParticipatingMemories => '参与记忆';

  @override
  String get workflowEdgeFail => '失败';

  @override
  String get workflowEdgeAlways => '始终';

  @override
  String workflowEdgeScreenHas(String target) {
    return '屏幕有「$target」';
  }

  @override
  String workflowEdgeScreenEmpty(String target) {
    return '屏幕无「$target」';
  }

  @override
  String get workflowEdgeSuccess => '成功';

  @override
  String get workflowEdgeWhenSuccess => '成功时走';

  @override
  String get workflowEdgeWhenFail => '失败时走';

  @override
  String get workflowEdgeWhenAlways => '始终走';

  @override
  String get workflowEdgeHasText => '屏幕有这些文字';

  @override
  String get workflowEdgeNoText => '屏幕没有这些文字';

  @override
  String get workflowEdgeConditionTitle => '连线条件';

  @override
  String get workflowEdgeWhenLabel => '何时走这条线';

  @override
  String get workflowEdgeScreenTextLabel => '屏幕判断文字';

  @override
  String get workflowEdgeScreenTextHint => '如：更新提示、跳过、确认';

  @override
  String get workflowEdgeDelete => '删除连线';

  @override
  String workflowScreenRange(num w, num h) {
    return '屏幕范围：x 0~$w，y 0~$h';
  }

  @override
  String get workflowCanvasTitle => '节点连线画布';

  @override
  String get workflowApply => '应用';

  @override
  String get workflowGetScreenRange => '获取屏幕范围';

  @override
  String get workflowCanvasHelp =>
      '连线=执行顺序/条件 · 点节点编辑、长按拖动布局、从底部圆点拖线到另一节点、点连线改条件';

  @override
  String get workflowCanvasSynced => '画布内容已同步，记得保存';

  @override
  String get workflowNameRequired => '请填写名称';

  @override
  String get workflowCanvasNoNodes => '画布还没有节点，请先添加步骤';

  @override
  String get workflowNameAndStepRequired => '请填写名称并至少添加一步';

  @override
  String get workflowEditTitle => '编辑工作流';

  @override
  String get workflowNewTitle => '新建工作流';

  @override
  String get workflowNameLabel => '名称（如：微信回消息）';

  @override
  String get workflowDescLabel => '描述（可选）';

  @override
  String get workflowCanvasModeHint => '此工作流使用画布（分支/条件），请在右上角画布中编辑';

  @override
  String get workflowCanvasPreview => '画布节点预览';

  @override
  String get workflowStepsLabel => '步骤（长按拖拽排序）';

  @override
  String get workflowAddStep => '添加步骤';

  @override
  String get workflowNoStepsHint => '还没有步骤，点“添加步骤”或右上角画布开始';

  @override
  String workflowRunConfirmTitle(String name) {
    return '执行「$name」';
  }

  @override
  String workflowRunConfirmDesc(int count) {
    return '共 $count 步：点击“执行”后按顺序操作手机（敏感步骤需你确认）';
  }

  @override
  String get workflowRun => '执行';

  @override
  String get workflowScreenTitle => '手机操作工作流';

  @override
  String get workflowEmptyHint =>
      '还没有工作流。点右下角 + 新建：把常用手机操作编排成序列，之后对 AI 说“帮我执行 XX”即可。';

  @override
  String workflowStepCount(int count) {
    return '$count 步';
  }

  @override
  String get stepScroll => '滚动';

  @override
  String get stepLaunchApp => '启动应用';

  @override
  String get stepTapXy => '坐标点击';

  @override
  String get stepSwipe => '滑动';

  @override
  String get stepWait => '等待';

  @override
  String get stepGoHome => '返回主页';

  @override
  String stepSummaryInput(String text) {
    return '输入：$text';
  }

  @override
  String stepSummaryWait(num ms) {
    return '$ms 毫秒';
  }

  @override
  String stepSummaryLaunch(String target) {
    return '启动 $target';
  }

  @override
  String get stepSummaryBackPrev => '返回上一页';

  @override
  String get stepSummaryGoHome => '返回手机主页';

  @override
  String get stepEditTitle => '编辑步骤';

  @override
  String get stepActionLabel => '动作';

  @override
  String get stepInputLabel => '输入内容（≤50 字）';

  @override
  String get stepSwipeStartX => '起 x';

  @override
  String get stepSwipeStartY => '起 y';

  @override
  String get stepSwipeEndX => '终 x';

  @override
  String get stepSwipeEndY => '终 y';

  @override
  String get stepSwipeDuration => '时长 ms';

  @override
  String get stepWaitMsLabel => '等待毫秒（100-10000）';

  @override
  String get stepBackPrevNoParam => '返回上一页（无需参数）';

  @override
  String get stepGoHomeNoParam => '返回手机主页（无需参数）';

  @override
  String get stepAppLabel => '应用';

  @override
  String get stepAppHint => '点右侧图标从已安装应用选择';

  @override
  String get stepTargetHint => '点右侧图标从当前屏幕点选，或手输节点文本';

  @override
  String get stepPickAppTooltip => '从应用列表选择';

  @override
  String get stepPickScreenTooltip => '从当前屏幕点选';

  @override
  String get stepConfirmAgain => '此步需再次确认';

  @override
  String get nodePickReaderServiceError =>
      '读屏服务未连接：App 更新后需在系统设置里重新开启「读屏（无障碍）」';

  @override
  String get nodePickReaderDisabled => '未开启读屏（无障碍），无法读取当前屏幕';

  @override
  String get nodePickTitle => '选择操作目标';

  @override
  String get nodePickOpenAppHint => '可先打开目标应用再回来点选，或直接手输目标文本';

  @override
  String get nodePickEnableScreenReader => '去开启读屏';

  @override
  String get nodePickCurrentScreen => '当前屏幕';

  @override
  String get nodePickRecentApps => '最近打开的应用';

  @override
  String nodePickRecentAppsPkg(String pkg) {
    return '最近打开的应用（$pkg）';
  }

  @override
  String get nodePickExternalHint => '来自最近浏览的页面，执行时会按文字在当前屏幕重新匹配';

  @override
  String get nodePickIconNoText => '图标（无文字）';

  @override
  String get nodePickIconButton => '图标按钮';

  @override
  String appPickLoadFailed(String err) {
    return '无法读取应用列表：$err';
  }

  @override
  String get appPickUnknownError => '未知错误';

  @override
  String get appPickNoApps => '未读取到已安装应用';

  @override
  String appPickLoadError(String err) {
    return '加载失败：$err';
  }

  @override
  String get appPickTitle => '选择应用';

  @override
  String get appPickSearchHint => '搜索应用名，如：微信、抖音';

  @override
  String get appPickNoResult => '未找到应用';

  @override
  String get shizukuRequestSent => '已发起授权请求，请在系统弹窗中点击允许';

  @override
  String get shizukuRequestFailed => '已授权或发起失败，请检查状态后重试';

  @override
  String get shizukuNotRunning => 'Shizuku 服务未运行：请先在 Shizuku app（或 ADB）启动服务';

  @override
  String get shizukuIntro =>
      'Shizuku 让 AI 获得系统级能力（应用列表/系统设置/模拟操作前置）。需先安装 Shizuku app 并启动服务（root 直启，或电脑 ADB 执行 start.sh），再在下方请求授权。授权后可在本页验证读取应用列表与执行 Shell。';

  @override
  String get shizukuReRequest => '重新请求授权';

  @override
  String get shizukuRequest => '请求授权';

  @override
  String get shizukuLoadApps => '读取已安装应用列表（测试）';

  @override
  String get shizukuShellDebug => 'Shell 调试';

  @override
  String get shizukuShellHint => '如 pm list packages -3';

  @override
  String get shizukuExecute => '执行';

  @override
  String profileLoadFail(String e) {
    return '加载失败: $e';
  }

  @override
  String get profileSaveSuccess => '保存成功';

  @override
  String profileSaveFail(String e) {
    return '保存失败: $e';
  }

  @override
  String get profileEditInfo => '编辑资料';

  @override
  String get profileHeightCm => '身高 (cm)';

  @override
  String get profileWeightKg => '体重 (kg)';

  @override
  String get profileBio => '个人描述';

  @override
  String get profileMySpace => '我的空间';

  @override
  String get profileMyState => '我的状态';

  @override
  String get profileEightDimWeekly => '八维状态与周视图';

  @override
  String get profileRelationProgress => '与伙伴的关系进度';

  @override
  String get profileDiaryMood => '记录每天的心情';

  @override
  String get profileMyMemos => '我的备忘';

  @override
  String get profileMemoTip => '随手记，不忘记';

  @override
  String get relationTypePartner => '对象/伴侣';

  @override
  String get relationTypeHusband => '老公';

  @override
  String get relationTypeBestie => '闺蜜';

  @override
  String get relationTypeBro => '兄弟';

  @override
  String get relationTypeBuddy => '死党';

  @override
  String get relationTypeFamily => '家人';

  @override
  String get relationTypeFriend => '朋友';

  @override
  String relationLoadFail(String e) {
    return '加载失败: $e';
  }

  @override
  String relationPartnerLabel(String rt) {
    return '我的对象 · $rt';
  }

  @override
  String get relationNetwork => '关系网';

  @override
  String get relationMyPartner => '我的对象';

  @override
  String get relationPartnerNote => '对象身份与性别以此处为准，AI 不会默认你的对象是异性';

  @override
  String get relationAllRoles => '全部角色关系';

  @override
  String relationSaveFail(String e) {
    return '保存失败: $e';
  }

  @override
  String relationSetTitle(String name) {
    return '设置「$name」的关系';
  }

  @override
  String get relationTypeLabel => '关系类型';

  @override
  String get relationIsPartner => '这是我的对象/伴侣';

  @override
  String get relationIsPartnerHint => '设为对象后，AI 会明确知道你的对象是谁（支持同性）';

  @override
  String get relationDescOptional => '关系描述（可选）';

  @override
  String get relationDescHint => '例如：互称老公，关系亲密';

  @override
  String stateHistTrendTitle(String characterName) {
    return '$characterName · 状态趋势';
  }

  @override
  String get stateHistEmpty =>
      '还没有状态历史记录。\n多和 TA 聊聊天，每次对话后的状态评估会自动记录在这里（最多保留最近 20 次）。';

  @override
  String stateHistCurve(String cn) {
    return '$cn 变化曲线';
  }

  @override
  String stateHistRecentSnapshots(int count) {
    return '最近 $count 次评估快照';
  }

  @override
  String get stateHistInsufficientSnapshots => '快照不足 2 条时无法对比';

  @override
  String get stateHistSpiderCompare => '蛛网对比';

  @override
  String get stateHistEarlier => '较早';

  @override
  String get stateHistLater => '较晚';

  @override
  String stateHistEarlierAt(String t) {
    return '较早 $t';
  }

  @override
  String stateHistLaterAt(String t) {
    return '较晚 $t';
  }

  @override
  String get myStateSaved => '状态已保存，AI 角色会感知到你的状态';

  @override
  String get myStateTitle => '我的可视化状态';

  @override
  String myStateLoadFail(String error) {
    return '加载失败: $error';
  }

  @override
  String myStateUpdatedAt(String time) {
    return '更新于 $time';
  }

  @override
  String get myStateSliderHint =>
      '拖动滑块调整你当前的状态，保存后 AI 角色在聊天中会感知到（例如心情低落时角色会更温柔地关心你）';

  @override
  String get myStateReset => '重置为默认';

  @override
  String get myStateSaving => '保存中...';

  @override
  String get myStateSave => '保存状态';

  @override
  String get cropTitle => '调整头像';

  @override
  String get cropLoadingImage => '图片加载中，请稍候再试';

  @override
  String get cropTimeoutRetry => '裁剪超时，请重试';

  @override
  String get cropProcessing => '处理中…';

  @override
  String get cropFailedRetry => '裁剪失败，请重试';

  @override
  String get cropDragHint => '拖动调整位置 · 双指缩放';

  @override
  String get agreeTitle => '用户协议与免责声明';

  @override
  String get agreeSection1Title => '一、软件性质';

  @override
  String get agreeSection1Body =>
      '本项目为开源、自托管软件（MIT License），由使用者自行下载、部署并运行在自己的设备或服务器上。作者以个人身份无偿维护本开源项目，不对任何使用者提供商业化服务承诺。';

  @override
  String get agreeSection2Title => '二、自担风险';

  @override
  String get agreeSection2Body =>
      '软件按「现状」（AS IS）提供，不附带任何明示或默示的担保，包括但不限于适销性、特定用途适用性。因部署、配置、使用、升级过程中出现的任何数据丢失、损坏、服务中断或财产损失，均由使用者自行承担。';

  @override
  String get agreeSection3Title => '三、内容责任';

  @override
  String get agreeSection3Body =>
      '本软件为通用工具，AI 生成的对话、图片、文字等内容由使用者自行配置的模型、提示词与数据产生，不代表作者观点。作者不对使用者或任何第三方基于本软件产生、传播的内容与行为承担任何责任。';

  @override
  String get agreeSection4Title => '四、数据安全';

  @override
  String get agreeSection4Body =>
      '数据默认存储在使用者自己的服务器。请自行做好备份、密钥保管与访问控制（如防火墙、HTTPS、修改默认管理员账号）。因未妥善保护导致的隐私泄露、数据被篡改等后果，由使用者自行负责。';

  @override
  String get agreeSection5Title => '五、合法使用';

  @override
  String get agreeSection5Body =>
      '使用者须遵守所在地法律法规，不得将本软件用于违法、侵权、骚扰、诈骗等用途；不得利用软件生成的内容侵犯他人合法权益。使用者的一切使用行为及其后果均与作者无关。';

  @override
  String get agreeSection6Title => '六、远程与多人访问';

  @override
  String get agreeSection6Body =>
      '将服务器暴露到公网、通过 Tailscale 等组网分享给他人使用前，使用者须自行评估风险并承担相应责任，包括但不限于他人通过账号或权限管理不当产生的后果。';

  @override
  String get agreeSection7Title => '七、协议变更';

  @override
  String get agreeSection7Body => '本协议内容可能随版本更新调整，继续使用本软件即视为接受最新版本协议。';

  @override
  String get backupTitle => '数据备份';

  @override
  String get backupSubtitle => '把 SQLite 数据库与配置导出为压缩包，备份可随时恢复';

  @override
  String get backupExport => '导出备份';

  @override
  String get backupExporting => '正在备份…';

  @override
  String get backupExportSuccess => '备份已保存到手机';

  @override
  String backupExportSuccessWithSize(Object size) {
    return '备份已保存到手机（$size）';
  }

  @override
  String get backupExportCanceled => '已取消保存';

  @override
  String get backupExportFailed => '备份失败，请重试';

  @override
  String get backupAdminOnly => '仅主账号可管理备份';

  @override
  String get backupRestoreTitle => '恢复指引';

  @override
  String get backupRestoreNote =>
      '备份包含 SQLite 数据库与配置，各平台恢复方式略有不同，以下为通用步骤（操作前请先停止服务并自行留好当前数据的副本）。';

  @override
  String get backupRestoreStep1 => '停止本次服务进程';

  @override
  String get backupRestoreStep2 => '解压备份包，用其中的数据覆盖 backend/data 目录';

  @override
  String get backupRestoreStep3 => '重新启动服务，数据即恢复完成';

  @override
  String get backupUrlHint => '若手机无法直接保存，可在浏览器或电脑访问以下链接下载（需登录主账号）：';

  @override
  String get backupUrlCopy => '复制链接';

  @override
  String get backupCopied => '已复制';

  @override
  String get backupFileLabel => '备份文件';

  @override
  String get backgroundKeepalive => '后台保活';

  @override
  String get backgroundKeepaliveHint => '退到后台后持续监听新消息并在后台弹通知（关闭后不再后台运行）';

  @override
  String get groupConnection => '连接';

  @override
  String get groupExperience => '体验';

  @override
  String get groupSystem => '系统';

  @override
  String get groupAbout => '关于';

  @override
  String get experienceSettingsTitle => '体验设置';

  @override
  String get experienceSettingsSubtitle => '手机感知 / 免打扰 / 扩展 / 应用容貌';

  @override
  String get weaveLibrarySubtitle => '全景记忆 · 编织成球';

  @override
  String get permissionManagementTitle => '权限管理';

  @override
  String get permissionManagementHint => 'AI 能力 / 主账号 / 服务器功能';

  @override
  String get accountAdminTitle => '主账号管理';

  @override
  String get accountAdminHint => '设置哪些账号是主账号（可管理服务器配置）';

  @override
  String get accountAdminOnly => '仅主账号可管理';

  @override
  String get accountAdminListTitle => '账号列表';

  @override
  String get accountMainLabel => '主账号';

  @override
  String get accountAdminLoadFailed => '加载失败，请重试';

  @override
  String get accountAdminSaved => '已保存';

  @override
  String get accountAdminFailed => '操作失败，请重试';

  @override
  String get accountAdminKeepOne => '至少保留一个主账号';

  @override
  String get updateAnnouncementHint => '最近更新内容，按天查看';

  @override
  String get userAgreementTitle => '用户协议';

  @override
  String get userAgreementHint => '服务条款与隐私说明';

  @override
  String shizukuReadAppsFailed(Object err) {
    return '读取失败：$err';
  }

  @override
  String get shizukuAppSeparator => '、';

  @override
  String shizukuThirdPartyAppCount(Object apps, Object count) {
    return '共 $count 个第三方应用：\n$apps';
  }

  @override
  String get unknownError => '未知错误';

  @override
  String get extRetry => '重试';

  @override
  String get extCollapse => '收起';

  @override
  String get extExpandFull => '展开全文';

  @override
  String get extUsageGuide => '使用教程';

  @override
  String get extView => '查看';

  @override
  String get extExpand => '展开';

  @override
  String get extCustomConfig => '自定义设定';

  @override
  String get extDoyinInjectHint => '注入到 AI 抖音创作中（图文/回复生成时生效）';

  @override
  String get extConfigExampleHint => '例如：发内容时多讲讲我们的故事，用温柔一点的语气…';

  @override
  String get extSaveConfig => '保存设定';

  @override
  String get extPendingHint => '待批准的抖音发布/回复请求请在「AI 好友」页右上角小信封查看';

  @override
  String get extConfigSaved => '自定义设定已保存';

  @override
  String extSaveFailed(Object err) {
    return '保存失败：$err';
  }

  @override
  String agentMindRetrievalCount(Object count) {
    return '$count 条';
  }

  @override
  String agentMindRetrievalHitReturn(Object hit, Object returned) {
    return '召回 $hit / 返回 $returned';
  }

  @override
  String get extHintDiary => '你是一位温柔细心的日记助手。\n你的目标：…';

  @override
  String get extHintGreeting => '你好呀，今天过得怎么样？';

  @override
  String get extHintWrite => '写文章，起标题，帮我写';

  @override
  String get extHintWriter => '你是写作助手。\n你的目标：…';

  @override
  String get loginConfirmNewPassword => '确认新密码';

  @override
  String get loginForgotPassword => '忘记密码？修改';

  @override
  String get loginNewPassword => '新密码';

  @override
  String get loginResetFail => '重置失败，请检查用户名或服务器连接';

  @override
  String get loginResetInvalid => '请填写用户名与两次一致的新密码';

  @override
  String get loginResetOk => '密码已重置，请用新密码登录';

  @override
  String get memoryCurrentRetention => '当前保留率';

  @override
  String memoryDecayHorizon(Object days) {
    return '未来 $days 天';
  }

  @override
  String get memoryDecayTitle => '记忆衰减曲线';

  @override
  String get memoryNextReview => '下次复习';

  @override
  String get memoryNextReviewNone => '暂未安排';

  @override
  String get memoryReviewCount => '已复习次数';

  @override
  String get memoryStrengthDays => '强度（天）';

  @override
  String get themeColorBlue => '蓝';

  @override
  String get themeColorCyan => '青';

  @override
  String get themeColorGreen => '绿';

  @override
  String get themeColorOrange => '橙';

  @override
  String get themeColorPink => '粉';

  @override
  String get themeColorPurple => '紫';

  @override
  String get mcpToolsTitle => 'MCP 工具';

  @override
  String get mcpToolsSubtitle => '管理 MCP Server 与工具权限';

  @override
  String get mcpAddServer => '添加服务器';

  @override
  String get mcpEditServer => '编辑服务器';

  @override
  String get mcpStatusConnected => '已连接';

  @override
  String get mcpStatusDisconnected => '未连接';

  @override
  String get mcpStatusError => '错误';

  @override
  String mcpToolsCount(Object n) {
    return '$n 个工具';
  }

  @override
  String get mcpConnect => '连接';

  @override
  String get mcpDisconnect => '断开';

  @override
  String get mcpTest => '测试';

  @override
  String get mcpDelete => '删除';

  @override
  String get mcpTransportLabel => '传输';

  @override
  String get mcpTransportStdio => 'stdio';

  @override
  String get mcpTransportSse => 'SSE';

  @override
  String get mcpTransportHttp => 'HTTP';

  @override
  String get mcpName => '名称';

  @override
  String get mcpNameRequired => '名称不能为空';

  @override
  String get mcpNameHint => '唯一标识（字母/数字/_-）';

  @override
  String get mcpCommand => '命令';

  @override
  String get mcpArgs => '参数（每行一个）';

  @override
  String get mcpEnv => '环境变量（KEY=值，每行一个）';

  @override
  String get mcpUrl => 'URL 地址';

  @override
  String get mcpHeaders => '自定义请求头（JSON）';

  @override
  String get mcpEnabled => '启用';

  @override
  String get mcpAutoConnect => '启动时自动连接';

  @override
  String get mcpSave => '保存';

  @override
  String get mcpCancel => '取消';

  @override
  String get mcpDeleteConfirmTitle => '删除 MCP Server';

  @override
  String mcpDeleteConfirmBody(Object name) {
    return '确定删除 $name？将断开连接并移除配置。';
  }

  @override
  String get mcpDeleteSuccess => '已删除';

  @override
  String mcpDeleteFail(Object err) {
    return '删除失败：$err';
  }

  @override
  String get mcpConnectSuccess => '连接成功';

  @override
  String get mcpDisconnectSuccess => '已断开';

  @override
  String mcpConnectFail(Object err) {
    return '连接失败：$err';
  }

  @override
  String mcpTestSuccess(Object n) {
    return '测试成功，发现 $n 个工具';
  }

  @override
  String mcpTestFail(Object err) {
    return '测试失败：$err';
  }

  @override
  String get mcpSaveSuccess => '已保存';

  @override
  String mcpSaveFail(Object err) {
    return '保存失败：$err';
  }

  @override
  String mcpLoadFail(Object err) {
    return '加载失败：$err';
  }

  @override
  String get mcpTools => '工具';

  @override
  String get mcpToolsEmpty => '暂无工具（连接后自动发现）';

  @override
  String get mcpToolsRefresh => '刷新';

  @override
  String get mcpRiskLow => '低风险';

  @override
  String get mcpRiskMedium => '中风险';

  @override
  String get mcpRiskHigh => '高风险';

  @override
  String get mcpPermissionLabel => '权限';

  @override
  String get mcpPermissionAllow => '允许';

  @override
  String get mcpPermissionAsk => '询问';

  @override
  String get mcpPermissionForbid => '禁止';

  @override
  String get mcpPermissionSaved => '权限已保存';

  @override
  String mcpPermissionSaveFail(Object err) {
    return '权限保存失败：$err';
  }

  @override
  String get mcpPreset => '预设模板';

  @override
  String get mcpPresetFilesystem => '文件系统';

  @override
  String get mcpPresetGithub => 'GitHub';

  @override
  String get mcpPresetSqlite => 'SQLite';

  @override
  String get mcpNoServers => '还没有 MCP Server，点击右上角添加。';

  @override
  String get mcpRecentCalls => '最近调用';

  @override
  String get mcpCallOk => '成功';

  @override
  String get mcpCallTimeout => '超时';

  @override
  String get mcpCallBlocked => '禁止';

  @override
  String get mcpCallFailed => '失败';

  @override
  String get toolResult => '工具结果';

  @override
  String get mcpResources => '资源';

  @override
  String get mcpPrompts => '提示词';

  @override
  String get gameTitle => '游戏';

  @override
  String get gameSelectGameType => '选择游戏';

  @override
  String get gameUndercover => '谁是卧底';

  @override
  String get gameTruthOrDare => '真心话大冒险';

  @override
  String get gameTwentyQ => '猜词20问';

  @override
  String get gameSingle => '单人';

  @override
  String get gameDual => '双人';

  @override
  String get gameMulti => '多人';

  @override
  String get gameDescriptionUndercover => '描述词语、投票找出卧底';

  @override
  String get gameDescriptionTruthOrDare => '轮流选择真心话或大冒险';

  @override
  String get gameDescriptionTwentyQ => '用20个是非问句猜出对方想的词';

  @override
  String get gameStart => '开始';

  @override
  String get gameSpectator => '观战';

  @override
  String get gamePlayer => '玩家';

  @override
  String get gameMyTurn => '轮到你';

  @override
  String get gameDescribe => '描述';

  @override
  String get gameVote => '投票';

  @override
  String get gameTruth => '真心话';

  @override
  String get gameDare => '大冒险';

  @override
  String get gameAsk => '提问';

  @override
  String get gameGuess => '猜词';

  @override
  String get gameArchive => '游乐手札';

  @override
  String get gameSelectPlayers => '选择 AI 角色';

  @override
  String get gameUserRole => '你的身份';

  @override
  String get gameUserAsPlayer => '以玩家身份加入';

  @override
  String get gameUserAsSpectator => '作为观战者（默认）';

  @override
  String gameStartFailed(Object err) {
    return '开始失败：$err';
  }

  @override
  String get gameAbort => '解散';

  @override
  String get gameAbortConfirm => '确定解散本局？战绩不会被记录。';

  @override
  String get gameLoading => '加载中…';

  @override
  String get gameNoPlayers => '请选择参与角色';

  @override
  String get gameWaiting => '等待其他玩家…';

  @override
  String get gameFinished => '对局结束';

  @override
  String get gameWinLabel => '赢家';

  @override
  String get gameDrawLabel => '平局';

  @override
  String get gameYourWord => '你的词';

  @override
  String get gameYourRole => '你的身份';

  @override
  String get gamePhase => '阶段';

  @override
  String get gameRound => '回合';

  @override
  String get gameCurrentTurn => '当前行动';

  @override
  String get gameChooseTruthOrDare => '选择真心话还是大冒险';

  @override
  String get gameSendMessage => '输入…';

  @override
  String get gameSend => '发送';

  @override
  String get gameCancel => '取消';

  @override
  String get gameGuessWord => '猜对方想的词';

  @override
  String get gameAnswerYes => '是';

  @override
  String get gameAnswerNo => '否';

  @override
  String get gameAnswerPossible => '可能';

  @override
  String get gameAnswerUncertain => '不确定';

  @override
  String get gameVoteFor => '投票给';

  @override
  String get gameDescribeHint => '用一句话描述你的词（不能说出词）';

  @override
  String get gameAskHint => '问一个是非问句';

  @override
  String gameErrorMessage(Object err) {
    return '操作失败：$err';
  }

  @override
  String get gameRoomTitle => '游戏房间';

  @override
  String get gameSpectatorView => '观战视角';

  @override
  String get gameNoArchive => '暂无对局记录';

  @override
  String get gameHistoryTitle => '游乐手札';

  @override
  String archivePlayerCount(Object count) {
    return '$count 人';
  }

  @override
  String archiveRounds(Object rounds) {
    return '$rounds 回合';
  }

  @override
  String archiveWinner(Object names) {
    return '赢家：$names';
  }

  @override
  String get archiveDraw => '平局';

  @override
  String get archivePlayers => '玩家';

  @override
  String get archiveTimeline => '时间线';

  @override
  String archiveRoundLabel(Object round) {
    return '第$round回合';
  }

  @override
  String get archiveWinnerSide => '胜方';

  @override
  String get gameKill => '刀人';

  @override
  String get gameCheck => '验人';

  @override
  String get gameSpeak => '发言';

  @override
  String get gameSpeakHint => '说说你的判断（狼人杀白天）';

  @override
  String get gameDeclare => '声明';

  @override
  String get gameDeclareHint => '声明数字（1-10）';

  @override
  String get gameFollow => '跟牌';

  @override
  String get gameChallenge => '质疑';

  @override
  String get gameChallengeHint => '质疑上一家的声明';

  @override
  String get gameSoupAskHint => '问一个是非问句（主持人是/否/可能/无关/不知道）';

  @override
  String get gameSoupGuess => '猜真相';

  @override
  String get gameSoupGuessHint => '说出你猜的真相';

  @override
  String get gameHistoryEmpty => '还没有对局记录';

  @override
  String get gameFilterAll => '全部';

  @override
  String get gameFilterWerewolf => '狼人杀';

  @override
  String get gameFilterLiarsBar => '骗子酒馆';

  @override
  String get gameFilterTurtleSoup => '海龟汤';

  @override
  String get gameFilterUndercover => '谁是卧底';

  @override
  String get gameFilterTruthOrDare => '真心话大冒险';

  @override
  String get gameFilterTwentyQ => '猜词20问';
}
