import "package:flutter/material.dart";
import "../../services/shizuku_service.dart";
import "package:ai_companion/theme/tokens.dart";

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
      _showSnack(sent
          ? "已发起授权请求，请在系统弹窗中点击允许"
          : _serverRunning
              ? "已授权或发起失败，请检查状态后重试"
              : "Shizuku 服务未运行：请先在 Shizuku app（或 ADB）启动服务");
    }
    await Future.delayed(const Duration(milliseconds: 1500));
    await _refresh();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _loadApps() async {
    setState(() => _busy = true);
    final r = await ShizukuService.getAppList();
    if (mounted) {
      setState(() {
        _busy = false;
        _packages = (r["packages"] as List<dynamic>? ?? []).cast<String>();
        if (r["ok"] != true) {
          _showSnack("读取失败：${r["error"] ?? "未知错误"}");
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
    final scheme = Theme.of(context).colorScheme;
    const subColor = AppColors.textSecondary;
    const iconColor = AppColors.accent;

    return Scaffold(
      appBar: AppBar(title: const Text("Shizuku 权限")),
      body: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 24),
        children: [
          // 说明
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Text(
              "Shizuku 让 AI 获得系统级能力（应用列表/系统设置/模拟操作前置）。"
              "需先安装 Shizuku app 并启动服务（root 直启，或电脑 ADB 执行 start.sh），"
              "再在下方请求授权。授权后可在本页验证读取应用列表与执行 Shell。",
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
                _statusRow(scheme, iconColor, Icons.dns_outlined, "Shizuku 服务", _serverRunning),
                const SizedBox(height: 6),
                _statusRow(scheme, iconColor, Icons.verified_user_outlined, "本应用授权", _granted),
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
                const Text("操作", style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy ? null : _request,
                    icon: const Icon(Icons.key, size: 18),
                    label: Text(_granted ? "重新请求授权" : "请求授权"),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _loadApps,
                    icon: const Icon(Icons.apps, size: 18),
                    label: const Text("读取已安装应用列表（测试）"),
                  ),
                ),
                if (_packages.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    "共 ${_packages.length} 个第三方应用：\n${_packages.take(12).join("、")}${_packages.length > 12 ? " …" : ""}",
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
                const Text("Shell 调试", style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                TextField(
                  controller: TextEditingController(text: _shellCmd),
                  onChanged: (v) => _shellCmd = v,
                  decoration: const InputDecoration(
                    isDense: true,
                    hintText: "如 pm list packages -3",
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _runShell,
                    icon: const Icon(Icons.terminal, size: 18),
                    label: const Text("执行"),
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
    return Row(
      children: [
        Icon(icon, size: 20, color: ok ? AppColors.success : iconColor),
        const SizedBox(width: 10),
        Text(label, style: const TextStyle(fontSize: 14)),
        const Spacer(),
        Text(
          ok ? "正常" : "未就绪",
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
