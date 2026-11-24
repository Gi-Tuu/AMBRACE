import 'package:ambrace_core/ambrace_core.dart';
import 'package:flutter/material.dart';

import '../../l10n/reader_strings.dart';

/// 小家像素房间（对齐 App HomeVisualScreen）：四房间切换、家具按格摆放（拖拽保存）、
/// 角色立绘、生活动作与宠物互动。素材复用 App 的 life_visual 资产。
class LifeHomeVisualPage extends StatefulWidget {
  const LifeHomeVisualPage({
    super.key,
    required this.client,
    required this.character,
    required this.rs,
    required this.cs,
  });

  final AmbApiClient client;
  final Character character;
  final ReaderStrings rs;
  final CoreStrings cs;

  @override
  State<LifeHomeVisualPage> createState() => _LifeHomeVisualPageState();
}

class _Furniture {
  _Furniture({
    required this.key,
    required this.name,
    required this.gx,
    required this.gy,
    required this.gw,
    required this.gh,
    required this.rotation,
    this.action,
  });

  final String key;
  final String name;
  double gx, gy;
  final double gw, gh;
  int rotation;
  final String? action;

  factory _Furniture.fromMap(Map<String, dynamic> m) => _Furniture(
        key: m['key'] as String? ?? '',
        name: m['name'] as String? ?? '',
        gx: (m['gx'] as num? ?? 0).toDouble(),
        gy: (m['gy'] as num? ?? 0).toDouble(),
        gw: (m['gw'] as num? ?? 1).toDouble(),
        gh: (m['gh'] as num? ?? 1).toDouble(),
        rotation: ((m['rotation'] as num? ?? 0).toInt()).clamp(0, 7),
        action: m['action'] as String?,
      );

  Map<String, dynamic> toPayload() => {
        'key': key,
        'gx': gx,
        'gy': gy,
        'gw': gw,
        'gh': gh,
        'rotation': rotation,
      };
}

class _Room {
  _Room({required this.id, required this.name, required this.furniture});

  final String id;
  final String name;
  final List<_Furniture> furniture;

  factory _Room.fromMap(Map<String, dynamic> m) => _Room(
        id: m['id'] as String? ?? '',
        name: m['name'] as String? ?? '',
        furniture: [
          for (final f in (m['furniture'] as List? ?? const []))
            _Furniture.fromMap(Map<String, dynamic>.from(f as Map)),
        ],
      );

  Map<String, dynamic> toPayload() => {
        'id': id,
        'furniture': [for (final f in furniture) f.toPayload()],
      };
}

class _LifeHomeVisualPageState extends State<LifeHomeVisualPage> {
  static const double _cols = 16;
  static const double _rows = 12;

  late final ReaderApi _api = ReaderApi(widget.client);

