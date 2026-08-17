import "dart:collection";
import "package:flutter/material.dart";
import "../../services/api_client.dart";

class ArchiveScreen extends StatefulWidget {
  final String characterName;
  final int sessionId;
  const ArchiveScreen({super.key, required this.characterName, required this.sessionId});
  @override
  State<ArchiveScreen> createState() => _ArchiveScreenState();
}

class _ArchiveScreenState extends State<ArchiveScreen> {
  List<Map<String, dynamic>> _days = [];
  bool _loading = true;
  String? _error;

  final Set<String> _expandedYears = {};
  final Set<String> _expandedMonths = {};
  final Set<String> _expandedDays = {};

  @override
  void initState() {
    super.initState();
    _loadArchive();
  }

  Future<void> _loadArchive() async {
    final api = ApiClient();
    try {
      final data = await api.getArchive(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _days = List<Map<String, dynamic>>.from(data["days"] as List);
        _loading = false;
        final now = DateTime.now();
        final curYear = now.year.toString();
        final curMonth = now.month.toString().padLeft(2, "0");
        _expandedYears.add(curYear);
        _expandedMonths.add("$curYear-$curMonth");
      });
    } catch (e) {
      setState(() { _error = "加载失败"; _loading = false; });
    }
  }

  SplayTreeMap<String, SplayTreeMap<String, List<Map<String, dynamic>>>> _groupByYearMonth() {
    final result = SplayTreeMap<String, SplayTreeMap<String, List<Map<String, dynamic>>>>((a, b) => b.compareTo(a));
    for (final day in _days) {
      final date = day["date"] as String;
      final parts = date.split("-");
      if (parts.length != 3) continue;
      final year = parts[0];
      final month = parts[1];
      result.putIfAbsent(year, () => SplayTreeMap<String, List<Map<String, dynamic>>>((a, b) => b.compareTo(a)));
      result[year]!.putIfAbsent(month, () => []);
      result[year]![month]!.add(day);
    }
    return result;
  }

  String _monthLabel(String m) {
    final labels = ["", "一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"];
    final idx = int.tryParse(m);
    return (idx != null && idx >= 1 && idx <= 12) ? labels[idx] : "$m月";
  }

  String _formatDate(String dateStr) {
    final parts = dateStr.split("-");
    if (parts.length != 3) return dateStr;
    return "${parts[1]}月${parts[2]}日";
  }

  String _formatBeijingTime(String isoTime) {
    if (isoTime.isEmpty || isoTime.length < 19) return "";
    try {
      final tp = isoTime.substring(11, 19);
      final p = tp.split(":");
      var h = int.parse(p[0]) + 8;
      if (h >= 24) h -= 24;
      return "${h.toString().padLeft(2, "0")}:${p[1]}";
    } catch (_) {
      return "";
    }
  }

  bool _isDayExpanded(String date) {
    return _expandedDays.contains("${widget.sessionId}_$date");
  }

  String _dayKey(String date) {
    return "${widget.sessionId}_$date";
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text("${widget.characterName}的聊天记录")),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _days.isEmpty
                  ? const Center(child: Text("暂无聊天记录"))
                  : _buildArchiveTree(),
    );
  }

  Widget _buildArchiveTree() {
    final grouped = _groupByYearMonth();
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        for (final yearEntry in grouped.entries)
          _buildYearSection(yearEntry.key, yearEntry.value),
      ],
    );
  }

  Widget _buildYearSection(String year, SplayTreeMap<String, List<Map<String, dynamic>>> months) {
    final isExpanded = _expandedYears.contains(year);
    final totalDays = months.values.fold(0, (sum, days) => sum + days.length);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Column(
        children: [
          InkWell(
            onTap: () {
              setState(() {
                if (isExpanded) {
                  _expandedYears.remove(year);
                  for (final k in months.keys) {
                    _expandedMonths.remove("$year-$k");
                  }
                } else {
                  _expandedYears.add(year);
                }
              });
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
              ),
              child: Row(
                children: [
                  Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 20, color: const Color(0xFFC6C6C8)),
                  const SizedBox(width: 8),
                  Text(year, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                  const Spacer(),
                  Text("$totalDays 天", style: const TextStyle(fontSize: 12, color: Color(0xFF8E8E93))),
                ],
              ),
            ),
          ),
          if (isExpanded)
            ...months.entries.map((mEntry) => _buildMonthSection(year, mEntry.key, mEntry.value)),
        ],
      ),
    );
  }

  Widget _buildMonthSection(String year, String month, List<Map<String, dynamic>> days) {
    final key = "$year-$month";
    final isExpanded = _expandedMonths.contains(key);
    final totalMsg = days.fold(0, (sum, d) => sum + (d["count"] as int));
    return Column(
      children: [
        InkWell(
          onTap: () {
            setState(() {
              if (isExpanded) {
                _expandedMonths.remove(key);
              } else {
                _expandedMonths.add(key);
              }
            });
          },
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.only(left: 24, right: 16, top: 10, bottom: 10),
            decoration: BoxDecoration(
              border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
            ),
            child: Row(
              children: [
                Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: const Color(0xFFC6C6C8)),
                const SizedBox(width: 6),
                Text(_monthLabel(month), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
                const Spacer(),
                Text("$totalMsg 条消息", style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
              ],
            ),
          ),
        ),
        if (isExpanded)
          ...days.map((day) => _buildDaySection(day)),
      ],
    );
  }

  Widget _buildDaySection(Map<String, dynamic> day) {
    final date = day["date"] as String;
    final messages = List<Map<String, dynamic>>.from(day["messages"] as List);
    final count = day["count"] as int;
    final isExpanded = _isDayExpanded(date);
    return Card(
      margin: const EdgeInsets.only(left: 36, right: 8, top: 4, bottom: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: () {
              final k = _dayKey(date);
              setState(() {
                if (isExpanded) {
                  _expandedDays.remove(k);
                } else {
                  _expandedDays.add(k);
                }
              });
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
              decoration: BoxDecoration(
                border: Border(bottom: BorderSide(color: Theme.of(context).dividerColor)),
              ),
              child: Row(
                children: [
                  Icon(isExpanded ? Icons.expand_more : Icons.chevron_right, size: 16, color: const Color(0xFFC6C6C8)),
                  const SizedBox(width: 4),
                  Text(_formatDate(date), style: const TextStyle(fontWeight: FontWeight.w500, fontSize: 13, color: Color(0xFF8E8E93))),
                  const Spacer(),
                  Text("$count 条", style: const TextStyle(fontSize: 11, color: Color(0xFF8E8E93))),
                ],
              ),
            ),
          ),
          if (isExpanded)
            ...messages.map((msg) {
              final isUser = msg["sender_type"] == "user";
              final content = msg["content"] as String;
              final imageUrl = msg["image_url"] as String?;
              final time = _formatBeijingTime(msg["created_at"] as String);
              return Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(time, style: const TextStyle(fontSize: 11, color: Colors.grey, fontFamily: "monospace")),
                    const SizedBox(width: 6),
                    Text(isUser ? "我" : widget.characterName,
                        style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: isUser ? Colors.blue : Colors.green)),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Row(
                        children: [
                          if (imageUrl != null && imageUrl.isNotEmpty) ...[
                            const Icon(Icons.image, size: 14, color: Colors.grey),
                            const SizedBox(width: 4),
                          ],
                          Expanded(child: Text(content, style: const TextStyle(fontSize: 14))),
                        ],
                      ),
                    ),
                  ],
                ),
              );
            }),
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}
