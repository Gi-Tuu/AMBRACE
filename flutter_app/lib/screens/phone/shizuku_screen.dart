import "package:flutter/material.dart";
import "../../services/shizuku_service.dart";
import "package:ai_companion/theme/tokens.dart";
import "package:flutter_gen/gen_l10n/app_localizations.dart";

/// Shizuku 权限通道设置页（2026-08-12）：状态 / 授权 / 应用列表与 Shell 测试
/// 前置：手机安装 Shizuku app 并启动服务（root 或 ADB：电脑执行
///   adb shell sh /sdcard/Android/data/moe.shizuku.privileged.api/start.sh）
class ShizukuScreen extends StatefulWidget {
  const ShizukuScreen({super.key});

  @override
  State<ShizukuScreen> createState() => _ShizukuScreenState();
}

class _ShizukuScreenState extends State<ShizukuScreen> with WidgetsBindingObserver {
  bool _serverRunning = false;
  bool _granted = false;
  bool _busy = false;
  List<String> _packages = [];
  String _shellCmd = "pm list packages -3";
  String _shellOut = "";
  String _shellErr = "";

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 从 Shizuku 授权弹窗/系统设置返回后自动刷新状态
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    final st = await ShizukuService.status();
    if (mounted) {
      setState(() {
        _serverRunning = st["serverRunning"] == true;
        _granted = st["permissionGranted"] == true;
      });
    }
  }

  Future<void> _request() async {
    setState(() => _busy = true);
    final sent = await ShizukuService.requestPermission();
    if (mounted) {
      final l10n = AppLocalizations.of(context)!;
      _showSnack(sent
          ? l10n.shizukuRequestSent
          : _serverRunning
              ? l10n.shizukuRequestFailed
              : l10n.shizukuNotRunning);
    }
    await Future.delayed(const Duration(milliseconds: 1500));
    await _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _loadApps() async {
    setState(() => _busy = true);
    final l10n = AppLocalizations.of(context)!;
    final r = await ShizukuService.getAppList();
    if (mounted) {
      setState(() {
        _busy = false;
        _packages = (r["packages"] as List<dynamic>? ?? []).cast<String>();
        if (r["ok"] != true) {
          final err = (r["error"] as String?) ?? "";
          _showSnack(l10n.shizukuReadAppsFailed(err.isEmpty ? l10n.unknownError : err));
        }
      });
    }
  }

  Future<void> _runShell() async {
    if (_shellCmd.trim().isEmpty) return;
    setState(() => _busy = true);
    final r = await ShizukuService.runShell(_shellCmd.trim());
    if (mounted) {
      setState(() {
        _busy = false;
        _shellOut = (r["stdout"] as String? ?? "").trim();
        _shellErr = (r["stderr"] as String? ?? "").trim();
      });
    }
  }

  void _showSnack(String msg) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final scheme = Theme.of(context).colorScheme;
    const subColor = AppColors.textSecondary;
    const iconColor = AppColors.accent;

    return Scaffold(
      appBar: AppBar(title: Text(l10n.ppShizukuTitle)),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 24),
        children: [
          // 说明
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Text(
              l10n.shizukuIntro,
              style: const TextStyle(fontSize: 13, color: subColor),
            ),
          ),
          // 状态卡
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.surface,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _statusRow(scheme, iconColor, Icons.dns_outlined, l10n.ppShizukuServer, _serverRunning),
                const SizedBox(height: 6),
                _statusRow(scheme, iconColor, Icons.verified_user_outlined, l10n.ppShizukuGranted, _granted),
              ],
            ),
          ),
          // 操作
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.surface,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.chatOpDefault, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy ? null : _request,
                    icon: const Icon(Icons.key, size: 18),
                    label: Text(_granted ? l10n.shizukuReRequest : l10n.shizukuRequest),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _loadApps,
                    icon: const Icon(Icons.apps, size: 18),
                    label: Text(l10n.shizukuLoadApps),
                  ),
                ),
                if (_packages.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    l10n.shizukuThirdPartyAppCount(_packages.length, _packages.take(12).join(l10n.shizukuAppSeparator) + (_packages.length > 12 ? " …" : "")),
                    style: const TextStyle(fontSize: 12, color: subColor),
                  ),
                ],
              ],
            ),
          ),
          // Shell 调试
          Container(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.surface,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l10n.shizukuShellDebug, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                TextField(
                  controller: TextEditingController(text: _shellCmd),
                  onChanged: (v) => _shellCmd = v,
                  decoration: InputDecoration(
                    isDense: true,
                    hintText: l10n.shizukuShellHint,
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _runShell,
                    icon: const Icon(Icons.terminal, size: 18),
                    label: Text(l10n.shizukuExecute),
                  ),
                ),
                if (_shellOut.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: SelectableText(
                      _shellOut.length > 600 ? _shellOut.substring(0, 600) : _shellOut,
                      style: const TextStyle(fontSize: 11),
                    ),
                  ),
                if (_shellErr.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: SelectableText(
                      _shellErr,
                      style: TextStyle(fontSize: 11, color: scheme.error),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _statusRow(ColorScheme scheme, Color iconColor, IconData icon, String label, bool ok) {
    final l10n = AppLocalizations.of(context)!;
    return Row(
      children: [
        Icon(icon, size: 20, color: ok ? AppColors.success : iconColor),
        const SizedBox(width: 10),
        Text(label, style: const TextStyle(fontSize: 14)),
        const Spacer(),
        Text(
          ok ? l10n.pitchNormal : l10n.ppNotReady,
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
            color: ok ? AppColors.success : AppColors.textSecondary,
          ),
        ),
      ],
    );
  }
}
