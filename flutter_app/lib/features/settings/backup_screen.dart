import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

import '../../services/api_client.dart';

/// 数据备份页（#54，2026-08-23）：
/// - 点击「导出备份」→ 后端触发备份 → 下载 zip → 用系统保存对话框存到手机
/// - 底部提供通用恢复指引（备份含 SQLite 数据库 + 配置，各平台步骤略有差异）
class BackupScreen extends StatefulWidget {
  const BackupScreen({super.key});

  @override
  State<BackupScreen> createState() => _BackupScreenState();
}

class _BackupScreenState extends State<BackupScreen> {
  bool _busy = false;
  Map<String, dynamic>? _backupInfo;

  Future<void> _exportBackup() async {
    final l10n = AppLocalizations.of(context)!;
    setState(() => _busy = true);
    try {
      // 1) 触发备份（当天已存在则返回现有文件信息；仅主账号）
      final info = await ApiClient().triggerBackup();
      final size = (info['size'] as num?)?.toInt() ?? 0;
      // 2) 下载备份 zip 字节
      final bytes = await ApiClient().downloadBackupBytes();
      if (!mounted) return;
      setState(() => _backupInfo = info);
      if (bytes.isEmpty) throw Exception('empty backup');

      // 3) 系统保存对话框（先把字节写进用户选定位置；取消会返回 null）
      final base = '${info['path'] ?? ''}'
          .replaceAll(RegExp(r'\.zip$'), '')
          .replaceAll(RegExp(r'[^A-Za-z0-9_\-.]'), '_');
      final fileName = 'ambrace-backup-$base.zip';
      final savedPath = await FilePicker.saveFile(
        dialogTitle: l10n.backupExport,
        fileName: fileName,
        type: FileType.any,
        bytes: bytes,
      );
      if (!mounted) return;
      if (savedPath == null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.backupExportCanceled)),
        );
        return;
      }
      final msg = size > 0
          ? l10n.backupExportSuccessWithSize(_formatSize(size))
          : l10n.backupExportSuccess;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
    } on Exception catch (e) {
      if (!mounted) return;
      final msg = e.toString();
      // 403 等鉴权错误 → 主账号提示；其余 → 展示下载链接兜底
      final isForbidden = msg.contains('403') || msg.contains('forbidden');
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(isForbidden ? l10n.backupAdminOnly : l10n.backupExportFailed),
        action: !isForbidden
            ? SnackBarAction(label: l10n.backupUrlCopy, onPressed: () => _copyBackupUrl())
            : null,
      ));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _formatSize(int bytes) {
    if (bytes >= 1024 * 1024) return '${(bytes / 1024 / 1024).toStringAsFixed(1)}MB';
    if (bytes >= 1024) return '${(bytes / 1024).toStringAsFixed(0)}KB';
    return '${bytes}B';
  }

  Future<void> _copyBackupUrl() async {
    final l10n = AppLocalizations.of(context)!;
    await Clipboard.setData(ClipboardData(text: ApiClient().backupDownloadUrl));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.backupCopied)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: Text(l10n.backupTitle)),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Card(
            color: scheme.surface,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  const Icon(Icons.backup_outlined, size: 44, color: Colors.teal),
                  const SizedBox(height: 8),
                  Text(l10n.backupSubtitle, style: const TextStyle(fontSize: 14)),
                  const SizedBox(height: 14),
                  FilledButton.icon(
                    onPressed: _busy ? null : _exportBackup,
                    icon: _busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.download_outlined),
                    label: Text(_busy ? l10n.backupExporting : l10n.backupExport),
                    style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(46)),
                  ),
                  if (_backupInfo != null) ...[
                    const SizedBox(height: 12),
                    Text(
                      '${l10n.backupFileLabel}: ${_backupInfo!['path']}',
                      style: const TextStyle(fontSize: 12, color: Colors.black54),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 20),
          Text(
            l10n.backupRestoreTitle,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 10),
          Text(l10n.backupRestoreNote,
              style: const TextStyle(fontSize: 13, color: Colors.black54)),
          const SizedBox(height: 12),
          _StepRow(index: '1', text: l10n.backupRestoreStep1),
          _StepRow(index: '2', text: l10n.backupRestoreStep2),
          _StepRow(index: '3', text: l10n.backupRestoreStep3),
          const SizedBox(height: 20),
          Card(
            color: scheme.surfaceContainerHighest.withValues(alpha: 0.4),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(l10n.backupUrlHint,
                      style: const TextStyle(fontSize: 12, color: Colors.black54)),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          ApiClient().backupDownloadUrl,
                          style: const TextStyle(fontSize: 12, fontFamily: 'monospace'),
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.copy, size: 18),
                        tooltip: l10n.backupUrlCopy,
                        onPressed: _copyBackupUrl,
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({required this.index, required this.text});
  final String index;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CircleAvatar(
            radius: 11,
            backgroundColor: Theme.of(context).colorScheme.primaryContainer,
            child: Text(index,
                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
          ),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}
