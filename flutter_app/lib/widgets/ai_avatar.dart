
import 'package:flutter/material.dart';
import '../services/api_client.dart';

class AIAvatar extends StatelessWidget {
  final String name;
  final double size;
  final String? imageUrl;

  const AIAvatar({
    super.key,
    required this.name,
    this.size = 40,
    this.imageUrl,
  });

  @override
  Widget build(BuildContext context) {
    return CircleAvatar(
      radius: size / 2,
      backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
      child: (imageUrl != null && imageUrl!.isNotEmpty)
          ? ClipOval(
              child: Image.network(ApiClient().resolveUrl(imageUrl), width: size, height: size, fit: BoxFit.cover),
            )
          : Text(
              name.isNotEmpty ? name[0] : 'AI',
              style: TextStyle(
                fontSize: size * 0.45,
                fontWeight: FontWeight.bold,
                color: Theme.of(context).colorScheme.onSecondaryContainer,
              ),
            ),
    );
  }
}
