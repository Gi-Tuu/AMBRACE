import 'package:flutter/material.dart';

import '../../services/api/permission_api.dart';
import '../../services/api_client.dart';

/// AI 能力权限设置（2026-08-12，参考 Operit 工具权限模型）
/// 三档：允许 / 每次询问 / 禁止；全局默认 + 每能力例外（例外优先）。
class PermissionSettingsScreen extends StatefulWidget {
  const PermissionSettingsScreen({super.key});

  @override
  State<PermissionSettingsScreen> createState() =>
      _PermissionSettingsScreenState();
}

class _PermissionSettingsScreenState extends State<PermissionSettingsScreen> {
  final _api = ApiClient();
  bool _loading = true;
  String _globalLevel = 'allow';
  Map<String, String> _scopes = {};

  static const _scopeOrder = <String>[
    'image_gen',
    'image_understand',
    'tts',
    'asr',
    'browser',
    'douyin',
    'extension',
  ];

  static const _scopeMeta =
      <String, ({IconData icon, String title, String desc})>{
    'image_gen': (
      icon: Icons.auto_awesome,
      title: '生图',
      desc: 'AI 生成图片发给你（聊天内发图/主动生图）'
    ),
    'image_understand': (
      icon: Icons.image_search_outlined,
      title: '识图',
      desc: 'AI 理解你发来的图片内容（本地识图）'
    ),
    'tts': (
      icon: Icons.record_voice_over_outlined,
      title: '语音回复',
      desc: 'AI 用语音回复你（TTS 合成）'
    ),
    'asr': (icon: Icons.mic_none, title: '语音转写', desc: '转写你的语音消息（ASR 识别）'),
    'browser': (icon: Icons.public, title: '浏览器', desc: '浏览器扩展：AI 搜索网页、读取页面'),
    'douyin': (
      icon: Icons.music_note_outlined,
      title: '抖音',
      desc: '抖音扩展：发布图文、回复评论'
    ),
    'extension': (
      icon: Icons.extension_outlined,
      title: '扩展',
      desc: '其他扩展/插件的能力调用'
    ),
  };

  static const _levels = <String>['allow', 'ask', 'forbid'];
  static const _levelLabels = <String, String>{
    'allow': '允许',
    'ask': '每次询问',
    'forbid': '禁止',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.getPermissions();
      if (!mounted) return;
      setState(() {
        _globalLevel = data['global_level'] as String? ?? 'allow';
        final scopes = (data['scopes'] as Map?) ?? {};
        _scopes = scopes.map((k, v) => MapEntry(k.toString(), v.toString()));
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save({String? globalLevel, Map<String, String>? scopes}) async {
    try {
      final data = await _api.updatePermissions(
        globalLevel: globalLevel,
        scopes: scopes,
      );
      if (!mounted) return;
      setState(() {
        if (globalLevel != null) {
          _globalLevel = data['global_level'] as String? ?? _globalLevel;
        }
        final updated = (data['scopes'] as Map?) ?? {};
        _scopes = updated.map((k, v) => MapEntry(k.toString(), v.toString()));
      });
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('保存失败，请重试')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF2F2F7),
      appBar: AppBar(
        title: const Text('AI 能力权限'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.only(top: 8, bottom: 24),
              children: [
                _group(
                  title: '全局默认',
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            '所有能力的默认档位；未单独设置的能力跟随全局默认',
                            style: TextStyle(
                                fontSize: 12, color: Color(0xFF8E8E93)),
                          ),
                          const SizedBox(height: 10),
                          _levelSelector(
                            value: _globalLevel,
                            onChanged: (v) {
                              setState(() => _globalLevel = v);
                              _save(globalLevel: v);
                            },
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                _group(
                  title: '各能力',
                  children: [
                    for (final scope in _scopeOrder) _scopeRow(scope),
                  ],
                ),
                const Padding(
                  padding: EdgeInsets.fromLTRB(16, 4, 16, 0),
                  child: Text(
                    '「每次询问」：AI 调用该能力前会先征求你的同意（目前生图支持询问交互，其余能力询问时暂不执行）。',
                    style: TextStyle(
                        fontSize: 11, color: Color(0xFF8E8E93), height: 1.4),
                  ),
                ),
              ],
            ),
    );
  }

  Widget _group({required String title, required List<Widget> children}) {
    return Padding(
      padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 16, bottom: 6),
            child: Text(
              title,
              style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: Color(0xFF8E8E93),
              ),
            ),
          ),
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(children: children),
          ),
        ],
      ),
    );
  }

  Widget _scopeRow(String scope) {
    final meta = _scopeMeta[scope]!;
    final level = _scopes[scope] ?? _globalLevel;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
      decoration: const BoxDecoration(
        border:
            Border(bottom: BorderSide(color: Color(0xFFF0F0F2), width: 0.5)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(meta.icon, size: 18, color: const Color(0xFF007AFF)),
              const SizedBox(width: 8),
              Text(
                meta.title,
                style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w500,
                    color: Color(0xFF1C1C1E)),
              ),
              const Spacer(),
              Text(
                _levelLabels[level] ?? '允许',
                style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93)),
              ),
            ],
          ),
          const SizedBox(height: 3),
          Padding(
            padding: const EdgeInsets.only(left: 26),
            child: Text(
              meta.desc,
              style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93)),
            ),
          ),
          const SizedBox(height: 8),
          Padding(
            padding: const EdgeInsets.only(left: 26),
            child: _levelSelector(
              value: level,
              onChanged: (v) {
                setState(() => _scopes[scope] = v);
                _save(scopes: {scope: v});
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _levelSelector(
      {required String value, required ValueChanged<String> onChanged}) {
    return SegmentedButton<String>(
      segments: [
        for (final lv in _levels)
          ButtonSegment(
            value: lv,
            label: Text(_levelLabels[lv]!,
                style: TextStyle(
                    fontSize: 12,
                    color: value == lv ? const Color(0xFF007AFF) : null)),
          ),
      ],
      selected: {value},
      onSelectionChanged: (s) => onChanged(s.first),
      showSelectedIcon: false,
      style: const ButtonStyle(
        visualDensity: VisualDensity.compact,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}
