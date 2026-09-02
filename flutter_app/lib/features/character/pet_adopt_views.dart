// F7-c-1（2026-08-31）自 features/character/pet_screen.dart 拆分迁入；逻辑逐字节保持。
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import '../../theme/tokens.dart';
import '../../providers/pets_provider.dart';
import '../../services/api_client.dart';

List<(String, String)> adoptableSpecies(AppLocalizations l10n) => [
  ("cat", l10n.speciesCat),
  ("dog", l10n.speciesDog),
  ("parrot", l10n.speciesParrot),
  ("rabbit", l10n.speciesRabbit),
  ("hamster", l10n.speciesHamster),
  ("snake", l10n.speciesSnake),
  ("gecko", l10n.speciesGecko),
];

class AdoptView extends StatelessWidget {
  final PetsProvider p;
  final void Function(PetsProvider p, String species, String label) onAdopt;
  const AdoptView({super.key, required this.p, required this.onAdopt});

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        Text(l10n.adoptHeading, style: const TextStyle(fontSize: AppTypography.titleSize, fontWeight: AppTypography.titleWeight)),
        const SizedBox(height: 4),
        Text(l10n.adoptSubtitle,
            style: TextStyle(fontSize: 13, color: AppColors.textMuted)),
        const SizedBox(height: 16),
        GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: 0.82,
          children: [
            for (final (species, label) in adoptableSpecies(l10n))
              SpeciesCard(
                species: species,
                label: label,
                locked: false,
                onTap: () => onAdopt(p, species, label),
              ),
          ],
        ),
      ],
    );
  }
}

class SpeciesCard extends StatelessWidget {
  final String species;
  final String label;
  final bool locked;
  final VoidCallback onTap;
  const SpeciesCard({super.key, 
    required this.species,
    required this.label,
    required this.locked,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final url = ApiClient().resolveUrl("/uploads/pets_assets/$species/idle.png");
    return InkWell(
      onTap: locked ? null : onTap,
      borderRadius: BorderRadius.circular(AppRadius.md),
      child: Opacity(
        opacity: locked ? 0.45 : 1,
        child: Column(
          children: [
            Expanded(
              child: Container(
                width: double.infinity,
                decoration: BoxDecoration(
                  color: AppColors.surfaceAlt,
                  borderRadius: BorderRadius.circular(AppRadius.md),
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(AppRadius.md),
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      Image.network(
                        url,
                        fit: BoxFit.cover,
                        gaplessPlayback: true,
                        errorBuilder: (c, e, s) => Icon(Icons.pets, size: 44, color: AppColors.textTertiary),
                      ),
                      if (locked)
                        const Center(child: Icon(Icons.lock, color: Colors.white70, size: 28)),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 6),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
                if (locked) ...[
                  const SizedBox(width: 3),
                  Text(l10n.comingSoon, style: TextStyle(fontSize: 11, color: AppColors.textSecondary)),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