  bool _loading = true;
  bool _error = false;
  bool _busy = false;
  List<_Room> _rooms = const [];
  List<Map<String, dynamic>> _pets = const [];
  Map<String, dynamic>? _player;
  int _roomIndex = 0;
  int? _dragIndex;
  Offset _dragStart = Offset.zero;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = false;
    });
    try {
      final data = await _api.getLifeHomeState(widget.character.id);
      final rooms = [
        for (final r in (data['rooms'] as List? ?? const []))
          _Room.fromMap(Map<String, dynamic>.from(r as Map)),
      ];
      final current = data['current_room'] as String? ?? '';
      final index = rooms.indexWhere((r) => r.id == current);
      if (!mounted) return;
      setState(() {
        _rooms = rooms;
        _roomIndex = index >= 0 ? index : 0;
        _pets = [
          for (final p in (data['pets'] as List? ?? const []))
            Map<String, dynamic>.from(p as Map),
        ];
        _player = (data['player'] as Map?)?.cast<String, dynamic>();
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = true;
      });
    }
  }

  Future<void> _saveLayout() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await _api.saveLifeHomeLayout(
          widget.character.id, [for (final r in _rooms) r.toPayload()]);
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('layoutSaved'))));
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('layoutSaveFailed'))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _doAction(String action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final data =
          await _api.lifeHomeEvent(widget.character.id, action);
      if (!mounted) return;
      if (data['player'] is Map) {
        setState(() => _player =
            (data['player'] as Map).cast<String, dynamic>());
      }
      if (data['open_panel'] == 'game_console') {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(widget.rs.t('gameInAppOnly'))));
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('actionFailed'))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _petAction(String action, int petId) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final data =
          await _api.lifeHomeEvent(widget.character.id, action, petId: petId);
      final pet = data['pet'];
      if (!mounted || pet is! Map) return;
      setState(() {
        _pets = [
          for (final p in _pets)
            if (p['id'] == pet['id']) {...p, ...pet.cast<String, dynamic>()} else p,
        ];
      });
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(widget.rs.t('actionFailed'))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _asset(String key, int rotation) =>
      'assets/life_visual/furn_${key}_${rotation.clamp(0, 7)}.png';

  void _onDragStart(int index, Offset local) {
    setState(() {
      _dragIndex = index;
      _dragStart = local;
    });
  }

  void _onDragUpdate(Offset local, double cell) {
    final index = _dragIndex;
    if (index == null) return;
    final f = _rooms[_roomIndex].furniture[index];
    final dx = (local.dx - _dragStart.dx) / cell;
    final dy = (local.dy - _dragStart.dy) / cell;
    setState(() {
      f.gx = (f.gx + dx).clamp(0.0, _cols - f.gw);
      f.gy = (f.gy + dy).clamp(0.0, _rows - f.gh);
      _dragStart = local;
    });
  }

  void _showFurnitureSheet(int index) {
    final f = _rooms[_roomIndex].furniture[index];
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            leading: const Icon(Icons.chair_outlined),
            title: Text(f.name.isEmpty ? f.key : f.name),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Row(children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () {
                    setState(() => f.rotation = (f.rotation + 1) % 8);
                    Navigator.pop(ctx);
                    _saveLayout();
                  },
                  icon: const Icon(Icons.rotate_90_degrees_ccw, size: 18),
                  label: Text(
                      '${widget.rs.t('rotate')} ${f.rotation} → ${(f.rotation + 1) % 8}'),
                ),
              ),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _roomCanvas(double cell) {
    final room = _rooms[_roomIndex];
    return SizedBox(
      width: cell * _cols,
      height: cell * _rows,
      child: ClipRect(
        child: Stack(children: [
          Positioned.fill(
            child: Image.asset('assets/life_visual/floor_${room.id}.png',
                fit: BoxFit.fill,
                errorBuilder: (_, __, ___) => Container(
                      color: Theme.of(context).colorScheme.surfaceContainerHighest,
                    )),
          ),
          Positioned(
            left: 0,
            top: 0,
            width: cell * _cols,
            height: cell * 2.2,
            child: Opacity(
              opacity: 0.9,
              child: Image.asset('assets/life_visual/wall_${room.id}.png',
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => const SizedBox.shrink()),
            ),
          ),
          // 角色立绘（站位固定，移动操作仍回 App）
          Positioned(
            left: cell * 7.2,
            top: cell * 5.6,
            width: cell * 1.3,
            height: cell * 1.9,
            child: Image.asset('assets/life_visual/char_ai.png',
                fit: BoxFit.contain,
                errorBuilder: (_, __, ___) => const SizedBox.shrink()),
          ),
          for (var i = 0; i < room.furniture.length; i++)
            _furnitureAt(room.furniture[i], i, cell),
        ]),
      ),
    );
  }

  Widget _furnitureAt(_Furniture f, int index, double cell) {
    return Positioned(
      left: f.gx * cell,
      top: f.gy * cell,
      width: f.gw * cell,
      height: f.gh * cell,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => _showFurnitureSheet(index),
        onLongPressStart: (d) => _onDragStart(index, d.localPosition),
        onLongPressMoveUpdate: (d) => _onDragUpdate(d.localPosition, cell),
        onLongPressEnd: (_) {
          final dragged = _dragIndex;
          setState(() => _dragIndex = null);
          if (dragged != null) _saveLayout();
        },
        child: DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
            border: Border.all(
                color: _dragIndex == index
                    ? Theme.of(context).colorScheme.primary
                    : Colors.transparent),
          ),
          child: Image.asset(_asset(f.key, f.rotation),
              fit: BoxFit.contain,
              alignment: Alignment.bottomCenter,
              errorBuilder: (_, __, ___) => Image.asset(
                    'assets/life_visual/furn_${f.key}.png',
                    fit: BoxFit.contain,
                    alignment: Alignment.bottomCenter,
                    errorBuilder: (_, __, ___) => Container(
                      alignment: Alignment.center,
                      color: Theme.of(context)
                          .colorScheme
                          .surfaceContainerHighest
                          .withValues(alpha: 0.8),
                      child: Text(f.name,
                          style: Theme.of(context).textTheme.labelSmall),
                    ),
                  )),
        ),
      ),
    );
  }

  Widget _playerBar() {
    final p = _player;
    if (p == null) return const SizedBox.shrink();
    final rs = widget.rs;
    Widget chip(String label, String key) {
      final v = (p[key] as num? ?? 0).toInt();
      return Chip(
        label: Text('$label $v'),
        visualDensity: VisualDensity.compact,
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 0),
      child: Wrap(spacing: 6, children: [
        chip(rs.t('homeStamina'), 'stamina'),
        chip(rs.t('petMood'), 'mood'),
        chip(rs.t('petHunger'), 'hunger'),
        if (_busy)
          const Padding(
            padding: EdgeInsets.all(8),
            child: SizedBox(
                width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
          ),
      ]),
    );
  }

  Widget _actionsBar() {
    final rs = widget.rs;
    final actions = <(String, String)>[
      ('sleep', rs.t('homeSleep')),
      ('work', rs.t('homeWork')),
      ('cook', rs.t('homeCook')),
      ('eat', rs.t('homeEat')),
      ('tv', rs.t('homeTv')),
      ('read', rs.t('homeRead')),
      ('shower', rs.t('homeShower')),
      ('exercise', rs.t('homeExercise')),
      ('music', rs.t('homeMusic')),
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          for (final (key, label) in actions)
            ActionChip(
              label: Text(label),
              onPressed: _busy ? null : () => _doAction(key),
            ),
        ],
      ),
    );
  }

  Widget _petsBar() {
    if (_pets.isEmpty) return const SizedBox.shrink();
    final rs = widget.rs;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
          child: Text(rs.t('pets'), style: Theme.of(context).textTheme.titleSmall),
        ),
        for (final p in _pets)
          Card(
            margin: const EdgeInsets.fromLTRB(12, 0, 12, 6),
            elevation: 0,
            child: ListTile(
              dense: true,
              title: Text('${p['name'] ?? ''}'),
              subtitle: Text(
                  '${rs.t('petHunger')} ${p['hunger'] ?? 0} · '
                  '${rs.t('petMood')} ${p['mood'] ?? 0} · '
                  '${rs.t('petEnergy')} ${p['energy'] ?? 0}'),
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => _petAction('pet_feed', (p['id'] as num).toInt()),
                  child: Text(rs.t('petFeed')),
                ),
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => _petAction('pet_pet', (p['id'] as num).toInt()),
                  child: Text(rs.t('homePet')),
                ),
              ]),
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.rs.t('lifeHomeTitle')} · ${widget.character.name}'),
        actions: [
          IconButton(
            tooltip: widget.rs.t('resetView'),
            icon: const Icon(Icons.refresh),
            onPressed: _load,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(widget.rs.t('loadFailed')),
                    const SizedBox(height: 8),
                    TextButton(onPressed: _load, child: Text(widget.rs.t('retry'))),
                  ]),
                )
              : ListView(children: [
                  _playerBar(),
                  if (_rooms.length > 1)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                      child: Wrap(
                        spacing: 6,
                        children: [
                          for (var i = 0; i < _rooms.length; i++)
                            ChoiceChip(
                              label: Text(_rooms[i].name),
                              selected: i == _roomIndex,
                              onSelected: (_) => setState(() => _roomIndex = i),
                            ),
                        ],
                      ),
                    ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
                    child: AspectRatio(
                      aspectRatio: _cols / _rows,
                      child: LayoutBuilder(
                        builder: (ctx, c) =>
                            Center(child: _roomCanvas(c.maxWidth / _cols)),
                      ),
                    ),
                  ),
                  _actionsBar(),
                  _petsBar(),
                  const SizedBox(height: 16),
                ]),
    );
  }
}
