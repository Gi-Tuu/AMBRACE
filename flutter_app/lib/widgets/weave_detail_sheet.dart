import 'package:flutter/material.dart';

import '../models/memory.dart';
import '../models/weave_card.dart';
import '../screens/memory/memory_detail_screen.dart';
import '../utils/beijing_time.dart';

/// 织库卡片详情小窗（列表页与画布页共用）
class WeaveDetailSheet extends StatelessWidget {
  final WeaveCard card;
  final Future<void> Function() onDelete;

  const WeaveDetailSheet(
      {super.key, required this.card, required this.onDelete});

  Widget _kv(IconData icon, String label, String value) {
    final empty = value == '不详';
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 15, color: const Color(0xFF007AFF)),
        const SizedBox(width: 6),
        Text(
          '$label：',
          style: const TextStyle(
            fontSize: 12.5,
            fontWeight: FontWeight.w500,
            color: Color(0xFF1C1C1E),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: TextStyle(
              fontSize: 12.5,
              color: empty ? const Color(0xFFC7C7CC) : const Color(0xFF555555),
            ),
          ),
        ),
      ],
    );
  }

  Widget _listBlock(String title, List<String> items) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w600,
              color: Color(0xFF1C1C1E),
            ),
          ),
          const SizedBox(height: 8),
          for (final it in items)
            Padding(
              padding: const EdgeInsets.only(bottom: 5),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '· ',
                    style: TextStyle(fontSize: 12.5, color: Color(0xFF007AFF)),
                  ),
                  Expanded(
                    child: Text(
                      it,
                      style: const TextStyle(
                        fontSize: 12.5,
                        height: 1.45,
                        color: Color(0xFF555555),
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final d = card.detail;
    return Container(
      height: MediaQuery.of(context).size.height * 0.74,
      decoration: const BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      child: Column(
        children: [
          const SizedBox(height: 8),
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(
              color: const Color(0xFFD1D1D6),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 12, 8, 4),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    card.title,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                      color: Color(0xFF1C1C1E),
                    ),
                  ),
                ),
                if (card.characterName.isNotEmpty)
                  Container(
                    margin: const EdgeInsets.only(right: 6),
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF2F2F7),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      card.characterName,
                      style: const TextStyle(
                          fontSize: 11, color: Color(0xFF8E8E93)),
                    ),
                  ),
                IconButton(
                  icon: const Icon(Icons.close,
                      size: 20, color: Color(0xFF8E8E93)),
                  onPressed: () => Navigator.pop(context),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(18, 12, 18, 8),
              children: [
                Text(
                  card.summary,
                  style: const TextStyle(
                      fontSize: 13, height: 1.5, color: Color(0xFF666666)),
                ),
                const SizedBox(height: 12),
                if (d != null) ...[
                  _kv(Icons.schedule, '时间', d.time),
                  const SizedBox(height: 7),
                  _kv(Icons.wb_sunny_outlined, '天气', d.weather),
                  const SizedBox(height: 7),
                  _kv(Icons.place_outlined, '地点', d.location),
                  const SizedBox(height: 7),
                  _kv(Icons.sentiment_satisfied_outlined, '心情', d.mood),
                  _listBlock('事件', d.events),
                  _listBlock('细节', d.details),
                ],
                const SizedBox(height: 6),
                if (card.memories != null && card.memories!.isNotEmpty) ...[
                  const Text(
                    '参与记忆',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: Color(0xFF1C1C1E),
                    ),
                  ),
                  const SizedBox(height: 8),
                  for (final m in card.memories!) _memoryTile(context, m),
                ],
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(18, 6, 18, 14),
            child: SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                style: OutlinedButton.styleFrom(
                  foregroundColor: const Color(0xFFFF3B30),
                  side: const BorderSide(color: Color(0xFFFF3B30)),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                onPressed: () async {
                  Navigator.pop(context);
                  await onDelete();
                },
                child: const Text('删除卡片'),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Memory _toMemory(WeaveMemoryRef m) => Memory(
        id: m.id,
        memoryType: m.memoryType,
        subType: m.subType,
        sourceLabel: m.sourceLabel,
        sourceIcon: m.sourceIcon,
        content: m.content,
        importance: m.importancePct.round(),
        importancePct: m.importancePct,
        createdAt: m.createdAt,
      );

  void _openMemory(BuildContext ctx, WeaveMemoryRef m) {
    Navigator.push(
      ctx,
      MaterialPageRoute<void>(
        builder: (_) => MemoryDetailScreen(memory: _toMemory(m)),
      ),
    );
  }

  Widget _memoryTile(BuildContext ctx, WeaveMemoryRef m) {
    return GestureDetector(
      onTap: () => _openMemory(ctx, m),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: const Color(0xFFF2F2F7),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              m.sourceIcon ?? '📌',
              style: const TextStyle(fontSize: 13),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (m.sourceLabel != null && m.sourceLabel!.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 3),
                      child: Text(
                        m.sourceLabel!,
                        style: const TextStyle(
                            fontSize: 10.5, color: Color(0xFF007AFF)),
                      ),
                    ),
                  Text(
                    m.content,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 12, height: 1.4, color: Color(0xFF444444)),
                  ),
                  if (m.createdAt.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 3),
                      child: Text(
                        formatInTz(m.createdAt),
                        style: const TextStyle(
                            fontSize: 10.5, color: Color(0xFFC7C7CC)),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
