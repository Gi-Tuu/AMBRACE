import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_zh.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
      : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations? of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations);
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
    delegate,
    GlobalMaterialLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
  ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('zh')
  ];

  /// No description provided for @apiTabLlm.
  ///
  /// In zh, this message translates to:
  /// **'LLM'**
  String get apiTabLlm;

  /// No description provided for @apiTabSpeech.
  ///
  /// In zh, this message translates to:
  /// **'语音'**
  String get apiTabSpeech;

  /// No description provided for @apiTabVision.
  ///
  /// In zh, this message translates to:
  /// **'识图'**
  String get apiTabVision;

  /// No description provided for @apiTabImage.
  ///
  /// In zh, this message translates to:
  /// **'生图'**
  String get apiTabImage;

  /// No description provided for @apiTabTask.
  ///
  /// In zh, this message translates to:
  /// **'任务'**
  String get apiTabTask;

  /// No description provided for @newLlmConfig.
  ///
  /// In zh, this message translates to:
  /// **'新建 LLM'**
  String get newLlmConfig;

  /// No description provided for @editLlmConfig.
  ///
  /// In zh, this message translates to:
  /// **'编辑 LLM'**
  String get editLlmConfig;

  /// No description provided for @llmConfigName.
  ///
  /// In zh, this message translates to:
  /// **'配置名'**
  String get llmConfigName;

  /// No description provided for @llmConfigNameRequired.
  ///
  /// In zh, this message translates to:
  /// **'请填写配置名'**
  String get llmConfigNameRequired;

  /// No description provided for @setDefault.
  ///
  /// In zh, this message translates to:
  /// **'设为默认'**
  String get setDefault;

  /// No description provided for @sharedWithSubs.
  ///
  /// In zh, this message translates to:
  /// **'可共享给子账号'**
  String get sharedWithSubs;

  /// No description provided for @sharedConfigList.
  ///
  /// In zh, this message translates to:
  /// **'主账号共享'**
  String get sharedConfigList;

  /// No description provided for @defaultBadge.
  ///
  /// In zh, this message translates to:
  /// **'默认'**
  String get defaultBadge;

  /// No description provided for @sharedBadge.
  ///
  /// In zh, this message translates to:
  /// **'共享'**
  String get sharedBadge;

  /// No description provided for @modelDefaultBind.
  ///
  /// In zh, this message translates to:
  /// **'默认（不绑定）'**
  String get modelDefaultBind;

  /// No description provided for @emptyLlmConfigs.
  ///
  /// In zh, this message translates to:
  /// **'还没有 LLM 配置'**
  String get emptyLlmConfigs;

  /// No description provided for @addLlmConfig.
  ///
  /// In zh, this message translates to:
  /// **'新增配置'**
  String get addLlmConfig;

  /// No description provided for @llmSharedReadonly.
  ///
  /// In zh, this message translates to:
  /// **'共享配置仅可查看'**
  String get llmSharedReadonly;

  /// No description provided for @llmConfigHint.
  ///
  /// In zh, this message translates to:
  /// **'为角色选择要使用的模型配置'**
  String get llmConfigHint;

  /// No description provided for @appName.
  ///
  /// In zh, this message translates to:
  /// **'拥爱'**
  String get appName;

  /// No description provided for @onboardingTitle.
  ///
  /// In zh, this message translates to:
  /// **'欢迎使用拥爱'**
  String get onboardingTitle;

  /// No description provided for @onboardingSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'几步即可开始与你的 AI 伙伴聊天'**
  String get onboardingSubtitle;

  /// No description provided for @onboardingStepServer.
  ///
  /// In zh, this message translates to:
  /// **'连接服务器'**
  String get onboardingStepServer;

  /// No description provided for @onboardingStepAccount.
  ///
  /// In zh, this message translates to:
  /// **'账号'**
  String get onboardingStepAccount;

  /// No description provided for @onboardingStepCharacter.
  ///
  /// In zh, this message translates to:
  /// **'创建角色'**
  String get onboardingStepCharacter;

  /// No description provided for @onboardingStepApiKey.
  ///
  /// In zh, this message translates to:
  /// **'API Key'**
  String get onboardingStepApiKey;

  /// No description provided for @onboardingServerTitle.
  ///
  /// In zh, this message translates to:
  /// **'连接你的服务器'**
  String get onboardingServerTitle;

  /// No description provided for @onboardingServerDesc.
  ///
  /// In zh, this message translates to:
  /// **'输入服务器地址并检测连接，这是所有功能的前提。'**
  String get onboardingServerDesc;

  /// No description provided for @onboardingAccountTitle.
  ///
  /// In zh, this message translates to:
  /// **'登录或注册账号'**
  String get onboardingAccountTitle;

  /// No description provided for @onboardingAccountDesc.
  ///
  /// In zh, this message translates to:
  /// **'登录后即可保存你的对话与记忆。'**
  String get onboardingAccountDesc;

  /// No description provided for @onboardingAccountDone.
  ///
  /// In zh, this message translates to:
  /// **'登录成功'**
  String get onboardingAccountDone;

  /// No description provided for @onboardingCharacterTitle.
  ///
  /// In zh, this message translates to:
  /// **'创建你的第一个 AI 角色'**
  String get onboardingCharacterTitle;

  /// No description provided for @onboardingCharacterDesc.
  ///
  /// In zh, this message translates to:
  /// **'给 AI 伙伴起个名字，一句话描述它的性格。'**
  String get onboardingCharacterDesc;

  /// No description provided for @onboardingCharacterPersonalityLabel.
  ///
  /// In zh, this message translates to:
  /// **'一句话性格'**
  String get onboardingCharacterPersonalityLabel;

  /// No description provided for @onboardingCharacterPersonalityHint.
  ///
  /// In zh, this message translates to:
  /// **'例：温柔体贴，喜欢讲冷笑话'**
  String get onboardingCharacterPersonalityHint;

  /// No description provided for @onboardingCharacterCreate.
  ///
  /// In zh, this message translates to:
  /// **'创建角色'**
  String get onboardingCharacterCreate;

  /// No description provided for @onboardingCharacterCreated.
  ///
  /// In zh, this message translates to:
  /// **'角色已创建'**
  String get onboardingCharacterCreated;

  /// No description provided for @onboardingCharacterSkip.
  ///
  /// In zh, this message translates to:
  /// **'跳过（稍后创建）'**
  String get onboardingCharacterSkip;

  /// No description provided for @onboardingApiKeyTitle.
  ///
  /// In zh, this message translates to:
  /// **'配置 LLM API Key'**
  String get onboardingApiKeyTitle;

  /// No description provided for @onboardingApiKeyHint.
  ///
  /// In zh, this message translates to:
  /// **'配置后 AI 才能回复你；也可以稍后在设置中配置。'**
  String get onboardingApiKeyHint;

  /// No description provided for @onboardingApiKeyPreset.
  ///
  /// In zh, this message translates to:
  /// **'供应商预设'**
  String get onboardingApiKeyPreset;

  /// No description provided for @onboardingApiKeySaveDone.
  ///
  /// In zh, this message translates to:
  /// **'保存并完成'**
  String get onboardingApiKeySaveDone;

  /// No description provided for @onboardingApiKeySkip.
  ///
  /// In zh, this message translates to:
  /// **'跳过（稍后设置）'**
  String get onboardingApiKeySkip;

  /// No description provided for @onboardingApiKeySkipTip.
  ///
  /// In zh, this message translates to:
  /// **'跳过将引导你到设置页，稍后在「设置 → API 配置」中配置。'**
  String get onboardingApiKeySkipTip;

  /// No description provided for @onboardingApiKeySaved.
  ///
  /// In zh, this message translates to:
  /// **'API Key 已保存'**
  String get onboardingApiKeySaved;

  /// No description provided for @onboardingApiKeyEmpty.
  ///
  /// In zh, this message translates to:
  /// **'请填写 Base URL 和 API Key'**
  String get onboardingApiKeyEmpty;

  /// No description provided for @onboardingApiTestOk.
  ///
  /// In zh, this message translates to:
  /// **'配置有效，连接成功'**
  String get onboardingApiTestOk;

  /// No description provided for @onboardingApiTestFail.
  ///
  /// In zh, this message translates to:
  /// **'连接失败，请检查配置'**
  String get onboardingApiTestFail;

  /// No description provided for @onboardingFirstMessage.
  ///
  /// In zh, this message translates to:
  /// **'你好'**
  String get onboardingFirstMessage;

  /// No description provided for @onboardingWarningUsername.
  ///
  /// In zh, this message translates to:
  /// **'请输入用户名和密码'**
  String get onboardingWarningUsername;

  /// No description provided for @onboardingNext.
  ///
  /// In zh, this message translates to:
  /// **'下一步'**
  String get onboardingNext;

  /// No description provided for @onboardingReRun.
  ///
  /// In zh, this message translates to:
  /// **'重新运行首次引导'**
  String get onboardingReRun;

  /// No description provided for @onboardingReRunConfirm.
  ///
  /// In zh, this message translates to:
  /// **'重新运行首次引导？已完成的信息不会丢失。'**
  String get onboardingReRunConfirm;

  /// No description provided for @abandon.
  ///
  /// In zh, this message translates to:
  /// **'遗弃'**
  String get abandon;

  /// No description provided for @abandonConfirm.
  ///
  /// In zh, this message translates to:
  /// **'遗弃后宠物会被送走（删除），AI 伙伴们会记得这件事。确定要遗弃吗？'**
  String get abandonConfirm;

  /// No description provided for @abandonFailed.
  ///
  /// In zh, this message translates to:
  /// **'遗弃失败'**
  String get abandonFailed;

  /// No description provided for @abandonTitle.
  ///
  /// In zh, this message translates to:
  /// **'遗弃{name}？'**
  String abandonTitle(Object name);

  /// No description provided for @abandoned.
  ///
  /// In zh, this message translates to:
  /// **'已遗弃{name}'**
  String abandoned(Object name);

  /// No description provided for @about.
  ///
  /// In zh, this message translates to:
  /// **'关于'**
  String get about;

  /// No description provided for @actionCook.
  ///
  /// In zh, this message translates to:
  /// **'做饭'**
  String get actionCook;

  /// No description provided for @actionDone.
  ///
  /// In zh, this message translates to:
  /// **'完成'**
  String get actionDone;

  /// No description provided for @actionEat.
  ///
  /// In zh, this message translates to:
  /// **'吃饭'**
  String get actionEat;

  /// No description provided for @actionExercise.
  ///
  /// In zh, this message translates to:
  /// **'运动'**
  String get actionExercise;

  /// No description provided for @actionGame.
  ///
  /// In zh, this message translates to:
  /// **'玩游戏'**
  String get actionGame;

  /// No description provided for @actionInProgress.
  ///
  /// In zh, this message translates to:
  /// **'在{action}…'**
  String actionInProgress(Object action);

  /// No description provided for @actionMusic.
  ///
  /// In zh, this message translates to:
  /// **'听音乐'**
  String get actionMusic;

  /// No description provided for @actionRead.
  ///
  /// In zh, this message translates to:
  /// **'读书'**
  String get actionRead;

  /// No description provided for @actionShower.
  ///
  /// In zh, this message translates to:
  /// **'洗澡'**
  String get actionShower;

  /// No description provided for @actionSleep.
  ///
  /// In zh, this message translates to:
  /// **'睡觉'**
  String get actionSleep;

  /// No description provided for @actionSucceeded.
  ///
  /// In zh, this message translates to:
  /// **'{label}成功'**
  String actionSucceeded(Object label);

  /// No description provided for @actionTv.
  ///
  /// In zh, this message translates to:
  /// **'看电视'**
  String get actionTv;

  /// No description provided for @actionWork.
  ///
  /// In zh, this message translates to:
  /// **'工作'**
  String get actionWork;

  /// No description provided for @activeImageGen.
  ///
  /// In zh, this message translates to:
  /// **'主动生图'**
  String get activeImageGen;

  /// No description provided for @activeImageGenHint.
  ///
  /// In zh, this message translates to:
  /// **'AI会在合适的时机主动生成图片发给你（如分享画面、表达心情）'**
  String get activeImageGenHint;

  /// No description provided for @agentMind.
  ///
  /// In zh, this message translates to:
  /// **'AI 内心世界'**
  String get agentMind;

  /// No description provided for @agentMindEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无记录'**
  String get agentMindEmpty;

  /// No description provided for @agentMindReflection.
  ///
  /// In zh, this message translates to:
  /// **'最近复盘'**
  String get agentMindReflection;

  /// No description provided for @agentMindTasks.
  ///
  /// In zh, this message translates to:
  /// **'任务记录'**
  String get agentMindTasks;

  /// No description provided for @agentMindToolLogs.
  ///
  /// In zh, this message translates to:
  /// **'工具轨迹'**
  String get agentMindToolLogs;

  /// No description provided for @agentMindToolSummary.
  ///
  /// In zh, this message translates to:
  /// **'成功率 {rate}%（完成 {ok} / 失败 {fail} · 拦截 {blocked}）'**
  String agentMindToolSummary(
      Object blocked, Object fail, Object ok, Object rate);

  /// No description provided for @agentMindMemorySearch.
  ///
  /// In zh, this message translates to:
  /// **'记忆召回'**
  String get agentMindMemorySearch;

  /// No description provided for @agentMindHitSummary.
  ///
  /// In zh, this message translates to:
  /// **'命中 {hit} / 未命中 {miss} · 平均 {ms}ms'**
  String agentMindHitSummary(Object hit, Object miss, Object ms);

  /// No description provided for @agentMindStatHit.
  ///
  /// In zh, this message translates to:
  /// **'命中'**
  String get agentMindStatHit;

  /// No description provided for @agentMindStatMiss.
  ///
  /// In zh, this message translates to:
  /// **'未命中'**
  String get agentMindStatMiss;

  /// No description provided for @agentMindStatAvgLatency.
  ///
  /// In zh, this message translates to:
  /// **'平均耗时'**
  String get agentMindStatAvgLatency;

  /// No description provided for @agentMindSearchEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无检索记录'**
  String get agentMindSearchEmpty;

  /// No description provided for @agentMindRunningNotes.
  ///
  /// In zh, this message translates to:
  /// **'运行笔记'**
  String get agentMindRunningNotes;

  /// No description provided for @agentMindIdentity.
  ///
  /// In zh, this message translates to:
  /// **'身份画像'**
  String get agentMindIdentity;

  /// No description provided for @agentMindPinned.
  ///
  /// In zh, this message translates to:
  /// **'置顶摘要'**
  String get agentMindPinned;

  /// No description provided for @agentMindNoteEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无运行笔记'**
  String get agentMindNoteEmpty;

  /// No description provided for @activityBrowse.
  ///
  /// In zh, this message translates to:
  /// **'浏览'**
  String get activityBrowse;

  /// No description provided for @activityLearn.
  ///
  /// In zh, this message translates to:
  /// **'学习'**
  String get activityLearn;

  /// No description provided for @activityLog.
  ///
  /// In zh, this message translates to:
  /// **'互动记录'**
  String get activityLog;

  /// No description provided for @add.
  ///
  /// In zh, this message translates to:
  /// **'添加'**
  String get add;

  /// No description provided for @addCommentHint.
  ///
  /// In zh, this message translates to:
  /// **'发表评论...'**
  String get addCommentHint;

  /// No description provided for @addFailed.
  ///
  /// In zh, this message translates to:
  /// **'添加失败'**
  String get addFailed;

  /// No description provided for @addMember.
  ///
  /// In zh, this message translates to:
  /// **'添加角色'**
  String get addMember;

  /// No description provided for @adopt.
  ///
  /// In zh, this message translates to:
  /// **'领养'**
  String get adopt;

  /// No description provided for @adoptFailed.
  ///
  /// In zh, this message translates to:
  /// **'领养失败'**
  String get adoptFailed;

  /// No description provided for @adoptFailedRetry.
  ///
  /// In zh, this message translates to:
  /// **'领养失败，请稍后重试'**
  String get adoptFailedRetry;

  /// No description provided for @adoptForChar.
  ///
  /// In zh, this message translates to:
  /// **'已帮 {name} 领养成功'**
  String adoptForChar(Object name);

  /// No description provided for @adoptForTa.
  ///
  /// In zh, this message translates to:
  /// **'帮 TA 领养'**
  String get adoptForTa;

  /// No description provided for @adoptHeading.
  ///
  /// In zh, this message translates to:
  /// **'领养一只小动物'**
  String get adoptHeading;

  /// No description provided for @adoptNewPet.
  ///
  /// In zh, this message translates to:
  /// **'领养新宠物'**
  String get adoptNewPet;

  /// No description provided for @adoptPetFor.
  ///
  /// In zh, this message translates to:
  /// **'帮 {name} 领养宠物'**
  String adoptPetFor(Object name);

  /// No description provided for @adoptSpecies.
  ///
  /// In zh, this message translates to:
  /// **'领养{label}'**
  String adoptSpecies(Object label);

  /// No description provided for @adoptSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'折纸风小宠物会成为家里的一员，AI 伙伴们也会记得它'**
  String get adoptSubtitle;

  /// No description provided for @aiBrowseHistory.
  ///
  /// In zh, this message translates to:
  /// **'AI 浏览记录'**
  String get aiBrowseHistory;

  /// No description provided for @aiDiary.
  ///
  /// In zh, this message translates to:
  /// **'AI日记'**
  String get aiDiary;

  /// No description provided for @aiDiaryHint.
  ///
  /// In zh, this message translates to:
  /// **'AI每天会撰写日记记录当天聊天'**
  String get aiDiaryHint;

  /// No description provided for @aiFriendFallback.
  ///
  /// In zh, this message translates to:
  /// **'AI 好友'**
  String get aiFriendFallback;

  /// No description provided for @aiGenerated.
  ///
  /// In zh, this message translates to:
  /// **'AI 生成'**
  String get aiGenerated;

  /// No description provided for @aiLife.
  ///
  /// In zh, this message translates to:
  /// **'AI 生活'**
  String get aiLife;

  /// No description provided for @aiLifeHint.
  ///
  /// In zh, this message translates to:
  /// **'TA的生活点滴/兴趣/产物'**
  String get aiLifeHint;

  /// No description provided for @aiOfflineLife.
  ///
  /// In zh, this message translates to:
  /// **'AI 离线生活'**
  String get aiOfflineLife;

  /// No description provided for @aiOfflineLifeHint.
  ///
  /// In zh, this message translates to:
  /// **'离线时角色会真实度过时间：状态变化、休息、反思、整理记忆（默认开启）'**
  String get aiOfflineLifeHint;

  /// No description provided for @aiPetsSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'拜访 TA 的宠物可以喂食 / 玩耍 / 清洁；TA 还没有宠物的话，也可以帮 TA 领养一只。'**
  String get aiPetsSubtitle;

  /// No description provided for @aiPetsTitle.
  ///
  /// In zh, this message translates to:
  /// **'角色们的宠物'**
  String get aiPetsTitle;

  /// No description provided for @aiPrivateChat.
  ///
  /// In zh, this message translates to:
  /// **'AI 间私聊'**
  String get aiPrivateChat;

  /// No description provided for @aiPrivateChatHint.
  ///
  /// In zh, this message translates to:
  /// **'开启后你的 AI 角色之间会偶尔私下聊天'**
  String get aiPrivateChatHint;

  /// No description provided for @aiPromised.
  ///
  /// In zh, this message translates to:
  /// **'AI 承诺'**
  String get aiPromised;

  /// No description provided for @aiSchedule.
  ///
  /// In zh, this message translates to:
  /// **'AI 的日程'**
  String get aiSchedule;

  /// No description provided for @aiWantsToCall.
  ///
  /// In zh, this message translates to:
  /// **'TA 想调用'**
  String get aiWantsToCall;

  /// No description provided for @albumTitle.
  ///
  /// In zh, this message translates to:
  /// **'相册'**
  String get albumTitle;

  /// No description provided for @allow.
  ///
  /// In zh, this message translates to:
  /// **'允许'**
  String get allow;

  /// No description provided for @featureFlagsTitle.
  ///
  /// In zh, this message translates to:
  /// **'服务器功能管理'**
  String get featureFlagsTitle;

  /// No description provided for @featureFlagsHint.
  ///
  /// In zh, this message translates to:
  /// **'即时生效，无需重启服务器'**
  String get featureFlagsHint;

  /// No description provided for @featureFlagsAdminOnly.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可管理服务器功能'**
  String get featureFlagsAdminOnly;

  /// No description provided for @flagLightReply.
  ///
  /// In zh, this message translates to:
  /// **'群聊精简回复模式'**
  String get flagLightReply;

  /// No description provided for @flagLightReplyHint.
  ///
  /// In zh, this message translates to:
  /// **'群聊/抖音回复用精简上下文，更省更快'**
  String get flagLightReplyHint;

  /// No description provided for @flagGroupRuntime.
  ///
  /// In zh, this message translates to:
  /// **'群聊统一智能回复'**
  String get flagGroupRuntime;

  /// No description provided for @flagGroupRuntimeHint.
  ///
  /// In zh, this message translates to:
  /// **'群聊回复接入角色记忆，各角色互不知晓彼此私事'**
  String get flagGroupRuntimeHint;

  /// No description provided for @flagSocialRuntime.
  ///
  /// In zh, this message translates to:
  /// **'平台动态统一回复'**
  String get flagSocialRuntime;

  /// No description provided for @flagSocialRuntimeHint.
  ///
  /// In zh, this message translates to:
  /// **'外部平台动态回复接入角色记忆'**
  String get flagSocialRuntimeHint;

  /// No description provided for @flagAdvanced.
  ///
  /// In zh, this message translates to:
  /// **'高级开关'**
  String get flagAdvanced;

  /// No description provided for @flagAdvancedHint.
  ///
  /// In zh, this message translates to:
  /// **'高级功能开关，请谨慎调整'**
  String get flagAdvancedHint;

  /// No description provided for @flagDetail.
  ///
  /// In zh, this message translates to:
  /// **'详情'**
  String get flagDetail;

  /// No description provided for @flagCollapse.
  ///
  /// In zh, this message translates to:
  /// **'收起'**
  String get flagCollapse;

  /// No description provided for @flagSaved.
  ///
  /// In zh, this message translates to:
  /// **'已保存'**
  String get flagSaved;

  /// No description provided for @flagError.
  ///
  /// In zh, this message translates to:
  /// **'切换失败，请重试'**
  String get flagError;

  /// No description provided for @flagWeave3D.
  ///
  /// In zh, this message translates to:
  /// **'织网 3D（实验）'**
  String get flagWeave3D;

  /// No description provided for @flagWeave3DHint.
  ///
  /// In zh, this message translates to:
  /// **'织库画布切换为 3D 球视图（实验功能，低端机可关）'**
  String get flagWeave3DHint;

  /// No description provided for @apiConfig.
  ///
  /// In zh, this message translates to:
  /// **'API 配置'**
  String get apiConfig;

  /// No description provided for @apiConfigHint.
  ///
  /// In zh, this message translates to:
  /// **'LLM / 生图服务（BYOK 与服务器级）'**
  String get apiConfigHint;

  /// No description provided for @appAlbum.
  ///
  /// In zh, this message translates to:
  /// **'相册'**
  String get appAlbum;

  /// No description provided for @appBrowser.
  ///
  /// In zh, this message translates to:
  /// **'浏览器'**
  String get appBrowser;

  /// No description provided for @appCalendar.
  ///
  /// In zh, this message translates to:
  /// **'日历'**
  String get appCalendar;

  /// No description provided for @appChat.
  ///
  /// In zh, this message translates to:
  /// **'畅聊'**
  String get appChat;

  /// No description provided for @appDescAlbum.
  ///
  /// In zh, this message translates to:
  /// **'AI 生成图片 + 我的上传'**
  String get appDescAlbum;

  /// No description provided for @appDescBrowser.
  ///
  /// In zh, this message translates to:
  /// **'浏览器扩展附属 · 搜索历史保留 7 天'**
  String get appDescBrowser;

  /// No description provided for @appDescCalendar.
  ///
  /// In zh, this message translates to:
  /// **'查看 / 写备注，AI 可见'**
  String get appDescCalendar;

  /// No description provided for @appDescChat.
  ///
  /// In zh, this message translates to:
  /// **'与角色畅聊'**
  String get appDescChat;

  /// No description provided for @appDescMarket.
  ///
  /// In zh, this message translates to:
  /// **'恢复误删的应用'**
  String get appDescMarket;

  /// No description provided for @appDescMemo.
  ///
  /// In zh, this message translates to:
  /// **'便签，AI 也会主动记录'**
  String get appDescMemo;

  /// No description provided for @appDescSettings.
  ///
  /// In zh, this message translates to:
  /// **'虚拟手机（占位）'**
  String get appDescSettings;

  /// No description provided for @appDescTheme.
  ///
  /// In zh, this message translates to:
  /// **'壁纸等手机美化'**
  String get appDescTheme;

  /// No description provided for @appMarket.
  ///
  /// In zh, this message translates to:
  /// **'应用市场'**
  String get appMarket;

  /// No description provided for @appMemo.
  ///
  /// In zh, this message translates to:
  /// **'备忘录'**
  String get appMemo;

  /// No description provided for @appPets.
  ///
  /// In zh, this message translates to:
  /// **'宠物'**
  String get appPets;

  /// No description provided for @appSettings.
  ///
  /// In zh, this message translates to:
  /// **'设置'**
  String get appSettings;

  /// No description provided for @appTheme.
  ///
  /// In zh, this message translates to:
  /// **'主题'**
  String get appTheme;

  /// No description provided for @appearance.
  ///
  /// In zh, this message translates to:
  /// **'样貌'**
  String get appearance;

  /// No description provided for @appearanceHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：长发飘飘，戴着眼镜，看起来很温柔'**
  String get appearanceHint;

  /// No description provided for @appearanceSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'皮肤、主题色与深浅模式，一键切换外观'**
  String get appearanceSubtitle;

  /// No description provided for @appearanceTitle.
  ///
  /// In zh, this message translates to:
  /// **'外观'**
  String get appearanceTitle;

  /// No description provided for @archiveBox.
  ///
  /// In zh, this message translates to:
  /// **'聊天记录箱'**
  String get archiveBox;

  /// No description provided for @archiveTitle.
  ///
  /// In zh, this message translates to:
  /// **'{a} · {b} 聊天记录箱'**
  String archiveTitle(Object a, Object b);

  /// No description provided for @arrangement.
  ///
  /// In zh, this message translates to:
  /// **'安排'**
  String get arrangement;

  /// No description provided for @artifactImage.
  ///
  /// In zh, this message translates to:
  /// **'作品'**
  String get artifactImage;

  /// No description provided for @artifactNote.
  ///
  /// In zh, this message translates to:
  /// **'笔记'**
  String get artifactNote;

  /// No description provided for @artifactText.
  ///
  /// In zh, this message translates to:
  /// **'创作'**
  String get artifactText;

  /// No description provided for @artifactsTab.
  ///
  /// In zh, this message translates to:
  /// **'产物库'**
  String get artifactsTab;

  /// No description provided for @artifactsTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name}的产物库'**
  String artifactsTitle(Object name);

  /// No description provided for @avatarUpdateFailed.
  ///
  /// In zh, this message translates to:
  /// **'头像更新失败'**
  String get avatarUpdateFailed;

  /// No description provided for @avatarUpdated.
  ///
  /// In zh, this message translates to:
  /// **'头像已更新'**
  String get avatarUpdated;

  /// No description provided for @back.
  ///
  /// In zh, this message translates to:
  /// **'返回'**
  String get back;

  /// No description provided for @backgroundInfo.
  ///
  /// In zh, this message translates to:
  /// **'背景信息'**
  String get backgroundInfo;

  /// No description provided for @basic.
  ///
  /// In zh, this message translates to:
  /// **'基本'**
  String get basic;

  /// No description provided for @birthday.
  ///
  /// In zh, this message translates to:
  /// **'生日'**
  String get birthday;

  /// No description provided for @browserTitle.
  ///
  /// In zh, this message translates to:
  /// **'浏览器'**
  String get browserTitle;

  /// No description provided for @browsingHint.
  ///
  /// In zh, this message translates to:
  /// **'开启「AI 离线生活」并授权浏览器后，TA 会真实浏览网页'**
  String get browsingHint;

  /// No description provided for @browsingTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name} · 浏览记录'**
  String browsingTitle(Object name);

  /// No description provided for @calendarHint.
  ///
  /// In zh, this message translates to:
  /// **'点击日期可查看 / 添加备注（AI 会在聊天中感知近期备注）'**
  String get calendarHint;

  /// No description provided for @calendarMemory.
  ///
  /// In zh, this message translates to:
  /// **'记忆库'**
  String get calendarMemory;

  /// No description provided for @calendarTitle.
  ///
  /// In zh, this message translates to:
  /// **'{y}年{m}月'**
  String calendarTitle(Object y, Object m);

  /// No description provided for @wfDefaultName.
  ///
  /// In zh, this message translates to:
  /// **'工作流'**
  String get wfDefaultName;

  /// No description provided for @wfImportConfirm.
  ///
  /// In zh, this message translates to:
  /// **'导入「{name}」？'**
  String wfImportConfirm(Object name);

  /// No description provided for @wfImportNoTemplates.
  ///
  /// In zh, this message translates to:
  /// **'暂无 workflow 型插件的模板'**
  String get wfImportNoTemplates;

  /// No description provided for @wfImportSuccess.
  ///
  /// In zh, this message translates to:
  /// **'工作流导入成功'**
  String get wfImportSuccess;

  /// No description provided for @wfImportTemplates.
  ///
  /// In zh, this message translates to:
  /// **'从插件模板导入'**
  String get wfImportTemplates;

  /// No description provided for @chatRunWf.
  ///
  /// In zh, this message translates to:
  /// **'执行「{name}」'**
  String chatRunWf(Object name);

  /// No description provided for @chatWfSteps.
  ///
  /// In zh, this message translates to:
  /// **'共 {count} 步，按顺序操作手机（敏感步骤需你确认）'**
  String chatWfSteps(Object count);

  /// No description provided for @chatWfDone.
  ///
  /// In zh, this message translates to:
  /// **'工作流执行完成：{summary}'**
  String chatWfDone(Object summary);

  /// No description provided for @chatWfInterrupted.
  ///
  /// In zh, this message translates to:
  /// **'工作流中断：{summary}'**
  String chatWfInterrupted(Object summary);

  /// No description provided for @chatWfStep.
  ///
  /// In zh, this message translates to:
  /// **'第{step}步{mark} {msg}'**
  String chatWfStep(Object mark, Object msg, Object step);

  /// No description provided for @chatNoAccessibility.
  ///
  /// In zh, this message translates to:
  /// **'未开启读屏（无障碍），无法执行操作'**
  String get chatNoAccessibility;

  /// No description provided for @chatSeqInterrupted.
  ///
  /// In zh, this message translates to:
  /// **'序列中断：{summary}'**
  String chatSeqInterrupted(Object summary);

  /// No description provided for @chatSeqDone.
  ///
  /// In zh, this message translates to:
  /// **'序列执行完成：{summary}'**
  String chatSeqDone(Object summary);

  /// No description provided for @chatPickTarget.
  ///
  /// In zh, this message translates to:
  /// **'AI 想操作当前屏幕，选一个目标'**
  String get chatPickTarget;

  /// No description provided for @nodeClickable.
  ///
  /// In zh, this message translates to:
  /// **'可点击'**
  String get nodeClickable;

  /// No description provided for @nodeInput.
  ///
  /// In zh, this message translates to:
  /// **'输入框'**
  String get nodeInput;

  /// No description provided for @chatNoNodes.
  ///
  /// In zh, this message translates to:
  /// **'当前屏幕暂无可操作节点'**
  String get chatNoNodes;

  /// No description provided for @chatOpDone.
  ///
  /// In zh, this message translates to:
  /// **'执行完成'**
  String get chatOpDone;

  /// No description provided for @seqReply.
  ///
  /// In zh, this message translates to:
  /// **'回复消息'**
  String get seqReply;

  /// No description provided for @seqPublish.
  ///
  /// In zh, this message translates to:
  /// **'发布朋友圈'**
  String get seqPublish;

  /// No description provided for @seqLike.
  ///
  /// In zh, this message translates to:
  /// **'点赞'**
  String get seqLike;

  /// No description provided for @seqPlay.
  ///
  /// In zh, this message translates to:
  /// **'播放/切歌'**
  String get seqPlay;

  /// No description provided for @seqCombo.
  ///
  /// In zh, this message translates to:
  /// **'组合操作'**
  String get seqCombo;

  /// No description provided for @seqInputLine.
  ///
  /// In zh, this message translates to:
  /// **'· 输入“{text}”'**
  String seqInputLine(Object text);

  /// No description provided for @seqClick.
  ///
  /// In zh, this message translates to:
  /// **'点击'**
  String get seqClick;

  /// No description provided for @seqLongClick.
  ///
  /// In zh, this message translates to:
  /// **'长按'**
  String get seqLongClick;

  /// No description provided for @seqClickLine.
  ///
  /// In zh, this message translates to:
  /// **'· {verb}“{target}”'**
  String seqClickLine(Object target, Object verb);

  /// No description provided for @chatSeqTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 想按序列操作手机'**
  String get chatSeqTitle;

  /// No description provided for @chatSeqDesc.
  ///
  /// In zh, this message translates to:
  /// **'场景：{type}\n{steps}\n\n{autoNote}干涉档位：轻度干涉（默认）——序列内不自动跳转 app，切换页面由你手动完成；密码/银行/支付类节点自动拒绝。'**
  String chatSeqDesc(Object autoNote, Object steps, Object type);

  /// No description provided for @chatSeqAutoNote.
  ///
  /// In zh, this message translates to:
  /// **'将自动切换到朋友圈页执行（自家 app 内导航，不跳转其他 app）。\n'**
  String get chatSeqAutoNote;

  /// No description provided for @reject.
  ///
  /// In zh, this message translates to:
  /// **'拒绝'**
  String get reject;

  /// No description provided for @allowOnce.
  ///
  /// In zh, this message translates to:
  /// **'允许本次'**
  String get allowOnce;

  /// No description provided for @allowMinute.
  ///
  /// In zh, this message translates to:
  /// **'允许1分钟'**
  String get allowMinute;

  /// No description provided for @chatOpDefault.
  ///
  /// In zh, this message translates to:
  /// **'操作'**
  String get chatOpDefault;

  /// No description provided for @chatOpTarget.
  ///
  /// In zh, this message translates to:
  /// **'点击/长按“{target}”'**
  String chatOpTarget(Object target);

  /// No description provided for @chatOpInput.
  ///
  /// In zh, this message translates to:
  /// **'输入文本'**
  String get chatOpInput;

  /// No description provided for @chatOpTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 想操作你的手机'**
  String get chatOpTitle;

  /// No description provided for @chatOpDesc.
  ///
  /// In zh, this message translates to:
  /// **'操作：{op}\n仅作用于当前可见页面，不跨应用跳转；密码/银行/支付类节点已自动拒绝。'**
  String chatOpDesc(Object op);

  /// No description provided for @chatInputTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 想帮你输入'**
  String get chatInputTitle;

  /// No description provided for @chatInputHint.
  ///
  /// In zh, this message translates to:
  /// **'输入内容（≤50字）'**
  String get chatInputHint;

  /// No description provided for @chatInputHintTarget.
  ///
  /// In zh, this message translates to:
  /// **'输入到“{target}”（≤50字）'**
  String chatInputHintTarget(Object target);

  /// No description provided for @input.
  ///
  /// In zh, this message translates to:
  /// **'输入'**
  String get input;

  /// No description provided for @chatFileSendFail.
  ///
  /// In zh, this message translates to:
  /// **'文件发送失败: {err}'**
  String chatFileSendFail(Object err);

  /// No description provided for @chatContinuous.
  ///
  /// In zh, this message translates to:
  /// **'连续发送'**
  String get chatContinuous;

  /// No description provided for @chatVoiceSend.
  ///
  /// In zh, this message translates to:
  /// **'语音发送'**
  String get chatVoiceSend;

  /// No description provided for @chatEmoji.
  ///
  /// In zh, this message translates to:
  /// **'表情'**
  String get chatEmoji;

  /// No description provided for @chatSendImage.
  ///
  /// In zh, this message translates to:
  /// **'发送图片'**
  String get chatSendImage;

  /// No description provided for @chatImageCaption.
  ///
  /// In zh, this message translates to:
  /// **'给图片配一句话（可选）...'**
  String get chatImageCaption;

  /// No description provided for @send.
  ///
  /// In zh, this message translates to:
  /// **'发送'**
  String get send;

  /// No description provided for @chatEmojiDownloaded.
  ///
  /// In zh, this message translates to:
  /// **'已下载「{name}」'**
  String chatEmojiDownloaded(Object name);

  /// No description provided for @chatEmojiAdd.
  ///
  /// In zh, this message translates to:
  /// **'添加'**
  String get chatEmojiAdd;

  /// No description provided for @chatEmojiHint.
  ///
  /// In zh, this message translates to:
  /// **'{desc}（点击表情自动下载并发送）'**
  String chatEmojiHint(Object desc);

  /// No description provided for @emojiMarketTab.
  ///
  /// In zh, this message translates to:
  /// **'市场'**
  String get emojiMarketTab;

  /// No description provided for @emojiMarketEmojiCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 个表情'**
  String emojiMarketEmojiCount(Object count);

  /// No description provided for @emojiMarketDownloading.
  ///
  /// In zh, this message translates to:
  /// **'下载中…'**
  String get emojiMarketDownloading;

  /// No description provided for @emojiMarketDownloadFail.
  ///
  /// In zh, this message translates to:
  /// **'下载失败，请重试'**
  String get emojiMarketDownloadFail;

  /// No description provided for @emojiMarketUninstall.
  ///
  /// In zh, this message translates to:
  /// **'卸载'**
  String get emojiMarketUninstall;

  /// No description provided for @emojiMarketUninstalled.
  ///
  /// In zh, this message translates to:
  /// **'已卸载'**
  String get emojiMarketUninstalled;

  /// No description provided for @emojiMarketEmpty.
  ///
  /// In zh, this message translates to:
  /// **'市场上还没有表情包'**
  String get emojiMarketEmpty;

  /// No description provided for @emojiMarketUnavailable.
  ///
  /// In zh, this message translates to:
  /// **'表情市场暂不可用，请稍后再试'**
  String get emojiMarketUnavailable;

  /// No description provided for @chatMicPermission.
  ///
  /// In zh, this message translates to:
  /// **'需要麦克风权限才能发送语音'**
  String get chatMicPermission;

  /// No description provided for @tzDefault.
  ///
  /// In zh, this message translates to:
  /// **'默认（北京时间 UTC+8）'**
  String get tzDefault;

  /// No description provided for @tzBeijing.
  ///
  /// In zh, this message translates to:
  /// **'北京 UTC+8'**
  String get tzBeijing;

  /// No description provided for @tzTokyo.
  ///
  /// In zh, this message translates to:
  /// **'东京 UTC+9'**
  String get tzTokyo;

  /// No description provided for @tzDubai.
  ///
  /// In zh, this message translates to:
  /// **'迪拜 UTC+4'**
  String get tzDubai;

  /// No description provided for @tzMoscow.
  ///
  /// In zh, this message translates to:
  /// **'莫斯科 UTC+3'**
  String get tzMoscow;

  /// No description provided for @tzParis.
  ///
  /// In zh, this message translates to:
  /// **'巴黎 UTC+1'**
  String get tzParis;

  /// No description provided for @tzLondon.
  ///
  /// In zh, this message translates to:
  /// **'伦敦 UTC+0'**
  String get tzLondon;

  /// No description provided for @tzNewYork.
  ///
  /// In zh, this message translates to:
  /// **'纽约 UTC-5'**
  String get tzNewYork;

  /// No description provided for @tzLosAngeles.
  ///
  /// In zh, this message translates to:
  /// **'洛杉矶 UTC-8'**
  String get tzLosAngeles;

  /// No description provided for @tzSydney.
  ///
  /// In zh, this message translates to:
  /// **'悉尼 UTC+10'**
  String get tzSydney;

  /// No description provided for @voiceXiaoxiao.
  ///
  /// In zh, this message translates to:
  /// **'晓晓 · 自然女声'**
  String get voiceXiaoxiao;

  /// No description provided for @voiceXiaoyi.
  ///
  /// In zh, this message translates to:
  /// **'晓伊 · 年轻女声'**
  String get voiceXiaoyi;

  /// No description provided for @voiceXiaobei.
  ///
  /// In zh, this message translates to:
  /// **'晓北 · 东北女声'**
  String get voiceXiaobei;

  /// No description provided for @voiceXiaoni.
  ///
  /// In zh, this message translates to:
  /// **'晓妮 · 陕西女声'**
  String get voiceXiaoni;

  /// No description provided for @voiceXiaojia.
  ///
  /// In zh, this message translates to:
  /// **'曉佳 · 粤语女声'**
  String get voiceXiaojia;

  /// No description provided for @voiceXiaoman.
  ///
  /// In zh, this message translates to:
  /// **'曉曼 · 粤语女声'**
  String get voiceXiaoman;

  /// No description provided for @voiceXiaozhen.
  ///
  /// In zh, this message translates to:
  /// **'曉臻 · 台湾女声'**
  String get voiceXiaozhen;

  /// No description provided for @voiceYunxi.
  ///
  /// In zh, this message translates to:
  /// **'云希 · 青年男声'**
  String get voiceYunxi;

  /// No description provided for @voiceYunjian.
  ///
  /// In zh, this message translates to:
  /// **'云健 · 磁性男声'**
  String get voiceYunjian;

  /// No description provided for @voiceYunyang.
  ///
  /// In zh, this message translates to:
  /// **'云扬 · 新闻男声'**
  String get voiceYunyang;

  /// No description provided for @voiceYunfeng.
  ///
  /// In zh, this message translates to:
  /// **'云枫 · 成熟男声'**
  String get voiceYunfeng;

  /// No description provided for @voiceYunlong.
  ///
  /// In zh, this message translates to:
  /// **'雲龍 · 粤语男声'**
  String get voiceYunlong;

  /// No description provided for @voicePreviewFailConfig.
  ///
  /// In zh, this message translates to:
  /// **'试听失败：语音合成不可用，请检查服务器语音配置'**
  String get voicePreviewFailConfig;

  /// No description provided for @voicePreviewFailNet.
  ///
  /// In zh, this message translates to:
  /// **'试听失败，请检查网络'**
  String get voicePreviewFailNet;

  /// No description provided for @avatarUploadFail.
  ///
  /// In zh, this message translates to:
  /// **'头像上传失败'**
  String get avatarUploadFail;

  /// No description provided for @voiceRate.
  ///
  /// In zh, this message translates to:
  /// **'语速'**
  String get voiceRate;

  /// No description provided for @voicePitch.
  ///
  /// In zh, this message translates to:
  /// **'语调'**
  String get voicePitch;

  /// No description provided for @pitchNormal.
  ///
  /// In zh, this message translates to:
  /// **'正常'**
  String get pitchNormal;

  /// No description provided for @saveFail.
  ///
  /// In zh, this message translates to:
  /// **'保存失败'**
  String get saveFail;

  /// No description provided for @createFriend.
  ///
  /// In zh, this message translates to:
  /// **'创建好友'**
  String get createFriend;

  /// No description provided for @toolboxTitle.
  ///
  /// In zh, this message translates to:
  /// **'工具箱'**
  String get toolboxTitle;

  /// No description provided for @editFriend.
  ///
  /// In zh, this message translates to:
  /// **'编辑好友'**
  String get editFriend;

  /// No description provided for @save.
  ///
  /// In zh, this message translates to:
  /// **'保存'**
  String get save;

  /// No description provided for @tapToPickAvatar.
  ///
  /// In zh, this message translates to:
  /// **'点击选择头像（可选）'**
  String get tapToPickAvatar;

  /// No description provided for @basicInfo.
  ///
  /// In zh, this message translates to:
  /// **'基础信息'**
  String get basicInfo;

  /// No description provided for @name.
  ///
  /// In zh, this message translates to:
  /// **'名字'**
  String get name;

  /// No description provided for @nameRequired.
  ///
  /// In zh, this message translates to:
  /// **'名字不能为空'**
  String get nameRequired;

  /// No description provided for @heightCm.
  ///
  /// In zh, this message translates to:
  /// **'身高(cm)'**
  String get heightCm;

  /// No description provided for @weightKg.
  ///
  /// In zh, this message translates to:
  /// **'体重(kg)'**
  String get weightKg;

  /// No description provided for @birthdayHint.
  ///
  /// In zh, this message translates to:
  /// **'YYYY-MM-DD（例如 1998-05-20）'**
  String get birthdayHint;

  /// No description provided for @gender.
  ///
  /// In zh, this message translates to:
  /// **'性别'**
  String get gender;

  /// No description provided for @genderOther.
  ///
  /// In zh, this message translates to:
  /// **'其他'**
  String get genderOther;

  /// No description provided for @genderFemale.
  ///
  /// In zh, this message translates to:
  /// **'女'**
  String get genderFemale;

  /// No description provided for @genderMale.
  ///
  /// In zh, this message translates to:
  /// **'男'**
  String get genderMale;

  /// No description provided for @backgroundInfoHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：出身、经历、性格成因，越详细 AI 越有立体感'**
  String get backgroundInfoHint;

  /// No description provided for @timezone.
  ///
  /// In zh, this message translates to:
  /// **'所在时区'**
  String get timezone;

  /// No description provided for @timezoneHelper.
  ///
  /// In zh, this message translates to:
  /// **'TA 所在地区的时区；朋友圈动态时间按此显示（默认=北京时间）'**
  String get timezoneHelper;

  /// No description provided for @personalityGroup.
  ///
  /// In zh, this message translates to:
  /// **'性格'**
  String get personalityGroup;

  /// No description provided for @personality.
  ///
  /// In zh, this message translates to:
  /// **'人格'**
  String get personality;

  /// No description provided for @personalityHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：温柔体贴，善解人意'**
  String get personalityHint;

  /// No description provided for @chatStyleHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：说话轻柔，喜欢用表情符号'**
  String get chatStyleHint;

  /// No description provided for @talkativeness.
  ///
  /// In zh, this message translates to:
  /// **'话痨度'**
  String get talkativeness;

  /// No description provided for @talkativenessLocked.
  ///
  /// In zh, this message translates to:
  /// **'锁定'**
  String get talkativenessLocked;

  /// No description provided for @talkativenessLockedHint.
  ///
  /// In zh, this message translates to:
  /// **'锁定后 AI 不会自动调整话痨度'**
  String get talkativenessLockedHint;

  /// No description provided for @generateGreetingAsk.
  ///
  /// In zh, this message translates to:
  /// **'是否生成问候语？'**
  String get generateGreetingAsk;

  /// No description provided for @generateGreetingDesc.
  ///
  /// In zh, this message translates to:
  /// **'用 LLM 为 TA 生成一句符合人设的开场白？'**
  String get generateGreetingDesc;

  /// No description provided for @generateGreetingDo.
  ///
  /// In zh, this message translates to:
  /// **'生成'**
  String get generateGreetingDo;

  /// No description provided for @generateGreetingSkip.
  ///
  /// In zh, this message translates to:
  /// **'跳过'**
  String get generateGreetingSkip;

  /// No description provided for @generateGreetingDone.
  ///
  /// In zh, this message translates to:
  /// **'问候语已生成'**
  String get generateGreetingDone;

  /// No description provided for @generateGreetingFail.
  ///
  /// In zh, this message translates to:
  /// **'生成失败，可稍后再试'**
  String get generateGreetingFail;

  /// No description provided for @voiceGroup.
  ///
  /// In zh, this message translates to:
  /// **'声音'**
  String get voiceGroup;

  /// No description provided for @voiceLabel.
  ///
  /// In zh, this message translates to:
  /// **'声音'**
  String get voiceLabel;

  /// No description provided for @voiceHelper.
  ///
  /// In zh, this message translates to:
  /// **'语音对话时使用的音色（默认=按性别）'**
  String get voiceHelper;

  /// No description provided for @voiceDefault.
  ///
  /// In zh, this message translates to:
  /// **'默认（按性别）'**
  String get voiceDefault;

  /// No description provided for @previewing.
  ///
  /// In zh, this message translates to:
  /// **'试听中…'**
  String get previewing;

  /// No description provided for @previewVoice.
  ///
  /// In zh, this message translates to:
  /// **'试听当前音色'**
  String get previewVoice;

  /// No description provided for @previewHint.
  ///
  /// In zh, this message translates to:
  /// **'固定文案合成，即时预览音色与语速语调'**
  String get previewHint;

  /// No description provided for @deleteFriend.
  ///
  /// In zh, this message translates to:
  /// **'删除好友'**
  String get deleteFriend;

  /// No description provided for @confirmDelete.
  ///
  /// In zh, this message translates to:
  /// **'确认删除'**
  String get confirmDelete;

  /// No description provided for @deleteFriendConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定要删除「{name}」吗？\n聊天记录和记忆将一并删除，不可恢复。'**
  String deleteFriendConfirm(Object name);

  /// No description provided for @delete.
  ///
  /// In zh, this message translates to:
  /// **'删除'**
  String get delete;

  /// No description provided for @deleteFail.
  ///
  /// In zh, this message translates to:
  /// **'删除失败'**
  String get deleteFail;

  /// No description provided for @usageStats.
  ///
  /// In zh, this message translates to:
  /// **'用量统计'**
  String get usageStats;

  /// No description provided for @myLlm.
  ///
  /// In zh, this message translates to:
  /// **'我的 LLM（BYOK）'**
  String get myLlm;

  /// No description provided for @myLlmHint.
  ///
  /// In zh, this message translates to:
  /// **'仅聊天主链路生效：开启后你自己的 OpenAI 兼容端点优先于服务器配置'**
  String get myLlmHint;

  /// No description provided for @enable.
  ///
  /// In zh, this message translates to:
  /// **'启用'**
  String get enable;

  /// No description provided for @apiKeyConfigured.
  ///
  /// In zh, this message translates to:
  /// **'API Key 已配置'**
  String get apiKeyConfigured;

  /// No description provided for @apiKeyNotConfigured.
  ///
  /// In zh, this message translates to:
  /// **'API Key 未配置，请填写下方 Key'**
  String get apiKeyNotConfigured;

  /// No description provided for @apiKeyNotConfiguredShort.
  ///
  /// In zh, this message translates to:
  /// **'API Key 未配置'**
  String get apiKeyNotConfiguredShort;

  /// No description provided for @apiKeyKeep.
  ///
  /// In zh, this message translates to:
  /// **'API Key（留空保持不变）'**
  String get apiKeyKeep;

  /// No description provided for @apiKeyHintReplace.
  ///
  /// In zh, this message translates to:
  /// **'已配置，输入新 Key 可替换（多个逗号分隔轮换）'**
  String get apiKeyHintReplace;

  /// No description provided for @testConnection.
  ///
  /// In zh, this message translates to:
  /// **'检测连接'**
  String get testConnection;

  /// No description provided for @saveMyConfig.
  ///
  /// In zh, this message translates to:
  /// **'保存我的配置'**
  String get saveMyConfig;

  /// No description provided for @srvLlm.
  ///
  /// In zh, this message translates to:
  /// **'服务器级 LLM（全局）'**
  String get srvLlm;

  /// No description provided for @srvLlmHint.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可管理：影响所有未配置 BYOK 的调用（日记/朋友圈/记忆等）'**
  String get srvLlmHint;

  /// No description provided for @llmPresets.
  ///
  /// In zh, this message translates to:
  /// **'LLM 供应商预设'**
  String get llmPresets;

  /// No description provided for @apiKeyRotateHint.
  ///
  /// In zh, this message translates to:
  /// **'多个 Key 用逗号分隔自动轮换'**
  String get apiKeyRotateHint;

  /// No description provided for @saveSrvLlm.
  ///
  /// In zh, this message translates to:
  /// **'保存服务器级 LLM'**
  String get saveSrvLlm;

  /// No description provided for @srvSpeech.
  ///
  /// In zh, this message translates to:
  /// **'服务器级语音大模型'**
  String get srvSpeech;

  /// No description provided for @srvSpeechHint.
  ///
  /// In zh, this message translates to:
  /// **'语音转写当前走本地 faster-whisper；云端 ASR 配置先落库，调用链路后续接入'**
  String get srvSpeechHint;

  /// No description provided for @speechPresets.
  ///
  /// In zh, this message translates to:
  /// **'语音供应商预设'**
  String get speechPresets;

  /// No description provided for @saveSrvSpeech.
  ///
  /// In zh, this message translates to:
  /// **'保存服务器级语音'**
  String get saveSrvSpeech;

  /// No description provided for @srvVlm.
  ///
  /// In zh, this message translates to:
  /// **'服务器级识图（图片理解）'**
  String get srvVlm;

  /// No description provided for @srvVlmHint.
  ///
  /// In zh, this message translates to:
  /// **'聊天/手机感知读图用：填 API Key 优先走云端视觉 API，不填则用本地 OCR（可选本地 VLM）'**
  String get srvVlmHint;

  /// No description provided for @vlmPresets.
  ///
  /// In zh, this message translates to:
  /// **'识图供应商预设'**
  String get vlmPresets;

  /// No description provided for @saveSrvVlm.
  ///
  /// In zh, this message translates to:
  /// **'保存服务器级识图'**
  String get saveSrvVlm;

  /// No description provided for @srvImageGen.
  ///
  /// In zh, this message translates to:
  /// **'服务器级生图（全局）'**
  String get srvImageGen;

  /// No description provided for @srvImageGenHint.
  ///
  /// In zh, this message translates to:
  /// **'聊天内 AI 发图使用；provider: dashscope=通义千问 / openai=OpenAI 兼容'**
  String get srvImageGenHint;

  /// No description provided for @imagePresets.
  ///
  /// In zh, this message translates to:
  /// **'生图供应商预设'**
  String get imagePresets;

  /// No description provided for @dailyLimit.
  ///
  /// In zh, this message translates to:
  /// **'每日限额（张）'**
  String get dailyLimit;

  /// No description provided for @saveSrvImageGen.
  ///
  /// In zh, this message translates to:
  /// **'保存服务器级生图'**
  String get saveSrvImageGen;

  /// No description provided for @srvTask.
  ///
  /// In zh, this message translates to:
  /// **'服务器级任务模型（按用途指定）'**
  String get srvTask;

  /// No description provided for @srvTaskHint.
  ///
  /// In zh, this message translates to:
  /// **'记忆/卡片/情绪/状态/复习/主动消息/日记/时光可分别指定模型；API Key 多个逗号分隔自动轮换；留空回退服务器级 LLM'**
  String get srvTaskHint;

  /// No description provided for @task.
  ///
  /// In zh, this message translates to:
  /// **'任务'**
  String get task;

  /// No description provided for @taskHint.
  ///
  /// In zh, this message translates to:
  /// **'选择要指定模型的任务'**
  String get taskHint;

  /// No description provided for @saveTaskConfig.
  ///
  /// In zh, this message translates to:
  /// **'保存任务配置'**
  String get saveTaskConfig;

  /// No description provided for @srvAdminOnly.
  ///
  /// In zh, this message translates to:
  /// **'服务器级配置仅主账号（user_id=1）可管理，请联系部署者配置。'**
  String get srvAdminOnly;

  /// No description provided for @saveSuccessEnabled.
  ///
  /// In zh, this message translates to:
  /// **'保存成功（enabled={enabled}）'**
  String saveSuccessEnabled(Object enabled);

  /// No description provided for @saveFailedErr.
  ///
  /// In zh, this message translates to:
  /// **'保存失败: {err}'**
  String saveFailedErr(Object err);

  /// No description provided for @loadConfigFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载配置失败：{err}'**
  String loadConfigFailed(Object err);

  /// No description provided for @connSuccess.
  ///
  /// In zh, this message translates to:
  /// **'连接成功：{model}（{latency}ms，Key尾号 {tail}）'**
  String connSuccess(Object latency, Object model, Object tail);

  /// No description provided for @connFailed.
  ///
  /// In zh, this message translates to:
  /// **'连接失败：{err}'**
  String connFailed(Object err);

  /// No description provided for @testRequestFailed.
  ///
  /// In zh, this message translates to:
  /// **'测试请求失败：{err}'**
  String testRequestFailed(Object err);

  /// No description provided for @presetSelectHint.
  ///
  /// In zh, this message translates to:
  /// **'选择后自动填入，Key 仍需手动填'**
  String get presetSelectHint;

  /// No description provided for @model.
  ///
  /// In zh, this message translates to:
  /// **'模型'**
  String get model;

  /// No description provided for @provider.
  ///
  /// In zh, this message translates to:
  /// **'供应商'**
  String get provider;

  /// No description provided for @setQuotaTotal.
  ///
  /// In zh, this message translates to:
  /// **'设置免费额度总量'**
  String get setQuotaTotal;

  /// No description provided for @quotaHint.
  ///
  /// In zh, this message translates to:
  /// **'单位：tokens（如 1000000）'**
  String get quotaHint;

  /// No description provided for @quotaCleared.
  ///
  /// In zh, this message translates to:
  /// **'已清除总额设置'**
  String get quotaCleared;

  /// No description provided for @quotaUpdated.
  ///
  /// In zh, this message translates to:
  /// **'总额已更新'**
  String get quotaUpdated;

  /// No description provided for @saveFailed.
  ///
  /// In zh, this message translates to:
  /// **'保存失败'**
  String get saveFailed;

  /// No description provided for @unitYi.
  ///
  /// In zh, this message translates to:
  /// **'{n}亿'**
  String unitYi(Object n);

  /// No description provided for @unitWan.
  ///
  /// In zh, this message translates to:
  /// **'{n}万'**
  String unitWan(Object n);

  /// No description provided for @llmUsageStats.
  ///
  /// In zh, this message translates to:
  /// **'LLM 用量统计'**
  String get llmUsageStats;

  /// No description provided for @loadFailedCheckServer.
  ///
  /// In zh, this message translates to:
  /// **'加载失败，请检查服务器连接'**
  String get loadFailedCheckServer;

  /// No description provided for @usedTotal.
  ///
  /// In zh, this message translates to:
  /// **'已用 / 总额'**
  String get usedTotal;

  /// No description provided for @setQuota.
  ///
  /// In zh, this message translates to:
  /// **'设置总额'**
  String get setQuota;

  /// No description provided for @totalTokensNoQuota.
  ///
  /// In zh, this message translates to:
  /// **'{total} tokens（未设置总额）'**
  String totalTokensNoQuota(Object total);

  /// No description provided for @remainingTokens.
  ///
  /// In zh, this message translates to:
  /// **'剩余约 {remaining} tokens'**
  String remainingTokens(Object remaining);

  /// No description provided for @today.
  ///
  /// In zh, this message translates to:
  /// **'今天'**
  String get today;

  /// No description provided for @last7Days.
  ///
  /// In zh, this message translates to:
  /// **'近 7 天'**
  String get last7Days;

  /// No description provided for @thisMonth.
  ///
  /// In zh, this message translates to:
  /// **'本月'**
  String get thisMonth;

  /// No description provided for @byModelUsage.
  ///
  /// In zh, this message translates to:
  /// **'按模型用量'**
  String get byModelUsage;

  /// No description provided for @byUserUsage.
  ///
  /// In zh, this message translates to:
  /// **'按账号用量'**
  String get byUserUsage;

  /// No description provided for @expandByAccount.
  ///
  /// In zh, this message translates to:
  /// **'按账号展开'**
  String get expandByAccount;

  /// No description provided for @collapseByAccount.
  ///
  /// In zh, this message translates to:
  /// **'收起'**
  String get collapseByAccount;

  /// No description provided for @unknown.
  ///
  /// In zh, this message translates to:
  /// **'未知'**
  String get unknown;

  /// No description provided for @etcModels.
  ///
  /// In zh, this message translates to:
  /// **'等 {count} 个模型'**
  String etcModels(Object count);

  /// No description provided for @usageNote.
  ///
  /// In zh, this message translates to:
  /// **'用量由本 App 每次 LLM 调用自动累计（近似值，非官方数据）；总额请按百炼控制台免费额度手动填写'**
  String get usageNote;

  /// No description provided for @ppEnabledOn.
  ///
  /// In zh, this message translates to:
  /// **'手机感知已开启，请按需打开下方采集项'**
  String get ppEnabledOn;

  /// No description provided for @ppEnabledOff.
  ///
  /// In zh, this message translates to:
  /// **'手机感知已关闭'**
  String get ppEnabledOff;

  /// No description provided for @ppOpenAccessibility.
  ///
  /// In zh, this message translates to:
  /// **'请在系统无障碍设置中开启“拥爱手机感知”'**
  String get ppOpenAccessibility;

  /// No description provided for @ppUsageNotGranted.
  ///
  /// In zh, this message translates to:
  /// **'仍未检测到“使用情况访问”授权，请到系统设置确认后重试'**
  String get ppUsageNotGranted;

  /// No description provided for @ppUsageGrantedWith.
  ///
  /// In zh, this message translates to:
  /// **'授权成功，已开启应用使用时长：{content}'**
  String ppUsageGrantedWith(Object content);

  /// No description provided for @ppUsageGrantedEmpty.
  ///
  /// In zh, this message translates to:
  /// **'授权成功，已开启应用使用时长（暂无数据，稍后自动上报）'**
  String get ppUsageGrantedEmpty;

  /// No description provided for @ppUsageOpenSettings.
  ///
  /// In zh, this message translates to:
  /// **'请在系统“使用情况访问”中允许“拥爱”，返回后将自动生效'**
  String get ppUsageOpenSettings;

  /// No description provided for @ppUsageEnabledWith.
  ///
  /// In zh, this message translates to:
  /// **'已开启应用使用时长：{content}'**
  String ppUsageEnabledWith(Object content);

  /// No description provided for @ppUsageEnabledEmpty.
  ///
  /// In zh, this message translates to:
  /// **'应用使用时长已开启（暂无数据，稍后自动上报）'**
  String get ppUsageEnabledEmpty;

  /// No description provided for @ppUsageDisabled.
  ///
  /// In zh, this message translates to:
  /// **'已关闭应用使用时长'**
  String get ppUsageDisabled;

  /// No description provided for @ppOpenNotification.
  ///
  /// In zh, this message translates to:
  /// **'请在系统“通知使用权”中开启“拥爱通知感知”'**
  String get ppOpenNotification;

  /// No description provided for @ppMediaDenied.
  ///
  /// In zh, this message translates to:
  /// **'未获得相册权限，请到系统设置中允许访问照片'**
  String get ppMediaDenied;

  /// No description provided for @ppMediaFilesDenied.
  ///
  /// In zh, this message translates to:
  /// **'未获得视频/音频权限，请到系统设置中允许'**
  String get ppMediaFilesDenied;

  /// No description provided for @ppCollectedWith.
  ///
  /// In zh, this message translates to:
  /// **'已采集：{preview}'**
  String ppCollectedWith(Object preview);

  /// No description provided for @ppCollectDisabled.
  ///
  /// In zh, this message translates to:
  /// **'手机感知未开启，请先开启总开关'**
  String get ppCollectDisabled;

  /// No description provided for @ppCollectNoSources.
  ///
  /// In zh, this message translates to:
  /// **'未选择采集项，请先勾选下方采集项'**
  String get ppCollectNoSources;

  /// No description provided for @ppCollectEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无可采集的信息（可能不在本 app 页面）'**
  String get ppCollectEmpty;

  /// No description provided for @ppCollectNetworkError.
  ///
  /// In zh, this message translates to:
  /// **'上传失败，请检查网络后重试'**
  String get ppCollectNetworkError;

  /// No description provided for @ppCollectDone.
  ///
  /// In zh, this message translates to:
  /// **'已完成采集'**
  String get ppCollectDone;

  /// No description provided for @ppClearedAll.
  ///
  /// In zh, this message translates to:
  /// **'已清除全部手机感知快照'**
  String get ppClearedAll;

  /// No description provided for @ppClearFailed.
  ///
  /// In zh, this message translates to:
  /// **'清除失败，请稍后重试'**
  String get ppClearFailed;

  /// No description provided for @ppShizukuUploadFailed.
  ///
  /// In zh, this message translates to:
  /// **'{text}（上报失败）'**
  String ppShizukuUploadFailed(Object text);

  /// No description provided for @ppLocEnabledOn.
  ///
  /// In zh, this message translates to:
  /// **'位置信息已开启，AI 可感知你所在城市'**
  String get ppLocEnabledOn;

  /// No description provided for @ppLocEnabledOff.
  ///
  /// In zh, this message translates to:
  /// **'位置信息已关闭'**
  String get ppLocEnabledOff;

  /// No description provided for @ppLocGpsEnabledWith.
  ///
  /// In zh, this message translates to:
  /// **'已开启获取地理位置：{loc}'**
  String ppLocGpsEnabledWith(Object loc);

  /// No description provided for @ppLocGpsDisabled.
  ///
  /// In zh, this message translates to:
  /// **'已关闭获取地理位置'**
  String get ppLocGpsDisabled;

  /// No description provided for @ppLocServiceOff.
  ///
  /// In zh, this message translates to:
  /// **'手机定位服务未开启，请到系统设置中打开'**
  String get ppLocServiceOff;

  /// No description provided for @ppLocDeniedForever.
  ///
  /// In zh, this message translates to:
  /// **'定位权限被永久拒绝，请到系统设置中手动授权'**
  String get ppLocDeniedForever;

  /// No description provided for @ppLocNoPermission.
  ///
  /// In zh, this message translates to:
  /// **'未获得定位权限，无法获取地理位置'**
  String get ppLocNoPermission;

  /// No description provided for @ppLocFailed.
  ///
  /// In zh, this message translates to:
  /// **'定位失败：{err}。可到窗边/室外重试，或在系统定位服务里开启“提高精确度”（Wi-Fi/蓝牙扫描）'**
  String ppLocFailed(Object err);

  /// No description provided for @ppLocCityLocated.
  ///
  /// In zh, this message translates to:
  /// **'{city}（已定位，不可自定义）'**
  String ppLocCityLocated(Object city);

  /// No description provided for @ppLocCoordsLocated.
  ///
  /// In zh, this message translates to:
  /// **'{lat},{lng}（已定位）'**
  String ppLocCoordsLocated(Object lat, Object lng);

  /// No description provided for @ppLocLocating.
  ///
  /// In zh, this message translates to:
  /// **'定位中…（获取地理位置已开启）'**
  String get ppLocLocating;

  /// No description provided for @ppLocUnset.
  ///
  /// In zh, this message translates to:
  /// **'未设置，点击填写'**
  String get ppLocUnset;

  /// No description provided for @ppLocFollowUser.
  ///
  /// In zh, this message translates to:
  /// **'与用户位置相同：{loc}'**
  String ppLocFollowUser(Object loc);

  /// No description provided for @ppLocNotSet.
  ///
  /// In zh, this message translates to:
  /// **'未设置'**
  String get ppLocNotSet;

  /// No description provided for @ppLocUser.
  ///
  /// In zh, this message translates to:
  /// **'用户：{loc}'**
  String ppLocUser(Object loc);

  /// No description provided for @ppLocFollow.
  ///
  /// In zh, this message translates to:
  /// **'AI 跟随用户'**
  String get ppLocFollow;

  /// No description provided for @ppLocGpsOn.
  ///
  /// In zh, this message translates to:
  /// **'定位已开启'**
  String get ppLocGpsOn;

  /// No description provided for @ppLocUnsetExpand.
  ///
  /// In zh, this message translates to:
  /// **'未设置位置，展开配置'**
  String get ppLocUnsetExpand;

  /// No description provided for @ppLocSetUser.
  ///
  /// In zh, this message translates to:
  /// **'设置用户位置'**
  String get ppLocSetUser;

  /// No description provided for @ppLocSetAi.
  ///
  /// In zh, this message translates to:
  /// **'设置 AI 位置'**
  String get ppLocSetAi;

  /// No description provided for @ppLocHint.
  ///
  /// In zh, this message translates to:
  /// **'如：广州 / 北京 / Tokyo'**
  String get ppLocHint;

  /// No description provided for @ppSourceScreen.
  ///
  /// In zh, this message translates to:
  /// **'屏幕'**
  String get ppSourceScreen;

  /// No description provided for @ppSourceClipboard.
  ///
  /// In zh, this message translates to:
  /// **'剪贴板'**
  String get ppSourceClipboard;

  /// No description provided for @ppSourceMedia.
  ///
  /// In zh, this message translates to:
  /// **'相册'**
  String get ppSourceMedia;

  /// No description provided for @ppSourceNotification.
  ///
  /// In zh, this message translates to:
  /// **'通知'**
  String get ppSourceNotification;

  /// No description provided for @ppClipboard.
  ///
  /// In zh, this message translates to:
  /// **'剪贴板'**
  String get ppClipboard;

  /// No description provided for @ppSubtitleOn.
  ///
  /// In zh, this message translates to:
  /// **'开启后 AI 好友可在你允许时了解手机状态'**
  String get ppSubtitleOn;

  /// No description provided for @ppSubtitleOff.
  ///
  /// In zh, this message translates to:
  /// **'默认关闭，开启后请选择下方采集项'**
  String get ppSubtitleOff;

  /// No description provided for @ppGroupSources.
  ///
  /// In zh, this message translates to:
  /// **'采集项'**
  String get ppGroupSources;

  /// No description provided for @ppScreenTitle.
  ///
  /// In zh, this message translates to:
  /// **'读屏（无障碍）'**
  String get ppScreenTitle;

  /// No description provided for @ppScreenRunning.
  ///
  /// In zh, this message translates to:
  /// **'服务运行中，会缓存最近非本 app 页面文字'**
  String get ppScreenRunning;

  /// No description provided for @ppScreenOff.
  ///
  /// In zh, this message translates to:
  /// **'未开启，点击后请到系统无障碍设置中开启'**
  String get ppScreenOff;

  /// No description provided for @ppClipboardSub.
  ///
  /// In zh, this message translates to:
  /// **'读取你最近复制的内容（仅聊天时前台读取）'**
  String get ppClipboardSub;

  /// No description provided for @ppMediaTitle.
  ///
  /// In zh, this message translates to:
  /// **'相册最近图片'**
  String get ppMediaTitle;

  /// No description provided for @ppMediaSub.
  ///
  /// In zh, this message translates to:
  /// **'读取最近 8 张图片的文件名与时间（不读取图片内容）'**
  String get ppMediaSub;

  /// No description provided for @ppMediaFilesTitle.
  ///
  /// In zh, this message translates to:
  /// **'媒体文件（视频/音频/文档）'**
  String get ppMediaFilesTitle;

  /// No description provided for @ppMediaFilesSub.
  ///
  /// In zh, this message translates to:
  /// **'读取最近视频/音频/文档的文件名与时间（仅元数据；文档需“所有文件访问”权限）'**
  String get ppMediaFilesSub;

  /// No description provided for @ppUsageStatsTitle.
  ///
  /// In zh, this message translates to:
  /// **'应用使用时长'**
  String get ppUsageStatsTitle;

  /// No description provided for @ppUsageStatsGranted.
  ///
  /// In zh, this message translates to:
  /// **'最近 24h 各应用使用时长，每 30 分钟自动上报给 AI'**
  String get ppUsageStatsGranted;

  /// No description provided for @ppUsageStatsNotGranted.
  ///
  /// In zh, this message translates to:
  /// **'未授权：开启后请到系统“使用情况访问”中允许'**
  String get ppUsageStatsNotGranted;

  /// No description provided for @ppActionsTitle.
  ///
  /// In zh, this message translates to:
  /// **'模拟操作'**
  String get ppActionsTitle;

  /// No description provided for @ppActionsOn.
  ///
  /// In zh, this message translates to:
  /// **'AI 可在你确认后点击/长按/滑动/输入（仅当前屏幕节点，敏感页面拒绝，默认关）'**
  String get ppActionsOn;

  /// No description provided for @ppActionsOff.
  ///
  /// In zh, this message translates to:
  /// **'默认关闭：AI 在获得你单次确认后帮你操作手机'**
  String get ppActionsOff;

  /// No description provided for @ppWorkflowTitle.
  ///
  /// In zh, this message translates to:
  /// **'自定义工作流'**
  String get ppWorkflowTitle;

  /// No description provided for @ppWorkflowSub.
  ///
  /// In zh, this message translates to:
  /// **'自建多步操作序列，对 AI 说“帮我执行 XX”即可触发（系统级操作需 Shizuku 授权）'**
  String get ppWorkflowSub;

  /// No description provided for @ppNotificationTitle.
  ///
  /// In zh, this message translates to:
  /// **'通知读取'**
  String get ppNotificationTitle;

  /// No description provided for @ppNotifRunning.
  ///
  /// In zh, this message translates to:
  /// **'服务运行中，会缓存最近收到的 app 通知文字'**
  String get ppNotifRunning;

  /// No description provided for @ppNotifOff.
  ///
  /// In zh, this message translates to:
  /// **'未开启，点击后请到系统“通知使用权”中开启'**
  String get ppNotifOff;

  /// No description provided for @ppAutoNotifyTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 主动提及通知'**
  String get ppAutoNotifyTitle;

  /// No description provided for @ppAutoNotifySub.
  ///
  /// In zh, this message translates to:
  /// **'AI 约每 5 分钟检查一次，主动提起你手机收到的新通知（默认关）'**
  String get ppAutoNotifySub;

  /// No description provided for @ppWhitelistTitle.
  ///
  /// In zh, this message translates to:
  /// **'通知白名单'**
  String get ppWhitelistTitle;

  /// No description provided for @ppWhitelistSub.
  ///
  /// In zh, this message translates to:
  /// **'勾选后只感知指定 app 的通知（默认全部）'**
  String get ppWhitelistSub;

  /// No description provided for @ppShizukuTitle.
  ///
  /// In zh, this message translates to:
  /// **'Shizuku 权限'**
  String get ppShizukuTitle;

  /// No description provided for @ppShizukuSub.
  ///
  /// In zh, this message translates to:
  /// **'系统级能力（应用列表/系统设置/模拟操作前置）：状态、授权与 Shell 测试'**
  String get ppShizukuSub;

  /// No description provided for @ppShizukuServer.
  ///
  /// In zh, this message translates to:
  /// **'Shizuku 服务'**
  String get ppShizukuServer;

  /// No description provided for @ppShizukuGranted.
  ///
  /// In zh, this message translates to:
  /// **'本应用授权'**
  String get ppShizukuGranted;

  /// No description provided for @ppReady.
  ///
  /// In zh, this message translates to:
  /// **'已就绪'**
  String get ppReady;

  /// No description provided for @ppNotReady.
  ///
  /// In zh, this message translates to:
  /// **'未就绪'**
  String get ppNotReady;

  /// No description provided for @ppCollecting.
  ///
  /// In zh, this message translates to:
  /// **'采集中…'**
  String get ppCollecting;

  /// No description provided for @ppCollectShizuku.
  ///
  /// In zh, this message translates to:
  /// **'采集系统状态并告诉 AI'**
  String get ppCollectShizuku;

  /// No description provided for @ppGroupLocation.
  ///
  /// In zh, this message translates to:
  /// **'位置'**
  String get ppGroupLocation;

  /// No description provided for @ppLocationTitle.
  ///
  /// In zh, this message translates to:
  /// **'位置信息'**
  String get ppLocationTitle;

  /// No description provided for @ppLocSubtitleOn.
  ///
  /// In zh, this message translates to:
  /// **'AI 可感知你所在城市，提供更自然的时间感知'**
  String get ppLocSubtitleOn;

  /// No description provided for @ppLocSubtitleOff.
  ///
  /// In zh, this message translates to:
  /// **'默认关闭：开启后 AI 才知道你在哪里'**
  String get ppLocSubtitleOff;

  /// No description provided for @ppLocGpsTitle.
  ///
  /// In zh, this message translates to:
  /// **'获取地理位置'**
  String get ppLocGpsTitle;

  /// No description provided for @ppLocGpsOnSub.
  ///
  /// In zh, this message translates to:
  /// **'已开启：用户位置由定位获取，不可自定义'**
  String get ppLocGpsOnSub;

  /// No description provided for @ppLocGpsOffSub.
  ///
  /// In zh, this message translates to:
  /// **'开启后自动获取你所在位置'**
  String get ppLocGpsOffSub;

  /// No description provided for @ppLocUserTitle.
  ///
  /// In zh, this message translates to:
  /// **'用户位置'**
  String get ppLocUserTitle;

  /// No description provided for @ppLocAiTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 位置'**
  String get ppLocAiTitle;

  /// No description provided for @ppLocFollowTitle.
  ///
  /// In zh, this message translates to:
  /// **'位置跟随'**
  String get ppLocFollowTitle;

  /// No description provided for @ppLocFollowOnSub.
  ///
  /// In zh, this message translates to:
  /// **'AI 位置与用户相同，不可自定义'**
  String get ppLocFollowOnSub;

  /// No description provided for @ppLocFollowOffSub.
  ///
  /// In zh, this message translates to:
  /// **'开启后 AI 位置跟随用户位置'**
  String get ppLocFollowOffSub;

  /// No description provided for @ppGroupPrivacy.
  ///
  /// In zh, this message translates to:
  /// **'隐私说明'**
  String get ppGroupPrivacy;

  /// No description provided for @ppPrivacyNote.
  ///
  /// In zh, this message translates to:
  /// **'· 全部能力默认关闭，逐项授权，关闭立即生效\n· 仅读取文本与图片元数据；图片如需理解，走本地 OCR/VLM 转文字，绝不上传云端模型\n· 密码框、银行支付类页面自动跳过\n· 数据只发送到你自己的服务器，快照 30 分钟过期、最多保留 20 条'**
  String get ppPrivacyNote;

  /// No description provided for @ppGroupActions.
  ///
  /// In zh, this message translates to:
  /// **'操作与记录'**
  String get ppGroupActions;

  /// No description provided for @ppCollectNowTitle.
  ///
  /// In zh, this message translates to:
  /// **'立即采集一次'**
  String get ppCollectNowTitle;

  /// No description provided for @ppCollectNowSub.
  ///
  /// In zh, this message translates to:
  /// **'采集当前屏幕/剪贴板/相册并告诉 AI'**
  String get ppCollectNowSub;

  /// No description provided for @ppHistoryTitle.
  ///
  /// In zh, this message translates to:
  /// **'历史记录'**
  String get ppHistoryTitle;

  /// No description provided for @ppNoSnapshots.
  ///
  /// In zh, this message translates to:
  /// **'暂无快照'**
  String get ppNoSnapshots;

  /// No description provided for @ppRecentCount.
  ///
  /// In zh, this message translates to:
  /// **'最近 {count} 条'**
  String ppRecentCount(Object count);

  /// No description provided for @ppLockContentName.
  ///
  /// In zh, this message translates to:
  /// **'手机'**
  String get ppLockContentName;

  /// No description provided for @ppClearAll.
  ///
  /// In zh, this message translates to:
  /// **'清除全部快照'**
  String get ppClearAll;

  /// No description provided for @ppLocAi.
  ///
  /// In zh, this message translates to:
  /// **'AI：{loc}'**
  String ppLocAi(Object loc);

  /// No description provided for @providerLocalHint.
  ///
  /// In zh, this message translates to:
  /// **'openai / dashscope / 本地'**
  String get providerLocalHint;

  /// No description provided for @worldGroup.
  ///
  /// In zh, this message translates to:
  /// **'角色设定'**
  String get worldGroup;

  /// No description provided for @lorebookTitle.
  ///
  /// In zh, this message translates to:
  /// **'设定条目（Lorebook）'**
  String get lorebookTitle;

  /// No description provided for @lorebookHint.
  ///
  /// In zh, this message translates to:
  /// **'关键词触发注入的既定设定，对话提到即生效'**
  String get lorebookHint;

  /// No description provided for @lorebookAdd.
  ///
  /// In zh, this message translates to:
  /// **'新增条目'**
  String get lorebookAdd;

  /// No description provided for @lorebookEdit.
  ///
  /// In zh, this message translates to:
  /// **'编辑条目'**
  String get lorebookEdit;

  /// No description provided for @lorebookTitleField.
  ///
  /// In zh, this message translates to:
  /// **'标题'**
  String get lorebookTitleField;

  /// No description provided for @lorebookContentField.
  ///
  /// In zh, this message translates to:
  /// **'内容'**
  String get lorebookContentField;

  /// No description provided for @lorebookKeywords.
  ///
  /// In zh, this message translates to:
  /// **'关键词（≥2 字，逗号分隔）'**
  String get lorebookKeywords;

  /// No description provided for @lorebookKeywordsHint.
  ///
  /// In zh, this message translates to:
  /// **'对话出现任一关键词即注入，如：我养的猫'**
  String get lorebookKeywordsHint;

  /// No description provided for @lorebookExclude.
  ///
  /// In zh, this message translates to:
  /// **'排除词（出现则不触发）'**
  String get lorebookExclude;

  /// No description provided for @lorebookExcludeHint.
  ///
  /// In zh, this message translates to:
  /// **'防止误触发，如：猫屎咖啡'**
  String get lorebookExcludeHint;

  /// No description provided for @lorebookEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无设定条目'**
  String get lorebookEmpty;

  /// No description provided for @lorebookActive.
  ///
  /// In zh, this message translates to:
  /// **'启用'**
  String get lorebookActive;

  /// No description provided for @lorebookStyleHint.
  ///
  /// In zh, this message translates to:
  /// **'用第三人称简洁描述设定内容（如：用户养了一只叫团团的橘猫）'**
  String get lorebookStyleHint;

  /// No description provided for @lorebookRegex.
  ///
  /// In zh, this message translates to:
  /// **'正则匹配'**
  String get lorebookRegex;

  /// No description provided for @lorebookRegexHint.
  ///
  /// In zh, this message translates to:
  /// **'把关键词当作正则表达式匹配（默认关闭，按子串匹配）'**
  String get lorebookRegexHint;

  /// No description provided for @lorebookProbability.
  ///
  /// In zh, this message translates to:
  /// **'触发概率'**
  String get lorebookProbability;

  /// No description provided for @lorebookProbabilityHint.
  ///
  /// In zh, this message translates to:
  /// **'关键词命中时注入的概率（100=必注入，0=不注入）'**
  String get lorebookProbabilityHint;

  /// No description provided for @lorebookGroup.
  ///
  /// In zh, this message translates to:
  /// **'包含组'**
  String get lorebookGroup;

  /// No description provided for @lorebookGroupHint.
  ///
  /// In zh, this message translates to:
  /// **'同一组的条目同轮只注入一条（留空=不分组）'**
  String get lorebookGroupHint;

  /// No description provided for @lorebookSticky.
  ///
  /// In zh, this message translates to:
  /// **'粘性轮数'**
  String get lorebookSticky;

  /// No description provided for @lorebookStickyHint.
  ///
  /// In zh, this message translates to:
  /// **'触发后持续注入的轮数（0=不持续）'**
  String get lorebookStickyHint;

  /// No description provided for @lorebookCooldown.
  ///
  /// In zh, this message translates to:
  /// **'冷却轮数'**
  String get lorebookCooldown;

  /// No description provided for @lorebookCooldownHint.
  ///
  /// In zh, this message translates to:
  /// **'触发后 N 轮内不再注入（0=关闭）'**
  String get lorebookCooldownHint;

  /// No description provided for @worldFactsTitle.
  ///
  /// In zh, this message translates to:
  /// **'世界设定'**
  String get worldFactsTitle;

  /// No description provided for @worldFactsHint.
  ///
  /// In zh, this message translates to:
  /// **'你定义的不可动摇事实，AI 推断不能覆盖'**
  String get worldFactsHint;

  /// No description provided for @worldFactsEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无世界设定'**
  String get worldFactsEmpty;

  /// No description provided for @worldFactAdd.
  ///
  /// In zh, this message translates to:
  /// **'添加设定'**
  String get worldFactAdd;

  /// No description provided for @worldFactContentHint.
  ///
  /// In zh, this message translates to:
  /// **'如：我住在杭州 / 我养了一只叫团团的猫'**
  String get worldFactContentHint;

  /// No description provided for @cancel.
  ///
  /// In zh, this message translates to:
  /// **'取消'**
  String get cancel;

  /// No description provided for @cancelled.
  ///
  /// In zh, this message translates to:
  /// **'已取消'**
  String get cancelled;

  /// No description provided for @changeFailed.
  ///
  /// In zh, this message translates to:
  /// **'修改失败'**
  String get changeFailed;

  /// No description provided for @changePassword.
  ///
  /// In zh, this message translates to:
  /// **'修改密码'**
  String get changePassword;

  /// No description provided for @changePasswordHint.
  ///
  /// In zh, this message translates to:
  /// **'长度≥8，需同时包含字母和数字'**
  String get changePasswordHint;

  /// No description provided for @charLife.
  ///
  /// In zh, this message translates to:
  /// **'角色生活'**
  String get charLife;

  /// No description provided for @charPetTitle.
  ///
  /// In zh, this message translates to:
  /// **'{char} 的 {pet}'**
  String charPetTitle(Object char, Object pet);

  /// No description provided for @charSettings.
  ///
  /// In zh, this message translates to:
  /// **'角色设置'**
  String get charSettings;

  /// No description provided for @chatArchive.
  ///
  /// In zh, this message translates to:
  /// **'聊天记录箱'**
  String get chatArchive;

  /// No description provided for @chatArchiveHint.
  ///
  /// In zh, this message translates to:
  /// **'历史聊天记录'**
  String get chatArchiveHint;

  /// No description provided for @chatOf.
  ///
  /// In zh, this message translates to:
  /// **'{name}的畅聊'**
  String chatOf(Object name);

  /// No description provided for @chatStyle.
  ///
  /// In zh, this message translates to:
  /// **'聊天风格'**
  String get chatStyle;

  /// No description provided for @checkIn.
  ///
  /// In zh, this message translates to:
  /// **'查岗'**
  String get checkIn;

  /// No description provided for @checkInHint.
  ///
  /// In zh, this message translates to:
  /// **'开启后 AI 主动找你时，能自然知道你正在用什么软件'**
  String get checkInHint;

  /// No description provided for @checking.
  ///
  /// In zh, this message translates to:
  /// **'正在检测...'**
  String get checking;

  /// No description provided for @chooseFriendFirst.
  ///
  /// In zh, this message translates to:
  /// **'请先在好友页面选择一位AI好友'**
  String get chooseFriendFirst;

  /// No description provided for @chooseSpecies.
  ///
  /// In zh, this message translates to:
  /// **'选择种类'**
  String get chooseSpecies;

  /// No description provided for @clean.
  ///
  /// In zh, this message translates to:
  /// **'清洁'**
  String get clean;

  /// No description provided for @cleanliness.
  ///
  /// In zh, this message translates to:
  /// **'清洁度'**
  String get cleanliness;

  /// No description provided for @close.
  ///
  /// In zh, this message translates to:
  /// **'关闭'**
  String get close;

  /// No description provided for @cognitiveLoop.
  ///
  /// In zh, this message translates to:
  /// **'认知循环'**
  String get cognitiveLoop;

  /// No description provided for @cognitiveLoopHint.
  ///
  /// In zh, this message translates to:
  /// **'开启后 AI 会带入当前状态、进行中话题与关系温度，对话与主动消息更懂你（默认关闭）'**
  String get cognitiveLoopHint;

  /// No description provided for @coldWar.
  ///
  /// In zh, this message translates to:
  /// **'冷战断联'**
  String get coldWar;

  /// No description provided for @coldWarHint.
  ///
  /// In zh, this message translates to:
  /// **'生气冷战期不回复消息，直到你哄好TA'**
  String get coldWarHint;

  /// No description provided for @collapse.
  ///
  /// In zh, this message translates to:
  /// **'收起'**
  String get collapse;

  /// No description provided for @comingSoon.
  ///
  /// In zh, this message translates to:
  /// **'即将上线'**
  String get comingSoon;

  /// No description provided for @comingSoonTemplate.
  ///
  /// In zh, this message translates to:
  /// **'{feature} 功能即将上线'**
  String comingSoonTemplate(Object feature);

  /// No description provided for @commentHint.
  ///
  /// In zh, this message translates to:
  /// **'输入评论...'**
  String get commentHint;

  /// No description provided for @completed.
  ///
  /// In zh, this message translates to:
  /// **'已完成'**
  String get completed;

  /// No description provided for @confirm.
  ///
  /// In zh, this message translates to:
  /// **'确认'**
  String get confirm;

  /// No description provided for @connectFail.
  ///
  /// In zh, this message translates to:
  /// **'连接失败，请检查地址'**
  String get connectFail;

  /// No description provided for @connectFailed.
  ///
  /// In zh, this message translates to:
  /// **'连接失败'**
  String get connectFailed;

  /// No description provided for @connectSuccess.
  ///
  /// In zh, this message translates to:
  /// **'连接成功'**
  String get connectSuccess;

  /// No description provided for @connected.
  ///
  /// In zh, this message translates to:
  /// **'已连接'**
  String get connected;

  /// No description provided for @connectionStatus.
  ///
  /// In zh, this message translates to:
  /// **'连接状态'**
  String get connectionStatus;

  /// No description provided for @content.
  ///
  /// In zh, this message translates to:
  /// **'内容'**
  String get content;

  /// No description provided for @contentAiHint.
  ///
  /// In zh, this message translates to:
  /// **'内容（AI 好友会在聊天中读到）'**
  String get contentAiHint;

  /// No description provided for @contentRequired.
  ///
  /// In zh, this message translates to:
  /// **'内容不能为空'**
  String get contentRequired;

  /// No description provided for @control.
  ///
  /// In zh, this message translates to:
  /// **'管制'**
  String get control;

  /// No description provided for @controlComingSoon.
  ///
  /// In zh, this message translates to:
  /// **'「管制」功能待设计，敬请期待'**
  String get controlComingSoon;

  /// No description provided for @controlHint.
  ///
  /// In zh, this message translates to:
  /// **'查岗的子功能（待设计中）'**
  String get controlHint;

  /// No description provided for @copied.
  ///
  /// In zh, this message translates to:
  /// **'已复制'**
  String get copied;

  /// No description provided for @copy.
  ///
  /// In zh, this message translates to:
  /// **'复制'**
  String get copy;

  /// No description provided for @create.
  ///
  /// In zh, this message translates to:
  /// **'创建'**
  String get create;

  /// No description provided for @createGroup.
  ///
  /// In zh, this message translates to:
  /// **'创建群聊'**
  String get createGroup;

  /// No description provided for @createGroupDialog.
  ///
  /// In zh, this message translates to:
  /// **'创建家庭群聊'**
  String get createGroupDialog;

  /// No description provided for @createRoleHint.
  ///
  /// In zh, this message translates to:
  /// **'先去创建一个 AI 角色吧\n每个角色都会有一台自己的小手机\n多角色家庭群聊开发中，敬请期待'**
  String get createRoleHint;

  /// No description provided for @creationGroup.
  ///
  /// In zh, this message translates to:
  /// **'创作'**
  String get creationGroup;

  /// No description provided for @currentPreview.
  ///
  /// In zh, this message translates to:
  /// **'当前：{mode} · {color}'**
  String currentPreview(Object mode, Object color);

  /// No description provided for @currentUser.
  ///
  /// In zh, this message translates to:
  /// **'当前用户'**
  String get currentUser;

  /// No description provided for @dailyGroup.
  ///
  /// In zh, this message translates to:
  /// **'日常'**
  String get dailyGroup;

  /// No description provided for @dark.
  ///
  /// In zh, this message translates to:
  /// **'深色'**
  String get dark;

  /// No description provided for @date.
  ///
  /// In zh, this message translates to:
  /// **'日期'**
  String get date;

  /// No description provided for @dateArchive.
  ///
  /// In zh, this message translates to:
  /// **'日期归档'**
  String get dateArchive;

  /// No description provided for @dateFormatHint.
  ///
  /// In zh, this message translates to:
  /// **'日期格式应为 YYYY-MM-DD'**
  String get dateFormatHint;

  /// No description provided for @dateFull.
  ///
  /// In zh, this message translates to:
  /// **'{year}年{month}月{day}日'**
  String dateFull(Object year, Object month, Object day);

  /// No description provided for @dateLinePattern.
  ///
  /// In zh, this message translates to:
  /// **'M月d日 EEEE'**
  String get dateLinePattern;

  /// No description provided for @dateMonthDay.
  ///
  /// In zh, this message translates to:
  /// **'{month}月{day}日'**
  String dateMonthDay(Object month, Object day);

  /// No description provided for @dateNotes.
  ///
  /// In zh, this message translates to:
  /// **'{date} 的备注'**
  String dateNotes(Object date);

  /// No description provided for @dayLabel.
  ///
  /// In zh, this message translates to:
  /// **'{m}月{d}日'**
  String dayLabel(Object m, Object d);

  /// No description provided for @daysCount.
  ///
  /// In zh, this message translates to:
  /// **'{n} 天'**
  String daysCount(Object n);

  /// No description provided for @daysKnown.
  ///
  /// In zh, this message translates to:
  /// **'认识 {name} 第 {days} 天'**
  String daysKnown(Object name, Object days);

  /// No description provided for @deepThinking.
  ///
  /// In zh, this message translates to:
  /// **'深度思考'**
  String get deepThinking;

  /// No description provided for @deleteCountdown.
  ///
  /// In zh, this message translates to:
  /// **'删除倒计时 · 3 天内自动清除'**
  String get deleteCountdown;

  /// No description provided for @deleteDiaryConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定删除 {date} 的日记？'**
  String deleteDiaryConfirm(Object date);

  /// No description provided for @deleteDiaryTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除日记'**
  String get deleteDiaryTitle;

  /// No description provided for @deleteEmojiConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定删除这个表情吗？'**
  String get deleteEmojiConfirm;

  /// No description provided for @deleteEmojiTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除表情'**
  String get deleteEmojiTitle;

  /// No description provided for @deleteFailed.
  ///
  /// In zh, this message translates to:
  /// **'删除失败，请重试'**
  String get deleteFailed;

  /// No description provided for @deleteFailedErr.
  ///
  /// In zh, this message translates to:
  /// **'删除失败: {err}'**
  String deleteFailedErr(Object err);

  /// No description provided for @deleteGroup.
  ///
  /// In zh, this message translates to:
  /// **'删除群聊'**
  String get deleteGroup;

  /// No description provided for @deleteGroupConfirm.
  ///
  /// In zh, this message translates to:
  /// **'将删除群及全部消息，确定吗？'**
  String get deleteGroupConfirm;

  /// No description provided for @deleteInDays.
  ///
  /// In zh, this message translates to:
  /// **'剩余 {days} 天后删除'**
  String deleteInDays(Object days);

  /// No description provided for @deleteInHours.
  ///
  /// In zh, this message translates to:
  /// **'剩余 {hours} 小时后删除'**
  String deleteInHours(Object hours);

  /// No description provided for @deleteMemoConfirm.
  ///
  /// In zh, this message translates to:
  /// **'删除后 AI 好友将不再看到这条备忘录，确定删除？'**
  String get deleteMemoConfirm;

  /// No description provided for @deleteMemoTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除备忘录'**
  String get deleteMemoTitle;

  /// No description provided for @deleteMemoryConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定要删除这条记忆吗？删除后不可恢复。'**
  String get deleteMemoryConfirm;

  /// No description provided for @deleteMemoryTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除记忆'**
  String get deleteMemoryTitle;

  /// No description provided for @deleteMessageConfirm.
  ///
  /// In zh, this message translates to:
  /// **'删除后无法恢复，确定删除这条消息吗？'**
  String get deleteMessageConfirm;

  /// No description provided for @deleteMessageTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除消息'**
  String get deleteMessageTitle;

  /// No description provided for @deleteMoment.
  ///
  /// In zh, this message translates to:
  /// **'删除动态'**
  String get deleteMoment;

  /// No description provided for @deleteMomentConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定删除这条朋友圈吗？它的评论和点赞将一并删除。'**
  String get deleteMomentConfirm;

  /// No description provided for @deletePhoto.
  ///
  /// In zh, this message translates to:
  /// **'删除照片'**
  String get deletePhoto;

  /// No description provided for @deletePhotoConfirm.
  ///
  /// In zh, this message translates to:
  /// **'删除后无法恢复，确定删除这张照片吗？'**
  String get deletePhotoConfirm;

  /// No description provided for @deleteSoon.
  ///
  /// In zh, this message translates to:
  /// **'即将删除'**
  String get deleteSoon;

  /// No description provided for @deleteTimerTooltip.
  ///
  /// In zh, this message translates to:
  /// **'删除这个计时'**
  String get deleteTimerTooltip;

  /// No description provided for @deleted.
  ///
  /// In zh, this message translates to:
  /// **'已删除'**
  String get deleted;

  /// No description provided for @deny.
  ///
  /// In zh, this message translates to:
  /// **'拒绝'**
  String get deny;

  /// No description provided for @detailTitle.
  ///
  /// In zh, this message translates to:
  /// **'详情'**
  String get detailTitle;

  /// No description provided for @diary.
  ///
  /// In zh, this message translates to:
  /// **'日记'**
  String get diary;

  /// No description provided for @diaryNoEntry.
  ///
  /// In zh, this message translates to:
  /// **'这天没有写日记'**
  String get diaryNoEntry;

  /// No description provided for @diaryCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 篇'**
  String diaryCount(Object count);

  /// No description provided for @diaryHint.
  ///
  /// In zh, this message translates to:
  /// **'TA每天写的日记'**
  String get diaryHint;

  /// No description provided for @diaryTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name}的日记'**
  String diaryTitle(Object name);

  /// No description provided for @disconnected.
  ///
  /// In zh, this message translates to:
  /// **'未连接'**
  String get disconnected;

  /// No description provided for @dnd.
  ///
  /// In zh, this message translates to:
  /// **'免打扰'**
  String get dnd;

  /// No description provided for @dndHint.
  ///
  /// In zh, this message translates to:
  /// **'设置免打扰时段'**
  String get dndHint;

  /// No description provided for @dndOff.
  ///
  /// In zh, this message translates to:
  /// **'关闭时沿用默认（凌晨 0-7 点静默）'**
  String get dndOff;

  /// No description provided for @dndOn.
  ///
  /// In zh, this message translates to:
  /// **'{start} - {end} 不发送主动消息'**
  String dndOn(Object start, Object end);

  /// No description provided for @dndPeriod.
  ///
  /// In zh, this message translates to:
  /// **'免打扰时段'**
  String get dndPeriod;

  /// No description provided for @doSomething.
  ///
  /// In zh, this message translates to:
  /// **'去做某事'**
  String get doSomething;

  /// No description provided for @done.
  ///
  /// In zh, this message translates to:
  /// **'完成'**
  String get done;

  /// No description provided for @download.
  ///
  /// In zh, this message translates to:
  /// **'下载'**
  String get download;

  /// No description provided for @downloadPack.
  ///
  /// In zh, this message translates to:
  /// **'下载表情包'**
  String get downloadPack;

  /// No description provided for @dragEditHint.
  ///
  /// In zh, this message translates to:
  /// **'拖动图标换位 · 点 ✕ 删除'**
  String get dragEditHint;

  /// No description provided for @durationMin.
  ///
  /// In zh, this message translates to:
  /// **'{min} 分钟'**
  String durationMin(Object min);

  /// No description provided for @durationSec.
  ///
  /// In zh, this message translates to:
  /// **'{sec} 秒'**
  String durationSec(Object sec);

  /// No description provided for @edit.
  ///
  /// In zh, this message translates to:
  /// **'编辑'**
  String get edit;

  /// No description provided for @editDiary.
  ///
  /// In zh, this message translates to:
  /// **'编辑日记'**
  String get editDiary;

  /// No description provided for @editDiscardConfirm.
  ///
  /// In zh, this message translates to:
  /// **'内容尚未发布，确定放弃编辑吗？'**
  String get editDiscardConfirm;

  /// No description provided for @editMemo.
  ///
  /// In zh, this message translates to:
  /// **'编辑备忘录'**
  String get editMemo;

  /// No description provided for @emojiAdded.
  ///
  /// In zh, this message translates to:
  /// **'表情已添加'**
  String get emojiAdded;

  /// No description provided for @emojiPack.
  ///
  /// In zh, this message translates to:
  /// **'表情包'**
  String get emojiPack;

  /// No description provided for @emotionAll.
  ///
  /// In zh, this message translates to:
  /// **'全部'**
  String get emotionAll;

  /// No description provided for @emotionFilter.
  ///
  /// In zh, this message translates to:
  /// **'情绪'**
  String get emotionFilter;

  /// No description provided for @emotionMemoryTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name} · 情绪记忆'**
  String emotionMemoryTitle(Object name);

  /// No description provided for @end.
  ///
  /// In zh, this message translates to:
  /// **'结束'**
  String get end;

  /// No description provided for @energy.
  ///
  /// In zh, this message translates to:
  /// **'精力'**
  String get energy;

  /// No description provided for @english.
  ///
  /// In zh, this message translates to:
  /// **'English'**
  String get english;

  /// No description provided for @eventClockEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无进行中的计时'**
  String get eventClockEmpty;

  /// No description provided for @eventClockHint.
  ///
  /// In zh, this message translates to:
  /// **'进行中的计时（到点会提醒）'**
  String get eventClockHint;

  /// No description provided for @eventClockTitle.
  ///
  /// In zh, this message translates to:
  /// **'事件时钟'**
  String get eventClockTitle;

  /// No description provided for @expand.
  ///
  /// In zh, this message translates to:
  /// **'展开'**
  String get expand;

  /// No description provided for @extensions.
  ///
  /// In zh, this message translates to:
  /// **'扩展'**
  String get extensions;

  /// No description provided for @extensionsHint.
  ///
  /// In zh, this message translates to:
  /// **'服务器端插件（Hook 扩展 AI 能力）'**
  String get extensionsHint;

  /// No description provided for @feed.
  ///
  /// In zh, this message translates to:
  /// **'喂食'**
  String get feed;

  /// No description provided for @file.
  ///
  /// In zh, this message translates to:
  /// **'文件'**
  String get file;

  /// No description provided for @fileTooLarge.
  ///
  /// In zh, this message translates to:
  /// **'文件不能超过 20MB'**
  String get fileTooLarge;

  /// No description provided for @followSystem.
  ///
  /// In zh, this message translates to:
  /// **'跟随系统'**
  String get followSystem;

  /// No description provided for @fontIconFuture.
  ///
  /// In zh, this message translates to:
  /// **'字体 / 图标（未来开放）'**
  String get fontIconFuture;

  /// No description provided for @fontIconHint.
  ///
  /// In zh, this message translates to:
  /// **'目前提供壁纸更换；字体与图标美化后续版本开放。'**
  String get fontIconHint;

  /// No description provided for @furnitureInactive.
  ///
  /// In zh, this message translates to:
  /// **'这个家具暂时不能互动'**
  String get furnitureInactive;

  /// No description provided for @goal.
  ///
  /// In zh, this message translates to:
  /// **'目标'**
  String get goal;

  /// No description provided for @goalActive.
  ///
  /// In zh, this message translates to:
  /// **'进行中'**
  String get goalActive;

  /// No description provided for @goalCompleted.
  ///
  /// In zh, this message translates to:
  /// **'已完成'**
  String get goalCompleted;

  /// No description provided for @goalFailed.
  ///
  /// In zh, this message translates to:
  /// **'未完成'**
  String get goalFailed;

  /// No description provided for @goalTypeCreative.
  ///
  /// In zh, this message translates to:
  /// **'创造'**
  String get goalTypeCreative;

  /// No description provided for @goalTypeExplore.
  ///
  /// In zh, this message translates to:
  /// **'探索'**
  String get goalTypeExplore;

  /// No description provided for @goalTypeGrowth.
  ///
  /// In zh, this message translates to:
  /// **'成长'**
  String get goalTypeGrowth;

  /// No description provided for @goalTypeRelationship.
  ///
  /// In zh, this message translates to:
  /// **'关系'**
  String get goalTypeRelationship;

  /// No description provided for @goalTypeSkill.
  ///
  /// In zh, this message translates to:
  /// **'技能'**
  String get goalTypeSkill;

  /// No description provided for @groupAddFail.
  ///
  /// In zh, this message translates to:
  /// **'添加失败'**
  String get groupAddFail;

  /// No description provided for @groupChatEmpty.
  ///
  /// In zh, this message translates to:
  /// **'群聊还没有消息，说点什么吧'**
  String get groupChatEmpty;

  /// No description provided for @groupInputHint.
  ///
  /// In zh, this message translates to:
  /// **'对群里的角色们说点什么…'**
  String get groupInputHint;

  /// No description provided for @groupMemberEmpty.
  ///
  /// In zh, this message translates to:
  /// **'群成员为空'**
  String get groupMemberEmpty;

  /// No description provided for @groupMembers.
  ///
  /// In zh, this message translates to:
  /// **'群成员'**
  String get groupMembers;

  /// No description provided for @groupNameLabel.
  ///
  /// In zh, this message translates to:
  /// **'群名称'**
  String get groupNameLabel;

  /// No description provided for @groupMuteFail.
  ///
  /// In zh, this message translates to:
  /// **'操作失败'**
  String get groupMuteFail;

  /// No description provided for @groupRemoveFail.
  ///
  /// In zh, this message translates to:
  /// **'移除失败'**
  String get groupRemoveFail;

  /// No description provided for @mute.
  ///
  /// In zh, this message translates to:
  /// **'静音'**
  String get mute;

  /// No description provided for @unmute.
  ///
  /// In zh, this message translates to:
  /// **'取消静音'**
  String get unmute;

  /// No description provided for @groupReplying.
  ///
  /// In zh, this message translates to:
  /// **'角色们正在回复…'**
  String get groupReplying;

  /// No description provided for @groupTitle.
  ///
  /// In zh, this message translates to:
  /// **'家庭群聊'**
  String get groupTitle;

  /// No description provided for @height.
  ///
  /// In zh, this message translates to:
  /// **'身高'**
  String get height;

  /// No description provided for @high.
  ///
  /// In zh, this message translates to:
  /// **'高'**
  String get high;

  /// No description provided for @highFreq.
  ///
  /// In zh, this message translates to:
  /// **'高频'**
  String get highFreq;

  /// No description provided for @holdToTalk.
  ///
  /// In zh, this message translates to:
  /// **'按住下方按钮说话'**
  String get holdToTalk;

  /// No description provided for @homeTitle.
  ///
  /// In zh, this message translates to:
  /// **'小家'**
  String get homeTitle;

  /// No description provided for @homeTitleMine.
  ///
  /// In zh, this message translates to:
  /// **'{nickname}的小家'**
  String homeTitleMine(Object nickname);

  /// No description provided for @homeTitleWithLover.
  ///
  /// In zh, this message translates to:
  /// **'{nickname}与{lover}的小家'**
  String homeTitleWithLover(Object lover, Object nickname);

  /// No description provided for @homeLayoutDragHint.
  ///
  /// In zh, this message translates to:
  /// **'长按家具可拖动摆放'**
  String get homeLayoutDragHint;

  /// No description provided for @homeLayoutSaveFailed.
  ///
  /// In zh, this message translates to:
  /// **'布局保存失败，已还原'**
  String get homeLayoutSaveFailed;

  /// No description provided for @homeLayoutSaved.
  ///
  /// In zh, this message translates to:
  /// **'布局已保存'**
  String get homeLayoutSaved;

  /// No description provided for @homeWorldMap.
  ///
  /// In zh, this message translates to:
  /// **'小家地图'**
  String get homeWorldMap;

  /// No description provided for @homeExit.
  ///
  /// In zh, this message translates to:
  /// **'出口'**
  String get homeExit;

  /// No description provided for @homeGoOut.
  ///
  /// In zh, this message translates to:
  /// **'出门'**
  String get homeGoOut;

  /// No description provided for @furnitureEdit.
  ///
  /// In zh, this message translates to:
  /// **'家具编辑'**
  String get furnitureEdit;

  /// No description provided for @furnitureEditHint.
  ///
  /// In zh, this message translates to:
  /// **'拖动或点选家具进行编辑'**
  String get furnitureEditHint;

  /// No description provided for @furnitureRevert.
  ///
  /// In zh, this message translates to:
  /// **'回退'**
  String get furnitureRevert;

  /// No description provided for @furnitureRotate.
  ///
  /// In zh, this message translates to:
  /// **'旋转'**
  String get furnitureRotate;

  /// No description provided for @furnitureConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定'**
  String get furnitureConfirm;

  /// No description provided for @hunger.
  ///
  /// In zh, this message translates to:
  /// **'饱食度'**
  String get hunger;

  /// No description provided for @image.
  ///
  /// In zh, this message translates to:
  /// **'图片'**
  String get image;

  /// No description provided for @imageGen.
  ///
  /// In zh, this message translates to:
  /// **'生图'**
  String get imageGen;

  /// No description provided for @imageGenHint.
  ///
  /// In zh, this message translates to:
  /// **'允许AI生成图片并发送给你（需服务器已配置生图服务）'**
  String get imageGenHint;

  /// No description provided for @imageSelected.
  ///
  /// In zh, this message translates to:
  /// **'已选择 1 张图片'**
  String get imageSelected;

  /// No description provided for @importanceHigh.
  ///
  /// In zh, this message translates to:
  /// **'很重要'**
  String get importanceHigh;

  /// No description provided for @importanceLow.
  ///
  /// In zh, this message translates to:
  /// **'一般'**
  String get importanceLow;

  /// No description provided for @importanceMax.
  ///
  /// In zh, this message translates to:
  /// **'极其重要'**
  String get importanceMax;

  /// No description provided for @importanceMedium.
  ///
  /// In zh, this message translates to:
  /// **'重要'**
  String get importanceMedium;

  /// No description provided for @importanceTitle.
  ///
  /// In zh, this message translates to:
  /// **'重要性'**
  String get importanceTitle;

  /// No description provided for @importanceVeryHigh.
  ///
  /// In zh, this message translates to:
  /// **'非常重要'**
  String get importanceVeryHigh;

  /// No description provided for @inProgress.
  ///
  /// In zh, this message translates to:
  /// **'进行中'**
  String get inProgress;

  /// No description provided for @inputHint.
  ///
  /// In zh, this message translates to:
  /// **'输入消息...'**
  String get inputHint;

  /// No description provided for @inputHintBatch.
  ///
  /// In zh, this message translates to:
  /// **'连续发送中，输入并收集消息...'**
  String get inputHintBatch;

  /// No description provided for @installed.
  ///
  /// In zh, this message translates to:
  /// **'已安装'**
  String get installed;

  /// No description provided for @interact.
  ///
  /// In zh, this message translates to:
  /// **'互动'**
  String get interact;

  /// No description provided for @interactFailed.
  ///
  /// In zh, this message translates to:
  /// **'互动失败，请稍后重试'**
  String get interactFailed;

  /// No description provided for @interactHintBase.
  ///
  /// In zh, this message translates to:
  /// **'点击宠物玩耍 · 点击食物喂食'**
  String get interactHintBase;

  /// No description provided for @interactHintClean.
  ///
  /// In zh, this message translates to:
  /// **' · 点击💩清洁'**
  String get interactHintClean;

  /// No description provided for @interests.
  ///
  /// In zh, this message translates to:
  /// **'兴趣'**
  String get interests;

  /// No description provided for @interestsGoalsTab.
  ///
  /// In zh, this message translates to:
  /// **'兴趣与目标'**
  String get interestsGoalsTab;

  /// No description provided for @interestsGoalsTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name}的兴趣与目标'**
  String interestsGoalsTitle(Object name);

  /// No description provided for @journeyDesc.
  ///
  /// In zh, this message translates to:
  /// **'从第一句“你好”到现在，我们一起经历的事'**
  String get journeyDesc;

  /// No description provided for @language.
  ///
  /// In zh, this message translates to:
  /// **'语言'**
  String get language;

  /// No description provided for @lifeHomeTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name} · AI 生活'**
  String lifeHomeTitle(Object name);

  /// No description provided for @lifeIntensity.
  ///
  /// In zh, this message translates to:
  /// **'离线生活强度'**
  String get lifeIntensity;

  /// No description provided for @lifeIntensityHint.
  ///
  /// In zh, this message translates to:
  /// **'越高角色生活越活跃（tick 更频繁、token 消耗更高）'**
  String get lifeIntensityHint;

  /// No description provided for @lifeShare.
  ///
  /// In zh, this message translates to:
  /// **'AI 生活分享'**
  String get lifeShare;

  /// No description provided for @lifeShareHint.
  ///
  /// In zh, this message translates to:
  /// **'角色会自然提起自己的生活点滴（信任越高越常提起）'**
  String get lifeShareHint;

  /// No description provided for @lifeTypeGoal.
  ///
  /// In zh, this message translates to:
  /// **'目标'**
  String get lifeTypeGoal;

  /// No description provided for @lifeTypeInterest.
  ///
  /// In zh, this message translates to:
  /// **'兴趣'**
  String get lifeTypeInterest;

  /// No description provided for @lifeTypeLife.
  ///
  /// In zh, this message translates to:
  /// **'生活'**
  String get lifeTypeLife;

  /// No description provided for @lifeTypeNote.
  ///
  /// In zh, this message translates to:
  /// **'笔记'**
  String get lifeTypeNote;

  /// No description provided for @lifeTypeReflection.
  ///
  /// In zh, this message translates to:
  /// **'反思'**
  String get lifeTypeReflection;

  /// No description provided for @light.
  ///
  /// In zh, this message translates to:
  /// **'浅色'**
  String get light;

  /// No description provided for @like.
  ///
  /// In zh, this message translates to:
  /// **'点赞'**
  String get like;

  /// No description provided for @likersText1.
  ///
  /// In zh, this message translates to:
  /// **'{names} 觉得很赞'**
  String likersText1(Object names);

  /// No description provided for @likersTextMany.
  ///
  /// In zh, this message translates to:
  /// **'{names} 等 {count} 人觉得很赞'**
  String likersTextMany(Object names, Object count);

  /// No description provided for @listMode.
  ///
  /// In zh, this message translates to:
  /// **'列表模式'**
  String get listMode;

  /// No description provided for @loadFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载失败'**
  String get loadFailed;

  /// No description provided for @loadFailedErr.
  ///
  /// In zh, this message translates to:
  /// **'加载失败: {err}'**
  String loadFailedErr(Object err);

  /// No description provided for @loadHomeFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载小家失败：{err}'**
  String loadHomeFailed(Object err);

  /// No description provided for @loadOriginalFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载原文失败: {err}'**
  String loadOriginalFailed(Object err);

  /// No description provided for @loadPetFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载角色宠物失败，请稍后重试'**
  String get loadPetFailed;

  /// No description provided for @lockMemory.
  ///
  /// In zh, this message translates to:
  /// **'锁定记忆（不遗忘）'**
  String get lockMemory;

  /// No description provided for @lockedFrozen.
  ///
  /// In zh, this message translates to:
  /// **'已锁定：强度与重要性冻结，不再遗忘'**
  String get lockedFrozen;

  /// No description provided for @lockedNoDecay.
  ///
  /// In zh, this message translates to:
  /// **'已锁定：不衰减 · 不删除'**
  String get lockedNoDecay;

  /// No description provided for @login.
  ///
  /// In zh, this message translates to:
  /// **'登录'**
  String get login;

  /// No description provided for @loginFailed.
  ///
  /// In zh, this message translates to:
  /// **'登录失败'**
  String get loginFailed;

  /// No description provided for @logout.
  ///
  /// In zh, this message translates to:
  /// **'退出登录'**
  String get logout;

  /// No description provided for @longPressAbandon.
  ///
  /// In zh, this message translates to:
  /// **'长按顶部宠物名片可遗弃'**
  String get longPressAbandon;

  /// No description provided for @low.
  ///
  /// In zh, this message translates to:
  /// **'低'**
  String get low;

  /// No description provided for @lowFreq.
  ///
  /// In zh, this message translates to:
  /// **'低频'**
  String get lowFreq;

  /// No description provided for @manualMoment.
  ///
  /// In zh, this message translates to:
  /// **'手动发送角色朋友圈'**
  String get manualMoment;

  /// No description provided for @manualMomentHint.
  ///
  /// In zh, this message translates to:
  /// **'让TA现在发一条动态'**
  String get manualMomentHint;

  /// No description provided for @marketDetailHooks.
  ///
  /// In zh, this message translates to:
  /// **'Hook 挂载点'**
  String get marketDetailHooks;

  /// No description provided for @marketDetailPermissions.
  ///
  /// In zh, this message translates to:
  /// **'权限声明'**
  String get marketDetailPermissions;

  /// No description provided for @marketHint.
  ///
  /// In zh, this message translates to:
  /// **'误删的应用可以在这里下回来'**
  String get marketHint;

  /// No description provided for @marketInstall.
  ///
  /// In zh, this message translates to:
  /// **'安装'**
  String get marketInstall;

  /// No description provided for @marketInstallFailed.
  ///
  /// In zh, this message translates to:
  /// **'安装失败'**
  String get marketInstallFailed;

  /// No description provided for @marketInstallSuccess.
  ///
  /// In zh, this message translates to:
  /// **'安装成功，可在「扩展」中启用'**
  String get marketInstallSuccess;

  /// No description provided for @marketInstalled.
  ///
  /// In zh, this message translates to:
  /// **'已安装'**
  String get marketInstalled;

  /// No description provided for @marketNoResult.
  ///
  /// In zh, this message translates to:
  /// **'没有找到匹配的插件'**
  String get marketNoResult;

  /// No description provided for @marketRiskTip.
  ///
  /// In zh, this message translates to:
  /// **'第三方插件与服务器同权限，请确认来源可信后再安装'**
  String get marketRiskTip;

  /// No description provided for @marketTrustTip.
  ///
  /// In zh, this message translates to:
  /// **'⚠️ 仅安装可信插件：插件与后端进程同权限（无沙箱）。请只安装来源可信的插件。'**
  String get marketTrustTip;

  /// No description provided for @marketSearchHint.
  ///
  /// In zh, this message translates to:
  /// **'搜索插件名称或描述'**
  String get marketSearchHint;

  /// No description provided for @marketSourceBuiltin.
  ///
  /// In zh, this message translates to:
  /// **'内置'**
  String get marketSourceBuiltin;

  /// No description provided for @marketTitle.
  ///
  /// In zh, this message translates to:
  /// **'应用市场'**
  String get marketTitle;

  /// No description provided for @marketplace.
  ///
  /// In zh, this message translates to:
  /// **'插件市场'**
  String get marketplace;

  /// No description provided for @marketplaceHint.
  ///
  /// In zh, this message translates to:
  /// **'发现并一键安装插件（内置市场）'**
  String get marketplaceHint;

  /// No description provided for @marketSourceRemote.
  ///
  /// In zh, this message translates to:
  /// **'远程'**
  String get marketSourceRemote;

  /// No description provided for @marketRemoteUpdate.
  ///
  /// In zh, this message translates to:
  /// **'更新'**
  String get marketRemoteUpdate;

  /// No description provided for @marketRemoteUpToDate.
  ///
  /// In zh, this message translates to:
  /// **'已是最新'**
  String get marketRemoteUpToDate;

  /// No description provided for @marketRemoteInstallTip.
  ///
  /// In zh, this message translates to:
  /// **'该插件来自远程第三方市场，与服务器同权限。请确认来源可信后再安装？'**
  String get marketRemoteInstallTip;

  /// No description provided for @marketRemoteInstallDisabled.
  ///
  /// In zh, this message translates to:
  /// **'远程市场安装已关闭（需在服务器配置开启）'**
  String get marketRemoteInstallDisabled;

  /// No description provided for @marketConsentTitle.
  ///
  /// In zh, this message translates to:
  /// **'权限确认'**
  String get marketConsentTitle;

  /// No description provided for @marketConsentTip.
  ///
  /// In zh, this message translates to:
  /// **'该插件需要以下权限。安装后插件将与服务器同权限运行：'**
  String get marketConsentTip;

  /// No description provided for @marketConsentAgree.
  ///
  /// In zh, this message translates to:
  /// **'同意并安装'**
  String get marketConsentAgree;

  /// No description provided for @marketPermWriteMemory.
  ///
  /// In zh, this message translates to:
  /// **'可读写 AI 记忆（可能污染记忆）'**
  String get marketPermWriteMemory;

  /// No description provided for @marketPermSendMessage.
  ///
  /// In zh, this message translates to:
  /// **'可主动给你发消息（可能骚扰）'**
  String get marketPermSendMessage;

  /// No description provided for @marketPermDouyinPublish.
  ///
  /// In zh, this message translates to:
  /// **'可发布抖音内容（公开内容）'**
  String get marketPermDouyinPublish;

  /// No description provided for @marketPermPersonaRead.
  ///
  /// In zh, this message translates to:
  /// **'可读取角色人设'**
  String get marketPermPersonaRead;

  /// No description provided for @marketPermMemoryRead.
  ///
  /// In zh, this message translates to:
  /// **'可读取 AI 记忆'**
  String get marketPermMemoryRead;

  /// No description provided for @marketPermLifeRead.
  ///
  /// In zh, this message translates to:
  /// **'可读取 AI 生活状态'**
  String get marketPermLifeRead;

  /// No description provided for @marketPermRelationshipRead.
  ///
  /// In zh, this message translates to:
  /// **'可读取关系网'**
  String get marketPermRelationshipRead;

  /// No description provided for @marketPermUnknown.
  ///
  /// In zh, this message translates to:
  /// **'自定义权限（{perm}）'**
  String marketPermUnknown(Object perm);

  /// No description provided for @marketRemoteConfig.
  ///
  /// In zh, this message translates to:
  /// **'远程市场'**
  String get marketRemoteConfig;

  /// No description provided for @marketRemoteConfigHint.
  ///
  /// In zh, this message translates to:
  /// **'配置远程市场地址，从第三方仓库发现并安装插件'**
  String get marketRemoteConfigHint;

  /// No description provided for @marketRemoteEnabled.
  ///
  /// In zh, this message translates to:
  /// **'启用远程市场'**
  String get marketRemoteEnabled;

  /// No description provided for @marketRemoteUrls.
  ///
  /// In zh, this message translates to:
  /// **'市场地址（每行一个，https）'**
  String get marketRemoteUrls;

  /// No description provided for @marketRemoteUrlsHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：https://raw.githubusercontent.com/AMBRACE-plugin/index.json'**
  String get marketRemoteUrlsHint;

  /// No description provided for @marketRemoteRefreshInterval.
  ///
  /// In zh, this message translates to:
  /// **'自动刷新间隔（小时）'**
  String get marketRemoteRefreshInterval;

  /// No description provided for @marketRemoteAllowedHosts.
  ///
  /// In zh, this message translates to:
  /// **'域名白名单（留空=不限 https）'**
  String get marketRemoteAllowedHosts;

  /// No description provided for @marketRemoteAllowedHostsHint.
  ///
  /// In zh, this message translates to:
  /// **'每行一个域名，例如 market.example.com'**
  String get marketRemoteAllowedHostsHint;

  /// No description provided for @marketRemoteMaxZip.
  ///
  /// In zh, this message translates to:
  /// **'安装包大小上限（MB）'**
  String get marketRemoteMaxZip;

  /// No description provided for @marketRemoteSave.
  ///
  /// In zh, this message translates to:
  /// **'保存配置'**
  String get marketRemoteSave;

  /// No description provided for @marketRemoteRefreshNow.
  ///
  /// In zh, this message translates to:
  /// **'立即刷新'**
  String get marketRemoteRefreshNow;

  /// No description provided for @marketRemoteRefreshing.
  ///
  /// In zh, this message translates to:
  /// **'刷新中…'**
  String get marketRemoteRefreshing;

  /// No description provided for @marketRemoteSaved.
  ///
  /// In zh, this message translates to:
  /// **'配置已保存'**
  String get marketRemoteSaved;

  /// No description provided for @marketRemoteRefreshed.
  ///
  /// In zh, this message translates to:
  /// **'刷新完成：{ok} 个市场更新成功'**
  String marketRemoteRefreshed(Object ok);

  /// No description provided for @marketRemoteNotReady.
  ///
  /// In zh, this message translates to:
  /// **'未启用远程市场或未配置地址'**
  String get marketRemoteNotReady;

  /// No description provided for @marketRemoteLastRefresh.
  ///
  /// In zh, this message translates to:
  /// **'上次刷新：{time}'**
  String marketRemoteLastRefresh(Object time);

  /// No description provided for @marketRemoteNever.
  ///
  /// In zh, this message translates to:
  /// **'从未刷新'**
  String get marketRemoteNever;

  /// No description provided for @marketRemoteAdd.
  ///
  /// In zh, this message translates to:
  /// **'添加市场地址'**
  String get marketRemoteAdd;

  /// No description provided for @marketRemoteConfirmDelete.
  ///
  /// In zh, this message translates to:
  /// **'删除该市场地址？'**
  String get marketRemoteConfirmDelete;

  /// No description provided for @me.
  ///
  /// In zh, this message translates to:
  /// **'我'**
  String get me;

  /// No description provided for @medium.
  ///
  /// In zh, this message translates to:
  /// **'中'**
  String get medium;

  /// No description provided for @memoHint.
  ///
  /// In zh, this message translates to:
  /// **'记点什么...（AI 也会主动记录）'**
  String get memoHint;

  /// No description provided for @memoTitle.
  ///
  /// In zh, this message translates to:
  /// **'备忘录'**
  String get memoTitle;

  /// No description provided for @memoTitleHint.
  ///
  /// In zh, this message translates to:
  /// **'标题（可选）'**
  String get memoTitleHint;

  /// No description provided for @memoryAll.
  ///
  /// In zh, this message translates to:
  /// **'全部'**
  String get memoryAll;

  /// No description provided for @memoryBook.
  ///
  /// In zh, this message translates to:
  /// **'记忆本'**
  String get memoryBook;

  /// No description provided for @memoryBookHint.
  ///
  /// In zh, this message translates to:
  /// **'与TA的共同记忆'**
  String get memoryBookHint;

  /// No description provided for @memoryBookTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name} 的记忆本'**
  String memoryBookTitle(Object name);

  /// No description provided for @memoryCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 条记忆'**
  String memoryCount(Object count);

  /// No description provided for @memoryDetailTitle.
  ///
  /// In zh, this message translates to:
  /// **'记忆详情'**
  String get memoryDetailTitle;

  /// No description provided for @memoryChainTitle.
  ///
  /// In zh, this message translates to:
  /// **'记忆链条'**
  String get memoryChainTitle;

  /// No description provided for @memoryChainChildren.
  ///
  /// In zh, this message translates to:
  /// **'关联记忆'**
  String get memoryChainChildren;

  /// No description provided for @memoryChainEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无关联记忆'**
  String get memoryChainEmpty;

  /// No description provided for @memoryEditContent.
  ///
  /// In zh, this message translates to:
  /// **'修改内容'**
  String get memoryEditContent;

  /// No description provided for @memoryEditContentHint.
  ///
  /// In zh, this message translates to:
  /// **'输入新的记忆内容'**
  String get memoryEditContentHint;

  /// No description provided for @memorySaveEdit.
  ///
  /// In zh, this message translates to:
  /// **'保存'**
  String get memorySaveEdit;

  /// No description provided for @memoryDeleteCascadeTitle.
  ///
  /// In zh, this message translates to:
  /// **'级联删除'**
  String get memoryDeleteCascadeTitle;

  /// No description provided for @memoryDeleteCascadeConfirm.
  ///
  /// In zh, this message translates to:
  /// **'将删除本条及其所有关联记忆，确定？'**
  String get memoryDeleteCascadeConfirm;

  /// No description provided for @memoryUpdatedOk.
  ///
  /// In zh, this message translates to:
  /// **'记忆已更新'**
  String get memoryUpdatedOk;

  /// No description provided for @memoryEvent.
  ///
  /// In zh, this message translates to:
  /// **'事件'**
  String get memoryEvent;

  /// No description provided for @memoryImpression.
  ///
  /// In zh, this message translates to:
  /// **'印象'**
  String get memoryImpression;

  /// No description provided for @memoryInsight.
  ///
  /// In zh, this message translates to:
  /// **'洞察'**
  String get memoryInsight;

  /// No description provided for @memoryPreference.
  ///
  /// In zh, this message translates to:
  /// **'偏好'**
  String get memoryPreference;

  /// No description provided for @memorySourceCharacter.
  ///
  /// In zh, this message translates to:
  /// **'角色'**
  String get memorySourceCharacter;

  /// No description provided for @memorySourceUser.
  ///
  /// In zh, this message translates to:
  /// **'用户'**
  String get memorySourceUser;

  /// No description provided for @memoryReview.
  ///
  /// In zh, this message translates to:
  /// **'记忆复习'**
  String get memoryReview;

  /// No description provided for @memoryReviewHint.
  ///
  /// In zh, this message translates to:
  /// **'AI会顺着聊天自然地提起记得的事（记得你的陪伴感）'**
  String get memoryReviewHint;

  /// No description provided for @memoryStrength.
  ///
  /// In zh, this message translates to:
  /// **'记忆强度 {pct}%'**
  String memoryStrength(Object pct);

  /// No description provided for @menu.
  ///
  /// In zh, this message translates to:
  /// **'菜单'**
  String get menu;

  /// No description provided for @mine.
  ///
  /// In zh, this message translates to:
  /// **'我的'**
  String get mine;

  /// No description provided for @minutesLater.
  ///
  /// In zh, this message translates to:
  /// **' 分钟后'**
  String get minutesLater;

  /// No description provided for @momentDateFull.
  ///
  /// In zh, this message translates to:
  /// **'{year}年{month}月{day}日 {time}'**
  String momentDateFull(Object year, Object month, Object day, Object time);

  /// No description provided for @momentHint.
  ///
  /// In zh, this message translates to:
  /// **'说点什么...'**
  String get momentHint;

  /// No description provided for @momentLimit.
  ///
  /// In zh, this message translates to:
  /// **'今日已达发布上限'**
  String get momentLimit;

  /// No description provided for @momentPublishFailed.
  ///
  /// In zh, this message translates to:
  /// **'发布失败: {msg}'**
  String momentPublishFailed(Object msg);

  /// No description provided for @momentPublished.
  ///
  /// In zh, this message translates to:
  /// **'朋友圈已发布: {content}'**
  String momentPublished(Object content);

  /// No description provided for @momentComposeHint.
  ///
  /// In zh, this message translates to:
  /// **'记录此刻的想法…'**
  String get momentComposeHint;

  /// No description provided for @moments.
  ///
  /// In zh, this message translates to:
  /// **'朋友圈'**
  String get moments;

  /// No description provided for @momentsComment.
  ///
  /// In zh, this message translates to:
  /// **'朋友圈评论、回复'**
  String get momentsComment;

  /// No description provided for @momentsCommentHint.
  ///
  /// In zh, this message translates to:
  /// **'AI会评论动态并回复用户的评论'**
  String get momentsCommentHint;

  /// No description provided for @momentsCount.
  ///
  /// In zh, this message translates to:
  /// **'{count}条'**
  String momentsCount(Object count);

  /// No description provided for @momentsHint.
  ///
  /// In zh, this message translates to:
  /// **'AI会发布朋友圈动态'**
  String get momentsHint;

  /// No description provided for @month1.
  ///
  /// In zh, this message translates to:
  /// **'一月'**
  String get month1;

  /// No description provided for @month10.
  ///
  /// In zh, this message translates to:
  /// **'十月'**
  String get month10;

  /// No description provided for @month11.
  ///
  /// In zh, this message translates to:
  /// **'十一月'**
  String get month11;

  /// No description provided for @month12.
  ///
  /// In zh, this message translates to:
  /// **'十二月'**
  String get month12;

  /// No description provided for @month2.
  ///
  /// In zh, this message translates to:
  /// **'二月'**
  String get month2;

  /// No description provided for @month3.
  ///
  /// In zh, this message translates to:
  /// **'三月'**
  String get month3;

  /// No description provided for @month4.
  ///
  /// In zh, this message translates to:
  /// **'四月'**
  String get month4;

  /// No description provided for @month5.
  ///
  /// In zh, this message translates to:
  /// **'五月'**
  String get month5;

  /// No description provided for @month6.
  ///
  /// In zh, this message translates to:
  /// **'六月'**
  String get month6;

  /// No description provided for @month7.
  ///
  /// In zh, this message translates to:
  /// **'七月'**
  String get month7;

  /// No description provided for @month8.
  ///
  /// In zh, this message translates to:
  /// **'八月'**
  String get month8;

  /// No description provided for @month9.
  ///
  /// In zh, this message translates to:
  /// **'九月'**
  String get month9;

  /// No description provided for @monthApr.
  ///
  /// In zh, this message translates to:
  /// **'四月'**
  String get monthApr;

  /// No description provided for @monthAug.
  ///
  /// In zh, this message translates to:
  /// **'八月'**
  String get monthAug;

  /// No description provided for @monthDec.
  ///
  /// In zh, this message translates to:
  /// **'十二月'**
  String get monthDec;

  /// No description provided for @monthFeb.
  ///
  /// In zh, this message translates to:
  /// **'二月'**
  String get monthFeb;

  /// No description provided for @monthJan.
  ///
  /// In zh, this message translates to:
  /// **'一月'**
  String get monthJan;

  /// No description provided for @monthJul.
  ///
  /// In zh, this message translates to:
  /// **'七月'**
  String get monthJul;

  /// No description provided for @monthJun.
  ///
  /// In zh, this message translates to:
  /// **'六月'**
  String get monthJun;

  /// No description provided for @monthMar.
  ///
  /// In zh, this message translates to:
  /// **'三月'**
  String get monthMar;

  /// No description provided for @monthMay.
  ///
  /// In zh, this message translates to:
  /// **'五月'**
  String get monthMay;

  /// No description provided for @monthNov.
  ///
  /// In zh, this message translates to:
  /// **'十一月'**
  String get monthNov;

  /// No description provided for @monthNumFallback.
  ///
  /// In zh, this message translates to:
  /// **'{num}月'**
  String monthNumFallback(Object num);

  /// No description provided for @monthNumeric.
  ///
  /// In zh, this message translates to:
  /// **'{month}月'**
  String monthNumeric(Object month);

  /// No description provided for @monthOct.
  ///
  /// In zh, this message translates to:
  /// **'十月'**
  String get monthOct;

  /// No description provided for @monthSep.
  ///
  /// In zh, this message translates to:
  /// **'九月'**
  String get monthSep;

  /// No description provided for @mood.
  ///
  /// In zh, this message translates to:
  /// **'心情'**
  String get mood;

  /// No description provided for @moodBadge.
  ///
  /// In zh, this message translates to:
  /// **'聊天页心情标识'**
  String get moodBadge;

  /// No description provided for @moodBadgeHint.
  ///
  /// In zh, this message translates to:
  /// **'聊天页角色名字旁显示当前心情表情（纯展示，独立开关）'**
  String get moodBadgeHint;

  /// No description provided for @moodGood.
  ///
  /// In zh, this message translates to:
  /// **'心情不错'**
  String get moodGood;

  /// No description provided for @moodGreat.
  ///
  /// In zh, this message translates to:
  /// **'心情好'**
  String get moodGreat;

  /// No description provided for @moodLow.
  ///
  /// In zh, this message translates to:
  /// **'有点低落'**
  String get moodLow;

  /// No description provided for @moodOk.
  ///
  /// In zh, this message translates to:
  /// **'心情一般'**
  String get moodOk;

  /// No description provided for @moreFunctions.
  ///
  /// In zh, this message translates to:
  /// **'更多功能'**
  String get moreFunctions;

  /// No description provided for @msgCount.
  ///
  /// In zh, this message translates to:
  /// **'{n} 条消息'**
  String msgCount(Object n);

  /// No description provided for @msgCountShort.
  ///
  /// In zh, this message translates to:
  /// **'{n} 条'**
  String msgCountShort(Object n);

  /// No description provided for @myDiary.
  ///
  /// In zh, this message translates to:
  /// **'我的日记'**
  String get myDiary;

  /// No description provided for @myEmoji.
  ///
  /// In zh, this message translates to:
  /// **'我的表情'**
  String get myEmoji;

  /// No description provided for @myMemos.
  ///
  /// In zh, this message translates to:
  /// **'我的备忘录'**
  String get myMemos;

  /// No description provided for @myPhone.
  ///
  /// In zh, this message translates to:
  /// **'我的手机'**
  String get myPhone;

  /// No description provided for @myPhoneComingSoon.
  ///
  /// In zh, this message translates to:
  /// **'我的手机 · 敬请期待（未来将映射你的手机，让 AI 可以了解你的使用）'**
  String get myPhoneComingSoon;

  /// No description provided for @myUploads.
  ///
  /// In zh, this message translates to:
  /// **'我的上传'**
  String get myUploads;

  /// No description provided for @nameTooLong.
  ///
  /// In zh, this message translates to:
  /// **'名字最多 5 个字'**
  String get nameTooLong;

  /// No description provided for @needTwoChars.
  ///
  /// In zh, this message translates to:
  /// **'至少需要 2 个角色才能创建家庭群聊'**
  String get needTwoChars;

  /// No description provided for @newMemo.
  ///
  /// In zh, this message translates to:
  /// **'新增备忘录'**
  String get newMemo;

  /// No description provided for @newPasswordHint.
  ///
  /// In zh, this message translates to:
  /// **'新密码（≥8位，字母+数字）'**
  String get newPasswordHint;

  /// No description provided for @nickname.
  ///
  /// In zh, this message translates to:
  /// **'昵称'**
  String get nickname;

  /// No description provided for @nicknameOptional.
  ///
  /// In zh, this message translates to:
  /// **'昵称（可选）'**
  String get nicknameOptional;

  /// No description provided for @noAccountRegister.
  ///
  /// In zh, this message translates to:
  /// **'没有账号？注册'**
  String get noAccountRegister;

  /// No description provided for @noActivities.
  ///
  /// In zh, this message translates to:
  /// **'还没有互动记录，摸摸宠物吧～'**
  String get noActivities;

  /// No description provided for @noAddableChars.
  ///
  /// In zh, this message translates to:
  /// **'没有可添加的角色'**
  String get noAddableChars;

  /// No description provided for @noAiImages.
  ///
  /// In zh, this message translates to:
  /// **'暂无 AI 生成图片'**
  String get noAiImages;

  /// No description provided for @noArchive.
  ///
  /// In zh, this message translates to:
  /// **'暂无聊天记录'**
  String get noArchive;

  /// No description provided for @noArtifacts.
  ///
  /// In zh, this message translates to:
  /// **'还没有产物，等 TA 离线时创作吧'**
  String get noArtifacts;

  /// No description provided for @noBrowsingRecords.
  ///
  /// In zh, this message translates to:
  /// **'还没有真实浏览记录'**
  String get noBrowsingRecords;

  /// No description provided for @noCharacters.
  ///
  /// In zh, this message translates to:
  /// **'还没有角色，先去创建 AI 角色吧'**
  String get noCharacters;

  /// No description provided for @noChars.
  ///
  /// In zh, this message translates to:
  /// **'暂无角色'**
  String get noChars;

  /// No description provided for @noChatRecords.
  ///
  /// In zh, this message translates to:
  /// **'TA 还没有聊天记录'**
  String get noChatRecords;

  /// No description provided for @noChats.
  ///
  /// In zh, this message translates to:
  /// **'暂无聊天'**
  String get noChats;

  /// No description provided for @noDetails.
  ///
  /// In zh, this message translates to:
  /// **'（无详情）'**
  String get noDetails;

  /// No description provided for @noDiary.
  ///
  /// In zh, this message translates to:
  /// **'暂无日记'**
  String get noDiary;

  /// No description provided for @noDiaryHint.
  ///
  /// In zh, this message translates to:
  /// **'还没有日记，点右下角写一篇吧（AI 好友会看到）'**
  String get noDiaryHint;

  /// No description provided for @noEmoji.
  ///
  /// In zh, this message translates to:
  /// **'暂无表情'**
  String get noEmoji;

  /// No description provided for @noEmotionRecords.
  ///
  /// In zh, this message translates to:
  /// **'还没有情绪记录\n多和 TA 聊聊，情绪波动会自动记录在这里'**
  String get noEmotionRecords;

  /// No description provided for @noGoals.
  ///
  /// In zh, this message translates to:
  /// **'还没有目标'**
  String get noGoals;

  /// No description provided for @noGroups.
  ///
  /// In zh, this message translates to:
  /// **'还没有家庭群聊'**
  String get noGroups;

  /// No description provided for @noInterests.
  ///
  /// In zh, this message translates to:
  /// **'还没有兴趣记录'**
  String get noInterests;

  /// No description provided for @noLifeRecords.
  ///
  /// In zh, this message translates to:
  /// **'还没有生活记录'**
  String get noLifeRecords;

  /// No description provided for @noMemories.
  ///
  /// In zh, this message translates to:
  /// **'暂无比记忆'**
  String get noMemories;

  /// No description provided for @noMemoriesInCategory.
  ///
  /// In zh, this message translates to:
  /// **'该分类下无比记忆'**
  String get noMemoriesInCategory;

  /// No description provided for @noMemos.
  ///
  /// In zh, this message translates to:
  /// **'暂无备忘录'**
  String get noMemos;

  /// No description provided for @noMemosHint.
  ///
  /// In zh, this message translates to:
  /// **'还没有备忘录，点右下角 + 添加一条吧'**
  String get noMemosHint;

  /// No description provided for @noMilestones.
  ///
  /// In zh, this message translates to:
  /// **'还没有值得纪念的时刻，多聊聊就有了'**
  String get noMilestones;

  /// No description provided for @noMoments.
  ///
  /// In zh, this message translates to:
  /// **'暂无动态'**
  String get noMoments;

  /// No description provided for @noNearbyFurniture.
  ///
  /// In zh, this message translates to:
  /// **'附近没有可互动的家具'**
  String get noNearbyFurniture;

  /// No description provided for @noPetForChar.
  ///
  /// In zh, this message translates to:
  /// **'{name} 还没有宠物'**
  String noPetForChar(Object name);

  /// No description provided for @noPets.
  ///
  /// In zh, this message translates to:
  /// **'暂无宠物'**
  String get noPets;

  /// No description provided for @noResultsHint.
  ///
  /// In zh, this message translates to:
  /// **'未找到相关结果，换个关键词试试'**
  String get noResultsHint;

  /// No description provided for @noSelfStatement.
  ///
  /// In zh, this message translates to:
  /// **'TA 还没有自述，聊一聊之后会慢慢形成'**
  String get noSelfStatement;

  /// No description provided for @noBasicInfo.
  ///
  /// In zh, this message translates to:
  /// **'暂无基本信息'**
  String get noBasicInfo;

  /// No description provided for @noTags.
  ///
  /// In zh, this message translates to:
  /// **'暂无标签'**
  String get noTags;

  /// No description provided for @noAppearanceDesc.
  ///
  /// In zh, this message translates to:
  /// **'暂无形象描述'**
  String get noAppearanceDesc;

  /// No description provided for @noUploadsHint.
  ///
  /// In zh, this message translates to:
  /// **'暂无上传图片，点右上角上传'**
  String get noUploadsHint;

  /// No description provided for @notCompleted.
  ///
  /// In zh, this message translates to:
  /// **'未完成'**
  String get notCompleted;

  /// No description provided for @noteHint.
  ///
  /// In zh, this message translates to:
  /// **'写一条备注（AI 也会看到）...'**
  String get noteHint;

  /// No description provided for @notifyWhitelist.
  ///
  /// In zh, this message translates to:
  /// **'通知白名单'**
  String get notifyWhitelist;

  /// No description provided for @notifyWhitelistEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无通知记录：先让几个 app 发通知，再回来勾选白名单'**
  String get notifyWhitelistEmpty;

  /// No description provided for @notifyWhitelistHint.
  ///
  /// In zh, this message translates to:
  /// **'未勾选任何 app = 全部允许；勾选后只感知勾选的通知。\n下方列表来自最近感知到的通知，勾选的 app 通知才会被 AI 看到。'**
  String get notifyWhitelistHint;

  /// No description provided for @off.
  ///
  /// In zh, this message translates to:
  /// **'关闭'**
  String get off;

  /// No description provided for @offlineLifeHint.
  ///
  /// In zh, this message translates to:
  /// **'开启「AI 离线生活」后，角色会在离线时真实度过时间'**
  String get offlineLifeHint;

  /// No description provided for @oldPassword.
  ///
  /// In zh, this message translates to:
  /// **'旧密码'**
  String get oldPassword;

  /// No description provided for @opFailedErr.
  ///
  /// In zh, this message translates to:
  /// **'操作失败: {err}'**
  String opFailedErr(Object err);

  /// No description provided for @opFailedRetry.
  ///
  /// In zh, this message translates to:
  /// **'操作失败，请重试'**
  String get opFailedRetry;

  /// No description provided for @openFailed.
  ///
  /// In zh, this message translates to:
  /// **'打开失败：{url}'**
  String openFailed(Object url);

  /// No description provided for @originalUnavailable.
  ///
  /// In zh, this message translates to:
  /// **'无法加载原文'**
  String get originalUnavailable;

  /// No description provided for @password.
  ///
  /// In zh, this message translates to:
  /// **'密码'**
  String get password;

  /// No description provided for @passwordChanged.
  ///
  /// In zh, this message translates to:
  /// **'密码已修改，下次请用新密码登录'**
  String get passwordChanged;

  /// No description provided for @petClean.
  ///
  /// In zh, this message translates to:
  /// **'清洁'**
  String get petClean;

  /// No description provided for @petEntry.
  ///
  /// In zh, this message translates to:
  /// **'宠物入口'**
  String get petEntry;

  /// No description provided for @petFullClean.
  ///
  /// In zh, this message translates to:
  /// **'宠物已经很干净啦'**
  String get petFullClean;

  /// No description provided for @petFullHunger.
  ///
  /// In zh, this message translates to:
  /// **'宠物已经吃饱啦'**
  String get petFullHunger;

  /// No description provided for @petFullPlay.
  ///
  /// In zh, this message translates to:
  /// **'宠物已经玩得很尽兴啦'**
  String get petFullPlay;

  /// No description provided for @petHunger.
  ///
  /// In zh, this message translates to:
  /// **'饥饿'**
  String get petHunger;

  /// No description provided for @petLimit3.
  ///
  /// In zh, this message translates to:
  /// **'最多只能养 3 只宠物'**
  String get petLimit3;

  /// No description provided for @petNameHint.
  ///
  /// In zh, this message translates to:
  /// **'给宠物起个名字（最多5个字）'**
  String get petNameHint;

  /// No description provided for @petNameLabel.
  ///
  /// In zh, this message translates to:
  /// **'宠物名字（最多5个字）'**
  String get petNameLabel;

  /// No description provided for @petNameRequired.
  ///
  /// In zh, this message translates to:
  /// **'请给宠物起个名字'**
  String get petNameRequired;

  /// No description provided for @petting.
  ///
  /// In zh, this message translates to:
  /// **'抚摸'**
  String get petting;

  /// No description provided for @phaseAfternoon.
  ///
  /// In zh, this message translates to:
  /// **'在过下午'**
  String get phaseAfternoon;

  /// No description provided for @phaseEvening.
  ///
  /// In zh, this message translates to:
  /// **'在过晚上'**
  String get phaseEvening;

  /// No description provided for @phaseLiving.
  ///
  /// In zh, this message translates to:
  /// **'在生活'**
  String get phaseLiving;

  /// No description provided for @phaseMorning.
  ///
  /// In zh, this message translates to:
  /// **'在过上午'**
  String get phaseMorning;

  /// No description provided for @phaseSleep.
  ///
  /// In zh, this message translates to:
  /// **'在睡觉'**
  String get phaseSleep;

  /// No description provided for @phoneOf.
  ///
  /// In zh, this message translates to:
  /// **'{name} 的小手机'**
  String phoneOf(Object name);

  /// No description provided for @phonePerception.
  ///
  /// In zh, this message translates to:
  /// **'手机感知'**
  String get phonePerception;

  /// No description provided for @phonePerceptionHint.
  ///
  /// In zh, this message translates to:
  /// **'让 AI 好友了解你的手机状态（读屏/剪贴板/相册）'**
  String get phonePerceptionHint;

  /// No description provided for @phonePetCareHint.
  ///
  /// In zh, this message translates to:
  /// **'{name} 会亲自照顾它；想帮忙的话，去主页宠物页拜访 TA 吧'**
  String phonePetCareHint(Object name);

  /// No description provided for @phoneShort.
  ///
  /// In zh, this message translates to:
  /// **'小手机'**
  String get phoneShort;

  /// No description provided for @pickOne.
  ///
  /// In zh, this message translates to:
  /// **'选一张'**
  String get pickOne;

  /// No description provided for @pinEmotion.
  ///
  /// In zh, this message translates to:
  /// **'收藏这段情绪'**
  String get pinEmotion;

  /// No description provided for @pinned.
  ///
  /// In zh, this message translates to:
  /// **'已收藏'**
  String get pinned;

  /// No description provided for @pinnedEmotion.
  ///
  /// In zh, this message translates to:
  /// **'已收藏这段情绪'**
  String get pinnedEmotion;

  /// No description provided for @pinnedSummary.
  ///
  /// In zh, this message translates to:
  /// **'{type} · 置顶摘要'**
  String pinnedSummary(Object type);

  /// No description provided for @play.
  ///
  /// In zh, this message translates to:
  /// **'玩耍'**
  String get play;

  /// No description provided for @pluginAll.
  ///
  /// In zh, this message translates to:
  /// **'全部'**
  String get pluginAll;

  /// No description provided for @pluginAuthor.
  ///
  /// In zh, this message translates to:
  /// **'作者'**
  String get pluginAuthor;

  /// No description provided for @pluginSource.
  ///
  /// In zh, this message translates to:
  /// **'来源'**
  String get pluginSource;

  /// No description provided for @pluginSourceRemote.
  ///
  /// In zh, this message translates to:
  /// **'来源：远程市场'**
  String get pluginSourceRemote;

  /// No description provided for @pluginSourceLocal.
  ///
  /// In zh, this message translates to:
  /// **'来源：本地导入'**
  String get pluginSourceLocal;

  /// No description provided for @pluginSourceBuiltin.
  ///
  /// In zh, this message translates to:
  /// **'来源：内置示例'**
  String get pluginSourceBuiltin;

  /// No description provided for @pluginSha256.
  ///
  /// In zh, this message translates to:
  /// **'校验和'**
  String get pluginSha256;

  /// No description provided for @pluginBridgeError.
  ///
  /// In zh, this message translates to:
  /// **'桥调用失败'**
  String get pluginBridgeError;

  /// No description provided for @pluginChatInputHint.
  ///
  /// In zh, this message translates to:
  /// **'输入消息…'**
  String get pluginChatInputHint;

  /// No description provided for @pluginChatSendFail.
  ///
  /// In zh, this message translates to:
  /// **'发送失败'**
  String get pluginChatSendFail;

  /// No description provided for @pluginClose.
  ///
  /// In zh, this message translates to:
  /// **'关闭'**
  String get pluginClose;

  /// No description provided for @pluginConfig.
  ///
  /// In zh, this message translates to:
  /// **'配置'**
  String get pluginConfig;

  /// No description provided for @pluginConfigChatName.
  ///
  /// In zh, this message translates to:
  /// **'名称'**
  String get pluginConfigChatName;

  /// No description provided for @pluginConfigGreeting.
  ///
  /// In zh, this message translates to:
  /// **'开场白'**
  String get pluginConfigGreeting;

  /// No description provided for @pluginConfigPersona.
  ///
  /// In zh, this message translates to:
  /// **'人设'**
  String get pluginConfigPersona;

  /// No description provided for @pluginConfigSaved.
  ///
  /// In zh, this message translates to:
  /// **'配置已保存'**
  String get pluginConfigSaved;

  /// No description provided for @pluginConfigSystemPrompt.
  ///
  /// In zh, this message translates to:
  /// **'技能提示词（systemPrompt）'**
  String get pluginConfigSystemPrompt;

  /// No description provided for @pluginConfigTriggers.
  ///
  /// In zh, this message translates to:
  /// **'触发词（逗号分隔）'**
  String get pluginConfigTriggers;

  /// No description provided for @pluginCopied.
  ///
  /// In zh, this message translates to:
  /// **'已复制'**
  String get pluginCopied;

  /// No description provided for @pluginDisabled.
  ///
  /// In zh, this message translates to:
  /// **'已禁用'**
  String get pluginDisabled;

  /// No description provided for @pluginDisabledToast.
  ///
  /// In zh, this message translates to:
  /// **'已禁用'**
  String get pluginDisabledToast;

  /// No description provided for @pluginEnabled.
  ///
  /// In zh, this message translates to:
  /// **'已启用'**
  String get pluginEnabled;

  /// No description provided for @pluginEnabledToast.
  ///
  /// In zh, this message translates to:
  /// **'已启用'**
  String get pluginEnabledToast;

  /// No description provided for @pluginExternalLink.
  ///
  /// In zh, this message translates to:
  /// **'已在浏览器中打开'**
  String get pluginExternalLink;

  /// No description provided for @pluginHooks.
  ///
  /// In zh, this message translates to:
  /// **'挂载点'**
  String get pluginHooks;

  /// No description provided for @pluginInstallFail.
  ///
  /// In zh, this message translates to:
  /// **'安装失败'**
  String get pluginInstallFail;

  /// No description provided for @pluginInstallSuccess.
  ///
  /// In zh, this message translates to:
  /// **'插件安装成功'**
  String get pluginInstallSuccess;

  /// No description provided for @pluginInstallZip.
  ///
  /// In zh, this message translates to:
  /// **'安装插件 zip'**
  String get pluginInstallZip;

  /// No description provided for @pluginMcp.
  ///
  /// In zh, this message translates to:
  /// **'插件协议'**
  String get pluginMcp;

  /// No description provided for @pluginNavBlocked.
  ///
  /// In zh, this message translates to:
  /// **'已拦截非插件页跳转'**
  String get pluginNavBlocked;

  /// No description provided for @pluginNeedZip.
  ///
  /// In zh, this message translates to:
  /// **'所选文件不是 .zip'**
  String get pluginNeedZip;

  /// No description provided for @pluginNoPlugins.
  ///
  /// In zh, this message translates to:
  /// **'暂无插件'**
  String get pluginNoPlugins;

  /// No description provided for @pluginNormal.
  ///
  /// In zh, this message translates to:
  /// **'普通'**
  String get pluginNormal;

  /// No description provided for @pluginNotWritable.
  ///
  /// In zh, this message translates to:
  /// **'只读（仅主账号可修改）'**
  String get pluginNotWritable;

  /// No description provided for @pluginOnlyAdmin.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可管理插件'**
  String get pluginOnlyAdmin;

  /// No description provided for @pluginOpen.
  ///
  /// In zh, this message translates to:
  /// **'打开'**
  String get pluginOpen;

  /// No description provided for @pluginOpenChat.
  ///
  /// In zh, this message translates to:
  /// **'打开聊天'**
  String get pluginOpenChat;

  /// No description provided for @pluginOpenPage.
  ///
  /// In zh, this message translates to:
  /// **'打开页面'**
  String get pluginOpenPage;

  /// No description provided for @pluginPageLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'页面加载失败'**
  String get pluginPageLoadFailed;

  /// No description provided for @pluginRiskHint.
  ///
  /// In zh, this message translates to:
  /// **'插件在服务器上执行，第三方插件与服务器同权限，请只安装可信来源的插件。'**
  String get pluginRiskHint;

  /// No description provided for @pluginRiskTitle.
  ///
  /// In zh, this message translates to:
  /// **'风险提示'**
  String get pluginRiskTitle;

  /// No description provided for @pluginSaveConfig.
  ///
  /// In zh, this message translates to:
  /// **'保存配置'**
  String get pluginSaveConfig;

  /// No description provided for @pluginSelectZip.
  ///
  /// In zh, this message translates to:
  /// **'请选择 zip 文件'**
  String get pluginSelectZip;

  /// No description provided for @pluginTypeChat.
  ///
  /// In zh, this message translates to:
  /// **'互动对话'**
  String get pluginTypeChat;

  /// No description provided for @pluginTypeHttp.
  ///
  /// In zh, this message translates to:
  /// **'常规'**
  String get pluginTypeHttp;

  /// No description provided for @pluginTypeHybrid.
  ///
  /// In zh, this message translates to:
  /// **'页面插件'**
  String get pluginTypeHybrid;

  /// No description provided for @pluginTypePrompt.
  ///
  /// In zh, this message translates to:
  /// **'Prompt 技能'**
  String get pluginTypePrompt;

  /// No description provided for @pluginTypeWorkflow.
  ///
  /// In zh, this message translates to:
  /// **'工作流模板'**
  String get pluginTypeWorkflow;

  /// No description provided for @pluginUninstall.
  ///
  /// In zh, this message translates to:
  /// **'卸载'**
  String get pluginUninstall;

  /// No description provided for @pluginUninstallConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定卸载该插件？其存储数据将一并清除。'**
  String get pluginUninstallConfirm;

  /// No description provided for @pluginUninstallFail.
  ///
  /// In zh, this message translates to:
  /// **'卸载失败'**
  String get pluginUninstallFail;

  /// No description provided for @pluginUninstallSuccess.
  ///
  /// In zh, this message translates to:
  /// **'插件已卸载'**
  String get pluginUninstallSuccess;

  /// No description provided for @pluginVersion.
  ///
  /// In zh, this message translates to:
  /// **'版本'**
  String get pluginVersion;

  /// No description provided for @pluginZeroCodeConfig.
  ///
  /// In zh, this message translates to:
  /// **'零代码配置'**
  String get pluginZeroCodeConfig;

  /// No description provided for @portraitGroup.
  ///
  /// In zh, this message translates to:
  /// **'形象'**
  String get portraitGroup;

  /// No description provided for @presentLine.
  ///
  /// In zh, this message translates to:
  /// **'此刻：{doing} · {moodText}'**
  String presentLine(Object doing, Object moodText);

  /// No description provided for @privacy.
  ///
  /// In zh, this message translates to:
  /// **'隐私'**
  String get privacy;

  /// No description provided for @privacyGroup.
  ///
  /// In zh, this message translates to:
  /// **'隐私'**
  String get privacyGroup;

  /// No description provided for @privacyHint.
  ///
  /// In zh, this message translates to:
  /// **'隐私上锁与聊天细节展示'**
  String get privacyHint;

  /// No description provided for @privacyLock.
  ///
  /// In zh, this message translates to:
  /// **'隐私上锁'**
  String get privacyLock;

  /// No description provided for @privacyLockHint.
  ///
  /// In zh, this message translates to:
  /// **'日记和小手机平常上锁，查看需向TA申请'**
  String get privacyLockHint;

  /// No description provided for @proactiveChat.
  ///
  /// In zh, this message translates to:
  /// **'主动交流'**
  String get proactiveChat;

  /// No description provided for @proactiveChatHint.
  ///
  /// In zh, this message translates to:
  /// **'闲置时AI会主动找您聊天'**
  String get proactiveChatHint;

  /// No description provided for @proactiveFrequency.
  ///
  /// In zh, this message translates to:
  /// **'主动频率'**
  String get proactiveFrequency;

  /// No description provided for @proactiveFrequencyHint.
  ///
  /// In zh, this message translates to:
  /// **'AI主动找您聊天的频率'**
  String get proactiveFrequencyHint;

  /// No description provided for @publish.
  ///
  /// In zh, this message translates to:
  /// **'发布'**
  String get publish;

  /// No description provided for @publishFailed.
  ///
  /// In zh, this message translates to:
  /// **'发布失败，请重试'**
  String get publishFailed;

  /// No description provided for @publishMoment.
  ///
  /// In zh, this message translates to:
  /// **'发布动态'**
  String get publishMoment;

  /// No description provided for @quote.
  ///
  /// In zh, this message translates to:
  /// **'引用'**
  String get quote;

  /// No description provided for @quotePrefix.
  ///
  /// In zh, this message translates to:
  /// **'引用'**
  String get quotePrefix;

  /// No description provided for @readOnly.
  ///
  /// In zh, this message translates to:
  /// **'（只读）'**
  String get readOnly;

  /// No description provided for @reasoningLevel.
  ///
  /// In zh, this message translates to:
  /// **'思考过程'**
  String get reasoningLevel;

  /// No description provided for @reasoningLevelHint.
  ///
  /// In zh, this message translates to:
  /// **'气泡顶部显示TA回复前的推理内容'**
  String get reasoningLevelHint;

  /// No description provided for @recordCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 条记录'**
  String recordCount(Object count);

  /// No description provided for @recordTime.
  ///
  /// In zh, this message translates to:
  /// **'记录时间'**
  String get recordTime;

  /// No description provided for @recordingPrefix.
  ///
  /// In zh, this message translates to:
  /// **'录音中'**
  String get recordingPrefix;

  /// No description provided for @recordingSuffix.
  ///
  /// In zh, this message translates to:
  /// **'秒，松开发送 · 上滑取消'**
  String get recordingSuffix;

  /// No description provided for @refresh.
  ///
  /// In zh, this message translates to:
  /// **'刷新'**
  String get refresh;

  /// No description provided for @register.
  ///
  /// In zh, this message translates to:
  /// **'注册'**
  String get register;

  /// No description provided for @registerFailed.
  ///
  /// In zh, this message translates to:
  /// **'注册失败'**
  String get registerFailed;

  /// No description provided for @releaseToCancel.
  ///
  /// In zh, this message translates to:
  /// **'松开取消'**
  String get releaseToCancel;

  /// No description provided for @remove.
  ///
  /// In zh, this message translates to:
  /// **'移除'**
  String get remove;

  /// No description provided for @removeImage.
  ///
  /// In zh, this message translates to:
  /// **'移除图片'**
  String get removeImage;

  /// No description provided for @rename.
  ///
  /// In zh, this message translates to:
  /// **'改名'**
  String get rename;

  /// No description provided for @renameFailed.
  ///
  /// In zh, this message translates to:
  /// **'改名失败'**
  String get renameFailed;

  /// No description provided for @renameHint.
  ///
  /// In zh, this message translates to:
  /// **'新名字（最多5个字）'**
  String get renameHint;

  /// No description provided for @reply.
  ///
  /// In zh, this message translates to:
  /// **'回复'**
  String get reply;

  /// No description provided for @replying.
  ///
  /// In zh, this message translates to:
  /// **'回复中'**
  String get replying;

  /// No description provided for @restoredToDesktop.
  ///
  /// In zh, this message translates to:
  /// **'已恢复到桌面'**
  String get restoredToDesktop;

  /// No description provided for @retry.
  ///
  /// In zh, this message translates to:
  /// **'重试'**
  String get retry;

  /// No description provided for @roleFallback.
  ///
  /// In zh, this message translates to:
  /// **'角色'**
  String get roleFallback;

  /// No description provided for @routine.
  ///
  /// In zh, this message translates to:
  /// **'作息'**
  String get routine;

  /// No description provided for @saveNote.
  ///
  /// In zh, this message translates to:
  /// **'保存备注'**
  String get saveNote;

  /// No description provided for @savedToAlbum.
  ///
  /// In zh, this message translates to:
  /// **'已保存到我的相册'**
  String get savedToAlbum;

  /// No description provided for @searchFail.
  ///
  /// In zh, this message translates to:
  /// **'搜索失败'**
  String get searchFail;

  /// No description provided for @searchFailDetail.
  ///
  /// In zh, this message translates to:
  /// **'搜索失败：{e}'**
  String searchFailDetail(Object e);

  /// No description provided for @searchHint.
  ///
  /// In zh, this message translates to:
  /// **'输入关键词搜索，历史保留 7 天'**
  String get searchHint;

  /// No description provided for @searchHistory.
  ///
  /// In zh, this message translates to:
  /// **'搜索历史（保留 7 天）'**
  String get searchHistory;

  /// No description provided for @searchPlaceholder.
  ///
  /// In zh, this message translates to:
  /// **'搜索内容...'**
  String get searchPlaceholder;

  /// No description provided for @searching.
  ///
  /// In zh, this message translates to:
  /// **'正在搜索…首次可能需要十几秒'**
  String get searching;

  /// No description provided for @selectFriend.
  ///
  /// In zh, this message translates to:
  /// **'选择一位好友'**
  String get selectFriend;

  /// No description provided for @selectMembersHint.
  ///
  /// In zh, this message translates to:
  /// **'选择要添加的角色：'**
  String get selectMembersHint;

  /// No description provided for @selectMinTwo.
  ///
  /// In zh, this message translates to:
  /// **'选择成员（至少 2 个）：'**
  String get selectMinTwo;

  /// No description provided for @selfStatement.
  ///
  /// In zh, this message translates to:
  /// **'自述'**
  String get selfStatement;

  /// No description provided for @sendChat.
  ///
  /// In zh, this message translates to:
  /// **'发送'**
  String get sendChat;

  /// No description provided for @sendDoc.
  ///
  /// In zh, this message translates to:
  /// **'发送文档'**
  String get sendDoc;

  /// No description provided for @sendFail.
  ///
  /// In zh, this message translates to:
  /// **'发送失败'**
  String get sendFail;

  /// No description provided for @sendImage.
  ///
  /// In zh, this message translates to:
  /// **'发送图片'**
  String get sendImage;

  /// No description provided for @sending.
  ///
  /// In zh, this message translates to:
  /// **'发送中...'**
  String get sending;

  /// No description provided for @serverAddress.
  ///
  /// In zh, this message translates to:
  /// **'服务器地址'**
  String get serverAddress;

  /// No description provided for @serverAddressHint.
  ///
  /// In zh, this message translates to:
  /// **'请输入电脑上显示的服务器地址（http://IP:8000）'**
  String get serverAddressHint;

  /// No description provided for @setStarToKeep.
  ///
  /// In zh, this message translates to:
  /// **'设置星级可以消除倒计时保留记忆'**
  String get setStarToKeep;

  /// No description provided for @settingsTitle.
  ///
  /// In zh, this message translates to:
  /// **'设置'**
  String get settingsTitle;

  /// No description provided for @showTools.
  ///
  /// In zh, this message translates to:
  /// **'调用能力'**
  String get showTools;

  /// No description provided for @showToolsHint.
  ///
  /// In zh, this message translates to:
  /// **'气泡内显示本次回复使用的能力（识图/生图/语音/扩展）'**
  String get showToolsHint;

  /// No description provided for @simpleThinking.
  ///
  /// In zh, this message translates to:
  /// **'简单思考'**
  String get simpleThinking;

  /// No description provided for @simplifiedChinese.
  ///
  /// In zh, this message translates to:
  /// **'简体中文'**
  String get simplifiedChinese;

  /// No description provided for @socialGroup.
  ///
  /// In zh, this message translates to:
  /// **'社交'**
  String get socialGroup;

  /// No description provided for @sourceBio.
  ///
  /// In zh, this message translates to:
  /// **'自述'**
  String get sourceBio;

  /// No description provided for @sourceChat.
  ///
  /// In zh, this message translates to:
  /// **'聊天'**
  String get sourceChat;

  /// No description provided for @sourceDiary.
  ///
  /// In zh, this message translates to:
  /// **'日记'**
  String get sourceDiary;

  /// No description provided for @sourceEmotion.
  ///
  /// In zh, this message translates to:
  /// **'对话评估'**
  String get sourceEmotion;

  /// No description provided for @sourceExtracted.
  ///
  /// In zh, this message translates to:
  /// **'提取'**
  String get sourceExtracted;

  /// No description provided for @sourceFrom.
  ///
  /// In zh, this message translates to:
  /// **'来源: {url}'**
  String sourceFrom(Object url);

  /// No description provided for @sourceInfo.
  ///
  /// In zh, this message translates to:
  /// **'来源信息'**
  String get sourceInfo;

  /// No description provided for @sourceLabel.
  ///
  /// In zh, this message translates to:
  /// **'来源'**
  String get sourceLabel;

  /// No description provided for @sourceMoment.
  ///
  /// In zh, this message translates to:
  /// **'朋友圈'**
  String get sourceMoment;

  /// No description provided for @sourcePrefix.
  ///
  /// In zh, this message translates to:
  /// **'来源：{source}'**
  String sourcePrefix(Object source);

  /// No description provided for @sourceRelationship.
  ///
  /// In zh, this message translates to:
  /// **'关系'**
  String get sourceRelationship;

  /// No description provided for @sourceStatus.
  ///
  /// In zh, this message translates to:
  /// **'状态'**
  String get sourceStatus;

  /// No description provided for @sourceStory.
  ///
  /// In zh, this message translates to:
  /// **'剧情线'**
  String get sourceStory;

  /// No description provided for @sourceTrigger.
  ///
  /// In zh, this message translates to:
  /// **'状态触发'**
  String get sourceTrigger;

  /// No description provided for @speciesCat.
  ///
  /// In zh, this message translates to:
  /// **'猫'**
  String get speciesCat;

  /// No description provided for @speciesDog.
  ///
  /// In zh, this message translates to:
  /// **'狗'**
  String get speciesDog;

  /// No description provided for @speciesGecko.
  ///
  /// In zh, this message translates to:
  /// **'守宫'**
  String get speciesGecko;

  /// No description provided for @speciesHamster.
  ///
  /// In zh, this message translates to:
  /// **'仓鼠'**
  String get speciesHamster;

  /// No description provided for @speciesParrot.
  ///
  /// In zh, this message translates to:
  /// **'鹦鹉'**
  String get speciesParrot;

  /// No description provided for @speciesPrefix.
  ///
  /// In zh, this message translates to:
  /// **'种类：{species}'**
  String speciesPrefix(Object species);

  /// No description provided for @speciesRabbit.
  ///
  /// In zh, this message translates to:
  /// **'兔子'**
  String get speciesRabbit;

  /// No description provided for @speciesSnake.
  ///
  /// In zh, this message translates to:
  /// **'蛇'**
  String get speciesSnake;

  /// No description provided for @stamina.
  ///
  /// In zh, this message translates to:
  /// **'体力'**
  String get stamina;

  /// No description provided for @standard.
  ///
  /// In zh, this message translates to:
  /// **'标准'**
  String get standard;

  /// No description provided for @start.
  ///
  /// In zh, this message translates to:
  /// **'开始'**
  String get start;

  /// No description provided for @stateAnger.
  ///
  /// In zh, this message translates to:
  /// **'怒气值'**
  String get stateAnger;

  /// No description provided for @stateComfort.
  ///
  /// In zh, this message translates to:
  /// **'舒适感'**
  String get stateComfort;

  /// No description provided for @stateDesire.
  ///
  /// In zh, this message translates to:
  /// **'性欲'**
  String get stateDesire;

  /// No description provided for @stateEmotionMemory.
  ///
  /// In zh, this message translates to:
  /// **'状态情绪记忆'**
  String get stateEmotionMemory;

  /// No description provided for @stateEmotionMemoryHint.
  ///
  /// In zh, this message translates to:
  /// **'回看 TA 最近的情绪波动、触发与剧情记录'**
  String get stateEmotionMemoryHint;

  /// No description provided for @stateFatigue.
  ///
  /// In zh, this message translates to:
  /// **'疲惫感'**
  String get stateFatigue;

  /// No description provided for @statePossessiveness.
  ///
  /// In zh, this message translates to:
  /// **'占有欲'**
  String get statePossessiveness;

  /// No description provided for @stateSensitivity.
  ///
  /// In zh, this message translates to:
  /// **'敏感度'**
  String get stateSensitivity;

  /// No description provided for @stateTemp.
  ///
  /// In zh, this message translates to:
  /// **'体温'**
  String get stateTemp;

  /// No description provided for @stateTrend.
  ///
  /// In zh, this message translates to:
  /// **'状态趋势'**
  String get stateTrend;

  /// No description provided for @stateTrendHint.
  ///
  /// In zh, this message translates to:
  /// **'回看八维状态变化曲线，对比不同时间点的状态'**
  String get stateTrendHint;

  /// No description provided for @stateTrigger.
  ///
  /// In zh, this message translates to:
  /// **'状态触发'**
  String get stateTrigger;

  /// No description provided for @stateTriggerHint.
  ///
  /// In zh, this message translates to:
  /// **'心情/怒气等状态达阈值时AI会主动表达（发消息/朋友圈）'**
  String get stateTriggerHint;

  /// No description provided for @stateUpdatedHint.
  ///
  /// In zh, this message translates to:
  /// **'最近评估于 {time} · 数值随时间自然变化（箭头=趋势）'**
  String stateUpdatedHint(Object time);

  /// No description provided for @status.
  ///
  /// In zh, this message translates to:
  /// **'状态'**
  String get status;

  /// No description provided for @statusGroup.
  ///
  /// In zh, this message translates to:
  /// **'状态'**
  String get statusGroup;

  /// No description provided for @statusHint.
  ///
  /// In zh, this message translates to:
  /// **'状态达阈值时的主动表达与冷战行为'**
  String get statusHint;

  /// No description provided for @storyCount.
  ///
  /// In zh, this message translates to:
  /// **'剧情'**
  String get storyCount;

  /// No description provided for @storyFilter.
  ///
  /// In zh, this message translates to:
  /// **'剧情'**
  String get storyFilter;

  /// No description provided for @subCategory.
  ///
  /// In zh, this message translates to:
  /// **'子分类'**
  String get subCategory;

  /// No description provided for @summaryGenFailed.
  ///
  /// In zh, this message translates to:
  /// **'生成失败，请稍后再试'**
  String get summaryGenFailed;

  /// No description provided for @summaryRegenFailed.
  ///
  /// In zh, this message translates to:
  /// **'重新生成失败，请检查服务器'**
  String get summaryRegenFailed;

  /// No description provided for @summaryRegenerated.
  ///
  /// In zh, this message translates to:
  /// **'置顶摘要已重新生成'**
  String get summaryRegenerated;

  /// No description provided for @supportAuthor.
  ///
  /// In zh, this message translates to:
  /// **'支持作者'**
  String get supportAuthor;

  /// No description provided for @supportAuthorHint.
  ///
  /// In zh, this message translates to:
  /// **'自愿支持用于维护/更新'**
  String get supportAuthorHint;

  /// No description provided for @switchMode.
  ///
  /// In zh, this message translates to:
  /// **'切换'**
  String get switchMode;

  /// No description provided for @switchSaveFail.
  ///
  /// In zh, this message translates to:
  /// **'开关保存失败，请稍后重试'**
  String get switchSaveFail;

  /// No description provided for @ta.
  ///
  /// In zh, this message translates to:
  /// **'TA'**
  String get ta;

  /// No description provided for @taCareEmpty.
  ///
  /// In zh, this message translates to:
  /// **'TA 还没开始照顾记录，正在和宠物培养感情～'**
  String get taCareEmpty;

  /// No description provided for @taCareLog.
  ///
  /// In zh, this message translates to:
  /// **'TA 的照顾记录'**
  String get taCareLog;

  /// No description provided for @taNoPet.
  ///
  /// In zh, this message translates to:
  /// **'{name} 还没有养宠物'**
  String taNoPet(Object name);

  /// No description provided for @taNoPetHint.
  ///
  /// In zh, this message translates to:
  /// **'TA 会自己决定领养一只小动物；想帮 TA 的话，可以去主页宠物页「拜访」'**
  String get taNoPetHint;

  /// No description provided for @tabAiInteraction.
  ///
  /// In zh, this message translates to:
  /// **'小手机'**
  String get tabAiInteraction;

  /// No description provided for @tabFriends.
  ///
  /// In zh, this message translates to:
  /// **'好友'**
  String get tabFriends;

  /// No description provided for @tabMoments.
  ///
  /// In zh, this message translates to:
  /// **'朋友圈'**
  String get tabMoments;

  /// No description provided for @tabPets.
  ///
  /// In zh, this message translates to:
  /// **'宠物'**
  String get tabPets;

  /// No description provided for @tapAvatarToChange.
  ///
  /// In zh, this message translates to:
  /// **'点击头像可裁剪更换'**
  String get tapAvatarToChange;

  /// No description provided for @tapToTest.
  ///
  /// In zh, this message translates to:
  /// **'点击检测连接'**
  String get tapToTest;

  /// No description provided for @tapToViewOriginal.
  ///
  /// In zh, this message translates to:
  /// **'点击查看原文内容'**
  String get tapToViewOriginal;

  /// No description provided for @themeAurora.
  ///
  /// In zh, this message translates to:
  /// **'极光'**
  String get themeAurora;

  /// No description provided for @themeCherry.
  ///
  /// In zh, this message translates to:
  /// **'樱花'**
  String get themeCherry;

  /// No description provided for @themeCoffee.
  ///
  /// In zh, this message translates to:
  /// **'暖咖'**
  String get themeCoffee;

  /// No description provided for @themeColor.
  ///
  /// In zh, this message translates to:
  /// **'主题色'**
  String get themeColor;

  /// No description provided for @skinTitle.
  ///
  /// In zh, this message translates to:
  /// **'皮肤'**
  String get skinTitle;

  /// No description provided for @skinNameIos.
  ///
  /// In zh, this message translates to:
  /// **'原生态'**
  String get skinNameIos;

  /// No description provided for @skinNameWarm.
  ///
  /// In zh, this message translates to:
  /// **'温柔陪伴'**
  String get skinNameWarm;

  /// No description provided for @skinNameMaterial.
  ///
  /// In zh, this message translates to:
  /// **'Material You'**
  String get skinNameMaterial;

  /// No description provided for @skinNamePaper.
  ///
  /// In zh, this message translates to:
  /// **'纸艺手账'**
  String get skinNamePaper;

  /// No description provided for @skinNameNeon.
  ///
  /// In zh, this message translates to:
  /// **'暗夜霓虹'**
  String get skinNameNeon;

  /// No description provided for @skinNameGlass.
  ///
  /// In zh, this message translates to:
  /// **'极光毛玻璃'**
  String get skinNameGlass;

  /// No description provided for @glassAuroraEnd.
  ///
  /// In zh, this message translates to:
  /// **'终色'**
  String get glassAuroraEnd;

  /// No description provided for @glassAuroraStart.
  ///
  /// In zh, this message translates to:
  /// **'起色'**
  String get glassAuroraStart;

  /// No description provided for @glassBackgroundGradient.
  ///
  /// In zh, this message translates to:
  /// **'渐变'**
  String get glassBackgroundGradient;

  /// No description provided for @glassBackgroundImage.
  ///
  /// In zh, this message translates to:
  /// **'相册图片'**
  String get glassBackgroundImage;

  /// No description provided for @glassBackgroundReset.
  ///
  /// In zh, this message translates to:
  /// **'重置'**
  String get glassBackgroundReset;

  /// No description provided for @glassBackgroundTitle.
  ///
  /// In zh, this message translates to:
  /// **'背景'**
  String get glassBackgroundTitle;

  /// No description provided for @glassBlurLabel.
  ///
  /// In zh, this message translates to:
  /// **'模糊'**
  String get glassBlurLabel;

  /// No description provided for @glassDimHint.
  ///
  /// In zh, this message translates to:
  /// **'文字看不清时调高压暗'**
  String get glassDimHint;

  /// No description provided for @glassDimLabel.
  ///
  /// In zh, this message translates to:
  /// **'压暗'**
  String get glassDimLabel;

  /// No description provided for @glassImagePickFailed.
  ///
  /// In zh, this message translates to:
  /// **'选择背景图片失败'**
  String get glassImagePickFailed;

  /// No description provided for @glassImageSaved.
  ///
  /// In zh, this message translates to:
  /// **'背景图片已保存'**
  String get glassImageSaved;

  /// No description provided for @glassPickImage.
  ///
  /// In zh, this message translates to:
  /// **'选择图片'**
  String get glassPickImage;

  /// No description provided for @themeMode.
  ///
  /// In zh, this message translates to:
  /// **'主题模式'**
  String get themeMode;

  /// No description provided for @themeOcean.
  ///
  /// In zh, this message translates to:
  /// **'海洋'**
  String get themeOcean;

  /// No description provided for @themeStarryNight.
  ///
  /// In zh, this message translates to:
  /// **'经典·星夜'**
  String get themeStarryNight;

  /// No description provided for @themeSunset.
  ///
  /// In zh, this message translates to:
  /// **'日落'**
  String get themeSunset;

  /// No description provided for @themeTitle.
  ///
  /// In zh, this message translates to:
  /// **'主题'**
  String get themeTitle;

  /// No description provided for @thinkAgain.
  ///
  /// In zh, this message translates to:
  /// **'再想想'**
  String get thinkAgain;

  /// No description provided for @timeline.
  ///
  /// In zh, this message translates to:
  /// **'时光'**
  String get timeline;

  /// No description provided for @timelineHint.
  ///
  /// In zh, this message translates to:
  /// **'TA的成长时间线'**
  String get timelineHint;

  /// No description provided for @timelineLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'时光加载失败'**
  String get timelineLoadFailed;

  /// No description provided for @timelineTitle.
  ///
  /// In zh, this message translates to:
  /// **'时光 · {name}'**
  String timelineTitle(Object name);

  /// No description provided for @timerDeleted.
  ///
  /// In zh, this message translates to:
  /// **'已删除该计时'**
  String get timerDeleted;

  /// No description provided for @todo.
  ///
  /// In zh, this message translates to:
  /// **'待办'**
  String get todo;

  /// No description provided for @totalCount.
  ///
  /// In zh, this message translates to:
  /// **'共 {count} 条'**
  String totalCount(Object count);

  /// No description provided for @triggerFilter.
  ///
  /// In zh, this message translates to:
  /// **'触发'**
  String get triggerFilter;

  /// No description provided for @typing.
  ///
  /// In zh, this message translates to:
  /// **'输入中...'**
  String get typing;

  /// No description provided for @unlockMemory.
  ///
  /// In zh, this message translates to:
  /// **'解锁记忆'**
  String get unlockMemory;

  /// No description provided for @unlockedResume.
  ///
  /// In zh, this message translates to:
  /// **'已解锁：恢复自然遗忘'**
  String get unlockedResume;

  /// No description provided for @unnamed.
  ///
  /// In zh, this message translates to:
  /// **'未命名'**
  String get unnamed;

  /// No description provided for @unpinned.
  ///
  /// In zh, this message translates to:
  /// **'已取消收藏'**
  String get unpinned;

  /// No description provided for @updateFailedErr.
  ///
  /// In zh, this message translates to:
  /// **'更新失败: {err}'**
  String updateFailedErr(Object err);

  /// No description provided for @updatedAt.
  ///
  /// In zh, this message translates to:
  /// **'更新于 {time}'**
  String updatedAt(Object time);

  /// No description provided for @upload.
  ///
  /// In zh, this message translates to:
  /// **'上传'**
  String get upload;

  /// No description provided for @uploadFail.
  ///
  /// In zh, this message translates to:
  /// **'上传失败'**
  String get uploadFail;

  /// No description provided for @uploadWallpaper.
  ///
  /// In zh, this message translates to:
  /// **'上传壁纸'**
  String get uploadWallpaper;

  /// No description provided for @uploadedToAlbum.
  ///
  /// In zh, this message translates to:
  /// **'已上传到相册'**
  String get uploadedToAlbum;

  /// No description provided for @userId.
  ///
  /// In zh, this message translates to:
  /// **'用户ID'**
  String get userId;

  /// No description provided for @userPromised.
  ///
  /// In zh, this message translates to:
  /// **'你承诺'**
  String get userPromised;

  /// No description provided for @username.
  ///
  /// In zh, this message translates to:
  /// **'用户名'**
  String get username;

  /// No description provided for @version.
  ///
  /// In zh, this message translates to:
  /// **'拥爱 v3.2.2'**
  String get version;

  /// No description provided for @viewAllComments.
  ///
  /// In zh, this message translates to:
  /// **'查看全部{count}条评论'**
  String viewAllComments(Object count);

  /// No description provided for @virtualPhone.
  ///
  /// In zh, this message translates to:
  /// **'虚拟手机（开发中）'**
  String get virtualPhone;

  /// No description provided for @virtualPhoneDesc.
  ///
  /// In zh, this message translates to:
  /// **'这里未来将承载小手机的完整可操作性：声音、通知、存储管理、应用权限、勿扰模式等，让每个 AI 角色拥有一台真正可操作的虚拟手机。目前仅占位，敬请期待。'**
  String get virtualPhoneDesc;

  /// No description provided for @visit.
  ///
  /// In zh, this message translates to:
  /// **'拜访'**
  String get visit;

  /// No description provided for @visualStateTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name} · 可视化状态'**
  String visualStateTitle(Object name);

  /// No description provided for @visualize.
  ///
  /// In zh, this message translates to:
  /// **'可视化'**
  String get visualize;

  /// No description provided for @voiceBargeInHint.
  ///
  /// In zh, this message translates to:
  /// **'已打断对方'**
  String get voiceBargeInHint;

  /// No description provided for @voiceCallEntry.
  ///
  /// In zh, this message translates to:
  /// **'语音通话'**
  String get voiceCallEntry;

  /// No description provided for @voiceCallEntrySub.
  ///
  /// In zh, this message translates to:
  /// **'实时语音对谈'**
  String get voiceCallEntrySub;

  /// No description provided for @voiceCallFailed.
  ///
  /// In zh, this message translates to:
  /// **'通话失败'**
  String get voiceCallFailed;

  /// No description provided for @voiceCallReady.
  ///
  /// In zh, this message translates to:
  /// **'通话中，请说话'**
  String get voiceCallReady;

  /// No description provided for @voiceCallTitle.
  ///
  /// In zh, this message translates to:
  /// **'语音通话'**
  String get voiceCallTitle;

  /// No description provided for @voiceCalling.
  ///
  /// In zh, this message translates to:
  /// **'正在接通…'**
  String get voiceCalling;

  /// No description provided for @voiceDisconnected.
  ///
  /// In zh, this message translates to:
  /// **'连接已断开'**
  String get voiceDisconnected;

  /// No description provided for @voiceEndCall.
  ///
  /// In zh, this message translates to:
  /// **'挂断'**
  String get voiceEndCall;

  /// No description provided for @voiceHoldToTalk.
  ///
  /// In zh, this message translates to:
  /// **'按住说话'**
  String get voiceHoldToTalk;

  /// No description provided for @voiceInterrupt.
  ///
  /// In zh, this message translates to:
  /// **'打断'**
  String get voiceInterrupt;

  /// No description provided for @voiceMicPermission.
  ///
  /// In zh, this message translates to:
  /// **'需要麦克风权限'**
  String get voiceMicPermission;

  /// No description provided for @voiceNotHeard.
  ///
  /// In zh, this message translates to:
  /// **'没听清，再说一遍'**
  String get voiceNotHeard;

  /// No description provided for @voiceRecording.
  ///
  /// In zh, this message translates to:
  /// **'说话中'**
  String get voiceRecording;

  /// No description provided for @voiceSpeaking.
  ///
  /// In zh, this message translates to:
  /// **'对方正在说话…'**
  String get voiceSpeaking;

  /// No description provided for @voiceVadOff.
  ///
  /// In zh, this message translates to:
  /// **'自动聆听'**
  String get voiceVadOff;

  /// No description provided for @voiceVadOn.
  ///
  /// In zh, this message translates to:
  /// **'自动聆听已开启'**
  String get voiceVadOn;

  /// No description provided for @voiceRetryMsg.
  ///
  /// In zh, this message translates to:
  /// **'网络好像不太稳定，要重试发送吗？（录音已保留）'**
  String get voiceRetryMsg;

  /// No description provided for @voiceSendFailed.
  ///
  /// In zh, this message translates to:
  /// **'语音发送失败'**
  String get voiceSendFailed;

  /// No description provided for @wallpaper.
  ///
  /// In zh, this message translates to:
  /// **'壁纸'**
  String get wallpaper;

  /// No description provided for @wallpaperChanged.
  ///
  /// In zh, this message translates to:
  /// **'壁纸已更换'**
  String get wallpaperChanged;

  /// No description provided for @weaveFullInject.
  ///
  /// In zh, this message translates to:
  /// **'全注入对话'**
  String get weaveFullInject;

  /// No description provided for @weaveFullInjectHint.
  ///
  /// In zh, this message translates to:
  /// **'开启后每次对话注入织库卡片，记忆更完整（token 消耗更高）'**
  String get weaveFullInjectHint;

  /// No description provided for @weekOverview.
  ///
  /// In zh, this message translates to:
  /// **'近 7 天概览'**
  String get weekOverview;

  /// No description provided for @weekday1.
  ///
  /// In zh, this message translates to:
  /// **'一'**
  String get weekday1;

  /// No description provided for @weekday2.
  ///
  /// In zh, this message translates to:
  /// **'二'**
  String get weekday2;

  /// No description provided for @weekday3.
  ///
  /// In zh, this message translates to:
  /// **'三'**
  String get weekday3;

  /// No description provided for @weekday4.
  ///
  /// In zh, this message translates to:
  /// **'四'**
  String get weekday4;

  /// No description provided for @weekday5.
  ///
  /// In zh, this message translates to:
  /// **'五'**
  String get weekday5;

  /// No description provided for @weekday6.
  ///
  /// In zh, this message translates to:
  /// **'六'**
  String get weekday6;

  /// No description provided for @weekday7.
  ///
  /// In zh, this message translates to:
  /// **'日'**
  String get weekday7;

  /// No description provided for @weekdayFri.
  ///
  /// In zh, this message translates to:
  /// **'星期五'**
  String get weekdayFri;

  /// No description provided for @weekdayMon.
  ///
  /// In zh, this message translates to:
  /// **'星期一'**
  String get weekdayMon;

  /// No description provided for @weekdaySat.
  ///
  /// In zh, this message translates to:
  /// **'星期六'**
  String get weekdaySat;

  /// No description provided for @weekdaySun.
  ///
  /// In zh, this message translates to:
  /// **'星期日'**
  String get weekdaySun;

  /// No description provided for @weekdayThu.
  ///
  /// In zh, this message translates to:
  /// **'星期四'**
  String get weekdayThu;

  /// No description provided for @weekdayTue.
  ///
  /// In zh, this message translates to:
  /// **'星期二'**
  String get weekdayTue;

  /// No description provided for @weekdayWed.
  ///
  /// In zh, this message translates to:
  /// **'星期三'**
  String get weekdayWed;

  /// No description provided for @weight.
  ///
  /// In zh, this message translates to:
  /// **'体重'**
  String get weight;

  /// No description provided for @whyMatters.
  ///
  /// In zh, this message translates to:
  /// **'意义：{why}'**
  String whyMatters(Object why);

  /// No description provided for @writeTodayDiary.
  ///
  /// In zh, this message translates to:
  /// **'写今天的日记'**
  String get writeTodayDiary;

  /// No description provided for @yearCountTotal.
  ///
  /// In zh, this message translates to:
  /// **'共 {count} 条记录'**
  String yearCountTotal(Object count);

  /// No description provided for @yearLabel.
  ///
  /// In zh, this message translates to:
  /// **'{year}年'**
  String yearLabel(Object year);

  /// No description provided for @yesterday.
  ///
  /// In zh, this message translates to:
  /// **'昨天'**
  String get yesterday;

  /// No description provided for @you.
  ///
  /// In zh, this message translates to:
  /// **'你'**
  String get you;

  /// No description provided for @invalidLink.
  ///
  /// In zh, this message translates to:
  /// **'链接无效'**
  String get invalidLink;

  /// No description provided for @openFailedManual.
  ///
  /// In zh, this message translates to:
  /// **'打开失败，可复制链接手动打开'**
  String get openFailedManual;

  /// No description provided for @supportIntro.
  ///
  /// In zh, this message translates to:
  /// **'如果这个应用给你带来了陪伴，欢迎请作者喝杯咖啡 ☕'**
  String get supportIntro;

  /// No description provided for @wechatReward.
  ///
  /// In zh, this message translates to:
  /// **'微信赞赏'**
  String get wechatReward;

  /// No description provided for @donateSupport.
  ///
  /// In zh, this message translates to:
  /// **'打赏支持'**
  String get donateSupport;

  /// No description provided for @donateOpenPage.
  ///
  /// In zh, this message translates to:
  /// **'打开主页支持作者'**
  String get donateOpenPage;

  /// No description provided for @donateNotOpen.
  ///
  /// In zh, this message translates to:
  /// **'作者暂未开启打赏渠道'**
  String get donateNotOpen;

  /// No description provided for @goSupport.
  ///
  /// In zh, this message translates to:
  /// **'去支持'**
  String get goSupport;

  /// No description provided for @notOpened.
  ///
  /// In zh, this message translates to:
  /// **'未开启'**
  String get notOpened;

  /// No description provided for @followDouyin.
  ///
  /// In zh, this message translates to:
  /// **'关注抖音'**
  String get followDouyin;

  /// No description provided for @douyinId.
  ///
  /// In zh, this message translates to:
  /// **'抖音号：{id}'**
  String douyinId(Object id);

  /// No description provided for @joinQQGroup.
  ///
  /// In zh, this message translates to:
  /// **'加入 QQ 群'**
  String get joinQQGroup;

  /// No description provided for @qqGroup.
  ///
  /// In zh, this message translates to:
  /// **'群号：{id}'**
  String qqGroup(Object id);

  /// No description provided for @supportFooter.
  ///
  /// In zh, this message translates to:
  /// **'打赏与关注纯属自愿，感谢你的支持 ❤️'**
  String get supportFooter;

  /// No description provided for @dndSaved.
  ///
  /// In zh, this message translates to:
  /// **'已保存免打扰设置'**
  String get dndSaved;

  /// No description provided for @dndSettings.
  ///
  /// In zh, this message translates to:
  /// **'免打扰设置'**
  String get dndSettings;

  /// No description provided for @notificationSection.
  ///
  /// In zh, this message translates to:
  /// **'通知'**
  String get notificationSection;

  /// No description provided for @messageNotifications.
  ///
  /// In zh, this message translates to:
  /// **'消息通知'**
  String get messageNotifications;

  /// No description provided for @msgNotifOnSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'AI好友新消息将弹横幅与系统通知'**
  String get msgNotifOnSubtitle;

  /// No description provided for @msgNotifOffSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'关闭后横幅与系统通知都不弹（红点仍更新）'**
  String get msgNotifOffSubtitle;

  /// No description provided for @enableDnd.
  ///
  /// In zh, this message translates to:
  /// **'启用免打扰'**
  String get enableDnd;

  /// No description provided for @dndOnSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'在设定时段内不推送通知'**
  String get dndOnSubtitle;

  /// No description provided for @dndOffSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'通知将正常推送'**
  String get dndOffSubtitle;

  /// No description provided for @dndStartLabel.
  ///
  /// In zh, this message translates to:
  /// **'开始时间'**
  String get dndStartLabel;

  /// No description provided for @dndEndLabel.
  ///
  /// In zh, this message translates to:
  /// **'结束时间'**
  String get dndEndLabel;

  /// No description provided for @dndStartAction.
  ///
  /// In zh, this message translates to:
  /// **'开始'**
  String get dndStartAction;

  /// No description provided for @dndEndAction.
  ///
  /// In zh, this message translates to:
  /// **'结束'**
  String get dndEndAction;

  /// No description provided for @dndNote.
  ///
  /// In zh, this message translates to:
  /// **'免打扰时段内，AI好友将不会推送新消息通知。\n例如: 22:00 ~ 08:00 适合夜间休息时段。'**
  String get dndNote;

  /// No description provided for @dyMemoryTitle.
  ///
  /// In zh, this message translates to:
  /// **'抖音记忆收紧'**
  String get dyMemoryTitle;

  /// No description provided for @dyMemoryOnSave.
  ///
  /// In zh, this message translates to:
  /// **'已开启：排除关系类私密记忆'**
  String get dyMemoryOnSave;

  /// No description provided for @dyMemoryOffSave.
  ///
  /// In zh, this message translates to:
  /// **'已关闭：按现状筛选记忆'**
  String get dyMemoryOffSave;

  /// No description provided for @dyMemorySaveFailed.
  ///
  /// In zh, this message translates to:
  /// **'保存失败：{err}'**
  String dyMemorySaveFailed(Object err);

  /// No description provided for @dyMemorySection.
  ///
  /// In zh, this message translates to:
  /// **'公开记忆注入'**
  String get dyMemorySection;

  /// No description provided for @dyMemorySwitchTitle.
  ///
  /// In zh, this message translates to:
  /// **'收紧私密记忆'**
  String get dyMemorySwitchTitle;

  /// No description provided for @dyMemorySwitchSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'开启后，抖音图文创作与评论回复不再注入「关系类」记忆（表白/金钱等无姓名但私密的内容）'**
  String get dyMemorySwitchSubtitle;

  /// No description provided for @dyMemoryNote.
  ///
  /// In zh, this message translates to:
  /// **'说明：无论开关状态，抖音都永不注入「身份画像」与含用户姓名的记忆。开启「收紧」后，关系类记忆（如表白、亲密互动、金钱往来）也会被排除，适合对外更谨慎的场景。'**
  String get dyMemoryNote;

  /// No description provided for @updateAnnouncement.
  ///
  /// In zh, this message translates to:
  /// **'更新公告'**
  String get updateAnnouncement;

  /// No description provided for @noUpdates.
  ///
  /// In zh, this message translates to:
  /// **'暂无更新记录'**
  String get noUpdates;

  /// No description provided for @updateNoDetail.
  ///
  /// In zh, this message translates to:
  /// **'（无明细）'**
  String get updateNoDetail;

  /// No description provided for @updateReason.
  ///
  /// In zh, this message translates to:
  /// **'原因：{reason}'**
  String updateReason(Object reason);

  /// No description provided for @copiedText.
  ///
  /// In zh, this message translates to:
  /// **'已复制：{text}'**
  String copiedText(Object text);

  /// No description provided for @permTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 能力权限'**
  String get permTitle;

  /// No description provided for @permGlobalDefault.
  ///
  /// In zh, this message translates to:
  /// **'全局默认'**
  String get permGlobalDefault;

  /// No description provided for @permGlobalDefaultHint.
  ///
  /// In zh, this message translates to:
  /// **'所有能力的默认档位；未单独设置的能力跟随全局默认'**
  String get permGlobalDefaultHint;

  /// No description provided for @permScopes.
  ///
  /// In zh, this message translates to:
  /// **'各能力'**
  String get permScopes;

  /// No description provided for @permAskNote.
  ///
  /// In zh, this message translates to:
  /// **'「每次询问」：AI 调用该能力前会先征求你的同意（目前生图支持询问交互，其余能力询问时暂不执行）。'**
  String get permAskNote;

  /// No description provided for @permSaveFailed.
  ///
  /// In zh, this message translates to:
  /// **'保存失败，请重试'**
  String get permSaveFailed;

  /// No description provided for @permLevelAllow.
  ///
  /// In zh, this message translates to:
  /// **'允许'**
  String get permLevelAllow;

  /// No description provided for @permLevelAsk.
  ///
  /// In zh, this message translates to:
  /// **'每次询问'**
  String get permLevelAsk;

  /// No description provided for @permLevelForbid.
  ///
  /// In zh, this message translates to:
  /// **'禁止'**
  String get permLevelForbid;

  /// No description provided for @permScopeImgTitle.
  ///
  /// In zh, this message translates to:
  /// **'生图'**
  String get permScopeImgTitle;

  /// No description provided for @permScopeImgDesc.
  ///
  /// In zh, this message translates to:
  /// **'AI 生成图片发给你（聊天内发图/主动生图）'**
  String get permScopeImgDesc;

  /// No description provided for @permScopeImgUnderstandTitle.
  ///
  /// In zh, this message translates to:
  /// **'识图'**
  String get permScopeImgUnderstandTitle;

  /// No description provided for @permScopeImgUnderstandDesc.
  ///
  /// In zh, this message translates to:
  /// **'AI 理解你发来的图片内容（本地识图）'**
  String get permScopeImgUnderstandDesc;

  /// No description provided for @permScopeTtsTitle.
  ///
  /// In zh, this message translates to:
  /// **'语音回复'**
  String get permScopeTtsTitle;

  /// No description provided for @permScopeTtsDesc.
  ///
  /// In zh, this message translates to:
  /// **'AI 用语音回复你（TTS 合成）'**
  String get permScopeTtsDesc;

  /// No description provided for @permScopeAsrTitle.
  ///
  /// In zh, this message translates to:
  /// **'语音转写'**
  String get permScopeAsrTitle;

  /// No description provided for @permScopeAsrDesc.
  ///
  /// In zh, this message translates to:
  /// **'转写你的语音消息（ASR 识别）'**
  String get permScopeAsrDesc;

  /// No description provided for @permScopeBrowserTitle.
  ///
  /// In zh, this message translates to:
  /// **'浏览器'**
  String get permScopeBrowserTitle;

  /// No description provided for @permScopeBrowserDesc.
  ///
  /// In zh, this message translates to:
  /// **'浏览器扩展：AI 搜索网页、读取页面'**
  String get permScopeBrowserDesc;

  /// No description provided for @permScopeDouyinTitle.
  ///
  /// In zh, this message translates to:
  /// **'抖音'**
  String get permScopeDouyinTitle;

  /// No description provided for @permScopeDouyinDesc.
  ///
  /// In zh, this message translates to:
  /// **'抖音扩展：发布图文、回复评论'**
  String get permScopeDouyinDesc;

  /// No description provided for @permScopeExtensionTitle.
  ///
  /// In zh, this message translates to:
  /// **'扩展'**
  String get permScopeExtensionTitle;

  /// No description provided for @permScopeExtensionDesc.
  ///
  /// In zh, this message translates to:
  /// **'其他扩展/插件的能力调用'**
  String get permScopeExtensionDesc;

  /// No description provided for @dyApprovalsTitle.
  ///
  /// In zh, this message translates to:
  /// **'抖音批准请求'**
  String get dyApprovalsTitle;

  /// No description provided for @dyApprovalsAiCreate.
  ///
  /// In zh, this message translates to:
  /// **'AI 创作'**
  String get dyApprovalsAiCreate;

  /// No description provided for @dyApprovalsEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无待批准的抖音内容'**
  String get dyApprovalsEmpty;

  /// No description provided for @dyApprovalsEmptyDraft.
  ///
  /// In zh, this message translates to:
  /// **'暂无待批准的草稿'**
  String get dyApprovalsEmptyDraft;

  /// No description provided for @dyApprovalsMemorySection.
  ///
  /// In zh, this message translates to:
  /// **'记忆'**
  String get dyApprovalsMemorySection;

  /// No description provided for @dyApprovalsRestrictHint.
  ///
  /// In zh, this message translates to:
  /// **'公开平台记忆注入时排除关系类私密记忆'**
  String get dyApprovalsRestrictHint;

  /// No description provided for @dyApprovalsRestrictFailed.
  ///
  /// In zh, this message translates to:
  /// **'记忆收紧设置失败：{err}'**
  String dyApprovalsRestrictFailed(Object err);

  /// No description provided for @dyApprovalsPromptHint.
  ///
  /// In zh, this message translates to:
  /// **'写点灵感或提示词（可留空，AI 会以自己的想法创作）'**
  String get dyApprovalsPromptHint;

  /// No description provided for @dyApprovalsPromptExample.
  ///
  /// In zh, this message translates to:
  /// **'例如：发一条表达你最近想法的图文…'**
  String get dyApprovalsPromptExample;

  /// No description provided for @dyApprovalsGenPost.
  ///
  /// In zh, this message translates to:
  /// **'生成图文'**
  String get dyApprovalsGenPost;

  /// No description provided for @dyApprovalsGenReply.
  ///
  /// In zh, this message translates to:
  /// **'生成回复'**
  String get dyApprovalsGenReply;

  /// No description provided for @dyApprovalsDraftCreated.
  ///
  /// In zh, this message translates to:
  /// **'已生成草稿'**
  String get dyApprovalsDraftCreated;

  /// No description provided for @dyApprovalsGenFailed.
  ///
  /// In zh, this message translates to:
  /// **'生成失败: {err}'**
  String dyApprovalsGenFailed(Object err);

  /// No description provided for @dyApprovalsConfirmed.
  ///
  /// In zh, this message translates to:
  /// **'已确认'**
  String get dyApprovalsConfirmed;

  /// No description provided for @dyApprovalsConfirmFailed.
  ///
  /// In zh, this message translates to:
  /// **'确认失败: {err}'**
  String dyApprovalsConfirmFailed(Object err);

  /// No description provided for @dyApprovalsRejected.
  ///
  /// In zh, this message translates to:
  /// **'已拒绝'**
  String get dyApprovalsRejected;

  /// No description provided for @dyApprovalsRejectFailed.
  ///
  /// In zh, this message translates to:
  /// **'拒绝失败: {err}'**
  String dyApprovalsRejectFailed(Object err);

  /// No description provided for @dyApprovalsImageUploaded.
  ///
  /// In zh, this message translates to:
  /// **'图片已上传'**
  String get dyApprovalsImageUploaded;

  /// No description provided for @dyApprovalsUploadFailed.
  ///
  /// In zh, this message translates to:
  /// **'上传失败: {err}'**
  String dyApprovalsUploadFailed(Object err);

  /// No description provided for @dyApprovalsCountdown.
  ///
  /// In zh, this message translates to:
  /// **'发布倒计时（{count}）'**
  String dyApprovalsCountdown(Object count);

  /// No description provided for @dyApprovalsCountdownHint.
  ///
  /// In zh, this message translates to:
  /// **'已确认，将在随机时间发布/回复，避开深夜静默'**
  String get dyApprovalsCountdownHint;

  /// No description provided for @dyKindImage.
  ///
  /// In zh, this message translates to:
  /// **'图文'**
  String get dyKindImage;

  /// No description provided for @dyKindReply.
  ///
  /// In zh, this message translates to:
  /// **'回复'**
  String get dyKindReply;

  /// No description provided for @dyApprovalsReplyTo.
  ///
  /// In zh, this message translates to:
  /// **'回复 {commenter}：{content}'**
  String dyApprovalsReplyTo(Object commenter, Object content);

  /// No description provided for @dyApprovalsPublishing.
  ///
  /// In zh, this message translates to:
  /// **'正在发布…'**
  String get dyApprovalsPublishing;

  /// No description provided for @dyApprovalsSoon.
  ///
  /// In zh, this message translates to:
  /// **'即将发布'**
  String get dyApprovalsSoon;

  /// No description provided for @dyApprovalsHourMin.
  ///
  /// In zh, this message translates to:
  /// **'{h} 小时 {m} 分'**
  String dyApprovalsHourMin(Object h, Object m);

  /// No description provided for @dyApprovalsMinSec.
  ///
  /// In zh, this message translates to:
  /// **'{m} 分 {s} 秒'**
  String dyApprovalsMinSec(Object m, Object s);

  /// No description provided for @dyApprovalsSec.
  ///
  /// In zh, this message translates to:
  /// **'{s} 秒'**
  String dyApprovalsSec(Object s);

  /// No description provided for @dyApprovalsKindPost.
  ///
  /// In zh, this message translates to:
  /// **'图文发布'**
  String get dyApprovalsKindPost;

  /// No description provided for @dyApprovalsKindReplyComment.
  ///
  /// In zh, this message translates to:
  /// **'回复评论'**
  String get dyApprovalsKindReplyComment;

  /// No description provided for @dyApprovalsFan.
  ///
  /// In zh, this message translates to:
  /// **'粉丝'**
  String get dyApprovalsFan;

  /// No description provided for @dyApprovalsNotFan.
  ///
  /// In zh, this message translates to:
  /// **'非粉丝'**
  String get dyApprovalsNotFan;

  /// No description provided for @dyApprovalsNoImage.
  ///
  /// In zh, this message translates to:
  /// **'未配图（发布时抖音自动生成配图）'**
  String get dyApprovalsNoImage;

  /// No description provided for @dyApprovalsImageCount.
  ///
  /// In zh, this message translates to:
  /// **'图片 {n} 张'**
  String dyApprovalsImageCount(Object n);

  /// No description provided for @dyApprovalsChooseImage.
  ///
  /// In zh, this message translates to:
  /// **'选择图片'**
  String get dyApprovalsChooseImage;

  /// No description provided for @dyApprovalsConfirmBtn.
  ///
  /// In zh, this message translates to:
  /// **'确认（随机时间发布）'**
  String get dyApprovalsConfirmBtn;

  /// No description provided for @dyApprovalsRejectBtn.
  ///
  /// In zh, this message translates to:
  /// **'拒绝'**
  String get dyApprovalsRejectBtn;

  /// No description provided for @aiFriendTitle.
  ///
  /// In zh, this message translates to:
  /// **'AI 好友'**
  String get aiFriendTitle;

  /// No description provided for @searchAiFriend.
  ///
  /// In zh, this message translates to:
  /// **'搜索 AI 好友'**
  String get searchAiFriend;

  /// No description provided for @familyGroupChat.
  ///
  /// In zh, this message translates to:
  /// **'家庭群聊'**
  String get familyGroupChat;

  /// No description provided for @familyGroupHint.
  ///
  /// In zh, this message translates to:
  /// **'和你的 AI 角色们一起聊天'**
  String get familyGroupHint;

  /// No description provided for @noMatchingFriend.
  ///
  /// In zh, this message translates to:
  /// **'没有匹配的好友'**
  String get noMatchingFriend;

  /// No description provided for @noAiFriend.
  ///
  /// In zh, this message translates to:
  /// **'还没有AI好友，点击右下角按钮创建'**
  String get noAiFriend;

  /// No description provided for @charListLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载失败: '**
  String get charListLoadFailed;

  /// No description provided for @charArchiveTitle.
  ///
  /// In zh, this message translates to:
  /// **'{name}的聊天记录'**
  String charArchiveTitle(Object name);

  /// No description provided for @noChatHistory.
  ///
  /// In zh, this message translates to:
  /// **'暂无聊天记录'**
  String get noChatHistory;

  /// No description provided for @archiveMsgCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 条消息'**
  String archiveMsgCount(Object count);

  /// No description provided for @archiveCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 条'**
  String archiveCount(Object count);

  /// No description provided for @privacyApproved.
  ///
  /// In zh, this message translates to:
  /// **'TA 同意了'**
  String get privacyApproved;

  /// No description provided for @privacyLater.
  ///
  /// In zh, this message translates to:
  /// **'稍后再看'**
  String get privacyLater;

  /// No description provided for @privacyView.
  ///
  /// In zh, this message translates to:
  /// **'查看'**
  String get privacyView;

  /// No description provided for @privacyRejected.
  ///
  /// In zh, this message translates to:
  /// **'TA 拒绝了'**
  String get privacyRejected;

  /// No description provided for @privacyGotIt.
  ///
  /// In zh, this message translates to:
  /// **'知道了'**
  String get privacyGotIt;

  /// No description provided for @privacyTooFrequent.
  ///
  /// In zh, this message translates to:
  /// **'申请太频繁啦，2 分钟后再试试'**
  String get privacyTooFrequent;

  /// No description provided for @privacyApplyFailed.
  ///
  /// In zh, this message translates to:
  /// **'申请失败，请稍后再试'**
  String get privacyApplyFailed;

  /// No description provided for @privacyLockedBy.
  ///
  /// In zh, this message translates to:
  /// **'TA 把{content}锁起来了'**
  String privacyLockedBy(Object content);

  /// No description provided for @privacyApplyHint.
  ///
  /// In zh, this message translates to:
  /// **'想看看就向 TA 申请吧'**
  String get privacyApplyHint;

  /// No description provided for @privacyCooldown.
  ///
  /// In zh, this message translates to:
  /// **'申请冷却中 {seconds} 秒'**
  String privacyCooldown(Object seconds);

  /// No description provided for @privacyApplying.
  ///
  /// In zh, this message translates to:
  /// **'申请中…'**
  String get privacyApplying;

  /// No description provided for @privacyApplyButton.
  ///
  /// In zh, this message translates to:
  /// **'向 TA 申请查看'**
  String get privacyApplyButton;

  /// No description provided for @privacyRefreshStatus.
  ///
  /// In zh, this message translates to:
  /// **'刷新状态'**
  String get privacyRefreshStatus;

  /// No description provided for @msgFileExpired.
  ///
  /// In zh, this message translates to:
  /// **'文件已过期（保留 5 天后自动清理）'**
  String get msgFileExpired;

  /// No description provided for @msgFileSizeExpired.
  ///
  /// In zh, this message translates to:
  /// **'{size} · 已过期'**
  String msgFileSizeExpired(Object size);

  /// No description provided for @voice.
  ///
  /// In zh, this message translates to:
  /// **'语音'**
  String get voice;

  /// No description provided for @voiceReply.
  ///
  /// In zh, this message translates to:
  /// **'语音回复'**
  String get voiceReply;

  /// No description provided for @thinkingProcess.
  ///
  /// In zh, this message translates to:
  /// **'思考过程'**
  String get thinkingProcess;

  /// No description provided for @calledAbility.
  ///
  /// In zh, this message translates to:
  /// **'调用能力'**
  String get calledAbility;

  /// No description provided for @imageLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'图片加载失败'**
  String get imageLoadFailed;

  /// No description provided for @continueLabel.
  ///
  /// In zh, this message translates to:
  /// **'继续'**
  String get continueLabel;

  /// No description provided for @quoteDeleted.
  ///
  /// In zh, this message translates to:
  /// **'原消息已删除'**
  String get quoteDeleted;

  /// No description provided for @playFailed.
  ///
  /// In zh, this message translates to:
  /// **'播放失败'**
  String get playFailed;

  /// No description provided for @msgQuoteLine.
  ///
  /// In zh, this message translates to:
  /// **'{sender}：{content}'**
  String msgQuoteLine(Object content, Object sender);

  /// No description provided for @weaveLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'画布加载失败，请重试'**
  String get weaveLoadFail;

  /// No description provided for @weaveDetailLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'详情加载失败'**
  String get weaveDetailLoadFail;

  /// No description provided for @weaveFallback2D.
  ///
  /// In zh, this message translates to:
  /// **'已自动切换到 2.5D 视图以提升流畅度'**
  String get weaveFallback2D;

  /// No description provided for @weaveFallback2DRenderError.
  ///
  /// In zh, this message translates to:
  /// **'已自动切换到 2.5D（3D 渲染异常，已反馈定位）'**
  String get weaveFallback2DRenderError;

  /// No description provided for @weaveFallback2DLowFps.
  ///
  /// In zh, this message translates to:
  /// **'已自动切换到 2.5D（检测到持续低帧率）'**
  String get weaveFallback2DLowFps;

  /// No description provided for @weaveFallback2DNodeLimit.
  ///
  /// In zh, this message translates to:
  /// **'已自动切换到 2.5D（当前节点数较多）'**
  String get weaveFallback2DNodeLimit;

  /// No description provided for @weaveSwitchedToLight.
  ///
  /// In zh, this message translates to:
  /// **'已切换轻量模式（3D 简化渲染）'**
  String get weaveSwitchedToLight;

  /// No description provided for @weaveCanvasTitle.
  ///
  /// In zh, this message translates to:
  /// **'织库画布'**
  String get weaveCanvasTitle;

  /// No description provided for @weaveModeAuto.
  ///
  /// In zh, this message translates to:
  /// **'全自动'**
  String get weaveModeAuto;

  /// No description provided for @weaveModeFull3D.
  ///
  /// In zh, this message translates to:
  /// **'3D 全量'**
  String get weaveModeFull3D;

  /// No description provided for @weaveModeLight3D.
  ///
  /// In zh, this message translates to:
  /// **'3D 轻量'**
  String get weaveModeLight3D;

  /// No description provided for @weaveMode2D.
  ///
  /// In zh, this message translates to:
  /// **'2.5D'**
  String get weaveMode2D;

  /// No description provided for @weaveNoCards.
  ///
  /// In zh, this message translates to:
  /// **'还没有卡片，先去列表页整理生成吧'**
  String get weaveNoCards;

  /// No description provided for @weaveNear7Days.
  ///
  /// In zh, this message translates to:
  /// **'近7天'**
  String get weaveNear7Days;

  /// No description provided for @weaveNear30Days.
  ///
  /// In zh, this message translates to:
  /// **'近30天'**
  String get weaveNear30Days;

  /// No description provided for @weaveAllCharacters.
  ///
  /// In zh, this message translates to:
  /// **'全部角色'**
  String get weaveAllCharacters;

  /// No description provided for @weaveAllMoods.
  ///
  /// In zh, this message translates to:
  /// **'全部心情'**
  String get weaveAllMoods;

  /// No description provided for @weaveAllTypes.
  ///
  /// In zh, this message translates to:
  /// **'全部类型'**
  String get weaveAllTypes;

  /// No description provided for @weaveCardsLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'加载失败，请重试'**
  String get weaveCardsLoadFail;

  /// No description provided for @weaveDone.
  ///
  /// In zh, this message translates to:
  /// **'已织好 {created} 张卡片'**
  String weaveDone(int created);

  /// No description provided for @weaveNoNewMemory.
  ///
  /// In zh, this message translates to:
  /// **'没有新的可整理记忆'**
  String get weaveNoNewMemory;

  /// No description provided for @weaveGenerateFail.
  ///
  /// In zh, this message translates to:
  /// **'整理失败，请稍后重试'**
  String get weaveGenerateFail;

  /// No description provided for @weaveNetworkFail.
  ///
  /// In zh, this message translates to:
  /// **'网络请求失败（{type}）'**
  String weaveNetworkFail(String type);

  /// No description provided for @weaveNoDuplicates.
  ///
  /// In zh, this message translates to:
  /// **'未发现重复卡片'**
  String get weaveNoDuplicates;

  /// No description provided for @weaveDedupCheckFail.
  ///
  /// In zh, this message translates to:
  /// **'查重失败：{err}'**
  String weaveDedupCheckFail(String err);

  /// No description provided for @weaveDedup.
  ///
  /// In zh, this message translates to:
  /// **'去重'**
  String get weaveDedup;

  /// No description provided for @weaveDedupConfirm.
  ///
  /// In zh, this message translates to:
  /// **'每组重复卡片将保留信息最全的一张，其余删除（参与记忆会合并，原始记忆不受影响）。确定执行吗？'**
  String get weaveDedupConfirm;

  /// No description provided for @weaveExecuteDedup.
  ///
  /// In zh, this message translates to:
  /// **'执行去重'**
  String get weaveExecuteDedup;

  /// No description provided for @weaveDedupMerged.
  ///
  /// In zh, this message translates to:
  /// **'已合并 {groups} 组，删除 {removed} 张重复卡片'**
  String weaveDedupMerged(int groups, int removed);

  /// No description provided for @weaveDedupFail.
  ///
  /// In zh, this message translates to:
  /// **'去重失败：{err}'**
  String weaveDedupFail(String err);

  /// No description provided for @weaveDeleteCard.
  ///
  /// In zh, this message translates to:
  /// **'删除卡片'**
  String get weaveDeleteCard;

  /// No description provided for @weaveDeleteCardConfirm.
  ///
  /// In zh, this message translates to:
  /// **'仅删除织库卡片，不影响原始记忆。确定删除吗？'**
  String get weaveDeleteCardConfirm;

  /// No description provided for @weaveLibraryTitle.
  ///
  /// In zh, this message translates to:
  /// **'织库'**
  String get weaveLibraryTitle;

  /// No description provided for @weaveOrganizeGenerate.
  ///
  /// In zh, this message translates to:
  /// **'整理生成'**
  String get weaveOrganizeGenerate;

  /// No description provided for @weaveCanvas.
  ///
  /// In zh, this message translates to:
  /// **'画布'**
  String get weaveCanvas;

  /// No description provided for @weaveAllDomain.
  ///
  /// In zh, this message translates to:
  /// **'全·织库'**
  String get weaveAllDomain;

  /// No description provided for @weavePrivateDomain.
  ///
  /// In zh, this message translates to:
  /// **'私·织库'**
  String get weavePrivateDomain;

  /// No description provided for @weaveCheckDup.
  ///
  /// In zh, this message translates to:
  /// **'查重'**
  String get weaveCheckDup;

  /// No description provided for @weaveCardCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 张卡片'**
  String weaveCardCount(int count);

  /// No description provided for @weaveNoMemoryCards.
  ///
  /// In zh, this message translates to:
  /// **'还没有织好的记忆卡片'**
  String get weaveNoMemoryCards;

  /// No description provided for @weaveTapTopRightGenerate.
  ///
  /// In zh, this message translates to:
  /// **'点右上角 ✨ 整理生成'**
  String get weaveTapTopRightGenerate;

  /// No description provided for @weaveDedupResult.
  ///
  /// In zh, this message translates to:
  /// **'查重结果：{groups} 组重复，将合并 {total} 张'**
  String weaveDedupResult(int groups, int total);

  /// No description provided for @weaveDedupResultDesc.
  ///
  /// In zh, this message translates to:
  /// **'每组保留信息最全的一张，重复卡片合并后删除（原始记忆不受影响）'**
  String get weaveDedupResultDesc;

  /// No description provided for @weaveKeepTitle.
  ///
  /// In zh, this message translates to:
  /// **'保留：{title}（{count} 条记忆）'**
  String weaveKeepTitle(String title, int count);

  /// No description provided for @weaveMergeTitle.
  ///
  /// In zh, this message translates to:
  /// **'合并：{title}（{count} 条记忆）'**
  String weaveMergeTitle(String title, int count);

  /// No description provided for @sheetTime.
  ///
  /// In zh, this message translates to:
  /// **'时间'**
  String get sheetTime;

  /// No description provided for @sheetWeather.
  ///
  /// In zh, this message translates to:
  /// **'天气'**
  String get sheetWeather;

  /// No description provided for @sheetLocation.
  ///
  /// In zh, this message translates to:
  /// **'地点'**
  String get sheetLocation;

  /// No description provided for @sheetDetails.
  ///
  /// In zh, this message translates to:
  /// **'细节'**
  String get sheetDetails;

  /// No description provided for @sheetParticipatingMemories.
  ///
  /// In zh, this message translates to:
  /// **'参与记忆'**
  String get sheetParticipatingMemories;

  /// No description provided for @workflowEdgeFail.
  ///
  /// In zh, this message translates to:
  /// **'失败'**
  String get workflowEdgeFail;

  /// No description provided for @workflowEdgeAlways.
  ///
  /// In zh, this message translates to:
  /// **'始终'**
  String get workflowEdgeAlways;

  /// No description provided for @workflowEdgeScreenHas.
  ///
  /// In zh, this message translates to:
  /// **'屏幕有「{target}」'**
  String workflowEdgeScreenHas(String target);

  /// No description provided for @workflowEdgeScreenEmpty.
  ///
  /// In zh, this message translates to:
  /// **'屏幕无「{target}」'**
  String workflowEdgeScreenEmpty(String target);

  /// No description provided for @workflowEdgeSuccess.
  ///
  /// In zh, this message translates to:
  /// **'成功'**
  String get workflowEdgeSuccess;

  /// No description provided for @workflowEdgeWhenSuccess.
  ///
  /// In zh, this message translates to:
  /// **'成功时走'**
  String get workflowEdgeWhenSuccess;

  /// No description provided for @workflowEdgeWhenFail.
  ///
  /// In zh, this message translates to:
  /// **'失败时走'**
  String get workflowEdgeWhenFail;

  /// No description provided for @workflowEdgeWhenAlways.
  ///
  /// In zh, this message translates to:
  /// **'始终走'**
  String get workflowEdgeWhenAlways;

  /// No description provided for @workflowEdgeHasText.
  ///
  /// In zh, this message translates to:
  /// **'屏幕有这些文字'**
  String get workflowEdgeHasText;

  /// No description provided for @workflowEdgeNoText.
  ///
  /// In zh, this message translates to:
  /// **'屏幕没有这些文字'**
  String get workflowEdgeNoText;

  /// No description provided for @workflowEdgeConditionTitle.
  ///
  /// In zh, this message translates to:
  /// **'连线条件'**
  String get workflowEdgeConditionTitle;

  /// No description provided for @workflowEdgeWhenLabel.
  ///
  /// In zh, this message translates to:
  /// **'何时走这条线'**
  String get workflowEdgeWhenLabel;

  /// No description provided for @workflowEdgeScreenTextLabel.
  ///
  /// In zh, this message translates to:
  /// **'屏幕判断文字'**
  String get workflowEdgeScreenTextLabel;

  /// No description provided for @workflowEdgeScreenTextHint.
  ///
  /// In zh, this message translates to:
  /// **'如：更新提示、跳过、确认'**
  String get workflowEdgeScreenTextHint;

  /// No description provided for @workflowEdgeDelete.
  ///
  /// In zh, this message translates to:
  /// **'删除连线'**
  String get workflowEdgeDelete;

  /// No description provided for @workflowScreenRange.
  ///
  /// In zh, this message translates to:
  /// **'屏幕范围：x 0~{w}，y 0~{h}'**
  String workflowScreenRange(num w, num h);

  /// No description provided for @workflowCanvasTitle.
  ///
  /// In zh, this message translates to:
  /// **'节点连线画布'**
  String get workflowCanvasTitle;

  /// No description provided for @workflowApply.
  ///
  /// In zh, this message translates to:
  /// **'应用'**
  String get workflowApply;

  /// No description provided for @workflowGetScreenRange.
  ///
  /// In zh, this message translates to:
  /// **'获取屏幕范围'**
  String get workflowGetScreenRange;

  /// No description provided for @workflowCanvasHelp.
  ///
  /// In zh, this message translates to:
  /// **'连线=执行顺序/条件 · 点节点编辑、长按拖动布局、从底部圆点拖线到另一节点、点连线改条件'**
  String get workflowCanvasHelp;

  /// No description provided for @workflowCanvasSynced.
  ///
  /// In zh, this message translates to:
  /// **'画布内容已同步，记得保存'**
  String get workflowCanvasSynced;

  /// No description provided for @workflowNameRequired.
  ///
  /// In zh, this message translates to:
  /// **'请填写名称'**
  String get workflowNameRequired;

  /// No description provided for @workflowCanvasNoNodes.
  ///
  /// In zh, this message translates to:
  /// **'画布还没有节点，请先添加步骤'**
  String get workflowCanvasNoNodes;

  /// No description provided for @workflowNameAndStepRequired.
  ///
  /// In zh, this message translates to:
  /// **'请填写名称并至少添加一步'**
  String get workflowNameAndStepRequired;

  /// No description provided for @workflowEditTitle.
  ///
  /// In zh, this message translates to:
  /// **'编辑工作流'**
  String get workflowEditTitle;

  /// No description provided for @workflowNewTitle.
  ///
  /// In zh, this message translates to:
  /// **'新建工作流'**
  String get workflowNewTitle;

  /// No description provided for @workflowNameLabel.
  ///
  /// In zh, this message translates to:
  /// **'名称（如：微信回消息）'**
  String get workflowNameLabel;

  /// No description provided for @workflowDescLabel.
  ///
  /// In zh, this message translates to:
  /// **'描述（可选）'**
  String get workflowDescLabel;

  /// No description provided for @workflowCanvasModeHint.
  ///
  /// In zh, this message translates to:
  /// **'此工作流使用画布（分支/条件），请在右上角画布中编辑'**
  String get workflowCanvasModeHint;

  /// No description provided for @workflowCanvasPreview.
  ///
  /// In zh, this message translates to:
  /// **'画布节点预览'**
  String get workflowCanvasPreview;

  /// No description provided for @workflowStepsLabel.
  ///
  /// In zh, this message translates to:
  /// **'步骤（长按拖拽排序）'**
  String get workflowStepsLabel;

  /// No description provided for @workflowAddStep.
  ///
  /// In zh, this message translates to:
  /// **'添加步骤'**
  String get workflowAddStep;

  /// No description provided for @workflowNoStepsHint.
  ///
  /// In zh, this message translates to:
  /// **'还没有步骤，点“添加步骤”或右上角画布开始'**
  String get workflowNoStepsHint;

  /// No description provided for @workflowRunConfirmTitle.
  ///
  /// In zh, this message translates to:
  /// **'执行「{name}」'**
  String workflowRunConfirmTitle(String name);

  /// No description provided for @workflowRunConfirmDesc.
  ///
  /// In zh, this message translates to:
  /// **'共 {count} 步：点击“执行”后按顺序操作手机（敏感步骤需你确认）'**
  String workflowRunConfirmDesc(int count);

  /// No description provided for @workflowRun.
  ///
  /// In zh, this message translates to:
  /// **'执行'**
  String get workflowRun;

  /// No description provided for @workflowScreenTitle.
  ///
  /// In zh, this message translates to:
  /// **'手机操作工作流'**
  String get workflowScreenTitle;

  /// No description provided for @workflowEmptyHint.
  ///
  /// In zh, this message translates to:
  /// **'还没有工作流。点右下角 + 新建：把常用手机操作编排成序列，之后对 AI 说“帮我执行 XX”即可。'**
  String get workflowEmptyHint;

  /// No description provided for @workflowStepCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 步'**
  String workflowStepCount(int count);

  /// No description provided for @stepScroll.
  ///
  /// In zh, this message translates to:
  /// **'滚动'**
  String get stepScroll;

  /// No description provided for @stepLaunchApp.
  ///
  /// In zh, this message translates to:
  /// **'启动应用'**
  String get stepLaunchApp;

  /// No description provided for @stepTapXy.
  ///
  /// In zh, this message translates to:
  /// **'坐标点击'**
  String get stepTapXy;

  /// No description provided for @stepSwipe.
  ///
  /// In zh, this message translates to:
  /// **'滑动'**
  String get stepSwipe;

  /// No description provided for @stepWait.
  ///
  /// In zh, this message translates to:
  /// **'等待'**
  String get stepWait;

  /// No description provided for @stepGoHome.
  ///
  /// In zh, this message translates to:
  /// **'返回主页'**
  String get stepGoHome;

  /// No description provided for @stepSummaryInput.
  ///
  /// In zh, this message translates to:
  /// **'输入：{text}'**
  String stepSummaryInput(String text);

  /// No description provided for @stepSummaryWait.
  ///
  /// In zh, this message translates to:
  /// **'{ms} 毫秒'**
  String stepSummaryWait(num ms);

  /// No description provided for @stepSummaryLaunch.
  ///
  /// In zh, this message translates to:
  /// **'启动 {target}'**
  String stepSummaryLaunch(String target);

  /// No description provided for @stepSummaryBackPrev.
  ///
  /// In zh, this message translates to:
  /// **'返回上一页'**
  String get stepSummaryBackPrev;

  /// No description provided for @stepSummaryGoHome.
  ///
  /// In zh, this message translates to:
  /// **'返回手机主页'**
  String get stepSummaryGoHome;

  /// No description provided for @stepEditTitle.
  ///
  /// In zh, this message translates to:
  /// **'编辑步骤'**
  String get stepEditTitle;

  /// No description provided for @stepActionLabel.
  ///
  /// In zh, this message translates to:
  /// **'动作'**
  String get stepActionLabel;

  /// No description provided for @stepInputLabel.
  ///
  /// In zh, this message translates to:
  /// **'输入内容（≤50 字）'**
  String get stepInputLabel;

  /// No description provided for @stepSwipeStartX.
  ///
  /// In zh, this message translates to:
  /// **'起 x'**
  String get stepSwipeStartX;

  /// No description provided for @stepSwipeStartY.
  ///
  /// In zh, this message translates to:
  /// **'起 y'**
  String get stepSwipeStartY;

  /// No description provided for @stepSwipeEndX.
  ///
  /// In zh, this message translates to:
  /// **'终 x'**
  String get stepSwipeEndX;

  /// No description provided for @stepSwipeEndY.
  ///
  /// In zh, this message translates to:
  /// **'终 y'**
  String get stepSwipeEndY;

  /// No description provided for @stepSwipeDuration.
  ///
  /// In zh, this message translates to:
  /// **'时长 ms'**
  String get stepSwipeDuration;

  /// No description provided for @stepWaitMsLabel.
  ///
  /// In zh, this message translates to:
  /// **'等待毫秒（100-10000）'**
  String get stepWaitMsLabel;

  /// No description provided for @stepBackPrevNoParam.
  ///
  /// In zh, this message translates to:
  /// **'返回上一页（无需参数）'**
  String get stepBackPrevNoParam;

  /// No description provided for @stepGoHomeNoParam.
  ///
  /// In zh, this message translates to:
  /// **'返回手机主页（无需参数）'**
  String get stepGoHomeNoParam;

  /// No description provided for @stepAppLabel.
  ///
  /// In zh, this message translates to:
  /// **'应用'**
  String get stepAppLabel;

  /// No description provided for @stepAppHint.
  ///
  /// In zh, this message translates to:
  /// **'点右侧图标从已安装应用选择'**
  String get stepAppHint;

  /// No description provided for @stepTargetHint.
  ///
  /// In zh, this message translates to:
  /// **'点右侧图标从当前屏幕点选，或手输节点文本'**
  String get stepTargetHint;

  /// No description provided for @stepPickAppTooltip.
  ///
  /// In zh, this message translates to:
  /// **'从应用列表选择'**
  String get stepPickAppTooltip;

  /// No description provided for @stepPickScreenTooltip.
  ///
  /// In zh, this message translates to:
  /// **'从当前屏幕点选'**
  String get stepPickScreenTooltip;

  /// No description provided for @stepConfirmAgain.
  ///
  /// In zh, this message translates to:
  /// **'此步需再次确认'**
  String get stepConfirmAgain;

  /// No description provided for @nodePickReaderServiceError.
  ///
  /// In zh, this message translates to:
  /// **'读屏服务未连接：App 更新后需在系统设置里重新开启「读屏（无障碍）」'**
  String get nodePickReaderServiceError;

  /// No description provided for @nodePickReaderDisabled.
  ///
  /// In zh, this message translates to:
  /// **'未开启读屏（无障碍），无法读取当前屏幕'**
  String get nodePickReaderDisabled;

  /// No description provided for @nodePickTitle.
  ///
  /// In zh, this message translates to:
  /// **'选择操作目标'**
  String get nodePickTitle;

  /// No description provided for @nodePickOpenAppHint.
  ///
  /// In zh, this message translates to:
  /// **'可先打开目标应用再回来点选，或直接手输目标文本'**
  String get nodePickOpenAppHint;

  /// No description provided for @nodePickEnableScreenReader.
  ///
  /// In zh, this message translates to:
  /// **'去开启读屏'**
  String get nodePickEnableScreenReader;

  /// No description provided for @nodePickCurrentScreen.
  ///
  /// In zh, this message translates to:
  /// **'当前屏幕'**
  String get nodePickCurrentScreen;

  /// No description provided for @nodePickRecentApps.
  ///
  /// In zh, this message translates to:
  /// **'最近打开的应用'**
  String get nodePickRecentApps;

  /// No description provided for @nodePickRecentAppsPkg.
  ///
  /// In zh, this message translates to:
  /// **'最近打开的应用（{pkg}）'**
  String nodePickRecentAppsPkg(String pkg);

  /// No description provided for @nodePickExternalHint.
  ///
  /// In zh, this message translates to:
  /// **'来自最近浏览的页面，执行时会按文字在当前屏幕重新匹配'**
  String get nodePickExternalHint;

  /// No description provided for @nodePickIconNoText.
  ///
  /// In zh, this message translates to:
  /// **'图标（无文字）'**
  String get nodePickIconNoText;

  /// No description provided for @nodePickIconButton.
  ///
  /// In zh, this message translates to:
  /// **'图标按钮'**
  String get nodePickIconButton;

  /// No description provided for @appPickLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'无法读取应用列表：{err}'**
  String appPickLoadFailed(String err);

  /// No description provided for @appPickUnknownError.
  ///
  /// In zh, this message translates to:
  /// **'未知错误'**
  String get appPickUnknownError;

  /// No description provided for @appPickNoApps.
  ///
  /// In zh, this message translates to:
  /// **'未读取到已安装应用'**
  String get appPickNoApps;

  /// No description provided for @appPickLoadError.
  ///
  /// In zh, this message translates to:
  /// **'加载失败：{err}'**
  String appPickLoadError(String err);

  /// No description provided for @appPickTitle.
  ///
  /// In zh, this message translates to:
  /// **'选择应用'**
  String get appPickTitle;

  /// No description provided for @appPickSearchHint.
  ///
  /// In zh, this message translates to:
  /// **'搜索应用名，如：微信、抖音'**
  String get appPickSearchHint;

  /// No description provided for @appPickNoResult.
  ///
  /// In zh, this message translates to:
  /// **'未找到应用'**
  String get appPickNoResult;

  /// No description provided for @shizukuRequestSent.
  ///
  /// In zh, this message translates to:
  /// **'已发起授权请求，请在系统弹窗中点击允许'**
  String get shizukuRequestSent;

  /// No description provided for @shizukuRequestFailed.
  ///
  /// In zh, this message translates to:
  /// **'已授权或发起失败，请检查状态后重试'**
  String get shizukuRequestFailed;

  /// No description provided for @shizukuNotRunning.
  ///
  /// In zh, this message translates to:
  /// **'Shizuku 服务未运行：请先在 Shizuku app（或 ADB）启动服务'**
  String get shizukuNotRunning;

  /// No description provided for @shizukuIntro.
  ///
  /// In zh, this message translates to:
  /// **'Shizuku 让 AI 获得系统级能力（应用列表/系统设置/模拟操作前置）。需先安装 Shizuku app 并启动服务（root 直启，或电脑 ADB 执行 start.sh），再在下方请求授权。授权后可在本页验证读取应用列表与执行 Shell。'**
  String get shizukuIntro;

  /// No description provided for @shizukuReRequest.
  ///
  /// In zh, this message translates to:
  /// **'重新请求授权'**
  String get shizukuReRequest;

  /// No description provided for @shizukuRequest.
  ///
  /// In zh, this message translates to:
  /// **'请求授权'**
  String get shizukuRequest;

  /// No description provided for @shizukuLoadApps.
  ///
  /// In zh, this message translates to:
  /// **'读取已安装应用列表（测试）'**
  String get shizukuLoadApps;

  /// No description provided for @shizukuShellDebug.
  ///
  /// In zh, this message translates to:
  /// **'Shell 调试'**
  String get shizukuShellDebug;

  /// No description provided for @shizukuShellHint.
  ///
  /// In zh, this message translates to:
  /// **'如 pm list packages -3'**
  String get shizukuShellHint;

  /// No description provided for @shizukuExecute.
  ///
  /// In zh, this message translates to:
  /// **'执行'**
  String get shizukuExecute;

  /// No description provided for @profileLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'加载失败: {e}'**
  String profileLoadFail(String e);

  /// No description provided for @profileSaveSuccess.
  ///
  /// In zh, this message translates to:
  /// **'保存成功'**
  String get profileSaveSuccess;

  /// No description provided for @profileSaveFail.
  ///
  /// In zh, this message translates to:
  /// **'保存失败: {e}'**
  String profileSaveFail(String e);

  /// No description provided for @profileEditInfo.
  ///
  /// In zh, this message translates to:
  /// **'编辑资料'**
  String get profileEditInfo;

  /// No description provided for @profileHeightCm.
  ///
  /// In zh, this message translates to:
  /// **'身高 (cm)'**
  String get profileHeightCm;

  /// No description provided for @profileWeightKg.
  ///
  /// In zh, this message translates to:
  /// **'体重 (kg)'**
  String get profileWeightKg;

  /// No description provided for @profileBio.
  ///
  /// In zh, this message translates to:
  /// **'个人描述'**
  String get profileBio;

  /// No description provided for @profileMySpace.
  ///
  /// In zh, this message translates to:
  /// **'我的空间'**
  String get profileMySpace;

  /// No description provided for @profileMyState.
  ///
  /// In zh, this message translates to:
  /// **'我的状态'**
  String get profileMyState;

  /// No description provided for @profileEightDimWeekly.
  ///
  /// In zh, this message translates to:
  /// **'八维状态与周视图'**
  String get profileEightDimWeekly;

  /// No description provided for @profileRelationProgress.
  ///
  /// In zh, this message translates to:
  /// **'与伙伴的关系进度'**
  String get profileRelationProgress;

  /// No description provided for @profileDiaryMood.
  ///
  /// In zh, this message translates to:
  /// **'记录每天的心情'**
  String get profileDiaryMood;

  /// No description provided for @profileMyMemos.
  ///
  /// In zh, this message translates to:
  /// **'我的备忘'**
  String get profileMyMemos;

  /// No description provided for @profileMemoTip.
  ///
  /// In zh, this message translates to:
  /// **'随手记，不忘记'**
  String get profileMemoTip;

  /// No description provided for @relationTypePartner.
  ///
  /// In zh, this message translates to:
  /// **'对象/伴侣'**
  String get relationTypePartner;

  /// No description provided for @relationTypeHusband.
  ///
  /// In zh, this message translates to:
  /// **'老公'**
  String get relationTypeHusband;

  /// No description provided for @relationTypeBestie.
  ///
  /// In zh, this message translates to:
  /// **'闺蜜'**
  String get relationTypeBestie;

  /// No description provided for @relationTypeBro.
  ///
  /// In zh, this message translates to:
  /// **'兄弟'**
  String get relationTypeBro;

  /// No description provided for @relationTypeBuddy.
  ///
  /// In zh, this message translates to:
  /// **'死党'**
  String get relationTypeBuddy;

  /// No description provided for @relationTypeFamily.
  ///
  /// In zh, this message translates to:
  /// **'家人'**
  String get relationTypeFamily;

  /// No description provided for @relationTypeFriend.
  ///
  /// In zh, this message translates to:
  /// **'朋友'**
  String get relationTypeFriend;

  /// No description provided for @relationLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'加载失败: {e}'**
  String relationLoadFail(String e);

  /// No description provided for @relationPartnerLabel.
  ///
  /// In zh, this message translates to:
  /// **'我的对象 · {rt}'**
  String relationPartnerLabel(String rt);

  /// No description provided for @relationNetwork.
  ///
  /// In zh, this message translates to:
  /// **'关系网'**
  String get relationNetwork;

  /// No description provided for @relationMyPartner.
  ///
  /// In zh, this message translates to:
  /// **'我的对象'**
  String get relationMyPartner;

  /// No description provided for @relationPartnerNote.
  ///
  /// In zh, this message translates to:
  /// **'对象身份与性别以此处为准，AI 不会默认你的对象是异性'**
  String get relationPartnerNote;

  /// No description provided for @relationAllRoles.
  ///
  /// In zh, this message translates to:
  /// **'全部角色关系'**
  String get relationAllRoles;

  /// No description provided for @relationSaveFail.
  ///
  /// In zh, this message translates to:
  /// **'保存失败: {e}'**
  String relationSaveFail(String e);

  /// No description provided for @relationSetTitle.
  ///
  /// In zh, this message translates to:
  /// **'设置「{name}」的关系'**
  String relationSetTitle(String name);

  /// No description provided for @relationTypeLabel.
  ///
  /// In zh, this message translates to:
  /// **'关系类型'**
  String get relationTypeLabel;

  /// No description provided for @relationIsPartner.
  ///
  /// In zh, this message translates to:
  /// **'这是我的对象/伴侣'**
  String get relationIsPartner;

  /// No description provided for @relationIsPartnerHint.
  ///
  /// In zh, this message translates to:
  /// **'设为对象后，AI 会明确知道你的对象是谁（支持同性）'**
  String get relationIsPartnerHint;

  /// No description provided for @relationDescOptional.
  ///
  /// In zh, this message translates to:
  /// **'关系描述（可选）'**
  String get relationDescOptional;

  /// No description provided for @relationDescHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：互称老公，关系亲密'**
  String get relationDescHint;

  /// No description provided for @stateHistTrendTitle.
  ///
  /// In zh, this message translates to:
  /// **'{characterName} · 状态趋势'**
  String stateHistTrendTitle(String characterName);

  /// No description provided for @stateHistEmpty.
  ///
  /// In zh, this message translates to:
  /// **'还没有状态历史记录。\n多和 TA 聊聊天，每次对话后的状态评估会自动记录在这里（最多保留最近 20 次）。'**
  String get stateHistEmpty;

  /// No description provided for @stateHistCurve.
  ///
  /// In zh, this message translates to:
  /// **'{cn} 变化曲线'**
  String stateHistCurve(String cn);

  /// No description provided for @stateHistRecentSnapshots.
  ///
  /// In zh, this message translates to:
  /// **'最近 {count} 次评估快照'**
  String stateHistRecentSnapshots(int count);

  /// No description provided for @stateHistInsufficientSnapshots.
  ///
  /// In zh, this message translates to:
  /// **'快照不足 2 条时无法对比'**
  String get stateHistInsufficientSnapshots;

  /// No description provided for @stateHistSpiderCompare.
  ///
  /// In zh, this message translates to:
  /// **'蛛网对比'**
  String get stateHistSpiderCompare;

  /// No description provided for @stateHistEarlier.
  ///
  /// In zh, this message translates to:
  /// **'较早'**
  String get stateHistEarlier;

  /// No description provided for @stateHistLater.
  ///
  /// In zh, this message translates to:
  /// **'较晚'**
  String get stateHistLater;

  /// No description provided for @stateHistEarlierAt.
  ///
  /// In zh, this message translates to:
  /// **'较早 {t}'**
  String stateHistEarlierAt(String t);

  /// No description provided for @stateHistLaterAt.
  ///
  /// In zh, this message translates to:
  /// **'较晚 {t}'**
  String stateHistLaterAt(String t);

  /// No description provided for @myStateSaved.
  ///
  /// In zh, this message translates to:
  /// **'状态已保存，AI 角色会感知到你的状态'**
  String get myStateSaved;

  /// No description provided for @myStateTitle.
  ///
  /// In zh, this message translates to:
  /// **'我的可视化状态'**
  String get myStateTitle;

  /// No description provided for @myStateLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'加载失败: {error}'**
  String myStateLoadFail(String error);

  /// No description provided for @myStateUpdatedAt.
  ///
  /// In zh, this message translates to:
  /// **'更新于 {time}'**
  String myStateUpdatedAt(String time);

  /// No description provided for @myStateSliderHint.
  ///
  /// In zh, this message translates to:
  /// **'拖动滑块调整你当前的状态，保存后 AI 角色在聊天中会感知到（例如心情低落时角色会更温柔地关心你）'**
  String get myStateSliderHint;

  /// No description provided for @myStateReset.
  ///
  /// In zh, this message translates to:
  /// **'重置为默认'**
  String get myStateReset;

  /// No description provided for @myStateSaving.
  ///
  /// In zh, this message translates to:
  /// **'保存中...'**
  String get myStateSaving;

  /// No description provided for @myStateSave.
  ///
  /// In zh, this message translates to:
  /// **'保存状态'**
  String get myStateSave;

  /// No description provided for @cropTitle.
  ///
  /// In zh, this message translates to:
  /// **'调整头像'**
  String get cropTitle;

  /// No description provided for @cropLoadingImage.
  ///
  /// In zh, this message translates to:
  /// **'图片加载中，请稍候再试'**
  String get cropLoadingImage;

  /// No description provided for @cropTimeoutRetry.
  ///
  /// In zh, this message translates to:
  /// **'裁剪超时，请重试'**
  String get cropTimeoutRetry;

  /// No description provided for @cropProcessing.
  ///
  /// In zh, this message translates to:
  /// **'处理中…'**
  String get cropProcessing;

  /// No description provided for @cropFailedRetry.
  ///
  /// In zh, this message translates to:
  /// **'裁剪失败，请重试'**
  String get cropFailedRetry;

  /// No description provided for @cropDragHint.
  ///
  /// In zh, this message translates to:
  /// **'拖动调整位置 · 双指缩放'**
  String get cropDragHint;

  /// No description provided for @agreeTitle.
  ///
  /// In zh, this message translates to:
  /// **'用户协议与免责声明'**
  String get agreeTitle;

  /// No description provided for @agreeSection1Title.
  ///
  /// In zh, this message translates to:
  /// **'一、软件性质'**
  String get agreeSection1Title;

  /// No description provided for @agreeSection1Body.
  ///
  /// In zh, this message translates to:
  /// **'本项目为开源、自托管软件（MIT License），由使用者自行下载、部署并运行在自己的设备或服务器上。作者以个人身份无偿维护本开源项目，不对任何使用者提供商业化服务承诺。'**
  String get agreeSection1Body;

  /// No description provided for @agreeSection2Title.
  ///
  /// In zh, this message translates to:
  /// **'二、自担风险'**
  String get agreeSection2Title;

  /// No description provided for @agreeSection2Body.
  ///
  /// In zh, this message translates to:
  /// **'软件按「现状」（AS IS）提供，不附带任何明示或默示的担保，包括但不限于适销性、特定用途适用性。因部署、配置、使用、升级过程中出现的任何数据丢失、损坏、服务中断或财产损失，均由使用者自行承担。'**
  String get agreeSection2Body;

  /// No description provided for @agreeSection3Title.
  ///
  /// In zh, this message translates to:
  /// **'三、内容责任'**
  String get agreeSection3Title;

  /// No description provided for @agreeSection3Body.
  ///
  /// In zh, this message translates to:
  /// **'本软件为通用工具，AI 生成的对话、图片、文字等内容由使用者自行配置的模型、提示词与数据产生，不代表作者观点。作者不对使用者或任何第三方基于本软件产生、传播的内容与行为承担任何责任。'**
  String get agreeSection3Body;

  /// No description provided for @agreeSection4Title.
  ///
  /// In zh, this message translates to:
  /// **'四、数据安全'**
  String get agreeSection4Title;

  /// No description provided for @agreeSection4Body.
  ///
  /// In zh, this message translates to:
  /// **'数据默认存储在使用者自己的服务器。请自行做好备份、密钥保管与访问控制（如防火墙、HTTPS、修改默认管理员账号）。因未妥善保护导致的隐私泄露、数据被篡改等后果，由使用者自行负责。'**
  String get agreeSection4Body;

  /// No description provided for @agreeSection5Title.
  ///
  /// In zh, this message translates to:
  /// **'五、合法使用'**
  String get agreeSection5Title;

  /// No description provided for @agreeSection5Body.
  ///
  /// In zh, this message translates to:
  /// **'使用者须遵守所在地法律法规，不得将本软件用于违法、侵权、骚扰、诈骗等用途；不得利用软件生成的内容侵犯他人合法权益。使用者的一切使用行为及其后果均与作者无关。'**
  String get agreeSection5Body;

  /// No description provided for @agreeSection6Title.
  ///
  /// In zh, this message translates to:
  /// **'六、远程与多人访问'**
  String get agreeSection6Title;

  /// No description provided for @agreeSection6Body.
  ///
  /// In zh, this message translates to:
  /// **'将服务器暴露到公网、通过 Tailscale 等组网分享给他人使用前，使用者须自行评估风险并承担相应责任，包括但不限于他人通过账号或权限管理不当产生的后果。'**
  String get agreeSection6Body;

  /// No description provided for @agreeSection7Title.
  ///
  /// In zh, this message translates to:
  /// **'七、协议变更'**
  String get agreeSection7Title;

  /// No description provided for @agreeSection7Body.
  ///
  /// In zh, this message translates to:
  /// **'本协议内容可能随版本更新调整，继续使用本软件即视为接受最新版本协议。'**
  String get agreeSection7Body;

  /// No description provided for @backupTitle.
  ///
  /// In zh, this message translates to:
  /// **'数据备份'**
  String get backupTitle;

  /// No description provided for @backupSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'把 SQLite 数据库与配置导出为压缩包，备份可随时恢复'**
  String get backupSubtitle;

  /// No description provided for @backupExport.
  ///
  /// In zh, this message translates to:
  /// **'导出备份'**
  String get backupExport;

  /// No description provided for @backupExporting.
  ///
  /// In zh, this message translates to:
  /// **'正在备份…'**
  String get backupExporting;

  /// No description provided for @backupExportSuccess.
  ///
  /// In zh, this message translates to:
  /// **'备份已保存到手机'**
  String get backupExportSuccess;

  /// No description provided for @backupExportSuccessWithSize.
  ///
  /// In zh, this message translates to:
  /// **'备份已保存到手机（{size}）'**
  String backupExportSuccessWithSize(Object size);

  /// No description provided for @backupExportCanceled.
  ///
  /// In zh, this message translates to:
  /// **'已取消保存'**
  String get backupExportCanceled;

  /// No description provided for @backupExportFailed.
  ///
  /// In zh, this message translates to:
  /// **'备份失败，请重试'**
  String get backupExportFailed;

  /// No description provided for @backupAdminOnly.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可管理备份'**
  String get backupAdminOnly;

  /// No description provided for @backupRestoreTitle.
  ///
  /// In zh, this message translates to:
  /// **'恢复指引'**
  String get backupRestoreTitle;

  /// No description provided for @backupRestoreNote.
  ///
  /// In zh, this message translates to:
  /// **'备份包含 SQLite 数据库与配置，各平台恢复方式略有不同，以下为通用步骤（操作前请先停止服务并自行留好当前数据的副本）。'**
  String get backupRestoreNote;

  /// No description provided for @backupRestoreStep1.
  ///
  /// In zh, this message translates to:
  /// **'停止本次服务进程'**
  String get backupRestoreStep1;

  /// No description provided for @backupRestoreStep2.
  ///
  /// In zh, this message translates to:
  /// **'解压备份包，用其中的数据覆盖 backend/data 目录'**
  String get backupRestoreStep2;

  /// No description provided for @backupRestoreStep3.
  ///
  /// In zh, this message translates to:
  /// **'重新启动服务，数据即恢复完成'**
  String get backupRestoreStep3;

  /// No description provided for @backupUrlHint.
  ///
  /// In zh, this message translates to:
  /// **'若手机无法直接保存，可在浏览器或电脑访问以下链接下载（需登录主账号）：'**
  String get backupUrlHint;

  /// No description provided for @backupUrlCopy.
  ///
  /// In zh, this message translates to:
  /// **'复制链接'**
  String get backupUrlCopy;

  /// No description provided for @backupCopied.
  ///
  /// In zh, this message translates to:
  /// **'已复制'**
  String get backupCopied;

  /// No description provided for @backupFileLabel.
  ///
  /// In zh, this message translates to:
  /// **'备份文件'**
  String get backupFileLabel;

  /// No description provided for @backgroundKeepalive.
  ///
  /// In zh, this message translates to:
  /// **'后台保活'**
  String get backgroundKeepalive;

  /// No description provided for @backgroundKeepaliveHint.
  ///
  /// In zh, this message translates to:
  /// **'退到后台后持续监听新消息并在后台弹通知（关闭后不再后台运行）'**
  String get backgroundKeepaliveHint;

  /// No description provided for @groupConnection.
  ///
  /// In zh, this message translates to:
  /// **'连接'**
  String get groupConnection;

  /// No description provided for @groupExperience.
  ///
  /// In zh, this message translates to:
  /// **'体验'**
  String get groupExperience;

  /// No description provided for @groupSystem.
  ///
  /// In zh, this message translates to:
  /// **'系统'**
  String get groupSystem;

  /// No description provided for @groupAbout.
  ///
  /// In zh, this message translates to:
  /// **'关于'**
  String get groupAbout;

  /// No description provided for @experienceSettingsTitle.
  ///
  /// In zh, this message translates to:
  /// **'体验设置'**
  String get experienceSettingsTitle;

  /// No description provided for @experienceSettingsSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'手机感知 / 免打扰 / 扩展 / 外观'**
  String get experienceSettingsSubtitle;

  /// No description provided for @weaveLibrarySubtitle.
  ///
  /// In zh, this message translates to:
  /// **'全景记忆 · 编织成球'**
  String get weaveLibrarySubtitle;

  /// No description provided for @permissionManagementTitle.
  ///
  /// In zh, this message translates to:
  /// **'权限管理'**
  String get permissionManagementTitle;

  /// No description provided for @permissionManagementHint.
  ///
  /// In zh, this message translates to:
  /// **'AI 能力 / 主账号 / 服务器功能'**
  String get permissionManagementHint;

  /// No description provided for @accountAdminTitle.
  ///
  /// In zh, this message translates to:
  /// **'主账号管理'**
  String get accountAdminTitle;

  /// No description provided for @accountAdminHint.
  ///
  /// In zh, this message translates to:
  /// **'设置哪些账号是主账号（可管理服务器配置）'**
  String get accountAdminHint;

  /// No description provided for @accountAdminOnly.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可管理'**
  String get accountAdminOnly;

  /// No description provided for @accountAdminListTitle.
  ///
  /// In zh, this message translates to:
  /// **'账号列表'**
  String get accountAdminListTitle;

  /// No description provided for @accountMainLabel.
  ///
  /// In zh, this message translates to:
  /// **'主账号'**
  String get accountMainLabel;

  /// No description provided for @accountSubLabel.
  ///
  /// In zh, this message translates to:
  /// **'子账号'**
  String get accountSubLabel;

  /// No description provided for @accountAdminLoadFailed.
  ///
  /// In zh, this message translates to:
  /// **'加载失败，请重试'**
  String get accountAdminLoadFailed;

  /// No description provided for @accountAdminSaved.
  ///
  /// In zh, this message translates to:
  /// **'已保存'**
  String get accountAdminSaved;

  /// No description provided for @accountAdminFailed.
  ///
  /// In zh, this message translates to:
  /// **'操作失败，请重试'**
  String get accountAdminFailed;

  /// No description provided for @accountAdminKeepOne.
  ///
  /// In zh, this message translates to:
  /// **'至少保留一个主账号'**
  String get accountAdminKeepOne;

  /// No description provided for @updateAnnouncementHint.
  ///
  /// In zh, this message translates to:
  /// **'最近更新内容，按天查看'**
  String get updateAnnouncementHint;

  /// No description provided for @userAgreementTitle.
  ///
  /// In zh, this message translates to:
  /// **'用户协议'**
  String get userAgreementTitle;

  /// No description provided for @userAgreementHint.
  ///
  /// In zh, this message translates to:
  /// **'服务条款与隐私说明'**
  String get userAgreementHint;

  /// No description provided for @shizukuReadAppsFailed.
  ///
  /// In zh, this message translates to:
  /// **'读取失败：{err}'**
  String shizukuReadAppsFailed(Object err);

  /// No description provided for @shizukuAppSeparator.
  ///
  /// In zh, this message translates to:
  /// **'、'**
  String get shizukuAppSeparator;

  /// No description provided for @shizukuThirdPartyAppCount.
  ///
  /// In zh, this message translates to:
  /// **'共 {count} 个第三方应用：\n{apps}'**
  String shizukuThirdPartyAppCount(Object apps, Object count);

  /// No description provided for @unknownError.
  ///
  /// In zh, this message translates to:
  /// **'未知错误'**
  String get unknownError;

  /// No description provided for @extRetry.
  ///
  /// In zh, this message translates to:
  /// **'重试'**
  String get extRetry;

  /// No description provided for @extCollapse.
  ///
  /// In zh, this message translates to:
  /// **'收起'**
  String get extCollapse;

  /// No description provided for @extExpandFull.
  ///
  /// In zh, this message translates to:
  /// **'展开全文'**
  String get extExpandFull;

  /// No description provided for @extUsageGuide.
  ///
  /// In zh, this message translates to:
  /// **'使用教程'**
  String get extUsageGuide;

  /// No description provided for @extView.
  ///
  /// In zh, this message translates to:
  /// **'查看'**
  String get extView;

  /// No description provided for @extExpand.
  ///
  /// In zh, this message translates to:
  /// **'展开'**
  String get extExpand;

  /// No description provided for @extCustomConfig.
  ///
  /// In zh, this message translates to:
  /// **'自定义设定'**
  String get extCustomConfig;

  /// No description provided for @extDoyinInjectHint.
  ///
  /// In zh, this message translates to:
  /// **'注入到 AI 抖音创作中（图文/回复生成时生效）'**
  String get extDoyinInjectHint;

  /// No description provided for @extConfigExampleHint.
  ///
  /// In zh, this message translates to:
  /// **'例如：发内容时多讲讲我们的故事，用温柔一点的语气…'**
  String get extConfigExampleHint;

  /// No description provided for @extSaveConfig.
  ///
  /// In zh, this message translates to:
  /// **'保存设定'**
  String get extSaveConfig;

  /// No description provided for @extPendingHint.
  ///
  /// In zh, this message translates to:
  /// **'待批准的抖音发布/回复请求请在「AI 好友」页右上角小信封查看'**
  String get extPendingHint;

  /// No description provided for @extConfigSaved.
  ///
  /// In zh, this message translates to:
  /// **'自定义设定已保存'**
  String get extConfigSaved;

  /// No description provided for @extSaveFailed.
  ///
  /// In zh, this message translates to:
  /// **'保存失败：{err}'**
  String extSaveFailed(Object err);

  /// No description provided for @douyinBindRole.
  ///
  /// In zh, this message translates to:
  /// **'绑定角色'**
  String get douyinBindRole;

  /// No description provided for @douyinBindRoleHint.
  ///
  /// In zh, this message translates to:
  /// **'选择绑定抖音的角色（空=未绑定）'**
  String get douyinBindRoleHint;

  /// No description provided for @douyinBindNone.
  ///
  /// In zh, this message translates to:
  /// **'未绑定'**
  String get douyinBindNone;

  /// No description provided for @douyinBindSave.
  ///
  /// In zh, this message translates to:
  /// **'保存绑定'**
  String get douyinBindSave;

  /// No description provided for @douyinBindSaved.
  ///
  /// In zh, this message translates to:
  /// **'绑定角色已保存'**
  String get douyinBindSaved;

  /// No description provided for @wechatBindRole.
  ///
  /// In zh, this message translates to:
  /// **'微信绑定角色'**
  String get wechatBindRole;

  /// No description provided for @wechatBindRoleHint.
  ///
  /// In zh, this message translates to:
  /// **'选择本家庭角色作为微信（ClawBot）绑定角色（空=未绑定）'**
  String get wechatBindRoleHint;

  /// No description provided for @wechatBindNone.
  ///
  /// In zh, this message translates to:
  /// **'未绑定'**
  String get wechatBindNone;

  /// No description provided for @wechatBindSave.
  ///
  /// In zh, this message translates to:
  /// **'保存绑定'**
  String get wechatBindSave;

  /// No description provided for @wechatBindSaved.
  ///
  /// In zh, this message translates to:
  /// **'绑定角色已保存'**
  String get wechatBindSaved;

  /// No description provided for @wechatBindLoading.
  ///
  /// In zh, this message translates to:
  /// **'加载中…'**
  String get wechatBindLoading;

  /// No description provided for @wechatBindNeedPick.
  ///
  /// In zh, this message translates to:
  /// **'请先选择要绑定的角色'**
  String get wechatBindNeedPick;

  /// No description provided for @channelBindingRole.
  ///
  /// In zh, this message translates to:
  /// **'绑定角色'**
  String get channelBindingRole;

  /// No description provided for @channelBindingRoleHint.
  ///
  /// In zh, this message translates to:
  /// **'选择本家庭角色作为该渠道的绑定角色'**
  String get channelBindingRoleHint;

  /// No description provided for @channelBindingBotDefault.
  ///
  /// In zh, this message translates to:
  /// **'默认'**
  String get channelBindingBotDefault;

  /// No description provided for @channelBindingNone.
  ///
  /// In zh, this message translates to:
  /// **'未绑定'**
  String get channelBindingNone;

  /// No description provided for @channelBindingLoading.
  ///
  /// In zh, this message translates to:
  /// **'加载中…'**
  String get channelBindingLoading;

  /// No description provided for @channelBindingEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无绑定'**
  String get channelBindingEmpty;

  /// No description provided for @channelBindingSave.
  ///
  /// In zh, this message translates to:
  /// **'保存'**
  String get channelBindingSave;

  /// No description provided for @channelBindingSaved.
  ///
  /// In zh, this message translates to:
  /// **'绑定已保存'**
  String get channelBindingSaved;

  /// No description provided for @channelBindingUnbound.
  ///
  /// In zh, this message translates to:
  /// **'已解绑'**
  String get channelBindingUnbound;

  /// No description provided for @channelBindingUnbind.
  ///
  /// In zh, this message translates to:
  /// **'解绑'**
  String get channelBindingUnbind;

  /// No description provided for @channelBindingUnbindConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定解绑该 bot 的渠道绑定？解绑后需重新扫码/绑定才能恢复'**
  String get channelBindingUnbindConfirm;

  /// No description provided for @channelBindingMainOnly.
  ///
  /// In zh, this message translates to:
  /// **'仅主账号可配置渠道绑定'**
  String get channelBindingMainOnly;

  /// No description provided for @channelBindingNeedPick.
  ///
  /// In zh, this message translates to:
  /// **'请先选择要绑定的角色'**
  String get channelBindingNeedPick;

  /// No description provided for @agentMindRetrievalCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 条'**
  String agentMindRetrievalCount(Object count);

  /// No description provided for @agentMindRetrievalHitReturn.
  ///
  /// In zh, this message translates to:
  /// **'召回 {hit} / 返回 {returned}'**
  String agentMindRetrievalHitReturn(Object hit, Object returned);

  /// No description provided for @extHintDiary.
  ///
  /// In zh, this message translates to:
  /// **'你是一位温柔细心的日记助手。\n你的目标：…'**
  String get extHintDiary;

  /// No description provided for @extHintGreeting.
  ///
  /// In zh, this message translates to:
  /// **'你好呀，今天过得怎么样？'**
  String get extHintGreeting;

  /// No description provided for @extHintWrite.
  ///
  /// In zh, this message translates to:
  /// **'写文章，起标题，帮我写'**
  String get extHintWrite;

  /// No description provided for @extHintWriter.
  ///
  /// In zh, this message translates to:
  /// **'你是写作助手。\n你的目标：…'**
  String get extHintWriter;

  /// No description provided for @loginConfirmNewPassword.
  ///
  /// In zh, this message translates to:
  /// **'确认新密码'**
  String get loginConfirmNewPassword;

  /// No description provided for @loginForgotPassword.
  ///
  /// In zh, this message translates to:
  /// **'忘记密码？修改'**
  String get loginForgotPassword;

  /// No description provided for @loginNewPassword.
  ///
  /// In zh, this message translates to:
  /// **'新密码'**
  String get loginNewPassword;

  /// No description provided for @loginResetFail.
  ///
  /// In zh, this message translates to:
  /// **'重置失败，请检查用户名或服务器连接'**
  String get loginResetFail;

  /// No description provided for @loginResetInvalid.
  ///
  /// In zh, this message translates to:
  /// **'请填写用户名与两次一致的新密码'**
  String get loginResetInvalid;

  /// No description provided for @loginResetOk.
  ///
  /// In zh, this message translates to:
  /// **'密码已重置，请用新密码登录'**
  String get loginResetOk;

  /// No description provided for @memoryCurrentRetention.
  ///
  /// In zh, this message translates to:
  /// **'当前保留率'**
  String get memoryCurrentRetention;

  /// No description provided for @memoryDecayHorizon.
  ///
  /// In zh, this message translates to:
  /// **'未来 {days} 天'**
  String memoryDecayHorizon(Object days);

  /// No description provided for @memoryDecayTitle.
  ///
  /// In zh, this message translates to:
  /// **'记忆衰减曲线'**
  String get memoryDecayTitle;

  /// No description provided for @memoryNextReview.
  ///
  /// In zh, this message translates to:
  /// **'下次复习'**
  String get memoryNextReview;

  /// No description provided for @memoryNextReviewNone.
  ///
  /// In zh, this message translates to:
  /// **'暂未安排'**
  String get memoryNextReviewNone;

  /// No description provided for @memoryReviewCount.
  ///
  /// In zh, this message translates to:
  /// **'已复习次数'**
  String get memoryReviewCount;

  /// No description provided for @memoryStrengthDays.
  ///
  /// In zh, this message translates to:
  /// **'强度（天）'**
  String get memoryStrengthDays;

  /// No description provided for @themeColorBlue.
  ///
  /// In zh, this message translates to:
  /// **'蓝'**
  String get themeColorBlue;

  /// No description provided for @themeColorCyan.
  ///
  /// In zh, this message translates to:
  /// **'青'**
  String get themeColorCyan;

  /// No description provided for @themeColorGreen.
  ///
  /// In zh, this message translates to:
  /// **'绿'**
  String get themeColorGreen;

  /// No description provided for @themeColorOrange.
  ///
  /// In zh, this message translates to:
  /// **'橙'**
  String get themeColorOrange;

  /// No description provided for @themeColorPink.
  ///
  /// In zh, this message translates to:
  /// **'粉'**
  String get themeColorPink;

  /// No description provided for @themeColorPurple.
  ///
  /// In zh, this message translates to:
  /// **'紫'**
  String get themeColorPurple;

  /// No description provided for @mcpToolsTitle.
  ///
  /// In zh, this message translates to:
  /// **'MCP 工具'**
  String get mcpToolsTitle;

  /// No description provided for @mcpToolsSubtitle.
  ///
  /// In zh, this message translates to:
  /// **'管理 MCP Server 与工具权限'**
  String get mcpToolsSubtitle;

  /// No description provided for @mcpAddServer.
  ///
  /// In zh, this message translates to:
  /// **'添加服务器'**
  String get mcpAddServer;

  /// No description provided for @mcpEditServer.
  ///
  /// In zh, this message translates to:
  /// **'编辑服务器'**
  String get mcpEditServer;

  /// No description provided for @mcpStatusConnected.
  ///
  /// In zh, this message translates to:
  /// **'已连接'**
  String get mcpStatusConnected;

  /// No description provided for @mcpStatusDisconnected.
  ///
  /// In zh, this message translates to:
  /// **'未连接'**
  String get mcpStatusDisconnected;

  /// No description provided for @mcpStatusError.
  ///
  /// In zh, this message translates to:
  /// **'错误'**
  String get mcpStatusError;

  /// No description provided for @mcpToolsCount.
  ///
  /// In zh, this message translates to:
  /// **'{n} 个工具'**
  String mcpToolsCount(Object n);

  /// No description provided for @mcpConnect.
  ///
  /// In zh, this message translates to:
  /// **'连接'**
  String get mcpConnect;

  /// No description provided for @mcpDisconnect.
  ///
  /// In zh, this message translates to:
  /// **'断开'**
  String get mcpDisconnect;

  /// No description provided for @mcpTest.
  ///
  /// In zh, this message translates to:
  /// **'测试'**
  String get mcpTest;

  /// No description provided for @mcpDelete.
  ///
  /// In zh, this message translates to:
  /// **'删除'**
  String get mcpDelete;

  /// No description provided for @mcpTransportLabel.
  ///
  /// In zh, this message translates to:
  /// **'传输'**
  String get mcpTransportLabel;

  /// No description provided for @mcpTransportStdio.
  ///
  /// In zh, this message translates to:
  /// **'stdio'**
  String get mcpTransportStdio;

  /// No description provided for @mcpTransportSse.
  ///
  /// In zh, this message translates to:
  /// **'SSE'**
  String get mcpTransportSse;

  /// No description provided for @mcpTransportHttp.
  ///
  /// In zh, this message translates to:
  /// **'HTTP'**
  String get mcpTransportHttp;

  /// No description provided for @mcpName.
  ///
  /// In zh, this message translates to:
  /// **'名称'**
  String get mcpName;

  /// No description provided for @mcpNameRequired.
  ///
  /// In zh, this message translates to:
  /// **'名称不能为空'**
  String get mcpNameRequired;

  /// No description provided for @mcpNameHint.
  ///
  /// In zh, this message translates to:
  /// **'唯一标识（字母/数字/_-）'**
  String get mcpNameHint;

  /// No description provided for @mcpCommand.
  ///
  /// In zh, this message translates to:
  /// **'命令'**
  String get mcpCommand;

  /// No description provided for @mcpArgs.
  ///
  /// In zh, this message translates to:
  /// **'参数（每行一个）'**
  String get mcpArgs;

  /// No description provided for @mcpEnv.
  ///
  /// In zh, this message translates to:
  /// **'环境变量（KEY=值，每行一个）'**
  String get mcpEnv;

  /// No description provided for @mcpUrl.
  ///
  /// In zh, this message translates to:
  /// **'URL 地址'**
  String get mcpUrl;

  /// No description provided for @mcpHeaders.
  ///
  /// In zh, this message translates to:
  /// **'自定义请求头（JSON）'**
  String get mcpHeaders;

  /// No description provided for @mcpEnabled.
  ///
  /// In zh, this message translates to:
  /// **'启用'**
  String get mcpEnabled;

  /// No description provided for @mcpAutoConnect.
  ///
  /// In zh, this message translates to:
  /// **'启动时自动连接'**
  String get mcpAutoConnect;

  /// No description provided for @mcpSave.
  ///
  /// In zh, this message translates to:
  /// **'保存'**
  String get mcpSave;

  /// No description provided for @mcpCancel.
  ///
  /// In zh, this message translates to:
  /// **'取消'**
  String get mcpCancel;

  /// No description provided for @mcpDeleteConfirmTitle.
  ///
  /// In zh, this message translates to:
  /// **'删除 MCP Server'**
  String get mcpDeleteConfirmTitle;

  /// No description provided for @mcpDeleteConfirmBody.
  ///
  /// In zh, this message translates to:
  /// **'确定删除 {name}？将断开连接并移除配置。'**
  String mcpDeleteConfirmBody(Object name);

  /// No description provided for @mcpDeleteSuccess.
  ///
  /// In zh, this message translates to:
  /// **'已删除'**
  String get mcpDeleteSuccess;

  /// No description provided for @mcpDeleteFail.
  ///
  /// In zh, this message translates to:
  /// **'删除失败：{err}'**
  String mcpDeleteFail(Object err);

  /// No description provided for @mcpConnectSuccess.
  ///
  /// In zh, this message translates to:
  /// **'连接成功'**
  String get mcpConnectSuccess;

  /// No description provided for @mcpDisconnectSuccess.
  ///
  /// In zh, this message translates to:
  /// **'已断开'**
  String get mcpDisconnectSuccess;

  /// No description provided for @mcpConnectFail.
  ///
  /// In zh, this message translates to:
  /// **'连接失败：{err}'**
  String mcpConnectFail(Object err);

  /// No description provided for @mcpTestSuccess.
  ///
  /// In zh, this message translates to:
  /// **'测试成功，发现 {n} 个工具'**
  String mcpTestSuccess(Object n);

  /// No description provided for @mcpTestFail.
  ///
  /// In zh, this message translates to:
  /// **'测试失败：{err}'**
  String mcpTestFail(Object err);

  /// No description provided for @mcpSaveSuccess.
  ///
  /// In zh, this message translates to:
  /// **'已保存'**
  String get mcpSaveSuccess;

  /// No description provided for @mcpSaveFail.
  ///
  /// In zh, this message translates to:
  /// **'保存失败：{err}'**
  String mcpSaveFail(Object err);

  /// No description provided for @mcpLoadFail.
  ///
  /// In zh, this message translates to:
  /// **'加载失败：{err}'**
  String mcpLoadFail(Object err);

  /// No description provided for @mcpTools.
  ///
  /// In zh, this message translates to:
  /// **'工具'**
  String get mcpTools;

  /// No description provided for @mcpToolsEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无工具（连接后自动发现）'**
  String get mcpToolsEmpty;

  /// No description provided for @mcpToolsRefresh.
  ///
  /// In zh, this message translates to:
  /// **'刷新'**
  String get mcpToolsRefresh;

  /// No description provided for @mcpRiskLow.
  ///
  /// In zh, this message translates to:
  /// **'低风险'**
  String get mcpRiskLow;

  /// No description provided for @mcpRiskMedium.
  ///
  /// In zh, this message translates to:
  /// **'中风险'**
  String get mcpRiskMedium;

  /// No description provided for @mcpRiskHigh.
  ///
  /// In zh, this message translates to:
  /// **'高风险'**
  String get mcpRiskHigh;

  /// No description provided for @mcpPermissionLabel.
  ///
  /// In zh, this message translates to:
  /// **'权限'**
  String get mcpPermissionLabel;

  /// No description provided for @mcpPermissionAllow.
  ///
  /// In zh, this message translates to:
  /// **'允许'**
  String get mcpPermissionAllow;

  /// No description provided for @mcpPermissionAsk.
  ///
  /// In zh, this message translates to:
  /// **'询问'**
  String get mcpPermissionAsk;

  /// No description provided for @mcpPermissionForbid.
  ///
  /// In zh, this message translates to:
  /// **'禁止'**
  String get mcpPermissionForbid;

  /// No description provided for @mcpPermissionSaved.
  ///
  /// In zh, this message translates to:
  /// **'权限已保存'**
  String get mcpPermissionSaved;

  /// No description provided for @mcpPermissionSaveFail.
  ///
  /// In zh, this message translates to:
  /// **'权限保存失败：{err}'**
  String mcpPermissionSaveFail(Object err);

  /// No description provided for @mcpPreset.
  ///
  /// In zh, this message translates to:
  /// **'预设模板'**
  String get mcpPreset;

  /// No description provided for @mcpPresetFilesystem.
  ///
  /// In zh, this message translates to:
  /// **'文件系统'**
  String get mcpPresetFilesystem;

  /// No description provided for @mcpPresetGithub.
  ///
  /// In zh, this message translates to:
  /// **'GitHub'**
  String get mcpPresetGithub;

  /// No description provided for @mcpPresetSqlite.
  ///
  /// In zh, this message translates to:
  /// **'SQLite'**
  String get mcpPresetSqlite;

  /// No description provided for @mcpNoServers.
  ///
  /// In zh, this message translates to:
  /// **'还没有 MCP Server，点击右上角添加。'**
  String get mcpNoServers;

  /// No description provided for @mcpRecentCalls.
  ///
  /// In zh, this message translates to:
  /// **'最近调用'**
  String get mcpRecentCalls;

  /// No description provided for @mcpCallOk.
  ///
  /// In zh, this message translates to:
  /// **'成功'**
  String get mcpCallOk;

  /// No description provided for @mcpCallTimeout.
  ///
  /// In zh, this message translates to:
  /// **'超时'**
  String get mcpCallTimeout;

  /// No description provided for @mcpCallBlocked.
  ///
  /// In zh, this message translates to:
  /// **'禁止'**
  String get mcpCallBlocked;

  /// No description provided for @mcpCallFailed.
  ///
  /// In zh, this message translates to:
  /// **'失败'**
  String get mcpCallFailed;

  /// No description provided for @toolResult.
  ///
  /// In zh, this message translates to:
  /// **'工具结果'**
  String get toolResult;

  /// No description provided for @mcpResources.
  ///
  /// In zh, this message translates to:
  /// **'资源'**
  String get mcpResources;

  /// No description provided for @mcpPrompts.
  ///
  /// In zh, this message translates to:
  /// **'提示词'**
  String get mcpPrompts;

  /// No description provided for @gameTitle.
  ///
  /// In zh, this message translates to:
  /// **'游戏'**
  String get gameTitle;

  /// No description provided for @gameSelectGameType.
  ///
  /// In zh, this message translates to:
  /// **'选择游戏'**
  String get gameSelectGameType;

  /// No description provided for @gameUndercover.
  ///
  /// In zh, this message translates to:
  /// **'谁是卧底'**
  String get gameUndercover;

  /// No description provided for @gameTruthOrDare.
  ///
  /// In zh, this message translates to:
  /// **'真心话大冒险'**
  String get gameTruthOrDare;

  /// No description provided for @gameTwentyQ.
  ///
  /// In zh, this message translates to:
  /// **'猜词20问'**
  String get gameTwentyQ;

  /// No description provided for @gameSingle.
  ///
  /// In zh, this message translates to:
  /// **'单人'**
  String get gameSingle;

  /// No description provided for @gameDual.
  ///
  /// In zh, this message translates to:
  /// **'双人'**
  String get gameDual;

  /// No description provided for @gameMulti.
  ///
  /// In zh, this message translates to:
  /// **'多人'**
  String get gameMulti;

  /// No description provided for @gameDescriptionUndercover.
  ///
  /// In zh, this message translates to:
  /// **'描述词语、投票找出卧底'**
  String get gameDescriptionUndercover;

  /// No description provided for @gameDescriptionTruthOrDare.
  ///
  /// In zh, this message translates to:
  /// **'轮流选择真心话或大冒险'**
  String get gameDescriptionTruthOrDare;

  /// No description provided for @gameDescriptionTwentyQ.
  ///
  /// In zh, this message translates to:
  /// **'用20个是非问句猜出对方想的词'**
  String get gameDescriptionTwentyQ;

  /// No description provided for @gameStart.
  ///
  /// In zh, this message translates to:
  /// **'开始'**
  String get gameStart;

  /// No description provided for @gameSpectator.
  ///
  /// In zh, this message translates to:
  /// **'观战'**
  String get gameSpectator;

  /// No description provided for @gamePlayer.
  ///
  /// In zh, this message translates to:
  /// **'玩家'**
  String get gamePlayer;

  /// No description provided for @gameMyTurn.
  ///
  /// In zh, this message translates to:
  /// **'轮到你'**
  String get gameMyTurn;

  /// No description provided for @gameDescribe.
  ///
  /// In zh, this message translates to:
  /// **'描述'**
  String get gameDescribe;

  /// No description provided for @gameVote.
  ///
  /// In zh, this message translates to:
  /// **'投票'**
  String get gameVote;

  /// No description provided for @gameTruth.
  ///
  /// In zh, this message translates to:
  /// **'真心话'**
  String get gameTruth;

  /// No description provided for @gameDare.
  ///
  /// In zh, this message translates to:
  /// **'大冒险'**
  String get gameDare;

  /// No description provided for @gameAsk.
  ///
  /// In zh, this message translates to:
  /// **'提问'**
  String get gameAsk;

  /// No description provided for @gameGuess.
  ///
  /// In zh, this message translates to:
  /// **'猜词'**
  String get gameGuess;

  /// No description provided for @gameArchive.
  ///
  /// In zh, this message translates to:
  /// **'游乐手札'**
  String get gameArchive;

  /// No description provided for @gameSelectPlayers.
  ///
  /// In zh, this message translates to:
  /// **'选择 AI 角色'**
  String get gameSelectPlayers;

  /// No description provided for @gameUserRole.
  ///
  /// In zh, this message translates to:
  /// **'你的身份'**
  String get gameUserRole;

  /// No description provided for @gameUserAsPlayer.
  ///
  /// In zh, this message translates to:
  /// **'以玩家身份加入'**
  String get gameUserAsPlayer;

  /// No description provided for @gameUserAsSpectator.
  ///
  /// In zh, this message translates to:
  /// **'作为观战者（默认）'**
  String get gameUserAsSpectator;

  /// No description provided for @gameStartFailed.
  ///
  /// In zh, this message translates to:
  /// **'开始失败：{err}'**
  String gameStartFailed(Object err);

  /// No description provided for @gameAbort.
  ///
  /// In zh, this message translates to:
  /// **'解散'**
  String get gameAbort;

  /// No description provided for @gameAbortConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定解散本局？战绩不会被记录。'**
  String get gameAbortConfirm;

  /// No description provided for @gameSurrender.
  ///
  /// In zh, this message translates to:
  /// **'投降'**
  String get gameSurrender;

  /// No description provided for @gameSurrenderConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确认投降？单人/双人将直接判负，多人对局中你将转为观战。'**
  String get gameSurrenderConfirm;

  /// No description provided for @gameLoading.
  ///
  /// In zh, this message translates to:
  /// **'加载中…'**
  String get gameLoading;

  /// No description provided for @gameNoPlayers.
  ///
  /// In zh, this message translates to:
  /// **'请选择参与角色'**
  String get gameNoPlayers;

  /// No description provided for @gameWaiting.
  ///
  /// In zh, this message translates to:
  /// **'等待其他玩家…'**
  String get gameWaiting;

  /// No description provided for @gameFinished.
  ///
  /// In zh, this message translates to:
  /// **'对局结束'**
  String get gameFinished;

  /// No description provided for @gameWinLabel.
  ///
  /// In zh, this message translates to:
  /// **'赢家'**
  String get gameWinLabel;

  /// No description provided for @gameDrawLabel.
  ///
  /// In zh, this message translates to:
  /// **'平局'**
  String get gameDrawLabel;

  /// No description provided for @gameYourWord.
  ///
  /// In zh, this message translates to:
  /// **'你的词'**
  String get gameYourWord;

  /// No description provided for @gameYourRole.
  ///
  /// In zh, this message translates to:
  /// **'你的身份'**
  String get gameYourRole;

  /// No description provided for @gamePhase.
  ///
  /// In zh, this message translates to:
  /// **'阶段'**
  String get gamePhase;

  /// No description provided for @gameRound.
  ///
  /// In zh, this message translates to:
  /// **'回合'**
  String get gameRound;

  /// No description provided for @gameCurrentTurn.
  ///
  /// In zh, this message translates to:
  /// **'当前行动'**
  String get gameCurrentTurn;

  /// No description provided for @gameChooseTruthOrDare.
  ///
  /// In zh, this message translates to:
  /// **'选择真心话还是大冒险'**
  String get gameChooseTruthOrDare;

  /// No description provided for @gameSendMessage.
  ///
  /// In zh, this message translates to:
  /// **'输入…'**
  String get gameSendMessage;

  /// No description provided for @gameSend.
  ///
  /// In zh, this message translates to:
  /// **'发送'**
  String get gameSend;

  /// No description provided for @gameCancel.
  ///
  /// In zh, this message translates to:
  /// **'取消'**
  String get gameCancel;

  /// No description provided for @gameGuessWord.
  ///
  /// In zh, this message translates to:
  /// **'猜对方想的词'**
  String get gameGuessWord;

  /// No description provided for @gameAnswerYes.
  ///
  /// In zh, this message translates to:
  /// **'是'**
  String get gameAnswerYes;

  /// No description provided for @gameAnswerNo.
  ///
  /// In zh, this message translates to:
  /// **'否'**
  String get gameAnswerNo;

  /// No description provided for @gameAnswerPossible.
  ///
  /// In zh, this message translates to:
  /// **'可能'**
  String get gameAnswerPossible;

  /// No description provided for @gameAnswerUncertain.
  ///
  /// In zh, this message translates to:
  /// **'不确定'**
  String get gameAnswerUncertain;

  /// No description provided for @gameVoteFor.
  ///
  /// In zh, this message translates to:
  /// **'投票给'**
  String get gameVoteFor;

  /// No description provided for @gameDescribeHint.
  ///
  /// In zh, this message translates to:
  /// **'用一句话描述你的词（不能说出词）'**
  String get gameDescribeHint;

  /// No description provided for @gameAskHint.
  ///
  /// In zh, this message translates to:
  /// **'问一个是非问句'**
  String get gameAskHint;

  /// No description provided for @gameErrorMessage.
  ///
  /// In zh, this message translates to:
  /// **'操作失败：{err}'**
  String gameErrorMessage(Object err);

  /// No description provided for @gameRoomTitle.
  ///
  /// In zh, this message translates to:
  /// **'游戏房间'**
  String get gameRoomTitle;

  /// No description provided for @gameSpectatorView.
  ///
  /// In zh, this message translates to:
  /// **'观战视角'**
  String get gameSpectatorView;

  /// No description provided for @gameNoArchive.
  ///
  /// In zh, this message translates to:
  /// **'暂无对局记录'**
  String get gameNoArchive;

  /// No description provided for @gameHistoryTitle.
  ///
  /// In zh, this message translates to:
  /// **'游乐手札'**
  String get gameHistoryTitle;

  /// No description provided for @archivePlayerCount.
  ///
  /// In zh, this message translates to:
  /// **'{count} 人'**
  String archivePlayerCount(Object count);

  /// No description provided for @archiveRounds.
  ///
  /// In zh, this message translates to:
  /// **'{rounds} 回合'**
  String archiveRounds(Object rounds);

  /// No description provided for @archiveWinner.
  ///
  /// In zh, this message translates to:
  /// **'赢家：{names}'**
  String archiveWinner(Object names);

  /// No description provided for @archiveDraw.
  ///
  /// In zh, this message translates to:
  /// **'平局'**
  String get archiveDraw;

  /// No description provided for @archivePlayers.
  ///
  /// In zh, this message translates to:
  /// **'玩家'**
  String get archivePlayers;

  /// No description provided for @archiveTimeline.
  ///
  /// In zh, this message translates to:
  /// **'时间线'**
  String get archiveTimeline;

  /// No description provided for @archiveRoundLabel.
  ///
  /// In zh, this message translates to:
  /// **'第{round}回合'**
  String archiveRoundLabel(Object round);

  /// No description provided for @archiveWinnerSide.
  ///
  /// In zh, this message translates to:
  /// **'胜方'**
  String get archiveWinnerSide;

  /// No description provided for @gameKill.
  ///
  /// In zh, this message translates to:
  /// **'刀人'**
  String get gameKill;

  /// No description provided for @gameCheck.
  ///
  /// In zh, this message translates to:
  /// **'验人'**
  String get gameCheck;

  /// No description provided for @gameSpeak.
  ///
  /// In zh, this message translates to:
  /// **'发言'**
  String get gameSpeak;

  /// No description provided for @gameSpeakHint.
  ///
  /// In zh, this message translates to:
  /// **'说说你的判断（狼人杀白天）'**
  String get gameSpeakHint;

  /// No description provided for @gameDeclare.
  ///
  /// In zh, this message translates to:
  /// **'声明'**
  String get gameDeclare;

  /// No description provided for @gameDeclareHint.
  ///
  /// In zh, this message translates to:
  /// **'声明数字（1-10）'**
  String get gameDeclareHint;

  /// No description provided for @gameFollow.
  ///
  /// In zh, this message translates to:
  /// **'跟牌'**
  String get gameFollow;

  /// No description provided for @gameChallenge.
  ///
  /// In zh, this message translates to:
  /// **'质疑'**
  String get gameChallenge;

  /// No description provided for @gameChallengeHint.
  ///
  /// In zh, this message translates to:
  /// **'质疑上一家的声明'**
  String get gameChallengeHint;

  /// No description provided for @gameSoupAskHint.
  ///
  /// In zh, this message translates to:
  /// **'问一个是非问句（主持人是/否/可能/无关/不知道）'**
  String get gameSoupAskHint;

  /// No description provided for @gameSoupGuess.
  ///
  /// In zh, this message translates to:
  /// **'猜真相'**
  String get gameSoupGuess;

  /// No description provided for @gameSoupGuessHint.
  ///
  /// In zh, this message translates to:
  /// **'说出你猜的真相'**
  String get gameSoupGuessHint;

  /// No description provided for @gameHistoryEmpty.
  ///
  /// In zh, this message translates to:
  /// **'还没有对局记录'**
  String get gameHistoryEmpty;

  /// No description provided for @gameFilterAll.
  ///
  /// In zh, this message translates to:
  /// **'全部'**
  String get gameFilterAll;

  /// No description provided for @gameFilterWerewolf.
  ///
  /// In zh, this message translates to:
  /// **'狼人杀'**
  String get gameFilterWerewolf;

  /// No description provided for @gameFilterLiarsBar.
  ///
  /// In zh, this message translates to:
  /// **'骗子酒馆'**
  String get gameFilterLiarsBar;

  /// No description provided for @gameFilterTurtleSoup.
  ///
  /// In zh, this message translates to:
  /// **'海龟汤'**
  String get gameFilterTurtleSoup;

  /// No description provided for @gameFilterUndercover.
  ///
  /// In zh, this message translates to:
  /// **'谁是卧底'**
  String get gameFilterUndercover;

  /// No description provided for @gameFilterTruthOrDare.
  ///
  /// In zh, this message translates to:
  /// **'真心话大冒险'**
  String get gameFilterTruthOrDare;

  /// No description provided for @gameFilterTwentyQ.
  ///
  /// In zh, this message translates to:
  /// **'猜词20问'**
  String get gameFilterTwentyQ;

  /// No description provided for @accountLinking.
  ///
  /// In zh, this message translates to:
  /// **'账号关联'**
  String get accountLinking;

  /// No description provided for @accountLinkingHint.
  ///
  /// In zh, this message translates to:
  /// **'独立主账号 / 子账号关联'**
  String get accountLinkingHint;

  /// No description provided for @mainAccount.
  ///
  /// In zh, this message translates to:
  /// **'主账号'**
  String get mainAccount;

  /// No description provided for @subAccount.
  ///
  /// In zh, this message translates to:
  /// **'子账号'**
  String get subAccount;

  /// No description provided for @familyMemberCount.
  ///
  /// In zh, this message translates to:
  /// **'成员数 {count}'**
  String familyMemberCount(Object count);

  /// No description provided for @generateInvite.
  ///
  /// In zh, this message translates to:
  /// **'生成受邀码'**
  String get generateInvite;

  /// No description provided for @inviteCode.
  ///
  /// In zh, this message translates to:
  /// **'受邀码'**
  String get inviteCode;

  /// No description provided for @inviteCodeValidity.
  ///
  /// In zh, this message translates to:
  /// **'5 分钟内有效，一次性使用'**
  String get inviteCodeValidity;

  /// No description provided for @copyInvite.
  ///
  /// In zh, this message translates to:
  /// **'复制码'**
  String get copyInvite;

  /// No description provided for @kickSub.
  ///
  /// In zh, this message translates to:
  /// **'踢出'**
  String get kickSub;

  /// No description provided for @kickSubConfirm.
  ///
  /// In zh, this message translates to:
  /// **'确定踢出该子账号吗？'**
  String get kickSubConfirm;

  /// No description provided for @noSubAccounts.
  ///
  /// In zh, this message translates to:
  /// **'暂无子账号'**
  String get noSubAccounts;

  /// No description provided for @enterInviteCode.
  ///
  /// In zh, this message translates to:
  /// **'输入受邀码'**
  String get enterInviteCode;

  /// No description provided for @redeem.
  ///
  /// In zh, this message translates to:
  /// **'兑换'**
  String get redeem;

  /// No description provided for @redeemSuccess.
  ///
  /// In zh, this message translates to:
  /// **'关联成功'**
  String get redeemSuccess;

  /// No description provided for @redeemFailed.
  ///
  /// In zh, this message translates to:
  /// **'关联失败：{err}'**
  String redeemFailed(Object err);

  /// No description provided for @unlink.
  ///
  /// In zh, this message translates to:
  /// **'解除关联'**
  String get unlink;

  /// No description provided for @unlinkSuccess.
  ///
  /// In zh, this message translates to:
  /// **'已解除关联'**
  String get unlinkSuccess;

  /// No description provided for @unlinkFailed.
  ///
  /// In zh, this message translates to:
  /// **'解除失败：{err}'**
  String unlinkFailed(Object err);

  /// No description provided for @mainAccountNickname.
  ///
  /// In zh, this message translates to:
  /// **'主账号昵称'**
  String get mainAccountNickname;

  /// No description provided for @accountMainHint.
  ///
  /// In zh, this message translates to:
  /// **'你是独立主账号，可生成受邀码邀请子账号'**
  String get accountMainHint;

  /// No description provided for @accountSubHint.
  ///
  /// In zh, this message translates to:
  /// **'你是子账号，可兑换受邀码或解除关联'**
  String get accountSubHint;

  /// No description provided for @family.
  ///
  /// In zh, this message translates to:
  /// **'家庭'**
  String get family;

  /// No description provided for @healthRunningStatus.
  ///
  /// In zh, this message translates to:
  /// **'运行状态'**
  String get healthRunningStatus;

  /// No description provided for @healthAccessibility.
  ///
  /// In zh, this message translates to:
  /// **'无障碍服务'**
  String get healthAccessibility;

  /// No description provided for @healthAccessibilityNotConnected.
  ///
  /// In zh, this message translates to:
  /// **'系统已开但服务未连接'**
  String get healthAccessibilityNotConnected;

  /// No description provided for @healthNotificationAccess.
  ///
  /// In zh, this message translates to:
  /// **'通知读取'**
  String get healthNotificationAccess;

  /// No description provided for @healthNotificationNotConnected.
  ///
  /// In zh, this message translates to:
  /// **'系统已开但未连接'**
  String get healthNotificationNotConnected;

  /// No description provided for @healthShizukuAuthorized.
  ///
  /// In zh, this message translates to:
  /// **'已授权'**
  String get healthShizukuAuthorized;

  /// No description provided for @healthShizukuUnauthorized.
  ///
  /// In zh, this message translates to:
  /// **'未授权'**
  String get healthShizukuUnauthorized;

  /// No description provided for @healthShizukuNotRunning.
  ///
  /// In zh, this message translates to:
  /// **'未运行'**
  String get healthShizukuNotRunning;

  /// No description provided for @healthUsageAccess.
  ///
  /// In zh, this message translates to:
  /// **'使用情况访问'**
  String get healthUsageAccess;

  /// No description provided for @healthBatteryWhitelist.
  ///
  /// In zh, this message translates to:
  /// **'电池优化白名单'**
  String get healthBatteryWhitelist;

  /// No description provided for @healthBatteryAdded.
  ///
  /// In zh, this message translates to:
  /// **'已加入'**
  String get healthBatteryAdded;

  /// No description provided for @healthBatteryNotAdded.
  ///
  /// In zh, this message translates to:
  /// **'未加入（可能导致后台断开）'**
  String get healthBatteryNotAdded;

  /// No description provided for @relationTypeOther.
  ///
  /// In zh, this message translates to:
  /// **'其他'**
  String get relationTypeOther;

  /// No description provided for @gameWolfTeammates.
  ///
  /// In zh, this message translates to:
  /// **'🐺 狼队友：{team} 号'**
  String gameWolfTeammates(String team);

  /// No description provided for @gameHandCards.
  ///
  /// In zh, this message translates to:
  /// **'🃏 手牌：{cards}'**
  String gameHandCards(String cards);

  /// No description provided for @gameSeerChecks.
  ///
  /// In zh, this message translates to:
  /// **'🔮 查验：{checks}'**
  String gameSeerChecks(String checks);

  /// No description provided for @gameWolf.
  ///
  /// In zh, this message translates to:
  /// **'狼'**
  String get gameWolf;

  /// No description provided for @gameGoodPerson.
  ///
  /// In zh, this message translates to:
  /// **'好人'**
  String get gameGoodPerson;

  /// No description provided for @zoomIn.
  ///
  /// In zh, this message translates to:
  /// **'放大'**
  String get zoomIn;

  /// No description provided for @zoomOut.
  ///
  /// In zh, this message translates to:
  /// **'缩小'**
  String get zoomOut;

  /// No description provided for @resetView.
  ///
  /// In zh, this message translates to:
  /// **'复位'**
  String get resetView;

  /// No description provided for @streamSendFailed.
  ///
  /// In zh, this message translates to:
  /// **'流式发送失败：{err}'**
  String streamSendFailed(String err);

  /// No description provided for @imageSendFailed.
  ///
  /// In zh, this message translates to:
  /// **'图片发送失败：{err}'**
  String imageSendFailed(String err);

  /// No description provided for @emojiSendFailed.
  ///
  /// In zh, this message translates to:
  /// **'表情发送失败：{err}'**
  String emojiSendFailed(String err);

  /// No description provided for @noResponseFallback.
  ///
  /// In zh, this message translates to:
  /// **'TA 暂时没有回应你'**
  String get noResponseFallback;

  /// No description provided for @scopeAbility.
  ///
  /// In zh, this message translates to:
  /// **'能力'**
  String get scopeAbility;

  /// No description provided for @actionTarget.
  ///
  /// In zh, this message translates to:
  /// **'目标'**
  String get actionTarget;

  /// No description provided for @gameTimeoutMsg.
  ///
  /// In zh, this message translates to:
  /// **'加载超时，请检查网络后再试'**
  String get gameTimeoutMsg;

  /// No description provided for @notifChannelBackground.
  ///
  /// In zh, this message translates to:
  /// **'拥爱后台服务'**
  String get notifChannelBackground;

  /// No description provided for @notifChannelBackgroundDesc.
  ///
  /// In zh, this message translates to:
  /// **'后台轮询 AI 好友新消息的常驻通知'**
  String get notifChannelBackgroundDesc;

  /// No description provided for @notifRunningTitle.
  ///
  /// In zh, this message translates to:
  /// **'拥爱运行中'**
  String get notifRunningTitle;

  /// No description provided for @notifRunningDesc.
  ///
  /// In zh, this message translates to:
  /// **'正在后台监听 AI 好友的新消息'**
  String get notifRunningDesc;

  /// No description provided for @notifChannelAlert.
  ///
  /// In zh, this message translates to:
  /// **'重要提醒'**
  String get notifChannelAlert;

  /// No description provided for @notifChannelAlertDesc.
  ///
  /// In zh, this message translates to:
  /// **'查岗等重要通知'**
  String get notifChannelAlertDesc;

  /// No description provided for @emotionWave.
  ///
  /// In zh, this message translates to:
  /// **'情绪波动'**
  String get emotionWave;

  /// No description provided for @defaultNickname.
  ///
  /// In zh, this message translates to:
  /// **'用户'**
  String get defaultNickname;

  /// No description provided for @cooldown.
  ///
  /// In zh, this message translates to:
  /// **'冷却'**
  String get cooldown;

  /// No description provided for @unknownDetail.
  ///
  /// In zh, this message translates to:
  /// **'不详'**
  String get unknownDetail;

  /// No description provided for @performanceMotionTitle.
  ///
  /// In zh, this message translates to:
  /// **'性能与动效'**
  String get performanceMotionTitle;

  /// No description provided for @reduceMotionLabel.
  ///
  /// In zh, this message translates to:
  /// **'减少动效'**
  String get reduceMotionLabel;

  /// No description provided for @reduceMotionHint.
  ///
  /// In zh, this message translates to:
  /// **'关闭浮动、脉冲等持续动画'**
  String get reduceMotionHint;

  /// No description provided for @reduceBlurLabel.
  ///
  /// In zh, this message translates to:
  /// **'降低模糊效果'**
  String get reduceBlurLabel;

  /// No description provided for @reduceBlurHint.
  ///
  /// In zh, this message translates to:
  /// **'毛玻璃模糊减半，中低端机更流畅'**
  String get reduceBlurHint;

  /// No description provided for @memoryTraceTitle.
  ///
  /// In zh, this message translates to:
  /// **'记忆检索轨迹'**
  String get memoryTraceTitle;

  /// No description provided for @memoryTraceGroup.
  ///
  /// In zh, this message translates to:
  /// **'调试'**
  String get memoryTraceGroup;

  /// No description provided for @memoryTraceEmpty.
  ///
  /// In zh, this message translates to:
  /// **'暂无检索轨迹'**
  String get memoryTraceEmpty;

  /// No description provided for @memoryTraceQuery.
  ///
  /// In zh, this message translates to:
  /// **'查询'**
  String get memoryTraceQuery;

  /// No description provided for @memoryTraceRoute.
  ///
  /// In zh, this message translates to:
  /// **'路线'**
  String get memoryTraceRoute;

  /// No description provided for @memoryTraceCandidates.
  ///
  /// In zh, this message translates to:
  /// **'候选'**
  String get memoryTraceCandidates;

  /// No description provided for @memoryTraceLatency.
  ///
  /// In zh, this message translates to:
  /// **'延迟'**
  String get memoryTraceLatency;

  /// No description provided for @memoryTraceStatus.
  ///
  /// In zh, this message translates to:
  /// **'状态'**
  String get memoryTraceStatus;

  /// No description provided for @memoryTraceDense.
  ///
  /// In zh, this message translates to:
  /// **'稠密命中'**
  String get memoryTraceDense;

  /// No description provided for @memoryTraceSparse.
  ///
  /// In zh, this message translates to:
  /// **'稀疏命中'**
  String get memoryTraceSparse;

  /// No description provided for @memoryTraceRrf.
  ///
  /// In zh, this message translates to:
  /// **'RRF 融合'**
  String get memoryTraceRrf;

  /// No description provided for @memoryTraceRerank.
  ///
  /// In zh, this message translates to:
  /// **'重排 Top'**
  String get memoryTraceRerank;

  /// No description provided for @memoryTraceReturned.
  ///
  /// In zh, this message translates to:
  /// **'注入结果'**
  String get memoryTraceReturned;

  /// No description provided for @memoryTraceSteps.
  ///
  /// In zh, this message translates to:
  /// **'检索步骤'**
  String get memoryTraceSteps;

  /// No description provided for @memoryTraceNoData.
  ///
  /// In zh, this message translates to:
  /// **'暂无数据'**
  String get memoryTraceNoData;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'zh'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'zh':
      return AppLocalizationsZh();
  }

  throw FlutterError(
      'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
      'an issue with the localizations generation tool. Please file an issue '
      'on GitHub with a reproducible sample app and the gen-l10n configuration '
      'that was used.');
}
