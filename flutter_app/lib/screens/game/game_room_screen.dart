import 'package:flutter/material.dart';
import '../../l10n/app_localizations.dart';
import '../../providers/game_provider.dart';
import 'archive_card.dart';

/// 游戏浮层：阶段条、自己的身份牌/私密信息、公开事件流、
/// 当前轮到自己的动作按钮（描述/投票/选真心话或大冒险/提问/猜词）、
/// AI 回合自动轮询，结束后展示游乐手札卡片。
class GameRoomScreen extends StatefulWidget {
  final GameProvider provider;
  const GameRoomScreen({super.key, required this.provider});

  @override
  State<GameRoomScreen> createState() => _GameRoomScreenState();
}

class _GameRoomScreenState extends State<GameRoomScreen> {
  final TextEditingController _textCtrl = TextEditingController();
  String _selectedVoteSeat = '';

  GameProvider get p => widget.provider;

  @override
  void initState() {
    super.initState();
    p.startPolling();
  }

  @override
  void dispose() {
    p.stopPolling();
    _textCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final game = p.game;
    final isFinished = p.isFinished;
    return Scaffold(
      appBar: AppBar(
        title: Text(l10n.gameRoomTitle),
        actions: [
          if (p.hasSession && !isFinished)
            IconButton(
              tooltip: l10n.gameAbort,
              icon: const Icon(Icons.close),
              onPressed: _abort,
            ),
        ],
      ),
      body: game == null
          ? Center(child: Text(l10n.gameLoading))
          : isFinished
              ? _finishedView(l10n)
              : _playingView(l10n),
    );
  }

