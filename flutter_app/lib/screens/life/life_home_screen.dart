import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

import 'life_timeline_screen.dart';
import 'life_profile_screen.dart';
import 'life_artifacts_screen.dart';

/// AI 生活聚合页（2026-08-14）：AI生活 / 兴趣与目标 / 产物库 三个 tab
class LifeHomeScreen extends StatelessWidget {
  const LifeHomeScreen({super.key, required this.characterId, required this.characterName});

  final int characterId;
  final String characterName;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        backgroundColor: const Color(0xFFF2F2F7),
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          title: Text(l10n.lifeHomeTitle(characterName)),
          centerTitle: true,
          bottom: TabBar(
            tabs: [
              Tab(text: l10n.aiLife),
              Tab(text: l10n.interestsGoalsTab),
              Tab(text: l10n.artifactsTab),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            LifeTimelineScreen(
              characterId: characterId,
              characterName: characterName,
              showScaffold: false,
            ),
            LifeProfileScreen(
              characterId: characterId,
              characterName: characterName,
              showScaffold: false,
            ),
            LifeArtifactsScreen(
              characterId: characterId,
              characterName: characterName,
              showScaffold: false,
            ),
          ],
        ),
      ),
    );
  }
}
