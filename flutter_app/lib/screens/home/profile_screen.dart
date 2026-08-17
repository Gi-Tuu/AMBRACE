import "dart:io";
import "package:flutter/material.dart";
import "package:image_picker/image_picker.dart";
import "package:provider/provider.dart";
import "package:dio/dio.dart";
import "../../services/api_client.dart";
import "../../providers/settings_provider.dart";
import "../../models/user_profile.dart";
import "../../services/notification_service.dart";
import "../character/relationship_screen.dart";
import "../state/user_visual_state_screen.dart";
import "../diary/my_memos_screen.dart";
import "../diary/my_diary_screen.dart";

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  UserProfile? _profile;
  bool _loading = true;
  bool _saving = false;
  String? _error;
  bool _showEdit = false;

  final _nameCtrl = TextEditingController();
  final _bioCtrl = TextEditingController();
  final _heightCtrl = TextEditingController();
  final _weightCtrl = TextEditingController();
  final _bdayCtrl = TextEditingController();
  String _gender = "";

  @override
  void initState() {
    super.initState();
    NotificationService().setActiveScreen(ActiveScreen.other);
    _loadProfile();
  }

  Future<void> _loadProfile() async {
    final settings = context.read<SettingsProvider>();
    try {
      final r = await Dio().get(
        "${settings.serverUrl}/api/v1/auth/profile",
        options: Options(
          headers: {"Authorization": "Bearer ${settings.token}"},
          connectTimeout: const Duration(seconds: 5),
        ),
      );
      final profile = UserProfile.fromJson(r.data as Map<String, dynamic>);
      if (profile.avatarUrl != null && profile.avatarUrl!.isNotEmpty) {
        await settings.setAvatarUrl(profile.avatarUrl!);
      }
      if (!mounted) return;
      setState(() {
        _profile = profile;
        _loading = false;
        _nameCtrl.text = profile.nickname;
        _bioCtrl.text = profile.bio ?? "";
        _gender = profile.gender ?? "";
        if (profile.birthday != null) _bdayCtrl.text = profile.birthday!;
        if (profile.height != null) _heightCtrl.text = profile.height!.toStringAsFixed(1);
        if (profile.weight != null) _weightCtrl.text = profile.weight!.toStringAsFixed(1);
      });
    } catch (e) {
      setState(() { _error = "加载失败: $e"; _loading = false; });
    }
  }

  Future<void> _changeAvatar() async {
    final picked = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 1024,
      maxHeight: 1024,
      imageQuality: 85,
    );
    if (picked == null || !mounted) return;
    final settings = context.read<SettingsProvider>();
    try {
      final up = await ApiClient().uploadAvatar(File(picked.path));
      final url = up["url"] as String? ?? "";
      if (url.isEmpty) throw Exception("empty url");
      await ApiClient().updateProfile({"avatar_url": url});
      await settings.setAvatarUrl(url);
      await _loadProfile();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("头像已更新")));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text("头像更新失败")));
      }
    }
  }

  Future<void> _save() async {
    final settings = context.read<SettingsProvider>();
    setState(() { _saving = true; });
    try {
      final data = <String, dynamic>{
        "nickname": _nameCtrl.text.trim(),
        "bio": _bioCtrl.text.trim(),
        "gender": _gender,
        "birthday": _bdayCtrl.text.trim(),
      };
      if (_heightCtrl.text.trim().isNotEmpty) {
        data["height"] = double.tryParse(_heightCtrl.text.trim());
      }
      if (_weightCtrl.text.trim().isNotEmpty) {
        data["weight"] = double.tryParse(_weightCtrl.text.trim());
      }
      await Dio().put(
        "${settings.serverUrl}/api/v1/auth/profile",
        data: data,
        options: Options(
          headers: {"Authorization": "Bearer ${settings.token}"},
          connectTimeout: const Duration(seconds: 5),
        ),
      );
      if (_profile != null) {
        await settings.setNickname(_nameCtrl.text.trim());
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("保存成功")),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("保存失败: $e")),
        );
      }
    } finally {
      if (mounted) setState(() { _saving = false; });
    }
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _bioCtrl.dispose();
    _heightCtrl.dispose();
    _weightCtrl.dispose();
    _bdayCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<SettingsProvider>();
    const subColor = Color(0xFF8E8E93);
    const chevColor = Color(0xFFC6C6C8);
    final cardColor = Theme.of(context).colorScheme.surface;

    Widget group(String title, List<Widget> rows) {
      return Padding(
        padding: const EdgeInsets.only(left: 12, right: 12, bottom: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(left: 16, bottom: 6),
              child: Text(title,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: subColor)),
            ),
            Container(
              decoration: BoxDecoration(color: cardColor, borderRadius: BorderRadius.circular(12)),
              child: Column(children: rows),
            ),
          ],
        ),
      );
    }

    Widget row({
      required IconData icon,
      required String title,
      required String subtitle,
      required VoidCallback onTap,
    }) {
      return InkWell(
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 52),
          padding: const EdgeInsets.symmetric(horizontal: 14),
          child: Row(children: [
            Icon(icon, size: 20, color: const Color(0xFF007AFF)),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(title, style: const TextStyle(fontSize: 15, color: Color(0xFF1C1C1E))),
                  const SizedBox(height: 1),
                  Text(subtitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 11, color: subColor)),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, size: 18, color: chevColor),
          ]),
        ),
      );
    }

    Widget divider() => Container(
          height: 0.5,
          margin: const EdgeInsets.only(left: 46),
          color: Theme.of(context).dividerColor,
        );

    return Scaffold(
      appBar: AppBar(title: const Text("我的")),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : ListView(
                  padding: const EdgeInsets.only(top: 8, bottom: 16),
                  children: [
                    // 用户卡：头像（点击换图）+ 昵称 + ID + 签名 + 编辑资料
                    Padding(
                      padding: const EdgeInsets.fromLTRB(12, 8, 12, 14),
                      child: Container(
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(color: cardColor, borderRadius: BorderRadius.circular(16)),
                        child: Row(children: [
                          GestureDetector(
                            onTap: _changeAvatar,
                            child: Stack(children: [
                              CircleAvatar(
                                radius: 28,
                                backgroundColor: Colors.blue.withValues(alpha: 0.2),
                                child: _profile?.avatarUrl != null && _profile!.avatarUrl!.isNotEmpty
                                    ? ClipOval(
                                        child: Image.network(
                                          ApiClient().resolveUrl(_profile!.avatarUrl!),
                                          width: 56,
                                          height: 56,
                                          fit: BoxFit.cover,
                                          errorBuilder: (context, error, stack) => Text(
                                            settings.nickname.isNotEmpty ? settings.nickname[0] : "?",
                                            style: const TextStyle(fontSize: 22),
                                          ),
                                        ),
                                      )
                                    : Text(
                                        settings.nickname.isNotEmpty ? settings.nickname[0] : "?",
                                        style: const TextStyle(fontSize: 22),
                                      ),
                              ),
                              Positioned(
                                right: 0,
                                bottom: 0,
                                child: Container(
                                  padding: const EdgeInsets.all(3),
                                  decoration: const BoxDecoration(color: Colors.blue, shape: BoxShape.circle),
                                  child: const Icon(Icons.camera_alt, size: 12, color: Colors.white),
                                ),
                              ),
                            ]),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(settings.nickname,
                                    style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                                const SizedBox(height: 2),
                                Text('ID: ${_profile?.username ?? settings.userId}',
                                    style: const TextStyle(fontSize: 12, color: subColor)),
                                if (_profile?.bio != null && _profile!.bio!.isNotEmpty) ...[
                                  const SizedBox(height: 2),
                                  Text(_profile!.bio!,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(fontSize: 12, color: subColor)),
                                ],
                              ],
                            ),
                          ),
                          TextButton(
                            onPressed: () => setState(() => _showEdit = !_showEdit),
                            child: Text(_showEdit ? '收起' : '编辑资料'),
                          ),
                        ]),
                      ),
                    ),
                    // 编辑资料表单（可折叠，保留原保存功能）
                    if (_showEdit)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(12, 0, 12, 14),
                        child: Container(
                          padding: const EdgeInsets.all(16),
                          decoration: BoxDecoration(color: cardColor, borderRadius: BorderRadius.circular(16)),
                          child: Column(children: [
                            TextField(
                              decoration: InputDecoration(
                                labelText: "用户名",
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.person),
                              ),
                              enabled: false,
                              controller: TextEditingController(text: _profile?.username ?? ""),
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: _nameCtrl,
                              decoration: InputDecoration(
                                labelText: "昵称",
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.edit),
                              ),
                            ),
                            const SizedBox(height: 12),
                            DropdownButtonFormField<String>(
                              value: _gender.isEmpty ? null : _gender,
                              decoration: InputDecoration(
                                labelText: "性别",
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.wc),
                              ),
                              items: const [
                                DropdownMenuItem(value: "male", child: Text("男")),
                                DropdownMenuItem(value: "female", child: Text("女")),
                                DropdownMenuItem(value: "other", child: Text("其他")),
                              ],
                              onChanged: (v) => setState(() => _gender = v ?? ""),
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: _bdayCtrl,
                              keyboardType: TextInputType.datetime,
                              decoration: InputDecoration(
                                labelText: "生日",
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.cake),
                                hintText: "YYYY-MM-DD（例如 1998-05-20）",
                              ),
                            ),
                            const SizedBox(height: 12),
                            Row(
                              children: [
                                Expanded(
                                  child: TextField(
                                    controller: _heightCtrl,
                                    keyboardType: TextInputType.number,
                                    decoration: InputDecoration(
                                      labelText: "身高 (cm)",
                                      border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                      prefixIcon: Icon(Icons.straighten),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: TextField(
                                    controller: _weightCtrl,
                                    keyboardType: TextInputType.number,
                                    decoration: InputDecoration(
                                      labelText: "体重 (kg)",
                                      border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                      prefixIcon: Icon(Icons.monitor_weight),
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: _bioCtrl,
                              maxLines: 4,
                              decoration: InputDecoration(
                                labelText: "个人描述",
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.description),
                                alignLabelWithHint: true,
                              ),
                            ),
                            const SizedBox(height: 16),
                            FilledButton.icon(
                              onPressed: _saving ? null : _save,
                              icon: _saving
                                  ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                                  : const Icon(Icons.save_outlined),
                              label: const Text("保存", style: TextStyle(fontWeight: FontWeight.bold)),
                              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                            ),
                          ]),
                        ),
                      ),
                    // 我的空间（用户级入口）
                    group('我的空间', [
                      row(
                        icon: Icons.donut_large,
                        title: '我的状态',
                        subtitle: '八维状态与周视图',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const UserVisualStateScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.favorite_outline,
                        title: '关系',
                        subtitle: '与伙伴的关系进度',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const RelationshipScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.menu_book_outlined,
                        title: '我的日记',
                        subtitle: '记录每天的心情',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const MyDiaryScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.sticky_note_2_outlined,
                        title: '我的备忘',
                        subtitle: '随手记，不忘记',
                        onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => const MyMemosScreen())),
                      ),
                    ]),
                  ],
                ),
    );
  }
}
