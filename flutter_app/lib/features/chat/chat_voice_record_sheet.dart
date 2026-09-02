// F7-b（2026-08-31）自 features/chat/chat_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:record/record.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../providers/chat_provider.dart';
// ── 语音录制面板：按住说话，松手发送，上滑取消，60s 上限，失败重试 ──
class ChatVoiceRecordSheet extends StatefulWidget {
  final ChatProvider chat;
  const ChatVoiceRecordSheet({super.key, required this.chat});

  @override
  State<ChatVoiceRecordSheet> createState() => _ChatVoiceRecordSheetState();
}

class _ChatVoiceRecordSheetState extends State<ChatVoiceRecordSheet> {
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  bool _sending = false;
  bool _cancelArmed = false;
  int _elapsed = 0;
  String? _path;
  Offset? _pressStart;
  Timer? _timer;
  static const int _maxSeconds = 60;

  @override
  void dispose() {
    _timer?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _startRecord() async {
    final l10n = AppLocalizations.of(context)!;
    final hasPerm = await _recorder.hasPermission();
    if (!hasPerm) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.chatMicPermission)));
      }
      return;
    }
    // m4a(AAC) 压缩格式：60s 语音约几百 KB，避免 wav PCM 大文件上传在弱网下断连（曾导致"发送失败"误报）
    _path = '${Directory.systemTemp.path}/aicompanion_voice_${DateTime.now().millisecondsSinceEpoch}.m4a';
    try {
      await _recorder.start(const RecordConfig(encoder: AudioEncoder.aacLc), path: _path!);
      if (mounted) {
        setState(() {
          _recording = true;
          _cancelArmed = false;
          _elapsed = 0;
        });
      }
      // 60s 上限：倒计时自动停止并发送
      _timer?.cancel();
      _timer = Timer.periodic(const Duration(seconds: 1), (_) {
        if (!mounted || !_recording) return;
        setState(() => _elapsed++);
        if (_elapsed >= _maxSeconds) {
          _timer?.cancel();
          _stopRecord(send: true);
        }
      });
    } catch (_) {}
  }

  Future<void> _stopRecord({required bool send}) async {
    if (!_recording) return;
    _timer?.cancel();
    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {}
    if (mounted) setState(() => _recording = false);
    if (!send || path == null || path.isEmpty) {
      _cleanup(path);
      return;
    }
    final file = File(path);
    if (!file.existsSync()) return;
    if (mounted) setState(() => _sending = true);
    final sec = _elapsed;
    await _sendWithRetry(file, sec);
  }

  Future<void> _sendWithRetry(File file, int sec) async {
    final l10n = AppLocalizations.of(context)!;
    while (mounted) {
      final ok = await widget.chat.uploadVoice(file, durationSec: sec);
      if (ok) break;
      if (!mounted) return;
      final retry = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(l10n.voiceSendFailed),
          content: Text(l10n.voiceRetryMsg),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(l10n.cancel),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(l10n.retry),
            ),
          ],
        ),
      );
      if (retry != true) {
        _cleanup(file.path);
        return;
      }
    }
    if (mounted) Navigator.pop(context);
  }

  void _cleanup(String? path) {
    try {
      if (path != null && File(path).existsSync()) File(path).deleteSync();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final theme = Theme.of(context);
    final hint = _sending
        ? l10n.sending
        : (_recording
            ? (_cancelArmed
                ? l10n.releaseToCancel
                : '${l10n.recordingPrefix} $_elapsed/$_maxSeconds${l10n.recordingSuffix}')
            : l10n.holdToTalk);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              hint,
              style: TextStyle(
                fontSize: 14,
                color: _cancelArmed ? Colors.orange.shade700 : (_recording ? Colors.red.shade400 : Colors.grey.shade600),
              ),
            ),
            const SizedBox(height: 16),
            GestureDetector(
              onLongPressStart: (d) {
                _pressStart = d.globalPosition;
                _startRecord();
              },
              onLongPressMoveUpdate: (d) {
                if (!_recording || _pressStart == null) return;
                final dy = d.globalPosition.dy - _pressStart!.dy;
                final armed = dy < -80;
                if (armed != _cancelArmed && mounted) {
                  setState(() => _cancelArmed = armed);
                }
              },
              onLongPressEnd: (_) {
                if (_cancelArmed) {
                  _stopRecord(send: false);
                  if (mounted) Navigator.pop(context);
                } else {
                  _stopRecord(send: true);
                }
              },
              onLongPressCancel: () => _stopRecord(send: false),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 120),
                width: 92,
                height: 92,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _cancelArmed ? Colors.orange.shade600 : (_recording ? Colors.red.shade400 : theme.colorScheme.primary),
                  boxShadow: _recording
                      ? [BoxShadow(color: (_cancelArmed ? Colors.orange : Colors.red).withValues(alpha: 0.35), blurRadius: 18, spreadRadius: 4)]
                      : null,
                ),
                child: Icon(
                  _cancelArmed ? Icons.keyboard_arrow_up : (_recording ? Icons.graphic_eq : Icons.mic),
                  color: Colors.white,
                  size: 38,
                ),
              ),
            ),
            const SizedBox(height: 12),
            TextButton(
              onPressed: () {
                _stopRecord(send: false);
                Navigator.pop(context);
              },
              child: Text(l10n.cancel),
            ),
          ],
        ),
      ),
    );
  }
}
