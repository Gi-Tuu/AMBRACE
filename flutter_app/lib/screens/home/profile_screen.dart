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
import "package:ai_companion/theme/tokens.dart";
import "package:ai_companion/widgets/app_page_route.dart";
import "package:ai_companion/l10n/app_localizations.dart";

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
      final l10n = AppLocalizations.of(context)!;
      setState(() { _error = l10n.profileLoadFail(e.toString()); _loading = false; });
    }
  }

  Future<void> _changeAvatar() async {
    final l10n = AppLocalizations.of(context)!;
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
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.avatarUpdated)));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(l10n.avatarUpdateFailed)));
      }
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context)!;
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
          SnackBar(content: Text(l10n.profileSaveSuccess)),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(l10n.profileSaveFail(e.toString()))),
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
    final l10n = AppLocalizations.of(context)!;
    final settings = context.watch<SettingsProvider>();
    const subColor = AppColors.textSecondary;
    const chevColor = AppColors.separator;
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
            Icon(icon, size: 20, color: AppColors.accent),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(title, style: const TextStyle(fontSize: 15, color: AppColors.textPrimary)),
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
      appBar: AppBar(title: Text(l10n.mine)),
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
                            child: Text(_showEdit ? l10n.collapse : l10n.profileEditInfo),
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
                                labelText: l10n.username,
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
                                labelText: l10n.nickname,
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
                              initialValue: _gender.isEmpty ? null : _gender,
                              decoration: InputDecoration(
                                labelText: l10n.gender,
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.wc),
                              ),
                              items: [
                                DropdownMenuItem(value: "male", child: Text(l10n.genderMale)),
                                DropdownMenuItem(value: "female", child: Text(l10n.genderFemale)),
                                DropdownMenuItem(value: "other", child: Text(l10n.genderOther)),
                              ],
                              onChanged: (v) => setState(() => _gender = v ?? ""),
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: _bdayCtrl,
                              keyboardType: TextInputType.datetime,
                              decoration: InputDecoration(
                                labelText: l10n.birthday,
                                border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                    filled: true,
                    fillColor: Theme.of(context).colorScheme.surfaceContainerHighest.withValues(alpha: 0.5),
                                prefixIcon: Icon(Icons.cake),
                                hintText: l10n.birthdayHint,
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
                                      labelText: l10n.profileHeightCm,
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
                                      labelText: l10n.profileWeightKg,
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
                                labelText: l10n.profileBio,
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
                              label: Text(l10n.save, style: TextStyle(fontWeight: FontWeight.bold)),
                              style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                            ),
                          ]),
                        ),
                      ),
                    // 我的空间（用户级入口）
                    group(l10n.profileMySpace, [
                      row(
                        icon: Icons.donut_large,
                        title: l10n.profileMyState,
                        subtitle: l10n.profileEightDimWeekly,
                        onTap: () => Navigator.push(context, AppPageRoute(builder: (_) => const UserVisualStateScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.favorite_outline,
                        title: l10n.goalTypeRelationship,
                        subtitle: l10n.profileRelationProgress,
                        onTap: () => Navigator.push(context, AppPageRoute(builder: (_) => const RelationshipScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.menu_book_outlined,
                        title: l10n.myDiary,
                        subtitle: l10n.profileDiaryMood,
                        onTap: () => Navigator.push(context, AppPageRoute(builder: (_) => const MyDiaryScreen())),
                      ),
                      divider(),
                      row(
                        icon: Icons.sticky_note_2_outlined,
                        title: l10n.profileMyMemos,
                        subtitle: l10n.profileMemoTip,
                        onTap: () => Navigator.push(context, AppPageRoute(builder: (_) => const MyMemosScreen())),
                      ),
                    ]),
                  ],
                ),
    );
  }
}
