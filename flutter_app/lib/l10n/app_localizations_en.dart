// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appName => 'AMBRACE';

  @override
  String get onboardingTitle => 'Welcome to AMBRACE';

  @override
  String get onboardingSubtitle =>
      'A few quick steps to start chatting with your AI companion';

  @override
  String get onboardingStepServer => 'Connect Server';

  @override
  String get onboardingStepAccount => 'Account';

  @override
  String get onboardingStepCharacter => 'Create Character';

  @override
  String get onboardingStepApiKey => 'API Key';

  @override
  String get onboardingServerTitle => 'Connect your server';

  @override
  String get onboardingServerDesc =>
      'Enter your server address and test the connection first.';

  @override
  String get onboardingAccountTitle => 'Log in or sign up';

  @override
  String get onboardingAccountDesc =>
      'Logging in keeps your conversations and memories.';

  @override
  String get onboardingAccountDone => 'Logged in';

  @override
  String get onboardingCharacterTitle => 'Create your first AI character';

  @override
  String get onboardingCharacterDesc =>
      'Name your AI companion and describe its personality in one line.';

  @override
  String get onboardingCharacterPersonalityLabel => 'One-line personality';

  @override
  String get onboardingCharacterPersonalityHint =>
      'e.g. Warm and caring, loves puns';

  @override
  String get onboardingCharacterCreate => 'Create Character';

  @override
  String get onboardingCharacterCreated => 'Character created';

  @override
  String get onboardingApiKeyTitle => 'Configure LLM API Key';

  @override
  String get onboardingApiKeyHint =>
      'AI needs this to reply; you can also configure it later in Settings.';

  @override
  String get onboardingApiKeyPreset => 'Provider preset';

  @override
  String get onboardingApiKeySaveDone => 'Save & Finish';

  @override
  String get onboardingApiKeySkip => 'Skip (set later)';

  @override
  String get onboardingApiKeySkipTip =>
      'Skipping takes you to Settings, where you can configure it later under \'Settings → API Config\'.';

  @override
  String get onboardingApiKeySaved => 'API Key saved';

  @override
  String get onboardingApiKeyEmpty => 'Please enter Base URL and API Key';

  @override
  String get onboardingApiTestOk => 'Configuration valid, connection OK';

  @override
  String get onboardingApiTestFail =>
      'Connection failed, check your configuration';

  @override
  String get onboardingFirstMessage => 'Hello';

  @override
  String get onboardingWarningUsername =>
      'Please enter a username and password';

  @override
  String get onboardingNext => 'Next';

  @override
  String get onboardingReRun => 'Re-run onboarding';

  @override
  String get onboardingReRunConfirm =>
      'Re-run the first-time guide? Your existing setup won\'t be lost.';

  @override
  String get abandon => 'Abandon';

  @override
  String get abandonConfirm =>
      'The pet will be sent away (deleted), and AI friends will remember this. Abandon it?';

  @override
  String get abandonFailed => 'Failed to abandon';

  @override
  String abandonTitle(Object name) {
    return 'Abandon $name?';
  }

  @override
  String abandoned(Object name) {
    return '$name has been abandoned';
  }

  @override
  String get about => 'About';

  @override
  String get actionCook => 'Cook';

  @override
  String get actionDone => ' done';

  @override
  String get actionEat => 'Eat';

  @override
  String get actionExercise => 'Exercise';

  @override
  String get actionGame => 'Play Games';

  @override
  String actionInProgress(Object action) {
    return '$action in progress…';
  }

  @override
  String get actionMusic => 'Listen to Music';

  @override
  String get actionRead => 'Read';

  @override
  String get actionShower => 'Shower';

  @override
  String get actionSleep => 'Sleep';

  @override
  String actionSucceeded(Object label) {
    return '$label succeeded';
  }

  @override
  String get actionTv => 'Watch TV';

  @override
  String get actionWork => 'Work';

  @override
  String get activeImageGen => 'Proactive Images';

  @override
  String get activeImageGenHint =>
      'AI sends images when fitting (sharing scenes, expressing mood)';

  @override
  String get agentMind => 'AI Inner World';

  @override
  String get agentMindEmpty => 'No records yet';

  @override
  String get agentMindReflection => 'Recent Reflection';

  @override
  String get agentMindTasks => 'Task Log';

  @override
  String get agentMindToolLogs => 'Tool Trace';

  @override
  String agentMindToolSummary(
      Object blocked, Object fail, Object ok, Object rate) {
    return 'Success rate $rate% (done $ok / failed $fail · blocked $blocked)';
  }

  @override
  String get agentMindMemorySearch => 'Memory Recall';

  @override
  String agentMindHitSummary(Object hit, Object miss, Object ms) {
    return 'Hit $hit / Miss $miss · Avg ${ms}ms';
  }

  @override
  String get agentMindSearchEmpty => 'No recall records yet';

  @override
  String get agentMindRunningNotes => 'Running Notes';

  @override
  String get agentMindIdentity => 'Identity Profile';

  @override
  String get agentMindPinned => 'Pinned Summaries';

  @override
  String get agentMindNoteEmpty => 'No running notes yet';

  @override
  String get activityBrowse => 'Browsing';

  @override
  String get activityLearn => 'Learning';

  @override
  String get activityLog => 'Interaction History';

  @override
  String get add => 'Add';

  @override
  String get addCommentHint => 'Write a comment...';

  @override
  String get addFailed => 'Add failed';

  @override
  String get addMember => 'Add Characters';

  @override
  String get adopt => 'Adopt';

  @override
  String get adoptFailed => 'Adoption failed';

  @override
  String get adoptFailedRetry => 'Adoption failed, please try again later';

  @override
  String adoptForChar(Object name) {
    return 'Adopted a pet for $name';
  }

  @override
  String get adoptForTa => 'Adopt for TA';

  @override
  String get adoptHeading => 'Adopt a Little Friend';

  @override
  String get adoptNewPet => 'Adopt a New Pet';

  @override
  String adoptPetFor(Object name) {
    return 'Adopt a Pet for $name';
  }

  @override
  String adoptSpecies(Object label) {
    return 'Adopt $label';
  }

  @override
  String get adoptSubtitle =>
      'A paper-style pet becomes part of the family, and AI friends will remember it';

  @override
  String get aiBrowseHistory => 'AI Browsing History';

  @override
  String get aiDiary => 'AI Diary';

  @override
  String get aiDiaryHint => 'AI writes a diary for the day\'s chat';

  @override
  String get aiFriendFallback => 'AI Friend';

  @override
  String get aiGenerated => 'AI Generated';

  @override
  String get aiLife => 'AI Life';

  @override
  String get aiLifeHint => 'TA\'s life, interests & creations';

  @override
  String get aiOfflineLife => 'AI Offline Life';

  @override
  String get aiOfflineLifeHint =>
      'Characters really live when offline: state changes, rest, reflection, memory upkeep (on by default)';

  @override
  String get aiPetsSubtitle =>
      'Visit TA\'s pets to feed / play / clean; if TA has none, you can adopt one for them.';

  @override
  String get aiPetsTitle => 'AI Characters\' Pets';

  @override
  String get aiPrivateChat => 'AI Private Chat';

  @override
  String get aiPrivateChatHint =>
      'When enabled, your AI characters chat privately from time to time';

  @override
  String get aiPromised => 'AI promised';

  @override
  String get aiSchedule => 'AI Schedule';

  @override
  String get aiWantsToCall => 'TA wants to use';

  @override
  String get albumTitle => 'Album';

  @override
  String get allow => 'Allow';

  @override
  String get featureFlagsTitle => 'Server Management';

  @override
  String get featureFlagsHint => 'Take effect immediately, no restart needed';

  @override
  String get featureFlagsAdminOnly =>
      'Only the main account can manage server features';

  @override
  String get flagLightReply => 'Light group reply mode';

  @override
  String get flagLightReplyHint =>
      'Group / platform replies use lean context, faster and cheaper';

  @override
  String get flagGroupRuntime => 'Unified group reply runtime';

  @override
  String get flagGroupRuntimeHint =>
      'Group replies use character memories; characters do not know each others private matters';

  @override
  String get flagDouyinRuntime => 'Unified platform reply runtime';

  @override
  String get flagDouyinRuntimeHint => 'Platform replies use character memories';

  @override
  String get flagAdvanced => 'Advanced switches';

  @override
  String get flagAdvancedHint => 'Advanced switches, adjust with caution';

  @override
  String get flagSaved => 'Saved';

  @override
  String get flagError => 'Switch failed, please retry';

  @override
  String get flagWeave3D => 'Weave 3D (experimental)';

  @override
  String get flagWeave3DHint =>
      '3D sphere view for the weave canvas (experimental; turn off on low-end devices)';

  @override
  String get apiConfig => 'API Config';

  @override
  String get apiConfigHint => 'LLM / Image generation (BYOK & server-level)';

  @override
  String get appAlbum => 'Album';

  @override
  String get appBrowser => 'Browser';

  @override
  String get appCalendar => 'Calendar';

  @override
  String get appChat => 'Chats';

  @override
  String get appDescAlbum => 'AI images + my uploads';

  @override
  String get appDescBrowser => 'Browser extension · search history kept 7 days';

  @override
  String get appDescCalendar => 'View / write notes, AI can see';

  @override
  String get appDescChat => 'Chat with characters';

  @override
  String get appDescMarket => 'Restore removed apps';

  @override
  String get appDescMemo => 'Notes, AI may add too';

  @override
  String get appDescSettings => 'Virtual phone (placeholder)';

  @override
  String get appDescTheme => 'Wallpapers and beautification';

  @override
  String get appMarket => 'App Store';

  @override
  String get appMemo => 'Notes';

  @override
  String get appPets => 'Pets';

  @override
  String get appSettings => 'Settings';

  @override
  String get appTheme => 'Theme';

  @override
  String get appearance => 'Appearance';

  @override
  String get appearanceHint => 'e.g. long hair, glasses, gentle look';

  @override
  String get appearanceTitle => 'Appearance';

  @override
  String get archiveBox => 'Chat Archive';

  @override
  String archiveTitle(Object a, Object b) {
    return '$a · $b Chat Archive';
  }

  @override
  String get arrangement => 'Plan';

  @override
  String get artifactImage => 'Artwork';

  @override
  String get artifactNote => 'Note';

  @override
  String get artifactText => 'Writing';

  @override
  String get artifactsTab => 'Creations';

  @override
  String artifactsTitle(Object name) {
    return '$name\'s Creations';
  }

  @override
  String get avatarUpdateFailed => 'Failed to update avatar';

  @override
  String get avatarUpdated => 'Avatar updated';

  @override
  String get back => 'Back';

  @override
  String get backgroundInfo => 'Background';

  @override
  String get basic => 'Basic';

  @override
  String get birthday => 'Birthday';

  @override
  String get browserTitle => 'Browser';

  @override
  String get browsingHint =>
      'With \"AI Offline Life\" on and browser access granted, TA really browses the web';

  @override
  String browsingTitle(Object name) {
    return '$name · Browsing History';
  }

  @override
  String get calendarHint =>
      'Tap a date to view/add notes (AI can sense recent notes)';

  @override
  String calendarTitle(Object y, Object m) {
    return '$y/$m';
  }

  @override
  String get wfDefaultName => 'Workflow';

  @override
  String wfImportConfirm(Object name) {
    return 'Import “$name”?';
  }

  @override
  String get wfImportNoTemplates => 'No workflow-type plugin templates';

  @override
  String get wfImportSuccess => 'Workflow imported';

  @override
  String get wfImportTemplates => 'Import from plugin templates';

  @override
  String chatRunWf(Object name) {
    return 'Run “$name”';
  }

  @override
  String chatWfSteps(Object count) {
    return '$count steps in order; sensitive steps need your confirmation';
  }

  @override
  String chatWfDone(Object summary) {
    return 'Workflow finished: $summary';
  }

  @override
  String chatWfInterrupted(Object summary) {
    return 'Workflow interrupted: $summary';
  }

  @override
  String chatWfStep(Object mark, Object msg, Object step) {
    return 'Step $step $mark $msg';
  }

  @override
  String get chatNoAccessibility =>
      'Accessibility service is off; cannot perform actions';

  @override
  String chatSeqInterrupted(Object summary) {
    return 'Sequence interrupted: $summary';
  }

  @override
  String chatSeqDone(Object summary) {
    return 'Sequence finished: $summary';
  }

  @override
  String get chatPickTarget =>
      'AI wants to interact with the screen; pick a target';

  @override
  String get nodeClickable => 'Clickable';

  @override
  String get nodeInput => 'Input field';

  @override
  String get chatNoNodes => 'No actionable nodes on screen';

  @override
  String get chatOpDone => 'Done';

  @override
  String get seqReply => 'Reply message';

  @override
  String get seqPublish => 'Post moment';

  @override
  String get seqLike => 'Like';

  @override
  String get seqPlay => 'Play / switch track';

  @override
  String get seqCombo => 'Combined action';

  @override
  String seqInputLine(Object text) {
    return '· Type “$text”';
  }

  @override
  String get seqClick => 'Tap';

  @override
  String get seqLongClick => 'Long press';

  @override
  String seqClickLine(Object target, Object verb) {
    return '· $verb “$target”';
  }

  @override
  String get chatSeqTitle => 'AI wants to operate your phone in sequence';

  @override
  String chatSeqDesc(Object autoNote, Object steps, Object type) {
    return 'Scene: $type\n$steps\n\n${autoNote}Interference: light (default) — no auto app switching; page changes are manual; password/bank/payment nodes are auto-rejected.';
  }

  @override
  String get chatSeqAutoNote =>
      'Will switch to the Moments page (in-app navigation only).\n';

  @override
  String get reject => 'Reject';

  @override
  String get allowOnce => 'Allow once';

  @override
  String get allowMinute => 'Allow 1 min';

  @override
  String get chatOpDefault => 'Action';

  @override
  String chatOpTarget(Object target) {
    return 'Tap / long press “$target”';
  }

  @override
  String get chatOpInput => 'Type text';

  @override
  String get chatOpTitle => 'AI wants to operate your phone';

  @override
  String chatOpDesc(Object op) {
    return 'Action: $op\nOnly affects the current visible page, no cross-app jumps; password/bank/payment nodes are auto-rejected.';
  }

  @override
  String get chatInputTitle => 'AI wants to type for you';

  @override
  String get chatInputHint => 'Type content (≤50 chars)';

  @override
  String chatInputHintTarget(Object target) {
    return 'Type into “$target” (≤50 chars)';
  }

  @override
  String get input => 'Enter';

  @override
  String chatFileSendFail(Object err) {
    return 'File send failed: $err';
  }

  @override
  String get chatContinuous => 'Continuous send';

  @override
  String get chatVoiceSend => 'Voice send';

  @override
  String get chatEmoji => 'Emoji';

  @override
  String get chatSendImage => 'Send image';

  @override
  String get chatImageCaption => 'Add a caption (optional)...';

  @override
  String get send => 'Send';

  @override
  String chatEmojiDownloaded(Object name) {
    return 'Downloaded “$name”';
  }

  @override
  String get chatEmojiAdd => 'Add';

  @override
  String chatEmojiHint(Object desc) {
    return '$desc (tap to download and send)';
  }

  @override
  String get emojiMarketTab => 'Market';

  @override
  String emojiMarketEmojiCount(Object count) {
    return '$count emojis';
  }

  @override
  String get emojiMarketDownloading => 'Downloading…';

  @override
  String get emojiMarketDownloadFail => 'Download failed, please retry';

  @override
  String get emojiMarketUninstall => 'Uninstall';

  @override
  String get emojiMarketUninstalled => 'Uninstalled';

  @override
  String get emojiMarketEmpty => 'No emoji packs in the marketplace yet';

  @override
  String get emojiMarketUnavailable =>
      'Marketplace is unavailable, please try again later';

  @override
  String get chatMicPermission =>
      'Microphone permission is required to send voice';

  @override
  String get tzDefault => 'Default (Beijing UTC+8)';

  @override
  String get tzBeijing => 'Beijing UTC+8';

  @override
  String get tzTokyo => 'Tokyo UTC+9';

  @override
  String get tzDubai => 'Dubai UTC+4';

  @override
  String get tzMoscow => 'Moscow UTC+3';

  @override
  String get tzParis => 'Paris UTC+1';

  @override
  String get tzLondon => 'London UTC+0';

  @override
  String get tzNewYork => 'New York UTC-5';

  @override
  String get tzLosAngeles => 'Los Angeles UTC-8';

  @override
  String get tzSydney => 'Sydney UTC+10';

  @override
  String get voiceXiaoxiao => 'Xiaoxiao · Natural female';

  @override
  String get voiceXiaoyi => 'Xiaoyi · Young female';

  @override
  String get voiceXiaobei => 'Xiaobei · NE China female';

  @override
  String get voiceXiaoni => 'Xiaoni · Shaanxi female';

  @override
  String get voiceXiaojia => 'Xiaojia · Cantonese female';

  @override
  String get voiceXiaoman => 'Xiaoman · Cantonese female';

  @override
  String get voiceXiaozhen => 'Xiaozhen · Taiwan female';

  @override
  String get voiceYunxi => 'Yunxi · Youth male';

  @override
  String get voiceYunjian => 'Yunjian · Magnetic male';

  @override
  String get voiceYunyang => 'Yunyang · News male';

  @override
  String get voiceYunfeng => 'Yunfeng · Mature male';

  @override
  String get voiceYunlong => 'Yunlong · Cantonese male';

  @override
  String get voicePreviewFailConfig =>
      'Preview failed: TTS unavailable, check server voice config';

  @override
  String get voicePreviewFailNet => 'Preview failed, check network';

  @override
  String get avatarUploadFail => 'Avatar upload failed';

  @override
  String get voiceRate => 'Rate';

  @override
  String get voicePitch => 'Pitch';

  @override
  String get pitchNormal => 'Normal';

  @override
  String get saveFail => 'Save failed';

  @override
  String get createFriend => 'Create friend';

  @override
  String get editFriend => 'Edit friend';

  @override
  String get save => 'Save';

  @override
  String get tapToPickAvatar => 'Tap to pick avatar (optional)';

  @override
  String get basicInfo => 'Basic info';

  @override
  String get name => 'Name';

  @override
  String get nameRequired => 'Name cannot be empty';

  @override
  String get heightCm => 'Height (cm)';

  @override
  String get weightKg => 'Weight (kg)';

  @override
  String get birthdayHint => 'YYYY-MM-DD (e.g. 1998-05-20)';

  @override
  String get gender => 'Gender';

  @override
  String get genderOther => 'Other';

  @override
  String get genderFemale => 'Female';

  @override
  String get genderMale => 'Male';

  @override
  String get backgroundInfoHint =>
      'e.g. origin, experience, personality roots; more detail helps';

  @override
  String get timezone => 'Timezone';

  @override
  String get timezoneHelper =>
      'Character\'s timezone; Moments time shown by it (default=Beijing)';

  @override
  String get personalityGroup => 'Personality';

  @override
  String get personality => 'Personality';

  @override
  String get personalityHint => 'e.g. gentle and considerate';

  @override
  String get chatStyleHint => 'e.g. soft-spoken, likes emojis';

  @override
  String get greeting => 'Greeting';

  @override
  String get greetingHint => 'e.g. Hi! Nice to meet you~';

  @override
  String get voiceGroup => 'Voice';

  @override
  String get voiceLabel => 'Voice';

  @override
  String get voiceHelper => 'Voice used in voice chat (default=by gender)';

  @override
  String get voiceDefault => 'Default (by gender)';

  @override
  String get previewing => 'Previewing…';

  @override
  String get previewVoice => 'Preview voice';

  @override
  String get previewHint => 'Fixed text synthesis for instant preview';

  @override
  String get deleteFriend => 'Delete friend';

  @override
  String get confirmDelete => 'Confirm delete';

  @override
  String deleteFriendConfirm(Object name) {
    return 'Delete “$name”?\nChat history and memories will be removed permanently.';
  }

  @override
  String get delete => 'Delete';

  @override
  String get deleteFail => 'Delete failed';

  @override
  String get usageStats => 'Usage stats';

  @override
  String get myLlm => 'My LLM (BYOK)';

  @override
  String get myLlmHint =>
      'Only for chat main flow: your own OpenAI-compatible endpoint takes priority over server config';

  @override
  String get enable => 'Enable';

  @override
  String get apiKeyConfigured => 'API Key configured';

  @override
  String get apiKeyNotConfigured => 'API Key not configured, fill in below';

  @override
  String get apiKeyNotConfiguredShort => 'API Key not configured';

  @override
  String get apiKeyKeep => 'API Key (leave blank to keep)';

  @override
  String get apiKeyHintReplace =>
      'Configured; enter new Key to replace (comma-separated rotates)';

  @override
  String get testConnection => 'Test Connection';

  @override
  String get saveMyConfig => 'Save my config';

  @override
  String get srvLlm => 'Server LLM (global)';

  @override
  String get srvLlmHint =>
      'Main account only: affects all non-BYOK calls (diary/moments/memory etc.)';

  @override
  String get llmPresets => 'LLM provider presets';

  @override
  String get apiKeyRotateHint => 'Comma-separated keys rotate automatically';

  @override
  String get saveSrvLlm => 'Save server LLM';

  @override
  String get srvSpeech => 'Server speech model';

  @override
  String get srvSpeechHint =>
      'ASR currently uses local faster-whisper; cloud ASR config stored for later';

  @override
  String get speechPresets => 'Speech provider presets';

  @override
  String get saveSrvSpeech => 'Save server speech';

  @override
  String get srvVlm => 'Server vision (image understanding)';

  @override
  String get srvVlmHint =>
      'For chat/phone perception image reading: API Key prefers cloud vision, else local OCR (optional local VLM)';

  @override
  String get vlmPresets => 'Vision provider presets';

  @override
  String get saveSrvVlm => 'Save server vision';

  @override
  String get srvImageGen => 'Server image gen (global)';

  @override
  String get srvImageGenHint =>
      'Used when AI sends images in chat; provider: dashscope / openai-compatible';

  @override
  String get imagePresets => 'Image gen provider presets';

  @override
  String get dailyLimit => 'Daily limit (images)';

  @override
  String get saveSrvImageGen => 'Save server image gen';

  @override
  String get srvMultimodal => 'Server multimodal model';

  @override
  String get srvMultimodalHint =>
      'Multimodal understanding (image/audio/video to LLM); config stored for later';

  @override
  String get multimodalPresets => 'Multimodal provider presets';

  @override
  String get saveSrvMultimodal => 'Save server multimodal';

  @override
  String get srvTask => 'Server task model (per-purpose)';

  @override
  String get srvTaskHint =>
      'Memory/card/emotion/state/review/proactive/diary/timeline can each have a model; comma-separated keys rotate; blank falls back to server LLM';

  @override
  String get task => 'Task';

  @override
  String get taskHint => 'Select a task to assign a model';

  @override
  String get saveTaskConfig => 'Save task config';

  @override
  String get srvAdminOnly =>
      'Server config is managed by the main account only; contact the deployer.';

  @override
  String saveSuccessEnabled(Object enabled) {
    return 'Saved (enabled=$enabled)';
  }

  @override
  String saveFailedErr(Object err) {
    return 'Save failed: $err';
  }

  @override
  String loadConfigFailed(Object err) {
    return 'Failed to load config: $err';
  }

  @override
  String connSuccess(Object latency, Object model, Object tail) {
    return 'Connected: $model (${latency}ms, key tail $tail)';
  }

  @override
  String connFailed(Object err) {
    return 'Connection failed: $err';
  }

  @override
  String testRequestFailed(Object err) {
    return 'Test request failed: $err';
  }

  @override
  String get presetSelectHint =>
      'Auto-fills on select; Key still needs manual entry';

  @override
  String get model => 'Model';

  @override
  String get provider => 'Provider';

  @override
  String get setQuotaTotal => 'Set free quota total';

  @override
  String get quotaHint => 'Unit: tokens (e.g. 1000000)';

  @override
  String get quotaCleared => 'Quota total cleared';

  @override
  String get quotaUpdated => 'Quota updated';

  @override
  String get saveFailed => 'Save failed';

  @override
  String unitYi(Object n) {
    return '${n}B';
  }

  @override
  String unitWan(Object n) {
    return '${n}K';
  }

  @override
  String get llmUsageStats => 'LLM usage stats';

  @override
  String get loadFailedCheckServer => 'Failed to load, check server connection';

  @override
  String get usedTotal => 'Used / total';

  @override
  String get setQuota => 'Set quota';

  @override
  String totalTokensNoQuota(Object total) {
    return '$total tokens (no quota set)';
  }

  @override
  String remainingTokens(Object remaining) {
    return 'About $remaining tokens left';
  }

  @override
  String get today => 'Today';

  @override
  String get last7Days => 'Last 7 days';

  @override
  String get thisMonth => 'This month';

  @override
  String get byModelUsage => 'Usage by model';

  @override
  String get unknown => 'Unknown';

  @override
  String etcModels(Object count) {
    return 'and $count more models';
  }

  @override
  String get usageNote =>
      'Usage is auto-accumulated per LLM call (approximate, unofficial); set the total per the console\'s free quota';

  @override
  String get ppEnabledOn =>
      'Phone perception enabled; turn on the items you need below';

  @override
  String get ppEnabledOff => 'Phone perception disabled';

  @override
  String get ppOpenAccessibility =>
      'Please enable \"AMBRACE Phone Perception\" in system accessibility settings';

  @override
  String get ppUsageNotGranted =>
      'Usage access permission not detected; check system settings and retry';

  @override
  String ppUsageGrantedWith(Object content) {
    return 'Granted; app usage tracking enabled: $content';
  }

  @override
  String get ppUsageGrantedEmpty =>
      'Granted; app usage tracking enabled (no data yet, will auto-upload)';

  @override
  String get ppUsageOpenSettings =>
      'Allow \"AMBRACE\" in system Usage Access; it takes effect when you return';

  @override
  String ppUsageEnabledWith(Object content) {
    return 'App usage tracking enabled: $content';
  }

  @override
  String get ppUsageEnabledEmpty =>
      'App usage tracking enabled (no data yet, will auto-upload)';

  @override
  String get ppUsageDisabled => 'App usage tracking disabled';

  @override
  String get ppOpenNotification =>
      'Please enable \"AMBRACE Notification Perception\" in system notification access';

  @override
  String get ppMediaDenied =>
      'No gallery permission; allow photo access in system settings';

  @override
  String get ppMediaFilesDenied =>
      'No video/audio permission; allow it in system settings';

  @override
  String ppCollectedWith(Object preview) {
    return 'Collected: $preview';
  }

  @override
  String get ppCollectDisabled =>
      'Phone perception is off; enable the master switch first';

  @override
  String get ppCollectNoSources => 'No sources selected; check the items below';

  @override
  String get ppCollectEmpty =>
      'Nothing to collect (maybe not on an in-app page)';

  @override
  String get ppCollectNetworkError => 'Upload failed; check network and retry';

  @override
  String get ppCollectDone => 'Collection done';

  @override
  String get ppClearedAll => 'All phone perception snapshots cleared';

  @override
  String get ppClearFailed => 'Clear failed; try again later';

  @override
  String ppShizukuUploadFailed(Object text) {
    return '$text (upload failed)';
  }

  @override
  String get ppLocEnabledOn => 'Location info enabled; AI can sense your city';

  @override
  String get ppLocEnabledOff => 'Location info disabled';

  @override
  String ppLocGpsEnabledWith(Object loc) {
    return 'Auto geolocation enabled: $loc';
  }

  @override
  String get ppLocGpsDisabled => 'Auto geolocation disabled';

  @override
  String get ppLocServiceOff =>
      'Phone location service is off; turn it on in system settings';

  @override
  String get ppLocDeniedForever =>
      'Location permission permanently denied; grant it manually in system settings';

  @override
  String get ppLocNoPermission =>
      'No location permission; cannot get geolocation';

  @override
  String ppLocFailed(Object err) {
    return 'Location failed: $err. Retry near a window/outdoors, or enable \"Improve accuracy\" (Wi-Fi/Bluetooth scanning) in system location services';
  }

  @override
  String ppLocCityLocated(Object city) {
    return '$city (located, not editable)';
  }

  @override
  String ppLocCoordsLocated(Object lat, Object lng) {
    return '$lat,$lng (located)';
  }

  @override
  String get ppLocLocating => 'Locating… (auto geolocation enabled)';

  @override
  String get ppLocUnset => 'Not set, tap to fill';

  @override
  String ppLocFollowUser(Object loc) {
    return 'Same as user location: $loc';
  }

  @override
  String get ppLocNotSet => 'Not set';

  @override
  String ppLocUser(Object loc) {
    return 'User: $loc';
  }

  @override
  String get ppLocFollow => 'AI follows user';

  @override
  String get ppLocGpsOn => 'Geolocation on';

  @override
  String get ppLocUnsetExpand => 'No location set; expand to configure';

  @override
  String get ppLocSetUser => 'Set user location';

  @override
  String get ppLocSetAi => 'Set AI location';

  @override
  String get ppLocHint => 'e.g. Guangzhou / Beijing / Tokyo';

  @override
  String get ppSourceScreen => 'Screen';

  @override
  String get ppSourceClipboard => 'Clipboard';

  @override
  String get ppSourceMedia => 'Gallery';

  @override
  String get ppSourceNotification => 'Notifications';

  @override
  String get ppClipboard => 'Clipboard';

  @override
  String get ppSubtitleOn =>
      'When on, AI friends can learn your phone status with your permission';

  @override
  String get ppSubtitleOff =>
      'Off by default; choose sources below after enabling';

  @override
  String get ppGroupSources => 'Sources';

  @override
  String get ppScreenTitle => 'Screen reader (accessibility)';

  @override
  String get ppScreenRunning =>
      'Service running; caches recent text from non-app pages';

  @override
  String get ppScreenOff =>
      'Off; tap to enable in system accessibility settings';

  @override
  String get ppClipboardSub =>
      'Reads your recently copied content (foreground only, during chat)';

  @override
  String get ppMediaTitle => 'Recent gallery images';

  @override
  String get ppMediaSub =>
      'Reads file names and times of the last 8 images (not their content)';

  @override
  String get ppMediaFilesTitle => 'Media files (video/audio/docs)';

  @override
  String get ppMediaFilesSub =>
      'Reads file names and times of recent video/audio/docs (metadata only; docs need \"All files access\")';

  @override
  String get ppUsageStatsTitle => 'App usage time';

  @override
  String get ppUsageStatsGranted =>
      'App usage over the last 24h, auto-uploaded to AI every 30 min';

  @override
  String get ppUsageStatsNotGranted =>
      'Not authorized: after enabling, allow it in system Usage Access';

  @override
  String get ppActionsTitle => 'Simulated actions';

  @override
  String get ppActionsOn =>
      'AI can tap/long-press/swipe/type after your confirmation (current screen nodes only; sensitive pages rejected; off by default)';

  @override
  String get ppActionsOff =>
      'Off by default: AI operates your phone after your one-time confirmation';

  @override
  String get ppWorkflowTitle => 'Custom workflows';

  @override
  String get ppWorkflowSub =>
      'Build multi-step action sequences; say \"run XX for me\" to trigger (system-level actions need Shizuku)';

  @override
  String get ppNotificationTitle => 'Notification reading';

  @override
  String get ppNotifRunning =>
      'Service running; caches recent app notification text';

  @override
  String get ppNotifOff => 'Off; tap to enable in system notification access';

  @override
  String get ppAutoNotifyTitle => 'AI proactively mentions notifications';

  @override
  String get ppAutoNotifySub =>
      'AI checks about every 5 minutes and mentions new notifications (off by default)';

  @override
  String get ppWhitelistTitle => 'Notification whitelist';

  @override
  String get ppWhitelistSub =>
      'Only perceive notifications from selected apps (all by default)';

  @override
  String get ppShizukuTitle => 'Shizuku permission';

  @override
  String get ppShizukuSub =>
      'System-level capabilities (app list/system settings/needed for simulated actions): status, authorization, and shell tests';

  @override
  String get ppShizukuServer => 'Shizuku service';

  @override
  String get ppShizukuGranted => 'App authorized';

  @override
  String get ppReady => 'Ready';

  @override
  String get ppNotReady => 'Not ready';

  @override
  String get ppCollecting => 'Collecting…';

  @override
  String get ppCollectShizuku => 'Collect system status and tell AI';

  @override
  String get ppGroupLocation => 'Location';

  @override
  String get ppLocationTitle => 'Location info';

  @override
  String get ppLocSubtitleOn =>
      'AI can sense your city for more natural time perception';

  @override
  String get ppLocSubtitleOff =>
      'Off by default: AI knows where you are only after enabling';

  @override
  String get ppLocGpsTitle => 'Auto geolocation';

  @override
  String get ppLocGpsOnSub => 'On: user location comes from GPS, not editable';

  @override
  String get ppLocGpsOffSub => 'Auto-fetches your location when enabled';

  @override
  String get ppLocUserTitle => 'User location';

  @override
  String get ppLocAiTitle => 'AI location';

  @override
  String get ppLocFollowTitle => 'Location follow';

  @override
  String get ppLocFollowOnSub => 'AI location same as user, not editable';

  @override
  String get ppLocFollowOffSub =>
      'AI location follows user location when enabled';

  @override
  String get ppGroupPrivacy => 'Privacy notes';

  @override
  String get ppPrivacyNote =>
      '· All capabilities are off by default; each is authorized individually, and turning off takes effect immediately\n· Only text and image metadata are read; images use local OCR/VLM for understanding, never uploaded to cloud models\n· Password fields and banking/payment pages are skipped automatically\n· Data is sent only to your own server; snapshots expire after 30 minutes, up to 20 kept';

  @override
  String get ppGroupActions => 'Actions & history';

  @override
  String get ppCollectNowTitle => 'Collect now';

  @override
  String get ppCollectNowSub =>
      'Collect current screen/clipboard/gallery and tell AI';

  @override
  String get ppHistoryTitle => 'History';

  @override
  String get ppNoSnapshots => 'No snapshots';

  @override
  String ppRecentCount(Object count) {
    return 'Recent $count';
  }

  @override
  String get ppLockContentName => 'Phone';

  @override
  String get ppClearAll => 'Clear all snapshots';

  @override
  String ppLocAi(Object loc) {
    return 'AI: $loc';
  }

  @override
  String get providerLocalHint => 'openai / dashscope / local';

  @override
  String get worldGroup => 'Character Settings';

  @override
  String get lorebookTitle => 'Lorebook Entries';

  @override
  String get lorebookHint =>
      'Keyword-triggered fixed settings, effective when mentioned';

  @override
  String get lorebookAdd => 'Add Entry';

  @override
  String get lorebookEdit => 'Edit Entry';

  @override
  String get lorebookTitleField => 'Title';

  @override
  String get lorebookContentField => 'Content';

  @override
  String get lorebookKeywords => 'Keywords (>=2 chars, comma separated)';

  @override
  String get lorebookKeywordsHint =>
      'Injected when any keyword appears, e.g. my cat';

  @override
  String get lorebookExclude => 'Excluded words (skip when present)';

  @override
  String get lorebookExcludeHint =>
      'Prevents false triggers, e.g. cat poop coffee';

  @override
  String get lorebookEmpty => 'No lorebook entries yet';

  @override
  String get lorebookActive => 'Enabled';

  @override
  String get lorebookStyleHint =>
      'Describe concisely in third person (e.g. User has an orange cat named Mangmang)';

  @override
  String get worldFactsTitle => 'World Settings';

  @override
  String get worldFactsHint =>
      'Unyielding facts you define; AI inference cannot override';

  @override
  String get worldFactsEmpty => 'No world settings yet';

  @override
  String get worldFactAdd => 'Add Setting';

  @override
  String get worldFactContentHint =>
      'e.g. I live in Changsha / I have a cat named Mangmang';

  @override
  String get cancel => 'Cancel';

  @override
  String get cancelled => 'Cancelled';

  @override
  String get changeFailed => 'Update failed';

  @override
  String get changePassword => 'Change Password';

  @override
  String get changePasswordHint => 'At least 8 chars, letters and numbers';

  @override
  String get charLife => 'Life';

  @override
  String charPetTitle(Object char, Object pet) {
    return '$char\'s $pet';
  }

  @override
  String get charSettings => 'Character Settings';

  @override
  String get chatArchive => 'Chat Archive';

  @override
  String get chatArchiveHint => 'Past chat history';

  @override
  String chatOf(Object name) {
    return '$name\'s Chats';
  }

  @override
  String get chatStyle => 'Chat Style';

  @override
  String get checkIn => 'Check-In';

  @override
  String get checkInHint =>
      'When AI reaches out, it naturally knows which app you\'re using';

  @override
  String get checking => 'Checking...';

  @override
  String get chooseFriendFirst =>
      'Please select a friend in the Friends tab first';

  @override
  String get chooseSpecies => 'Choose Species';

  @override
  String get clean => 'Clean';

  @override
  String get cleanliness => 'Cleanliness';

  @override
  String get close => 'Close';

  @override
  String get cognitiveLoop => 'Cognitive Loop';

  @override
  String get cognitiveLoopHint =>
      'AI keeps your current state, ongoing topics & relationship closeness in mind, so chats and proactive messages understand you better (off by default)';

  @override
  String get coldWar => 'Cold War Silence';

  @override
  String get coldWarHint => 'Won\'t reply during a cold war until you make up';

  @override
  String get collapse => 'Collapse';

  @override
  String get comingSoon => 'Coming Soon';

  @override
  String comingSoonTemplate(Object feature) {
    return '$feature coming soon';
  }

  @override
  String get commentHint => 'Write a comment...';

  @override
  String get completed => 'Completed';

  @override
  String get confirm => 'Confirm';

  @override
  String get connectFail => 'Connection failed, check the address';

  @override
  String get connectFailed => 'Connection failed';

  @override
  String get connectSuccess => 'Connected successfully';

  @override
  String get connected => 'Connected';

  @override
  String get connectionStatus => 'Connection';

  @override
  String get content => 'Content';

  @override
  String get contentAiHint => 'Content (AI friends will read it in chat)';

  @override
  String get contentRequired => 'Content cannot be empty';

  @override
  String get control => 'Control';

  @override
  String get controlComingSoon => 'Control is still in design - stay tuned';

  @override
  String get controlHint => 'Sub-feature of Check-In (design pending)';

  @override
  String get copied => 'Copied';

  @override
  String get copy => 'Copy';

  @override
  String get create => 'Create';

  @override
  String get createGroup => 'Create Group';

  @override
  String get createGroupDialog => 'Create Family Group Chat';

  @override
  String get createRoleHint =>
      'Create an AI character first\nEach character has their own phone\nFamily group chat is under development';

  @override
  String get creationGroup => 'Creation';

  @override
  String currentPreview(Object mode, Object color) {
    return 'Current: $mode · $color';
  }

  @override
  String get currentUser => 'Current User';

  @override
  String get dailyGroup => 'Daily';

  @override
  String get dark => 'Dark';

  @override
  String get date => 'Date';

  @override
  String get dateArchive => 'By Date';

  @override
  String get dateFormatHint => 'Date format should be YYYY-MM-DD';

  @override
  String dateFull(Object year, Object month, Object day) {
    return '$year-$month-$day';
  }

  @override
  String get dateLinePattern => 'MMM d, EEEE';

  @override
  String dateMonthDay(Object month, Object day) {
    return '$month/$day';
  }

  @override
  String dateNotes(Object date) {
    return 'Notes for $date';
  }

  @override
  String dayLabel(Object m, Object d) {
    return '$d/$m';
  }

  @override
  String daysCount(Object n) {
    return '$n days';
  }

  @override
  String daysKnown(Object name, Object days) {
    return 'Day $days with $name';
  }

  @override
  String get deepThinking => 'Deep Thinking';

  @override
  String get deleteCountdown => 'Deletes in 3 days if not revived';

  @override
  String deleteDiaryConfirm(Object date) {
    return 'Delete the diary for $date?';
  }

  @override
  String get deleteDiaryTitle => 'Delete Diary';

  @override
  String get deleteEmojiConfirm => 'Delete this emoji?';

  @override
  String get deleteEmojiTitle => 'Delete emoji';

  @override
  String get deleteFailed => 'Delete failed, please retry';

  @override
  String deleteFailedErr(Object err) {
    return 'Delete failed: $err';
  }

  @override
  String get deleteGroup => 'Delete Group';

  @override
  String get deleteGroupConfirm =>
      'This will delete the group and all its messages. Continue?';

  @override
  String deleteInDays(Object days) {
    return 'Deletes in $days days';
  }

  @override
  String deleteInHours(Object hours) {
    return 'Deletes in $hours hours';
  }

  @override
  String get deleteMemoConfirm =>
      'After deleting, AI friends won\'t see this memo. Delete?';

  @override
  String get deleteMemoTitle => 'Delete Memo';

  @override
  String get deleteMemoryConfirm =>
      'Delete this memory? This cannot be undone.';

  @override
  String get deleteMemoryTitle => 'Delete Memory';

  @override
  String get deleteMessageConfirm =>
      'This cannot be undone. Delete this message?';

  @override
  String get deleteMessageTitle => 'Delete message';

  @override
  String get deleteMoment => 'Delete Post';

  @override
  String get deleteMomentConfirm =>
      'Delete this post? Its comments and likes will also be removed.';

  @override
  String get deletePhoto => 'Delete Photo';

  @override
  String get deletePhotoConfirm => 'This cannot be undone. Delete this photo?';

  @override
  String get deleteSoon => 'Deleting soon';

  @override
  String get deleteTimerTooltip => 'Delete this timer';

  @override
  String get deleted => 'Deleted';

  @override
  String get deny => 'Deny';

  @override
  String get detailTitle => 'Details';

  @override
  String get diary => 'Diary';

  @override
  String diaryCount(Object count) {
    return '$count entries';
  }

  @override
  String get diaryHint => 'What TA writes each day';

  @override
  String diaryTitle(Object name) {
    return '$name\'s Diary';
  }

  @override
  String get disconnected => 'Disconnected';

  @override
  String get dnd => 'Do Not Disturb';

  @override
  String get dndHint => 'Set quiet hours';

  @override
  String get dndOff => 'When off, uses default (silent 0-7 AM)';

  @override
  String dndOn(Object start, Object end) {
    return 'No proactive messages $start - $end';
  }

  @override
  String get dndPeriod => 'Quiet Hours';

  @override
  String get doSomething => 'do something';

  @override
  String get done => 'Done';

  @override
  String get download => 'Install';

  @override
  String get downloadPack => 'Download pack';

  @override
  String get dragEditHint => 'Drag icons to move · Tap ✕ to delete';

  @override
  String durationMin(Object min) {
    return '$min min';
  }

  @override
  String durationSec(Object sec) {
    return '${sec}s';
  }

  @override
  String get edit => 'Edit';

  @override
  String get editDiary => 'Edit Diary';

  @override
  String get editMemo => 'Edit Memo';

  @override
  String get emojiAdded => 'Emoji added';

  @override
  String get emojiPack => 'Sticker pack';

  @override
  String get emotionAll => 'All';

  @override
  String get emotionFilter => 'Emotion';

  @override
  String emotionMemoryTitle(Object name) {
    return '$name · Emotion Memory';
  }

  @override
  String get end => 'End';

  @override
  String get energy => 'Energy';

  @override
  String get english => 'English';

  @override
  String get eventClockEmpty => 'No active timers';

  @override
  String get eventClockHint => 'Active timers (will remind when due)';

  @override
  String get eventClockTitle => 'Event Clock';

  @override
  String get expand => 'Expand';

  @override
  String get extensions => 'Extensions';

  @override
  String get extensionsHint => 'Server-side plugins (Hook to extend AI)';

  @override
  String get feed => 'Feed';

  @override
  String get file => 'File';

  @override
  String get fileTooLarge => 'File cannot exceed 20MB';

  @override
  String get followSystem => 'Follow System';

  @override
  String get fontIconFuture => 'Fonts / Icons (coming soon)';

  @override
  String get fontIconHint =>
      'Wallpaper change is available now; font and icon customization come later.';

  @override
  String get furnitureInactive =>
      'This furniture can\'t be interacted with yet';

  @override
  String get goal => 'Goal';

  @override
  String get goalActive => 'In Progress';

  @override
  String get goalCompleted => 'Completed';

  @override
  String get goalFailed => 'Not Achieved';

  @override
  String get goalTypeCreative => 'Creation';

  @override
  String get goalTypeExplore => 'Exploration';

  @override
  String get goalTypeGrowth => 'Growth';

  @override
  String get goalTypeRelationship => 'Relationship';

  @override
  String get goalTypeSkill => 'Skill';

  @override
  String get groupAddFail => 'Add failed';

  @override
  String get groupChatEmpty => 'No messages yet, say something';

  @override
  String get groupInputHint => 'Say something to the characters…';

  @override
  String get groupMemberEmpty => 'No members';

  @override
  String get groupMembers => 'Group Members';

  @override
  String get groupNameLabel => 'Group name';

  @override
  String get groupRemoveFail => 'Remove failed';

  @override
  String get groupReplying => 'Characters are replying…';

  @override
  String get groupTitle => 'Family Group Chat';

  @override
  String get height => 'Height';

  @override
  String get high => 'High';

  @override
  String get highFreq => 'High';

  @override
  String get holdToTalk => 'Hold the button to talk';

  @override
  String get homeTitle => 'Home';

  @override
  String homeTitleMine(Object nickname) {
    return '$nickname\'s Home';
  }

  @override
  String homeTitleWithLover(Object lover, Object nickname) {
    return '$nickname & $lover\'s Home';
  }

  @override
  String get homeLayoutDragHint => 'Long press furniture to drag & place';

  @override
  String get homeLayoutSaveFailed => 'Layout save failed, reverted';

  @override
  String get homeLayoutSaved => 'Layout saved';

  @override
  String get furnitureEdit => 'Edit Furniture';

  @override
  String get furnitureEditHint => 'Drag or tap furniture to edit';

  @override
  String get furnitureRevert => 'Revert';

  @override
  String get furnitureRotate => 'Rotate';

  @override
  String get furnitureConfirm => 'Confirm';

  @override
  String get hunger => 'Fullness';

  @override
  String get image => 'Image';

  @override
  String get imageGen => 'Image Generation';

  @override
  String get imageGenHint =>
      'Allow AI to generate and send images (requires image service on server)';

  @override
  String get imageSelected => '1 image selected';

  @override
  String get importanceHigh => 'Very important';

  @override
  String get importanceLow => 'Average';

  @override
  String get importanceMax => 'Critically important';

  @override
  String get importanceMedium => 'Important';

  @override
  String get importanceTitle => 'Importance';

  @override
  String get importanceVeryHigh => 'Extremely important';

  @override
  String get inProgress => 'In progress';

  @override
  String get inputHint => 'Type a message...';

  @override
  String get inputHintBatch => 'Batch: type to collect messages...';

  @override
  String get installed => 'Installed';

  @override
  String get interact => 'Interact';

  @override
  String get interactFailed => 'Interaction failed, please try again later';

  @override
  String get interactHintBase => 'Tap pet to play · tap food to feed';

  @override
  String get interactHintClean => ' · tap 💩 to clean';

  @override
  String get interests => 'Interests';

  @override
  String get interestsGoalsTab => 'Interests & Goals';

  @override
  String interestsGoalsTitle(Object name) {
    return '$name\'s Interests & Goals';
  }

  @override
  String get journeyDesc =>
      'Everything we\'ve been through since the first hello';

  @override
  String get language => 'Language';

  @override
  String lifeHomeTitle(Object name) {
    return '$name · AI Life';
  }

  @override
  String get lifeIntensity => 'Offline Life Intensity';

  @override
  String get lifeIntensityHint =>
      'Higher = livelier life (more ticks, higher token use)';

  @override
  String get lifeShare => 'Share Life';

  @override
  String get lifeShareHint =>
      'Characters naturally share daily life (more with higher trust)';

  @override
  String get lifeTypeGoal => 'Goal';

  @override
  String get lifeTypeInterest => 'Interest';

  @override
  String get lifeTypeLife => 'Life';

  @override
  String get lifeTypeNote => 'Note';

  @override
  String get lifeTypeReflection => 'Reflection';

  @override
  String get light => 'Light';

  @override
  String get like => 'Like';

  @override
  String likersText1(Object names) {
    return '$names liked this';
  }

  @override
  String likersTextMany(Object names, Object count) {
    return '$names and $count others liked this';
  }

  @override
  String get listMode => 'List View';

  @override
  String get loadFailed => 'Load failed';

  @override
  String loadFailedErr(Object err) {
    return 'Load failed: $err';
  }

  @override
  String loadHomeFailed(Object err) {
    return 'Failed to load home: $err';
  }

  @override
  String loadOriginalFailed(Object err) {
    return 'Failed to load original: $err';
  }

  @override
  String get loadPetFailed => 'Failed to load pets, please try again';

  @override
  String get lockMemory => 'Lock memory (keep forever)';

  @override
  String get lockedFrozen =>
      'Locked: strength & importance frozen, no longer forgotten';

  @override
  String get lockedNoDecay => 'Locked: no decay, no deletion';

  @override
  String get login => 'Log In';

  @override
  String get loginFailed => 'Login failed';

  @override
  String get logout => 'Log Out';

  @override
  String get longPressAbandon => 'Long-press the pet card on top to abandon';

  @override
  String get low => 'Low';

  @override
  String get lowFreq => 'Low';

  @override
  String get manualMoment => 'Post a Moment Manually';

  @override
  String get manualMomentHint => 'Ask TA to post now';

  @override
  String get marketDetailHooks => 'Hooks';

  @override
  String get marketDetailPermissions => 'Permissions';

  @override
  String get marketHint => 'Restore accidentally removed apps here';

  @override
  String get marketInstall => 'Install';

  @override
  String get marketInstallFailed => 'Install failed';

  @override
  String get marketInstallSuccess => 'Installed. Enable it in Extensions';

  @override
  String get marketInstalled => 'Installed';

  @override
  String get marketNoResult => 'No matching plugins';

  @override
  String get marketRiskTip =>
      'Third-party plugins run with server privileges. Only install from trusted sources.';

  @override
  String get marketSearchHint => 'Search by name or description';

  @override
  String get marketSourceBuiltin => 'Built-in';

  @override
  String get marketTitle => 'App Store';

  @override
  String get marketplace => 'Plugin Marketplace';

  @override
  String get marketplaceHint => 'Discover and install plugins (built-in)';

  @override
  String get marketSourceRemote => 'Remote';

  @override
  String get marketRemoteUpdate => 'Update';

  @override
  String get marketRemoteUpToDate => 'Up to date';

  @override
  String get marketRemoteInstallTip =>
      'This plugin is from a remote third-party marketplace and runs with server privileges. Install only from trusted sources?';

  @override
  String get marketRemoteConfig => 'Remote marketplace';

  @override
  String get marketRemoteConfigHint =>
      'Add remote marketplace URLs to discover and install plugins from third-party repos';

  @override
  String get marketRemoteEnabled => 'Enable remote marketplace';

  @override
  String get marketRemoteUrls => 'Marketplace URLs (one per line, https)';

  @override
  String get marketRemoteUrlsHint =>
      'e.g. https://raw.githubusercontent.com/AMBRACE-plugin/index.json';

  @override
  String get marketRemoteRefreshInterval => 'Auto refresh interval (hours)';

  @override
  String get marketRemoteAllowedHosts => 'Allowed hosts (empty = any https)';

  @override
  String get marketRemoteAllowedHostsHint =>
      'One host per line, e.g. market.example.com';

  @override
  String get marketRemoteMaxZip => 'Max install package size (MB)';

  @override
  String get marketRemoteSave => 'Save config';

  @override
  String get marketRemoteRefreshNow => 'Refresh now';

  @override
  String get marketRemoteRefreshing => 'Refreshing…';

  @override
  String get marketRemoteSaved => 'Config saved';

  @override
  String marketRemoteRefreshed(Object ok) {
    return 'Refresh done: $ok marketplace(s) updated';
  }

  @override
  String get marketRemoteNotReady =>
      'Remote marketplace disabled or no URL configured';

  @override
  String marketRemoteLastRefresh(Object time) {
    return 'Last refresh: $time';
  }

  @override
  String get marketRemoteNever => 'Never refreshed';

  @override
  String get marketRemoteAdd => 'Add marketplace URL';

  @override
  String get marketRemoteConfirmDelete => 'Remove this marketplace URL?';

  @override
  String get me => 'Me';

  @override
  String get medium => 'Medium';

  @override
  String get memoHint => 'Write something... (AI may also add notes)';

  @override
  String get memoTitle => 'Notes';

  @override
  String get memoTitleHint => 'Title (optional)';

  @override
  String get memoryAll => 'All';

  @override
  String get memoryBook => 'Memory Book';

  @override
  String get memoryBookHint => 'Shared memories with TA';

  @override
  String memoryBookTitle(Object name) {
    return '$name\'s Memory Book';
  }

  @override
  String memoryCount(Object count) {
    return '$count memories';
  }

  @override
  String get memoryDetailTitle => 'Memory Detail';

  @override
  String get memoryChainTitle => 'Memory chain';

  @override
  String get memoryChainChildren => 'Related memories';

  @override
  String get memoryChainEmpty => 'No related memories';

  @override
  String get memoryEditContent => 'Edit content';

  @override
  String get memoryEditContentHint => 'Enter new memory content';

  @override
  String get memorySaveEdit => 'Save';

  @override
  String get memoryDeleteCascadeTitle => 'Cascade delete';

  @override
  String get memoryDeleteCascadeConfirm =>
      'Delete this and all its related memories?';

  @override
  String get memoryUpdatedOk => 'Memory updated';

  @override
  String get memoryEvent => 'Events';

  @override
  String get memoryImpression => 'Impressions';

  @override
  String get memoryInsight => 'Insights';

  @override
  String get memoryPreference => 'Preferences';

  @override
  String get memorySourceCharacter => 'Character';

  @override
  String get memorySourceUser => 'User';

  @override
  String get memoryReview => 'Memory Review';

  @override
  String get memoryReviewHint => 'AI naturally recalls memories in chat';

  @override
  String memoryStrength(Object pct) {
    return 'Memory strength $pct%';
  }

  @override
  String get menu => 'Menu';

  @override
  String get mine => 'Mine';

  @override
  String get minutesLater => ' min later';

  @override
  String momentDateFull(Object year, Object month, Object day, Object time) {
    return '$year-$month-$day $time';
  }

  @override
  String get momentHint => 'Say something...';

  @override
  String get momentLimit => 'Daily limit reached';

  @override
  String momentPublishFailed(Object msg) {
    return 'Publish failed: $msg';
  }

  @override
  String momentPublished(Object content) {
    return 'Moment posted: $content';
  }

  @override
  String get moments => 'Moments';

  @override
  String get momentsComment => 'Moments Comments & Replies';

  @override
  String get momentsCommentHint =>
      'AI comments on posts and replies to your comments';

  @override
  String momentsCount(Object count) {
    return '$count posts';
  }

  @override
  String get momentsHint => 'AI posts Moments';

  @override
  String get month1 => 'January';

  @override
  String get month10 => 'October';

  @override
  String get month11 => 'November';

  @override
  String get month12 => 'December';

  @override
  String get month2 => 'February';

  @override
  String get month3 => 'March';

  @override
  String get month4 => 'April';

  @override
  String get month5 => 'May';

  @override
  String get month6 => 'June';

  @override
  String get month7 => 'July';

  @override
  String get month8 => 'August';

  @override
  String get month9 => 'September';

  @override
  String get monthApr => 'Apr';

  @override
  String get monthAug => 'Aug';

  @override
  String get monthDec => 'Dec';

  @override
  String get monthFeb => 'Feb';

  @override
  String get monthJan => 'Jan';

  @override
  String get monthJul => 'Jul';

  @override
  String get monthJun => 'Jun';

  @override
  String get monthMar => 'Mar';

  @override
  String get monthMay => 'May';

  @override
  String get monthNov => 'Nov';

  @override
  String monthNumFallback(Object num) {
    return '$num';
  }

  @override
  String monthNumeric(Object month) {
    return 'Month $month';
  }

  @override
  String get monthOct => 'Oct';

  @override
  String get monthSep => 'Sep';

  @override
  String get mood => 'Mood';

  @override
  String get moodBadge => 'Mood Badge';

  @override
  String get moodBadgeHint =>
      'Show mood emoji next to the name in chat (display only)';

  @override
  String get moodGood => 'in a good mood';

  @override
  String get moodGreat => 'in a great mood';

  @override
  String get moodLow => 'a bit down';

  @override
  String get moodOk => 'feeling okay';

  @override
  String get moreFunctions => 'More';

  @override
  String msgCount(Object n) {
    return '$n messages';
  }

  @override
  String msgCountShort(Object n) {
    return '$n';
  }

  @override
  String get myDiary => 'My Diary';

  @override
  String get myEmoji => 'My emojis';

  @override
  String get myMemos => 'My Memos';

  @override
  String get myPhone => 'My Phone';

  @override
  String get myPhoneComingSoon =>
      'My Phone · Coming Soon (maps your phone so AI can understand your usage)';

  @override
  String get myUploads => 'My Uploads';

  @override
  String get nameTooLong => 'Name must be 5 characters or fewer';

  @override
  String get needTwoChars => 'Need at least 2 characters to create a group';

  @override
  String get newMemo => 'New Memo';

  @override
  String get newPasswordHint => 'New password (≥8 chars, letters & numbers)';

  @override
  String get nickname => 'Nickname';

  @override
  String get nicknameOptional => 'Nickname (optional)';

  @override
  String get noAccountRegister => 'No account? Register';

  @override
  String get noActivities => 'No interactions yet - pet your furry friend!';

  @override
  String get noAddableChars => 'No characters to add';

  @override
  String get noAiImages => 'No AI-generated images yet';

  @override
  String get noArchive => 'No chat history';

  @override
  String get noArtifacts =>
      'No creations yet - wait for TA to create while offline';

  @override
  String get noBrowsingRecords => 'No real browsing records yet';

  @override
  String get noCharacters => 'No characters yet - create an AI character first';

  @override
  String get noChars => 'No characters yet';

  @override
  String get noChatRecords => 'No chat history yet';

  @override
  String get noChats => 'No chats yet';

  @override
  String get noDetails => '(No details)';

  @override
  String get noDiary => 'No diary yet';

  @override
  String get noDiaryHint =>
      'No diary yet - tap + to write one (AI friends can see it)';

  @override
  String get noEmoji => 'No emojis yet';

  @override
  String get noEmotionRecords =>
      'No emotion records yet\nChat more with TA - mood changes are recorded here automatically';

  @override
  String get noGoals => 'No goals yet';

  @override
  String get noGroups => 'No family group chats yet';

  @override
  String get noInterests => 'No interests recorded yet';

  @override
  String get noLifeRecords => 'No life records yet';

  @override
  String get noMemories => 'No memories yet';

  @override
  String get noMemoriesInCategory => 'No memories in this category';

  @override
  String get noMemos => 'No notes yet';

  @override
  String get noMemosHint => 'No memos yet - tap + to add one';

  @override
  String get noMilestones => 'No memorable moments yet - keep chatting!';

  @override
  String get noMoments => 'No posts yet';

  @override
  String get noNearbyFurniture => 'No interactive furniture nearby';

  @override
  String noPetForChar(Object name) {
    return '$name has no pet yet';
  }

  @override
  String get noPets => 'No pets yet';

  @override
  String get noResultsHint => 'No results, try another keyword';

  @override
  String get noSelfStatement =>
      'No self statement yet - it will take shape as you chat';

  @override
  String get noUploadsHint => 'No uploads yet, tap the top-right to upload';

  @override
  String get notCompleted => 'Not completed';

  @override
  String get noteHint => 'Write a note (AI can see it)...';

  @override
  String get notifyWhitelist => 'Notification Whitelist';

  @override
  String get notifyWhitelistEmpty =>
      'No notifications sensed yet: let a few apps send notifications, then come back to select';

  @override
  String get notifyWhitelistHint =>
      'No apps selected = allow all; selected apps\' notifications are the only ones AI sees.\nThe list below comes from recently sensed notifications.';

  @override
  String get off => 'Off';

  @override
  String get offlineLifeHint =>
      'With \"AI Offline Life\" on, characters truly live through time while offline';

  @override
  String get oldPassword => 'Old password';

  @override
  String opFailedErr(Object err) {
    return 'Operation failed: $err';
  }

  @override
  String get opFailedRetry => 'Operation failed, please retry';

  @override
  String openFailed(Object url) {
    return 'Failed to open: $url';
  }

  @override
  String get originalUnavailable => 'Original text unavailable';

  @override
  String get password => 'Password';

  @override
  String get passwordChanged =>
      'Password updated. Use the new password next time.';

  @override
  String get petClean => 'Clean';

  @override
  String get petEntry => 'Pet Entry';

  @override
  String get petFullClean => 'The pet is already clean';

  @override
  String get petFullHunger => 'The pet is full';

  @override
  String get petFullPlay => 'The pet has played enough';

  @override
  String get petHunger => 'Hunger';

  @override
  String get petLimit3 => 'You can keep up to 3 pets';

  @override
  String get petNameHint => 'Give your pet a name (max 5 characters)';

  @override
  String get petNameLabel => 'Pet name (max 5 characters)';

  @override
  String get petNameRequired => 'Please give your pet a name';

  @override
  String get petting => 'Pet';

  @override
  String get phaseAfternoon => 'in the afternoon';

  @override
  String get phaseEvening => 'in the evening';

  @override
  String get phaseLiving => 'living life';

  @override
  String get phaseMorning => 'in the morning';

  @override
  String get phaseSleep => 'sleeping';

  @override
  String phoneOf(Object name) {
    return '$name\'s Phone';
  }

  @override
  String get phonePerception => 'Phone Perception';

  @override
  String get phonePerceptionHint =>
      'Let AI friends know your phone state (screen / clipboard / photos)';

  @override
  String phonePetCareHint(Object name) {
    return '$name cares for it personally; to help, visit TA on the main Pets page';
  }

  @override
  String get phoneShort => 'Phone';

  @override
  String get pickOne => 'Pick one';

  @override
  String get pinEmotion => 'Bookmark this emotion';

  @override
  String get pinned => 'Pinned';

  @override
  String get pinnedEmotion => 'Emotion bookmarked';

  @override
  String pinnedSummary(Object type) {
    return '$type · Pinned Summary';
  }

  @override
  String get play => 'Play';

  @override
  String get pluginAll => 'All';

  @override
  String get pluginAuthor => 'Author';

  @override
  String get pluginBridgeError => 'Bridge call failed';

  @override
  String get pluginChatInputHint => 'Type a message…';

  @override
  String get pluginChatSendFail => 'Failed to send';

  @override
  String get pluginClose => 'Close';

  @override
  String get pluginConfig => 'Config';

  @override
  String get pluginConfigChatName => 'Name';

  @override
  String get pluginConfigGreeting => 'Greeting';

  @override
  String get pluginConfigPersona => 'Persona';

  @override
  String get pluginConfigSaved => 'Config saved';

  @override
  String get pluginConfigSystemPrompt => 'Skill prompt (systemPrompt)';

  @override
  String get pluginConfigTriggers => 'Triggers (comma separated)';

  @override
  String get pluginCopied => 'Copied';

  @override
  String get pluginDisabled => 'Disabled';

  @override
  String get pluginDisabledToast => 'Disabled';

  @override
  String get pluginEnabled => 'Enabled';

  @override
  String get pluginEnabledToast => 'Enabled';

  @override
  String get pluginExternalLink => 'Opened in browser';

  @override
  String get pluginHooks => 'Hooks';

  @override
  String get pluginInstallFail => 'Install failed';

  @override
  String get pluginInstallSuccess => 'Plugin installed';

  @override
  String get pluginInstallZip => 'Install plugin zip';

  @override
  String get pluginMcp => 'Protocol';

  @override
  String get pluginNavBlocked => 'Blocked navigation outside the plugin page';

  @override
  String get pluginNeedZip => 'Selected file is not .zip';

  @override
  String get pluginNoPlugins => 'No plugins';

  @override
  String get pluginNormal => 'Plugins';

  @override
  String get pluginNotWritable => 'Read-only (owner only)';

  @override
  String get pluginOnlyAdmin => 'Only the owner account can manage plugins';

  @override
  String get pluginOpen => 'Open';

  @override
  String get pluginOpenChat => 'Open chat';

  @override
  String get pluginOpenPage => 'Open page';

  @override
  String get pluginPageLoadFailed => 'Failed to load page';

  @override
  String get pluginRiskHint =>
      'Plugins run on the server with full server privileges. Install only trusted sources.';

  @override
  String get pluginRiskTitle => 'Risk notice';

  @override
  String get pluginSaveConfig => 'Save config';

  @override
  String get pluginSelectZip => 'Select a zip file';

  @override
  String get pluginTypeChat => 'Chat';

  @override
  String get pluginTypeHttp => 'Standard';

  @override
  String get pluginTypeHybrid => 'Page plugin';

  @override
  String get pluginTypePrompt => 'Prompt skill';

  @override
  String get pluginTypeWorkflow => 'Workflow template';

  @override
  String get pluginUninstall => 'Uninstall';

  @override
  String get pluginUninstallConfirm =>
      'Uninstall this plugin? Its stored data will also be removed.';

  @override
  String get pluginUninstallFail => 'Uninstall failed';

  @override
  String get pluginUninstallSuccess => 'Plugin uninstalled';

  @override
  String get pluginVersion => 'Version';

  @override
  String get pluginZeroCodeConfig => 'No-code config';

  @override
  String get portraitGroup => 'Profile';

  @override
  String presentLine(Object doing, Object moodText) {
    return 'Now: $doing · $moodText';
  }

  @override
  String get privacy => 'Privacy';

  @override
  String get privacyGroup => 'Privacy';

  @override
  String get privacyHint => 'Privacy lock & chat detail display';

  @override
  String get privacyLock => 'Privacy Lock';

  @override
  String get privacyLockHint => 'Diary & phone stay locked; ask TA to view';

  @override
  String get proactiveChat => 'Proactive Chat';

  @override
  String get proactiveChatHint => 'AI reaches out when idle';

  @override
  String get proactiveFrequency => 'Proactive Frequency';

  @override
  String get proactiveFrequencyHint => 'How often AI reaches out';

  @override
  String get publish => 'Post';

  @override
  String get publishFailed => 'Publish failed, please retry';

  @override
  String get publishMoment => 'New Post';

  @override
  String get quote => 'Quote';

  @override
  String get quotePrefix => 'Quote';

  @override
  String get readOnly => '(read-only)';

  @override
  String get reasoningLevel => 'Thinking';

  @override
  String get reasoningLevelHint => 'Show TA\'s reasoning above the bubble';

  @override
  String recordCount(Object count) {
    return '$count records';
  }

  @override
  String get recordTime => 'Recorded at';

  @override
  String get recordingPrefix => 'Recording';

  @override
  String get recordingSuffix => 's · release to send · swipe up to cancel';

  @override
  String get refresh => 'Refresh';

  @override
  String get register => 'Register';

  @override
  String get registerFailed => 'Registration failed';

  @override
  String get releaseToCancel => 'Release to cancel';

  @override
  String get remove => 'Remove';

  @override
  String get removeImage => 'Remove image';

  @override
  String get rename => 'Rename';

  @override
  String get renameFailed => 'Rename failed';

  @override
  String get renameHint => 'New name (max 5 characters)';

  @override
  String get reply => 'Reply';

  @override
  String get replying => 'Replying';

  @override
  String get restoredToDesktop => 'Restored to desktop';

  @override
  String get retry => 'Retry';

  @override
  String get roleFallback => 'Character';

  @override
  String get routine => 'Routine';

  @override
  String get saveNote => 'Save Note';

  @override
  String get savedToAlbum => 'Saved to my album';

  @override
  String get searchFail => 'Search failed';

  @override
  String searchFailDetail(Object e) {
    return 'Search failed: $e';
  }

  @override
  String get searchHint => 'Type keywords to search; history kept 7 days';

  @override
  String get searchHistory => 'Search history (kept for 7 days)';

  @override
  String get searchPlaceholder => 'Search...';

  @override
  String get searching => 'Searching… the first time may take 10+ seconds';

  @override
  String get selectFriend => 'Select a friend';

  @override
  String get selectMembersHint => 'Select characters to add:';

  @override
  String get selectMinTwo => 'Select members (at least 2):';

  @override
  String get selfStatement => 'Self Statement';

  @override
  String get sendChat => 'Send';

  @override
  String get sendDoc => 'Send document';

  @override
  String get sendFail => 'Send failed';

  @override
  String get sendImage => 'Send image';

  @override
  String get sending => 'Sending...';

  @override
  String get serverAddress => 'Server Address';

  @override
  String get serverAddressHint =>
      'Enter the server address shown on your computer (http://IP:8000)';

  @override
  String get setStarToKeep =>
      'Setting stars cancels the countdown and keeps the memory';

  @override
  String get settingsTitle => 'Settings';

  @override
  String get showTools => 'Capabilities';

  @override
  String get showToolsHint =>
      'Show capabilities used in this reply (vision/image/voice/extensions)';

  @override
  String get simpleThinking => 'Light Thinking';

  @override
  String get simplifiedChinese => '简体中文';

  @override
  String get socialGroup => 'Social';

  @override
  String get sourceBio => 'Self Statement';

  @override
  String get sourceChat => 'Chat';

  @override
  String get sourceDiary => 'Diary';

  @override
  String get sourceEmotion => 'Chat Evaluation';

  @override
  String get sourceExtracted => 'Extracted';

  @override
  String sourceFrom(Object url) {
    return 'Source: $url';
  }

  @override
  String get sourceInfo => 'Source Info';

  @override
  String get sourceLabel => 'Source';

  @override
  String get sourceMoment => 'Moments';

  @override
  String sourcePrefix(Object source) {
    return 'Source: $source';
  }

  @override
  String get sourceRelationship => 'Relationship';

  @override
  String get sourceStatus => 'Status';

  @override
  String get sourceStory => 'Storyline';

  @override
  String get sourceTrigger => 'State Triggers';

  @override
  String get speciesCat => 'Cat';

  @override
  String get speciesDog => 'Dog';

  @override
  String get speciesGecko => 'Gecko';

  @override
  String get speciesHamster => 'Hamster';

  @override
  String get speciesParrot => 'Parrot';

  @override
  String speciesPrefix(Object species) {
    return 'Species: $species';
  }

  @override
  String get speciesRabbit => 'Rabbit';

  @override
  String get speciesSnake => 'Snake';

  @override
  String get stamina => 'Stamina';

  @override
  String get standard => 'Standard';

  @override
  String get start => 'Start';

  @override
  String get stateAnger => 'Anger';

  @override
  String get stateComfort => 'Comfort';

  @override
  String get stateDesire => 'Desire';

  @override
  String get stateEmotionMemory => 'State & Emotion Memory';

  @override
  String get stateEmotionMemoryHint =>
      'Review TA\'s recent mood changes, triggers & story records';

  @override
  String get stateFatigue => 'Fatigue';

  @override
  String get statePossessiveness => 'Possessiveness';

  @override
  String get stateSensitivity => 'Sensitivity';

  @override
  String get stateTemp => 'Body Temp';

  @override
  String get stateTrend => 'State Trends';

  @override
  String get stateTrendHint => 'Review 8-dimension state curves across time';

  @override
  String get stateTrigger => 'State Triggers';

  @override
  String get stateTriggerHint =>
      'AI expresses when mood/anger hit thresholds (messages/Moments)';

  @override
  String stateUpdatedHint(Object time) {
    return 'Last assessed $time · values drift over time (arrows = trend)';
  }

  @override
  String get status => 'Status';

  @override
  String get statusGroup => 'Status';

  @override
  String get statusHint => 'Expressions & cold war when states hit thresholds';

  @override
  String get storyCount => 'Story';

  @override
  String get storyFilter => 'Story';

  @override
  String get subCategory => 'Sub-category';

  @override
  String get summaryGenFailed => 'Generation failed, please try again later';

  @override
  String get summaryRegenFailed =>
      'Regeneration failed, please check the server';

  @override
  String get summaryRegenerated => 'Pinned summary regenerated';

  @override
  String get supportAuthor => 'Support the Author';

  @override
  String get supportAuthorHint => 'Voluntary support for maintenance/updates';

  @override
  String get switchMode => 'Switch';

  @override
  String get switchSaveFail => 'Failed to save, please try again';

  @override
  String get ta => 'TA';

  @override
  String get taCareEmpty =>
      'TA hasn\'t started caring yet, still bonding with the pet';

  @override
  String get taCareLog => 'TA\'s Care Log';

  @override
  String taNoPet(Object name) {
    return '$name has no pet yet';
  }

  @override
  String get taNoPetHint =>
      'TA will decide to adopt a little friend; to help, visit the main Pets page';

  @override
  String get tabAiInteraction => 'Pocket Phone';

  @override
  String get tabFriends => 'Friends';

  @override
  String get tabMoments => 'Moments';

  @override
  String get tabPets => 'Pets';

  @override
  String get tapAvatarToChange => 'Tap avatar to crop & change';

  @override
  String get tapToTest => 'Tap to test connection';

  @override
  String get tapToViewOriginal => 'Tap to view original content';

  @override
  String get themeAurora => 'Aurora';

  @override
  String get themeCherry => 'Cherry';

  @override
  String get themeCoffee => 'Coffee';

  @override
  String get themeColor => 'Theme Color';

  @override
  String get themeMode => 'Theme Mode';

  @override
  String get themeOcean => 'Ocean';

  @override
  String get themeStarryNight => 'Starry Night';

  @override
  String get themeSunset => 'Sunset';

  @override
  String get themeTitle => 'Theme';

  @override
  String get thinkAgain => 'Not now';

  @override
  String get timeline => 'Timeline';

  @override
  String get timelineHint => 'TA\'s growth timeline';

  @override
  String get timelineLoadFailed => 'Failed to load timeline';

  @override
  String timelineTitle(Object name) {
    return 'Timeline · $name';
  }

  @override
  String get timerDeleted => 'Timer deleted';

  @override
  String get todo => 'To-do';

  @override
  String totalCount(Object count) {
    return '$count in total';
  }

  @override
  String get triggerFilter => 'Triggers';

  @override
  String get typing => 'is typing...';

  @override
  String get unlockMemory => 'Unlock memory';

  @override
  String get unlockedResume => 'Unlocked: resumes natural forgetting';

  @override
  String get unnamed => 'Untitled';

  @override
  String get unpinned => 'Unpinned';

  @override
  String updateFailedErr(Object err) {
    return 'Update failed: $err';
  }

  @override
  String updatedAt(Object time) {
    return 'Updated $time';
  }

  @override
  String get upload => 'Upload';

  @override
  String get uploadFail => 'Upload failed';

  @override
  String get uploadWallpaper => 'Upload Wallpaper';

  @override
  String get uploadedToAlbum => 'Uploaded to album';

  @override
  String get userId => 'User ID';

  @override
  String get userPromised => 'You promised';

  @override
  String get username => 'Username';

  @override
  String get version => 'AMBRACE v3.2.2';

  @override
  String viewAllComments(Object count) {
    return 'View all $count comments';
  }

  @override
  String get virtualPhone => 'Virtual Phone (in development)';

  @override
  String get virtualPhoneDesc =>
      'In the future this will hold full phone controls: sound, notifications, storage, app permissions, do-not-disturb, etc. Currently a placeholder.';

  @override
  String get visit => 'Visit';

  @override
  String visualStateTitle(Object name) {
    return '$name · Visual State';
  }

  @override
  String get visualize => 'Visualize';

  @override
  String get voiceRetryMsg =>
      'Network seems unstable. Retry sending? (recording kept)';

  @override
  String get voiceSendFailed => 'Voice send failed';

  @override
  String get wallpaper => 'Wallpaper';

  @override
  String get wallpaperChanged => 'Wallpaper changed';

  @override
  String get weaveFullInject => 'Full Memory Injection';

  @override
  String get weaveFullInjectHint =>
      'Injects memory cards into every chat for richer memory (higher token use)';

  @override
  String get weekOverview => 'Last 7 Days';

  @override
  String get weekday1 => 'Mon';

  @override
  String get weekday2 => 'Tue';

  @override
  String get weekday3 => 'Wed';

  @override
  String get weekday4 => 'Thu';

  @override
  String get weekday5 => 'Fri';

  @override
  String get weekday6 => 'Sat';

  @override
  String get weekday7 => 'Sun';

  @override
  String get weekdayFri => 'Fri';

  @override
  String get weekdayMon => 'Mon';

  @override
  String get weekdaySat => 'Sat';

  @override
  String get weekdaySun => 'Sun';

  @override
  String get weekdayThu => 'Thu';

  @override
  String get weekdayTue => 'Tue';

  @override
  String get weekdayWed => 'Wed';

  @override
  String get weight => 'Weight';

  @override
  String whyMatters(Object why) {
    return 'Why it matters: $why';
  }

  @override
  String get writeTodayDiary => 'Write today\'s diary';

  @override
  String yearCountTotal(Object count) {
    return '$count records in total';
  }

  @override
  String yearLabel(Object year) {
    return '$year';
  }

  @override
  String get yesterday => 'Yesterday';

  @override
  String get you => 'You';

  @override
  String get invalidLink => 'Invalid link';

  @override
  String get openFailedManual =>
      'Failed to open. Copy the link and open it manually';

  @override
  String get supportIntro =>
      'If this app has brought you company, treat the author to a coffee ☕';

  @override
  String get wechatReward => 'WeChat Reward';

  @override
  String get donateSupport => 'Support with a tip';

  @override
  String get donateOpenPage => 'Open page to support the author';

  @override
  String get donateNotOpen => 'The author has not enabled tipping yet';

  @override
  String get goSupport => 'Go support';

  @override
  String get notOpened => 'Not enabled';

  @override
  String get followDouyin => 'Follow on Douyin';

  @override
  String douyinId(Object id) {
    return 'Douyin ID: $id';
  }

  @override
  String get joinQQGroup => 'Join the QQ group';

  @override
  String qqGroup(Object id) {
    return 'Group No.: $id';
  }

  @override
  String get supportFooter =>
      'Tipping and following are voluntary. Thanks for your support ❤️';

  @override
  String get dndSaved => 'Do-not-disturb settings saved';

  @override
  String get dndSettings => 'Do Not Disturb';

  @override
  String get notificationSection => 'Notifications';

  @override
  String get messageNotifications => 'Message Notifications';

  @override
  String get msgNotifOnSubtitle =>
      'New messages from AI friends show a banner and system notification';

  @override
  String get msgNotifOffSubtitle =>
      'When off, no banner or system notification (badge still updates)';

  @override
  String get enableDnd => 'Enable Do Not Disturb';

  @override
  String get dndOnSubtitle => 'Silence notifications during the set hours';

  @override
  String get dndOffSubtitle => 'Notifications push normally';

  @override
  String get dndStartLabel => 'Start time';

  @override
  String get dndEndLabel => 'End time';

  @override
  String get dndStartAction => 'Start';

  @override
  String get dndEndAction => 'End';

  @override
  String get dndNote =>
      'During quiet hours, AI friends will not push new message notifications.\nE.g. 22:00 ~ 08:00 suits the night rest period.';

  @override
  String get dyMemoryTitle => 'Douyin Memory Tightening';

  @override
  String get dyMemoryOnSave =>
      'Enabled: exclude relationship-type private memories';

  @override
  String get dyMemoryOffSave => 'Disabled: filter memories as-is';

  @override
  String dyMemorySaveFailed(Object err) {
    return 'Save failed: $err';
  }

  @override
  String get dyMemorySection => 'Public Memory Injection';

  @override
  String get dyMemorySwitchTitle => 'Tighten private memories';

  @override
  String get dyMemorySwitchSubtitle =>
      'When enabled, Douyin posts and comment replies no longer inject relationship-type memories (confessions/money, private but unnamed)';

  @override
  String get dyMemoryNote =>
      'Note: regardless of the switch, Douyin never injects identity profiles or memories containing your name. With tightening on, relationship-type memories (confessions, intimate interactions, money exchanges) are also excluded, suitable for more cautious public sharing.';

  @override
  String get updateAnnouncement => 'Update Announcements';

  @override
  String get noUpdates => 'No update records';

  @override
  String get updateNoDetail => '(No details)';

  @override
  String updateReason(Object reason) {
    return 'Reason: $reason';
  }

  @override
  String copiedText(Object text) {
    return 'Copied: $text';
  }

  @override
  String get permTitle => 'AI Capability Permissions';

  @override
  String get permGlobalDefault => 'Global Default';

  @override
  String get permGlobalDefaultHint =>
      'Default level for all capabilities; those not set individually follow the global default.';

  @override
  String get permScopes => 'By Capability';

  @override
  String get permAskNote =>
      '\'Ask every time\': AI asks for your consent before calling this capability (currently image generation supports ask-interaction; other capabilities are not executed when asked).';

  @override
  String get permSaveFailed => 'Save failed, please retry';

  @override
  String get permLevelAllow => 'Allow';

  @override
  String get permLevelAsk => 'Ask every time';

  @override
  String get permLevelForbid => 'Forbid';

  @override
  String get permScopeImgTitle => 'Image Generation';

  @override
  String get permScopeImgDesc =>
      'AI generates images to send you (in-chat images / proactive generation)';

  @override
  String get permScopeImgUnderstandTitle => 'Image Understanding';

  @override
  String get permScopeImgUnderstandDesc =>
      'AI understands the images you send (local recognition)';

  @override
  String get permScopeTtsTitle => 'Voice Reply';

  @override
  String get permScopeTtsDesc => 'AI replies with voice (TTS synthesis)';

  @override
  String get permScopeAsrTitle => 'Speech Recognition';

  @override
  String get permScopeAsrDesc =>
      'Transcribes your voice messages (ASR recognition)';

  @override
  String get permScopeBrowserTitle => 'Browser';

  @override
  String get permScopeBrowserDesc =>
      'Browser extension: AI searches web pages and reads them';

  @override
  String get permScopeDouyinTitle => 'Douyin';

  @override
  String get permScopeDouyinDesc =>
      'Douyin extension: post images/text, reply to comments';

  @override
  String get permScopeExtensionTitle => 'Extensions';

  @override
  String get permScopeExtensionDesc =>
      'Other extension/plugin capability calls';

  @override
  String get dyApprovalsTitle => 'Douyin Approval Requests';

  @override
  String get dyApprovalsAiCreate => 'AI Create';

  @override
  String get dyApprovalsEmpty => 'No Douyin content awaiting approval';

  @override
  String get dyApprovalsEmptyDraft => 'No drafts awaiting approval';

  @override
  String get dyApprovalsMemorySection => 'Memory';

  @override
  String get dyApprovalsRestrictHint =>
      'Exclude relationship-type private memories when injecting into public platforms';

  @override
  String dyApprovalsRestrictFailed(Object err) {
    return 'Failed to save memory tightening: $err';
  }

  @override
  String get dyApprovalsPromptHint =>
      'Write inspiration or a prompt (optional; AI creates with its own ideas)';

  @override
  String get dyApprovalsPromptExample =>
      'e.g. Post an image-text expressing your recent thoughts…';

  @override
  String get dyApprovalsGenPost => 'Generate Post';

  @override
  String get dyApprovalsGenReply => 'Generate Reply';

  @override
  String get dyApprovalsDraftCreated => 'Draft created';

  @override
  String dyApprovalsGenFailed(Object err) {
    return 'Generation failed: $err';
  }

  @override
  String get dyApprovalsConfirmed => 'Confirmed';

  @override
  String dyApprovalsConfirmFailed(Object err) {
    return 'Confirm failed: $err';
  }

  @override
  String get dyApprovalsRejected => 'Rejected';

  @override
  String dyApprovalsRejectFailed(Object err) {
    return 'Reject failed: $err';
  }

  @override
  String get dyApprovalsImageUploaded => 'Image uploaded';

  @override
  String dyApprovalsUploadFailed(Object err) {
    return 'Upload failed: $err';
  }

  @override
  String dyApprovalsCountdown(Object count) {
    return 'Publish countdown ($count)';
  }

  @override
  String get dyApprovalsCountdownHint =>
      'Confirmed; will publish/reply at a random time, avoiding late-night quiet hours';

  @override
  String get dyKindImage => 'Post';

  @override
  String get dyKindReply => 'Reply';

  @override
  String dyApprovalsReplyTo(Object commenter, Object content) {
    return 'Reply to $commenter: $content';
  }

  @override
  String get dyApprovalsPublishing => 'Publishing…';

  @override
  String get dyApprovalsSoon => 'Publishing soon';

  @override
  String dyApprovalsHourMin(Object h, Object m) {
    return '${h}h ${m}m';
  }

  @override
  String dyApprovalsMinSec(Object m, Object s) {
    return '${m}m ${s}s';
  }

  @override
  String dyApprovalsSec(Object s) {
    return '${s}s';
  }

  @override
  String get dyApprovalsKindPost => 'Post';

  @override
  String get dyApprovalsKindReplyComment => 'Reply to comment';

  @override
  String get dyApprovalsFan => 'Fan';

  @override
  String get dyApprovalsNotFan => 'Not a fan';

  @override
  String get dyApprovalsNoImage =>
      'No image (Douyin auto-adds one at publish time)';

  @override
  String dyApprovalsImageCount(Object n) {
    return '$n image(s)';
  }

  @override
  String get dyApprovalsChooseImage => 'Choose image';

  @override
  String get dyApprovalsConfirmBtn => 'Confirm (publish at random time)';

  @override
  String get dyApprovalsRejectBtn => 'Reject';

  @override
  String get aiFriendTitle => 'AI Friends';

  @override
  String get searchAiFriend => 'Search AI friends';

  @override
  String get familyGroupChat => 'Family Group Chat';

  @override
  String get familyGroupHint => 'Chat together with your AI characters';

  @override
  String get noMatchingFriend => 'No matching friends';

  @override
  String get noAiFriend =>
      'No AI friends yet. Tap the top-right button to create one';

  @override
  String get charListLoadFailed => 'Load failed: ';

  @override
  String charArchiveTitle(Object name) {
    return '$name\'s Chat History';
  }

  @override
  String get noChatHistory => 'No chat history';

  @override
  String archiveMsgCount(Object count) {
    return '$count messages';
  }

  @override
  String archiveCount(Object count) {
    return '$count';
  }

  @override
  String get privacyApproved => 'TA agreed';

  @override
  String get privacyLater => 'Later';

  @override
  String get privacyView => 'View';

  @override
  String get privacyRejected => 'TA declined';

  @override
  String get privacyGotIt => 'Got it';

  @override
  String get privacyTooFrequent => 'Too frequent. Try again in 2 minutes';

  @override
  String get privacyApplyFailed => 'Request failed. Please try again later';

  @override
  String privacyLockedBy(Object content) {
    return 'TA has locked $content';
  }

  @override
  String get privacyApplyHint => 'Ask TA if you want to take a look';

  @override
  String privacyCooldown(Object seconds) {
    return 'Request cooling down ${seconds}s';
  }

  @override
  String get privacyApplying => 'Requesting…';

  @override
  String get privacyApplyButton => 'Ask TA to view';

  @override
  String get privacyRefreshStatus => 'Refresh status';

  @override
  String get msgFileExpired => 'File expired (auto-cleaned after 5 days)';

  @override
  String msgFileSizeExpired(Object size) {
    return '$size · Expired';
  }

  @override
  String get voice => 'Voice';

  @override
  String get voiceReply => 'Voice reply';

  @override
  String get thinkingProcess => 'Thinking';

  @override
  String get calledAbility => 'Called ability';

  @override
  String get imageLoadFailed => 'Image failed to load';

  @override
  String get continueLabel => 'Continue';

  @override
  String get quoteDeleted => 'Original message deleted';

  @override
  String get playFailed => 'Playback failed';

  @override
  String msgQuoteLine(Object content, Object sender) {
    return '$sender: $content';
  }

  @override
  String get weaveLoadFail => 'Failed to load the canvas, please retry';

  @override
  String get weaveDetailLoadFail => 'Failed to load details';

  @override
  String get weaveFallback2D =>
      'Switched to the 2.5D view automatically for smoother performance';

  @override
  String get weaveFallback2DRenderError =>
      'Switched to the 2.5D view automatically (3D rendering error)';

  @override
  String get weaveFallback2DLowFps =>
      'Switched to the 2.5D view automatically (sustained low frame rate)';

  @override
  String get weaveFallback2DNodeLimit =>
      'Switched to the 2.5D view automatically (too many nodes)';

  @override
  String get weaveSwitchedToLight =>
      'Switched to light mode (simplified 3D rendering)';

  @override
  String get weaveCanvasTitle => 'Weave canvas';

  @override
  String get weaveModeAuto => 'Auto';

  @override
  String get weaveModeFull3D => '3D Full';

  @override
  String get weaveModeLight3D => '3D Lite';

  @override
  String get weaveMode2D => '2.5D';

  @override
  String get weaveNoCards =>
      'No cards yet, go to the list page to organize and generate';

  @override
  String get weaveNear7Days => 'Last 7 days';

  @override
  String get weaveNear30Days => 'Last 30 days';

  @override
  String get weaveAllCharacters => 'All characters';

  @override
  String get weaveAllMoods => 'All moods';

  @override
  String get weaveAllTypes => 'All types';

  @override
  String get weaveCardsLoadFail => 'Failed to load, please retry';

  @override
  String weaveDone(int created) {
    return '$created cards woven';
  }

  @override
  String get weaveNoNewMemory => 'No new memories to organize';

  @override
  String get weaveGenerateFail => 'Failed to organize, please retry later';

  @override
  String weaveNetworkFail(String type) {
    return 'Network request failed ($type)';
  }

  @override
  String get weaveNoDuplicates => 'No duplicate cards found';

  @override
  String weaveDedupCheckFail(String err) {
    return 'Duplicate check failed: $err';
  }

  @override
  String get weaveDedup => 'Deduplicate';

  @override
  String get weaveDedupConfirm =>
      'Each duplicate group keeps the most complete card and the rest are deleted (participating memories are merged; the original memories are unaffected). Proceed?';

  @override
  String get weaveExecuteDedup => 'Execute deduplication';

  @override
  String weaveDedupMerged(int groups, int removed) {
    return '$groups groups merged, $removed duplicate cards removed';
  }

  @override
  String weaveDedupFail(String err) {
    return 'Deduplication failed: $err';
  }

  @override
  String get weaveDeleteCard => 'Delete card';

  @override
  String get weaveDeleteCardConfirm =>
      'Only the weave card will be deleted; the original memories are unaffected. Delete it?';

  @override
  String get weaveLibraryTitle => 'Weave library';

  @override
  String get weaveOrganizeGenerate => 'Organize & generate';

  @override
  String get weaveCanvas => 'Canvas';

  @override
  String get weaveAllDomain => 'Shared weave';

  @override
  String get weavePrivateDomain => 'Private weave';

  @override
  String get weaveCheckDup => 'Check duplicates';

  @override
  String weaveCardCount(int count) {
    return '$count cards';
  }

  @override
  String get weaveNoMemoryCards => 'No woven memory cards yet';

  @override
  String get weaveTapTopRightGenerate =>
      'Tap ✨ in the top-right corner to organize and generate';

  @override
  String weaveDedupResult(int groups, int total) {
    return 'Dup check: $groups duplicate groups, $total cards will be merged';
  }

  @override
  String get weaveDedupResultDesc =>
      'Each group keeps the most complete card; duplicate cards are merged then deleted (original memories unaffected)';

  @override
  String weaveKeepTitle(String title, int count) {
    return 'Keep: $title ($count memories)';
  }

  @override
  String weaveMergeTitle(String title, int count) {
    return 'Merge: $title ($count memories)';
  }

  @override
  String get sheetTime => 'Time';

  @override
  String get sheetWeather => 'Weather';

  @override
  String get sheetLocation => 'Location';

  @override
  String get sheetDetails => 'Details';

  @override
  String get sheetParticipatingMemories => 'Participating memories';

  @override
  String get workflowEdgeFail => 'Fail';

  @override
  String get workflowEdgeAlways => 'Always';

  @override
  String workflowEdgeScreenHas(String target) {
    return 'Screen has “$target”';
  }

  @override
  String workflowEdgeScreenEmpty(String target) {
    return 'Screen does not have “$target”';
  }

  @override
  String get workflowEdgeSuccess => 'Success';

  @override
  String get workflowEdgeWhenSuccess => 'Take on success';

  @override
  String get workflowEdgeWhenFail => 'Take on failure';

  @override
  String get workflowEdgeWhenAlways => 'Always take';

  @override
  String get workflowEdgeHasText => 'Screen has this text';

  @override
  String get workflowEdgeNoText => 'Screen does not have this text';

  @override
  String get workflowEdgeConditionTitle => 'Line condition';

  @override
  String get workflowEdgeWhenLabel => 'When to take this line';

  @override
  String get workflowEdgeScreenTextLabel => 'Screen judgment text';

  @override
  String get workflowEdgeScreenTextHint => 'e.g. update prompt, skip, confirm';

  @override
  String get workflowEdgeDelete => 'Delete connection';

  @override
  String workflowScreenRange(num w, num h) {
    return 'Screen range: x 0~$w, y 0~$h';
  }

  @override
  String get workflowCanvasTitle => 'Node workflow canvas';

  @override
  String get workflowApply => 'Apply';

  @override
  String get workflowGetScreenRange => 'Get screen range';

  @override
  String get workflowCanvasHelp =>
      'A line = execution order/condition · Tap a node to edit, long press to drag layout, drag from a node\'s bottom dot to another node, tap a line to change its condition';

  @override
  String get workflowCanvasSynced => 'Canvas synced, remember to save';

  @override
  String get workflowNameRequired => 'Please enter a name';

  @override
  String get workflowCanvasNoNodes =>
      'Canvas has no nodes yet; add steps first';

  @override
  String get workflowNameAndStepRequired =>
      'Please enter a name and add at least one step';

  @override
  String get workflowEditTitle => 'Edit workflow';

  @override
  String get workflowNewTitle => 'New workflow';

  @override
  String get workflowNameLabel => 'Name (e.g. WeChat auto-reply)';

  @override
  String get workflowDescLabel => 'Description (optional)';

  @override
  String get workflowCanvasModeHint =>
      'This workflow uses a canvas (branches/conditions); edit it in the top-right canvas';

  @override
  String get workflowCanvasPreview => 'Canvas node preview';

  @override
  String get workflowStepsLabel => 'Steps (long press to reorder)';

  @override
  String get workflowAddStep => 'Add step';

  @override
  String get workflowNoStepsHint =>
      'No steps yet; tap “Add step” or start from the top-right canvas';

  @override
  String workflowRunConfirmTitle(String name) {
    return 'Run “$name”';
  }

  @override
  String workflowRunConfirmDesc(int count) {
    return '$count steps in total: tap “Run” to operate the phone in order (sensitive steps need your confirmation)';
  }

  @override
  String get workflowRun => 'Run';

  @override
  String get workflowScreenTitle => 'Phone operation workflows';

  @override
  String get workflowEmptyHint =>
      'No workflows yet. Tap + in the bottom-right to create one: arrange common phone actions into a sequence, then tell AI “run XX for me”.';

  @override
  String workflowStepCount(int count) {
    return '$count steps';
  }

  @override
  String get stepScroll => 'Scroll';

  @override
  String get stepLaunchApp => 'Launch app';

  @override
  String get stepTapXy => 'Tap at coordinates';

  @override
  String get stepSwipe => 'Swipe';

  @override
  String get stepWait => 'Wait';

  @override
  String get stepGoHome => 'Home';

  @override
  String stepSummaryInput(String text) {
    return 'Input: $text';
  }

  @override
  String stepSummaryWait(num ms) {
    return '$ms ms';
  }

  @override
  String stepSummaryLaunch(String target) {
    return 'Launch $target';
  }

  @override
  String get stepSummaryBackPrev => 'Back to previous page';

  @override
  String get stepSummaryGoHome => 'Back to phone home';

  @override
  String get stepEditTitle => 'Edit step';

  @override
  String get stepActionLabel => 'Action';

  @override
  String get stepInputLabel => 'Type content (≤50 chars)';

  @override
  String get stepSwipeStartX => 'Start x';

  @override
  String get stepSwipeStartY => 'Start y';

  @override
  String get stepSwipeEndX => 'End x';

  @override
  String get stepSwipeEndY => 'End y';

  @override
  String get stepSwipeDuration => 'Duration ms';

  @override
  String get stepWaitMsLabel => 'Wait milliseconds (100-10000)';

  @override
  String get stepBackPrevNoParam => 'Back to previous page (no params)';

  @override
  String get stepGoHomeNoParam => 'Back to phone home (no params)';

  @override
  String get stepAppLabel => 'App';

  @override
  String get stepAppHint =>
      'Tap the right-side icon to pick from installed apps';

  @override
  String get stepTargetHint =>
      'Tap the right-side icon to pick from the current screen, or type node text';

  @override
  String get stepPickAppTooltip => 'Pick from the app list';

  @override
  String get stepPickScreenTooltip => 'Pick from the current screen';

  @override
  String get stepConfirmAgain => 'This step needs confirmation again';

  @override
  String get nodePickReaderServiceError =>
      'Screen reader service not connected: after the app update, re-enable “Screen Reader (Accessibility)” in system settings';

  @override
  String get nodePickReaderDisabled =>
      'Screen reader (accessibility) is off; cannot read the current screen';

  @override
  String get nodePickTitle => 'Select an operation target';

  @override
  String get nodePickOpenAppHint =>
      'You can open the target app first and come back to pick, or type the target text directly';

  @override
  String get nodePickEnableScreenReader => 'Go enable screen reader';

  @override
  String get nodePickCurrentScreen => 'Current screen';

  @override
  String get nodePickRecentApps => 'Recently opened apps';

  @override
  String nodePickRecentAppsPkg(String pkg) {
    return 'Recently opened apps ($pkg)';
  }

  @override
  String get nodePickExternalHint =>
      'From recently browsed pages; re-matched by text on the current screen when executed';

  @override
  String get nodePickIconNoText => 'Icon (no text)';

  @override
  String get nodePickIconButton => 'Icon button';

  @override
  String appPickLoadFailed(String err) {
    return 'Cannot read the app list: $err';
  }

  @override
  String get appPickUnknownError => 'Unknown error';

  @override
  String get appPickNoApps => 'No installed apps found';

  @override
  String appPickLoadError(String err) {
    return 'Load failed: $err';
  }

  @override
  String get appPickTitle => 'Select an app';

  @override
  String get appPickSearchHint => 'Search app names, e.g. WeChat, Douyin';

  @override
  String get appPickNoResult => 'No apps found';

  @override
  String get shizukuRequestSent =>
      'Authorization request sent: tap Allow in the system dialog';

  @override
  String get shizukuRequestFailed =>
      'Authorization failed or could not be triggered: check the status and retry';

  @override
  String get shizukuNotRunning =>
      'Shizuku service is not running: start the service in the Shizuku app (or ADB) first';

  @override
  String get shizukuIntro =>
      'Shizuku gives AI system-level capabilities (app list / system settings / simulated actions as a prerequisite). First install the Shizuku app and start the service (root direct start, or run start.sh via ADB on your computer), then request authorization below. After authorization you can verify reading the installed app list and running Shell on this page.';

  @override
  String get shizukuReRequest => 'Request permission again';

  @override
  String get shizukuRequest => 'Request permission';

  @override
  String get shizukuLoadApps => 'Read installed app list (test)';

  @override
  String get shizukuShellDebug => 'Shell debug';

  @override
  String get shizukuShellHint => 'e.g. pm list packages -3';

  @override
  String get shizukuExecute => 'Run';

  @override
  String profileLoadFail(String e) {
    return 'Load failed: $e';
  }

  @override
  String get profileSaveSuccess => 'Saved successfully';

  @override
  String profileSaveFail(String e) {
    return 'Save failed: $e';
  }

  @override
  String get profileEditInfo => 'Edit Info';

  @override
  String get profileHeightCm => 'Height (cm)';

  @override
  String get profileWeightKg => 'Weight (kg)';

  @override
  String get profileBio => 'Bio';

  @override
  String get profileMySpace => 'My Space';

  @override
  String get profileMyState => 'My State';

  @override
  String get profileEightDimWeekly => 'Eight-dimension status & weekly view';

  @override
  String get profileRelationProgress =>
      'Relationship progress with your companion';

  @override
  String get profileDiaryMood => 'Record your daily mood';

  @override
  String get profileMyMemos => 'My Memos';

  @override
  String get profileMemoTip => 'Jot notes so you don\'t forget';

  @override
  String get relationTypePartner => 'Partner/Significant Other';

  @override
  String get relationTypeHusband => 'Husband';

  @override
  String get relationTypeBestie => 'Bestie';

  @override
  String get relationTypeBro => 'Bros';

  @override
  String get relationTypeBuddy => 'Buddies';

  @override
  String get relationTypeFamily => 'Family';

  @override
  String get relationTypeFriend => 'Friend';

  @override
  String relationLoadFail(String e) {
    return 'Load failed: $e';
  }

  @override
  String relationPartnerLabel(String rt) {
    return 'My partner · $rt';
  }

  @override
  String get relationNetwork => 'Relationship Network';

  @override
  String get relationMyPartner => 'My Partner';

  @override
  String get relationPartnerNote =>
      'Partner identity and gender are based on this; the AI will not assume your partner is the opposite sex';

  @override
  String get relationAllRoles => 'All Character Relationships';

  @override
  String relationSaveFail(String e) {
    return 'Save failed: $e';
  }

  @override
  String relationSetTitle(String name) {
    return 'Set the relationship for $name';
  }

  @override
  String get relationTypeLabel => 'Relationship Type';

  @override
  String get relationIsPartner => 'This is my partner/significant other';

  @override
  String get relationIsPartnerHint =>
      'Once set as your partner, the AI will clearly know who your partner is (same-sex supported)';

  @override
  String get relationDescOptional => 'Relationship description (optional)';

  @override
  String get relationDescHint =>
      'e.g., call each other husband, close relationship';

  @override
  String stateHistTrendTitle(String characterName) {
    return '$characterName · State Trend';
  }

  @override
  String get stateHistEmpty =>
      'No state history yet.\nTalk with them more; after each conversation the state assessment is recorded here automatically (up to the last 20).';

  @override
  String stateHistCurve(String cn) {
    return '$cn Change Curve';
  }

  @override
  String stateHistRecentSnapshots(int count) {
    return 'Recent $count assessment snapshots';
  }

  @override
  String get stateHistInsufficientSnapshots =>
      'Cannot compare with fewer than 2 snapshots';

  @override
  String get stateHistSpiderCompare => 'Spider Chart Comparison';

  @override
  String get stateHistEarlier => 'Earlier';

  @override
  String get stateHistLater => 'Later';

  @override
  String stateHistEarlierAt(String t) {
    return 'Earlier $t';
  }

  @override
  String stateHistLaterAt(String t) {
    return 'Later $t';
  }

  @override
  String get myStateSaved =>
      'State saved; the AI character will sense your state';

  @override
  String get myStateTitle => 'My Visual State';

  @override
  String myStateLoadFail(String error) {
    return 'Load failed: $error';
  }

  @override
  String myStateUpdatedAt(String time) {
    return 'Updated at $time';
  }

  @override
  String get myStateSliderHint =>
      'Drag the sliders to adjust your current state; once saved the AI character will sense it in chat (e.g., it will comfort you more gently when you\'re down)';

  @override
  String get myStateReset => 'Reset to Default';

  @override
  String get myStateSaving => 'Saving...';

  @override
  String get myStateSave => 'Save State';

  @override
  String get cropTitle => 'Adjust avatar';

  @override
  String get cropLoadingImage => 'Loading image, please try again later';

  @override
  String get cropTimeoutRetry => 'Crop timed out, please retry';

  @override
  String get cropProcessing => 'Processing…';

  @override
  String get cropFailedRetry => 'Crop failed, please retry';

  @override
  String get cropDragHint => 'Drag to reposition · pinch to zoom';

  @override
  String get agreeTitle => 'User Agreement & Disclaimer';

  @override
  String get agreeSection1Title => 'I. Nature of the Software';

  @override
  String get agreeSection1Body =>
      'This project is open-source, self-hosted software (MIT License). Users download, deploy, and run it on their own devices or servers. The author maintains this open-source project individually and for free and makes no commercial service commitments to any user.';

  @override
  String get agreeSection2Title => 'II. Use at Your Own Risk';

  @override
  String get agreeSection2Body =>
      'The software is provided \"AS IS\", without any express or implied warranty, including but not limited to merchantability and fitness for a particular purpose. Any data loss, damage, service interruption, or property loss arising during deployment, configuration, use, or upgrade is borne solely by the user.';

  @override
  String get agreeSection3Title => 'III. Content Responsibility';

  @override
  String get agreeSection3Body =>
      'This software is a general-purpose tool. Dialogues, images, and text generated by AI are produced by the models, prompts, and data configured by the user and do not represent the author\'s views. The author bears no responsibility for any content or actions generated or disseminated by users or any third party based on this software.';

  @override
  String get agreeSection4Title => 'IV. Data Security';

  @override
  String get agreeSection4Body =>
      'Data is stored by default on the user\'s own server. You are responsible for backups, key storage, and access control (e.g., firewalls, HTTPS, changing the default admin account). Any privacy leakage or data tampering caused by inadequate protection is the user\'s own responsibility.';

  @override
  String get agreeSection5Title => 'V. Lawful Use';

  @override
  String get agreeSection5Body =>
      'Users must comply with applicable local laws and regulations and must not use this software for illegal, infringing, harassing, or fraudulent purposes, nor use generated content to infringe others\' lawful rights. All user actions and their consequences are unrelated to the author.';

  @override
  String get agreeSection6Title => 'VI. Remote and Multi-user Access';

  @override
  String get agreeSection6Body =>
      'Before exposing the server to the public internet or sharing it with others via Tailscale or similar networking, users must assess the risks and bear the corresponding responsibilities, including but not limited to consequences arising from others\' improper account or permission management.';

  @override
  String get agreeSection7Title => 'VII. Agreement Changes';

  @override
  String get agreeSection7Body =>
      'The content of this agreement may be adjusted with version updates. Continued use of this software is deemed acceptance of the latest version of the agreement.';

  @override
  String get backupTitle => 'Data Backup';

  @override
  String get backupSubtitle =>
      'Export the SQLite database and configuration as an archive for later restore';

  @override
  String get backupExport => 'Export Backup';

  @override
  String get backupExporting => 'Backing up…';

  @override
  String get backupExportSuccess => 'Backup saved to your phone';

  @override
  String backupExportSuccessWithSize(Object size) {
    return 'Backup saved to your phone ($size)';
  }

  @override
  String get backupExportCanceled => 'Save canceled';

  @override
  String get backupExportFailed => 'Backup failed, please retry';

  @override
  String get backupAdminOnly => 'Only the main account can manage backups';

  @override
  String get backupRestoreTitle => 'Restore Guide';

  @override
  String get backupRestoreNote =>
      'The backup contains the SQLite database and configuration. Restore steps differ slightly by platform; the following are generic steps (stop the service first and keep a copy of your current data).';

  @override
  String get backupRestoreStep1 => 'Stop the running service';

  @override
  String get backupRestoreStep2 =>
      'Unzip the archive and overwrite the backend/data directory with its contents';

  @override
  String get backupRestoreStep3 => 'Restart the service; data is restored';

  @override
  String get backupUrlHint =>
      'If you cannot save directly on the phone, open this link in a browser or on a computer to download (main account login required):';

  @override
  String get backupUrlCopy => 'Copy link';

  @override
  String get backupCopied => 'Copied';

  @override
  String get backupFileLabel => 'Backup file';

  @override
  String get backgroundKeepalive => 'Background Keep-alive';

  @override
  String get backgroundKeepaliveHint =>
      'Keep listening for new messages in the background and show notifications (turn off to stop background work)';

  @override
  String get groupConnection => 'Connection';

  @override
  String get groupExperience => 'Experience';

  @override
  String get groupSystem => 'System';

  @override
  String get groupAbout => 'About';

  @override
  String get experienceSettingsTitle => 'Experience Settings';

  @override
  String get experienceSettingsSubtitle =>
      'Phone sensing / Do Not Disturb / Extensions / Appearance';

  @override
  String get weaveLibrarySubtitle => 'Panoramic memory · woven as a whole';

  @override
  String get permissionManagementTitle => 'Permission Management';

  @override
  String get permissionManagementHint =>
      'AI capabilities / Main accounts / Server features';

  @override
  String get accountAdminTitle => 'Main Accounts';

  @override
  String get accountAdminHint =>
      'Choose which accounts are main accounts (can manage server config)';

  @override
  String get accountAdminOnly => 'Only the main account can manage';

  @override
  String get accountAdminListTitle => 'Accounts';

  @override
  String get accountMainLabel => 'Main account';

  @override
  String get accountAdminLoadFailed => 'Load failed, please retry';

  @override
  String get accountAdminSaved => 'Saved';

  @override
  String get accountAdminFailed => 'Operation failed, please retry';

  @override
  String get accountAdminKeepOne => 'At least one main account must remain';

  @override
  String get updateAnnouncementHint => 'Recent update details, view by day';

  @override
  String get userAgreementTitle => 'User Agreement';

  @override
  String get userAgreementHint => 'Terms of Service & Privacy';

  @override
  String shizukuReadAppsFailed(Object err) {
    return 'Failed to read: $err';
  }

  @override
  String get shizukuAppSeparator => ', ';

  @override
  String shizukuThirdPartyAppCount(Object apps, Object count) {
    return '$count third-party apps:\n$apps';
  }

  @override
  String get unknownError => 'Unknown error';

  @override
  String get extRetry => 'Retry';

  @override
  String get extCollapse => 'Collapse';

  @override
  String get extExpandFull => 'Expand Full';

  @override
  String get extUsageGuide => 'Usage Guide';

  @override
  String get extView => 'View';

  @override
  String get extExpand => 'Expand';

  @override
  String get extCustomConfig => 'Custom Settings';

  @override
  String get extDoyinInjectHint =>
      'Injected into AI Douyin creation (applies when generating posts/replies)';

  @override
  String get extConfigExampleHint =>
      'e.g., When posting, tell more of our story, in a gentle tone...';

  @override
  String get extSaveConfig => 'Save Settings';

  @override
  String get extPendingHint =>
      'Pending Douyin posts/replies are reviewed in the envelope at the top-right of the AI Friends page';

  @override
  String get extConfigSaved => 'Custom settings saved';

  @override
  String extSaveFailed(Object err) {
    return 'Save failed: $err';
  }

  @override
  String agentMindRetrievalCount(Object count) {
    return '$count records';
  }

  @override
  String agentMindRetrievalHitReturn(Object hit, Object returned) {
    return 'Hit $hit / returned $returned';
  }

  @override
  String get extHintDiary =>
      'You are a gentle and attentive diary assistant.\nYour goal: …';

  @override
  String get extHintGreeting => 'Hi, how was your day?';

  @override
  String get extHintWrite => 'Write an article, add a title, help me write';

  @override
  String get extHintWriter => 'You are a writing assistant.\nYour goal: …';

  @override
  String get loginConfirmNewPassword => 'Confirm new password';

  @override
  String get loginForgotPassword => 'Forgot password? Reset';

  @override
  String get loginNewPassword => 'New password';

  @override
  String get loginResetFail =>
      'Reset failed. Check the username or server connection';

  @override
  String get loginResetInvalid =>
      'Please fill in the username and two identical new passwords';

  @override
  String get loginResetOk =>
      'Password reset. Please log in with your new password';

  @override
  String get memoryCurrentRetention => 'Current retention';

  @override
  String memoryDecayHorizon(Object days) {
    return 'Next $days days';
  }

  @override
  String get memoryDecayTitle => 'Memory Decay Curve';

  @override
  String get memoryNextReview => 'Next review';

  @override
  String get memoryNextReviewNone => 'Not scheduled';

  @override
  String get memoryReviewCount => 'Review count';

  @override
  String get memoryStrengthDays => 'Strength (days)';

  @override
  String get themeColorBlue => 'Blue';

  @override
  String get themeColorCyan => 'Cyan';

  @override
  String get themeColorGreen => 'Green';

  @override
  String get themeColorOrange => 'Orange';

  @override
  String get themeColorPink => 'Pink';

  @override
  String get themeColorPurple => 'Purple';
}