  Widget _finishedView(AppLocalizations l10n) {
    final archive = p.archive;
    final winners = ((archive?['players'] as List?) ?? const [])
        .cast<Map<String, dynamic>>()
        .where((pl) => pl['result'] == 'won')
        .map((pl) => (pl['name'] as String?) ?? '')
        .where((s) => s.isNotEmpty)
        .toList();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(l10n.gameFinished,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        Text(
          winners.isNotEmpty
              ? '🏆 ${l10n.gameWinLabel}: ${winners.join('、')}'
              : '🏳️ ${l10n.gameDrawLabel}',
          style: const TextStyle(fontSize: 14, color: Colors.grey),
        ),
        const SizedBox(height: 12),
        if (archive != null)
          ArchiveCard(archive: archive)
        else
          Text(l10n.gameNoArchive),
        const SizedBox(height: 20),
        FilledButton.tonal(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(l10n.back),
        ),
      ],
    );
  }

  Widget _playingView(AppLocalizations l10n) {
    return Column(
      children: [
        _phaseBar(l10n),
        if (!p.isFinished) _identityCard(l10n),
        Expanded(child: _eventsList(l10n)),
        if (p.myTurn && !p.isFinished) _actionPanel(l10n),
      ],
    );
  }

  Widget _phaseBar(AppLocalizations l10n) {
    final g = p.game;
    final phase = (g?['phase'] as String?) ?? '';
    final round = ((g?['round'] as num?)?.toInt() ?? 0);
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        children: [
          Chip(
            avatar: const Icon(Icons.sports_esports, size: 16),
            label: Text('${l10n.gameRound} $round'),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '${l10n.gamePhase}: $phase',
              style: const TextStyle(fontSize: 13),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (p.myTurn)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.primary,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(l10n.gameMyTurn,
                  style: TextStyle(color: Theme.of(context).colorScheme.onPrimary, fontSize: 12)),
            )
          else
            Text(l10n.gameWaiting, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }

  Widget _identityCard(AppLocalizations l10n) {
    final my = p.my;
    if (my == null) return const SizedBox.shrink();
    final private = (my['private'] as Map<String, dynamic>?) ?? {};
    final role = (my['role'] as String?) ?? '';
    final word = (private['word'] as String?) ?? '';
    final wolfTeam = ((private['wolf_team'] as List?) ?? const []).map((e) => '$e').join('、');
    final cards = ((private['cards'] as List?) ?? const []).map((e) => '$e').join(' ');
    final checks = (private['checks'] as Map<String, dynamic>?) ?? {};
    final isSpectator = my['is_spectator'] == true;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${l10n.gameYourRole}: $role',
                style: const TextStyle(fontWeight: FontWeight.w600)),
            if (word.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('${l10n.gameYourWord}: $word',
                  style: const TextStyle(fontSize: 13)),
            ],
            if (wolfTeam.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('🐺 狼队友：$wolfTeam 号',
                  style: const TextStyle(fontSize: 13)),
            ],
            if (cards.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('🃏 手牌：$cards',
                  style: const TextStyle(fontSize: 13)),
            ],
            if (checks.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('🔮 查验：${checks.entries.map((e) => '${e.key}号${e.value == true ? "狼" : "好人"}').join('；')}',
                  style: const TextStyle(fontSize: 13)),
            ],
            if (isSpectator)
              Text(l10n.gameSpectatorView, style: const TextStyle(fontSize: 12, color: Colors.grey)),
          ],
        ),
      ),
    );
  }

  Widget _eventsList(AppLocalizations l10n) {
    final events = p.events.reversed.toList(); // 最新在上
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      itemCount: events.length,
      itemBuilder: (context, i) {
        final e = events[i];
        final content = (e['content'] as String?) ?? '';
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${e['round'] ?? ''}',
                style: const TextStyle(fontSize: 11, color: Colors.grey),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(content, style: const TextStyle(fontSize: 14)),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _actionPanel(AppLocalizations l10n) {
    final expected = p.myExpectedAction ?? '';
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
      ),
      child: _buildActions(expected, l10n),
    );
  }

  Widget _buildActions(String expected, AppLocalizations l10n) {
    switch (expected) {
      case 'describe':
        return _textAction(l10n, l10n.gameDescribeHint, null, 'describe');
      case 'vote':
        final aliveOthers = p.players
            .where((pl) => pl['is_spectator'] != true && pl['seat'] != p.userSeat && pl['alive'] == true)
            .toList();
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(l10n.gameVoteFor, style: const TextStyle(fontSize: 13)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                for (final pl in aliveOthers)
                  ChoiceChip(
                    label: Text('${pl['name'] ?? ''}'),
                    selected: _selectedVoteSeat == '${pl['seat']}',
                    onSelected: (_) => setState(() => _selectedVoteSeat = '${pl['seat']}'),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: _selectedVoteSeat.isEmpty
                  ? null
                  : () => _send('vote', {'target_seat': int.parse(_selectedVoteSeat)}),
              child: Text(l10n.gameVote),
            ),
          ],
        );
      case 'choose':
        return Row(
          children: [
            Expanded(
              child: FilledButton(
                onPressed: () => _send('choose', {'choice': 'truth'}),
                child: Text(l10n.gameTruth),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton.tonal(
                onPressed: () => _send('choose', {'choice': 'dare'}),
                child: Text(l10n.gameDare),
              ),
            ),
          ],
        );
      case 'give_truth':
        return _textAction(l10n, l10n.gameSendMessage, l10n.gameTruth, 'give_truth');
      case 'give_dare':
        return _textAction(l10n, l10n.gameSendMessage, l10n.gameDare, 'give_dare');
      case 'answer_truth':
      case 'complete_dare':
        return _textAction(l10n, l10n.gameSendMessage, null, expected);
      case 'ask':
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _textAction(l10n, l10n.gameAskHint, l10n.gameAsk, 'ask'),
            const SizedBox(height: 4),
            TextButton(
              onPressed: () => _guess(l10n),
              child: Text(l10n.gameGuess),
            ),
          ],
        );
      case 'answer':
        return Wrap(
          spacing: 8,
          children: [
            for (final (ans, label) in [
              ('yes', l10n.gameAnswerYes),
              ('no', l10n.gameAnswerNo),
              ('possible', l10n.gameAnswerPossible),
              ('uncertain', l10n.gameAnswerUncertain),
            ])
              ActionChip(
                label: Text(label),
                onPressed: () => _send('answer', {'answer': ans}),
              ),
          ],
        );
      case 'guess':
        return _textAction(l10n, l10n.gameGuessWord, l10n.gameGuess, 'guess');
      case 'kill':
        return _targetAction(l10n, l10n.gameVoteFor, l10n.gameKill, 'kill', 'target_seat');
      case 'check':
        return _targetAction(l10n, l10n.gameVoteFor, l10n.gameCheck, 'check', 'target_seat');
      case 'speak':
        return _textAction(l10n, l10n.gameSpeakHint, l10n.gameSpeak, 'speak');
      case 'declare':
        return _numberAction(l10n, l10n.gameDeclareHint, l10n.gameDeclare);
      case 'follow_or_challenge':
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _numberAction(l10n, l10n.gameDeclareHint, l10n.gameFollow),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              icon: const Icon(Icons.help_outline),
              label: Text(l10n.gameChallenge),
              onPressed: () => _send('challenge', {}),
            ),
          ],
        );
      case 'challenge':
        return FilledButton.tonal(
          onPressed: () => _send('challenge', {}),
          child: Text(l10n.gameChallenge),
        );
      case 'ask_soup':
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _textAction(l10n, l10n.gameSoupAskHint, l10n.gameAsk, 'ask_soup'),
            const SizedBox(height: 4),
            TextButton(
              onPressed: () => _guessSoup(l10n),
              child: Text(l10n.gameSoupGuess),
            ),
          ],
        );
      case 'guess_soup':
        return _textAction(l10n, l10n.gameSoupGuessHint, l10n.gameSoupGuess, 'guess_soup');
      case 'answer_soup':
        return Wrap(
          spacing: 8,
          children: [
            for (final (ans, label) in [
              ('yes', l10n.gameAnswerYes),
              ('no', l10n.gameAnswerNo),
              ('possible', l10n.gameAnswerPossible),
              ('unrelated', l10n.gameAnswerUncertain),
              ('unknown', l10n.gameAnswerUncertain),
            ])
              ActionChip(
                label: Text(label),
                onPressed: () => _send('answer_soup', {'answer': ans}),
              ),
          ],
        );
      default:
        return Text(l10n.gameWaiting, style: const TextStyle(fontSize: 13));
    }
  }

  Widget _targetAction(
      AppLocalizations l10n, String title, String confirmLabel, String action, String payloadKey) {
    final aliveOthers = p.players
        .where((pl) => pl['is_spectator'] != true && pl['seat'] != p.userSeat && pl['alive'] == true)
        .toList();
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(title, style: const TextStyle(fontSize: 13)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          children: [
            for (final pl in aliveOthers)
              ChoiceChip(
                label: Text('${pl['name'] ?? ''}'),
                selected: _selectedVoteSeat == '${pl['seat']}',
                onSelected: (_) => setState(() => _selectedVoteSeat = '${pl['seat']}'),
              ),
          ],
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: _selectedVoteSeat.isEmpty
              ? null
              : () => _send(action, {payloadKey: int.parse(_selectedVoteSeat)}),
          child: Text(confirmLabel),
        ),
      ],
    );
  }

  Widget _numberAction(AppLocalizations l10n, String hint, String label) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        TextField(
          controller: _textCtrl,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
            hintText: hint,
            border: const OutlineInputBorder(),
            isDense: true,
          ),
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: () {
            final n = int.tryParse(_textCtrl.text.trim());
            if (n == null || n < 1 || n > 10) return;
            _send('declare', {'number': n});
          },
          child: Text(label),
        ),
      ],
    );
  }

  void _guessSoup(AppLocalizations l10n) {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    _send('guess_soup', {'word': text});
  }

  Widget _textAction(AppLocalizations l10n, String hint, String? label, String action) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        TextField(
          controller: _textCtrl,
          decoration: InputDecoration(
            hintText: hint,
            border: const OutlineInputBorder(),
            isDense: true,
          ),
        ),
        const SizedBox(height: 8),
        FilledButton(
          onPressed: () {
            final text = _textCtrl.text.trim();
            if (text.isEmpty) return;
            if (action == 'ask' || action == 'guess' || action == 'guess_soup') {
              _send(action, {
                'content': text,
                if (action == 'guess' || action == 'guess_soup') 'word': text,
              });
            } else {
              _send(action, {'content': text});
            }
          },
          child: Text(label ?? l10n.gameSend),
        ),
      ],
    );
  }

  void _guess(AppLocalizations l10n) {
    final text = _textCtrl.text.trim();
    if (text.isEmpty) return;
    _send('guess', {'word': text});
  }

  Future<void> _send(String action, Map<String, dynamic> payload) async {
    await p.sendAction(action: action, payload: payload);
    if (mounted) {
      _textCtrl.clear();
      setState(() => _selectedVoteSeat = '');
    }
  }

  Future<void> _abort() async {
    final l10n = AppLocalizations.of(context)!;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.gameAbortConfirm),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: Text(l10n.gameCancel)),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(l10n.gameAbort)),
        ],
      ),
    );
    if (ok == true) {
      await p.abort();
      if (mounted) Navigator.of(context).pop();
    }
  }
}
