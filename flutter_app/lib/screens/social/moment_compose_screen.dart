import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:ai_companion/l10n/app_localizations.dart';

/// 发布动态独立页（微信式）：自带键盘避让，不再用底部弹层。
/// onSubmit 返回 true 表示发布成功，页面自行 pop；false 留在当前页。
class MomentComposeScreen extends StatefulWidget {
  final Future<bool> Function(String text, File? image)? onSubmit;

  const MomentComposeScreen({super.key, this.onSubmit});

  @override
  State<MomentComposeScreen> createState() => _MomentComposeScreenState();
}

class _MomentComposeScreenState extends State<MomentComposeScreen> {
  final _ctrl = TextEditingController();
  File? _image;
  bool _submitting = false;

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _pickImage() async {
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1920,
      maxHeight: 1920,
      imageQuality: 85,
    );
    if (picked != null && mounted) setState(() => _image = File(picked.path));
  }

  Future<void> _submit() async {
    final l10n = AppLocalizations.of(context)!;
    final text = _ctrl.text.trim();
    if (text.isEmpty && _image == null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l10n.contentRequired)));
      return;
    }
    if (_submitting) return;
    setState(() => _submitting = true);
    try {
      final ok = await widget.onSubmit?.call(text, _image) ?? false;
      if (ok && mounted) Navigator.of(context).pop(true);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<bool> _confirmDiscard() async {
    if (_ctrl.text.trim().isEmpty && _image == null) return true;
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        content: Text(l10n.editDiscardConfirm),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: Text(l10n.cancel)),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(l10n.confirm)),
        ],
      ),
    );
    return ok ?? false;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (await _confirmDiscard() && context.mounted) Navigator.pop(context);
      },
      child: Scaffold(
        // 默认 true：键盘弹起自动压缩 body，输入框永远可见
        resizeToAvoidBottomInset: true,
        appBar: AppBar(
          leading: TextButton(
            onPressed: () async {
              if (await _confirmDiscard() && context.mounted) {
                Navigator.of(context).pop();
              }
            },
            child: Text(l10n.cancel),
          ),
          title: Text(l10n.publishMoment),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: FilledButton(
                onPressed: _submitting ? null : _submit,
                child: _submitting
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(l10n.publish),
              ),
            ),
          ],
        ),
        body: SafeArea(
          child: Column(children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: TextField(
                controller: _ctrl,
                autofocus: true,
                maxLines: null,
                minLines: 8,
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(
                  hintText: l10n.momentComposeHint,
                  border: InputBorder.none,
                ),
              ),
            ),
            if (_image != null)
              Stack(children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.file(_image!, width: 160, height: 160, fit: BoxFit.cover),
                ),
                Positioned(
                  right: 4,
                  top: 4,
                  child: GestureDetector(
                    onTap: () => setState(() => _image = null),
                    child: const CircleAvatar(
                      radius: 12,
                      backgroundColor: Colors.black54,
                      child: Icon(Icons.close, size: 16, color: Colors.white),
                    ),
                  ),
                ),
              ]),
            const Spacer(),
            const Divider(height: 1),
            Row(children: [
              IconButton(
                onPressed: _pickImage,
                icon: Icon(Icons.image_outlined, color: scheme.primary),
                tooltip: l10n.image,
              ),
            ]),
          ]),
        ),
      ),
    );
  }
}
