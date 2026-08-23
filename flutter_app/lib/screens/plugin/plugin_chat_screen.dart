import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import '../../services/api_client.dart';

/// 48c：chat 型插件通用互动聊天页（零代码）
/// - greeting 开场白（来自 config.chat.greeting，随插件配置一起下发）
/// - 发送消息 → POST /api/v1/plugins/{name}/chat（persona 作 system prompt，BYOK 三级回退）
/// - 逐条展示对话；不建会话、不写记忆（后端保证）
class PluginChatScreen extends StatefulWidget {
  const PluginChatScreen({super.key, required this.plugin});

  final Map<String, dynamic> plugin;

  @override
  State<PluginChatScreen> createState() => _PluginChatScreenState();
}

class _Msg {
  _Msg(this.role, this.text);
  final String role; // user / assistant
  final String text;
}

class _PluginChatScreenState extends State<PluginChatScreen> {
  final TextEditingController _inputCtrl = TextEditingController();
  final ScrollController _scrollCtrl = ScrollController();
  final List<_Msg> _messages = [];
  bool _sending = false;
  bool _greetingShown = false;

  String get _pluginName => widget.plugin['name'] as String? ?? '';

  Map<String, dynamic>? get _chatCfg =>
      (widget.plugin['config'] as Map<String, dynamic>?)?['chat']
          as Map<String, dynamic>?;

  @override
  void initState() {
    super.initState();
    final greeting = ((_chatCfg?['greeting'] as String?) ?? '').trim();
    if (greeting.isNotEmpty) {
      _messages.add(_Msg('assistant', greeting));
      _greetingShown = true;
    }
  }

  @override
  void dispose() {
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  String _title() {
    final name = (_chatCfg?['name'] as String?) ?? '';
    return name.trim().isNotEmpty ? name.trim() : _pluginName;
  }

  Future<void> _send() async {
    final text = _inputCtrl.text.trim();
    if (text.isEmpty || _sending) return;
    // 历史 = 已展示的对话（跳过 greeting 开场白；当前输入由 input 单独传）
    final history = <Map<String, dynamic>>[];
    for (var i = _greetingShown ? 1 : 0; i < _messages.length; i++) {
      history.add({'role': _messages[i].role, 'content': _messages[i].text});
    }
    setState(() {
      _messages.add(_Msg('user', text));
      _sending = true;
    });
    _inputCtrl.clear();
    _scrollToBottom();
    try {
      final data = await ApiClient().pluginChat(_pluginName, input: text, history: history);
      final reply = ((data['reply'] as String?) ?? '').trim();
      if (!mounted) return;
      setState(() {
        _messages.add(_Msg('assistant', reply.isEmpty ? '…' : reply));
        _sending = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted) return;
      setState(() => _sending = false);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('${AppLocalizations.of(context)!.pluginChatSendFail}: $e'),
      ));
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(_title())),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scrollCtrl,
              padding: const EdgeInsets.all(12),
              itemCount: _messages.length,
              itemBuilder: (_, i) => _bubble(_messages[i]),
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: TextField(
                      controller: _inputCtrl,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onSubmitted: (_) => _send(),
                      decoration: InputDecoration(
                        hintText: l10n.pluginChatInputHint,
                        isDense: true,
                        filled: true,
                        fillColor: Theme.of(context)
                            .colorScheme
                            .surfaceContainerHighest
                            .withValues(alpha: 0.6),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(20),
                          borderSide: BorderSide.none,
                        ),
                        contentPadding:
                            const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    onPressed: _sending ? null : _send,
                    icon: _sending
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.send),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _bubble(_Msg m) {
    final isUser = m.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.75,
        ),
        decoration: BoxDecoration(
          color: isUser
              ? Theme.of(context).colorScheme.primaryContainer
              : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(14),
            topRight: const Radius.circular(14),
            bottomLeft: Radius.circular(isUser ? 14 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 14),
          ),
        ),
        child: Text(m.text, style: const TextStyle(fontSize: 14, height: 1.4)),
      ),
    );
  }
}
