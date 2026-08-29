import 'dart:async';
import 'package:vector_math/vector_math_64.dart' show Vector3;
import 'dart:math' as math;
import 'dart:ui' as ui;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:ai_companion/l10n/app_localizations.dart';
import 'package:dio/dio.dart' show DioException;
import 'package:flutter/services.dart' show rootBundle;
import 'package:provider/provider.dart';
import '../../providers/settings_provider.dart';
import '../../services/api_client.dart';
import '../../theme/aurora_tokens.dart';
import '../../widgets/floating_sheet.dart';
import '../../widgets/life_home_controls.dart';
import '../../widgets/life_home_world_map.dart';
import '../../widgets/shimmer.dart';
import '../character/pet_screen.dart';
import '../game/game_console_screen.dart';
import '../home/home_screen.dart';
import "package:ai_companion/theme/tokens.dart";
import "package:ai_companion/widgets/app_page_route.dart";

/// Aurora P4：全局「降低动效」读取（未包裹 Provider 兜底 false）。
bool _homeMaybeReduceMotion(BuildContext context) {
  try {
    return context.watch<SettingsProvider>().reduceMotion ||
        MediaQuery.disableAnimationsOf(context);
  } catch (_) {
    return MediaQuery.disableAnimationsOf(context);
  }
}

/// 小家 v3.2 家具自由摆放几何（纯函数，便于单测）：
/// 逻辑画布 640x480（16x12 格，每格 40px），家具坐标为浮点格坐标（可小数，不吸附格子）。
class HomeLayoutMath {
  static const double cell = 40;
  static const double canvasW = 640;
  static const double canvasH = 480;
  static const double gridCols = 16;
  static const double gridRows = 12;

  /// 家具命中检测（浮点）：logic 为画布内逻辑像素坐标；gx/gy/gw/gh 为格坐标
  static bool hitTestRect(Offset logic,
      {required double gx,
      required double gy,
      required double gw,
      required double gh}) {
    final rect = Rect.fromLTWH(gx * cell, gy * cell, gw * cell, gh * cell);
    return rect.contains(logic);
  }

  /// 逻辑像素坐标 → 格坐标
  static Offset logicToGrid(Offset logic) =>
      Offset(logic.dx / cell, logic.dy / cell);

  /// 拖动落位钳制：家具完整保持在画布内（左上角 0-16 / 0-12 格，自由小数）
  static Offset clampGrid(Offset g, double gw, double gh) => Offset(
        g.dx.clamp(0.0, gridCols - gw),
        g.dy.clamp(0.0, gridRows - gh),
      );

  // ── v3.3 家具朝向（rotation 字段，后端 0-7；0=前 1=后 2=左 3=右，斜向 4-7 预留）──
  static const int rotationMax = 7;

  /// 旋转循环切换：本轮先 4 方向（0-3），斜向素材就绪后改用 rotationMax 循环
  static int nextRotation(int rotation) => (rotation + 1) % 4;

  /// 后端透传钳制 0-7（防脏数据）
  static int clampRotation(int rotation) => rotation.clamp(0, rotationMax);
}

/// 生活可视·小家（v3.1.0+）：多房间像素家居 + 宠物素材 + 宠物互动
/// 触屏点地面移动、点家具/宠物弹居中弹窗交互；底部虚拟方向键 + 交互按钮（保留点屏移动）。
/// v3.2：长按家具 300ms 进入拖动态 → 浮点自由坐标（像素级）→ 松手落位自动保存。
class HomeVisualScreen extends StatefulWidget {
  const HomeVisualScreen({super.key});
  @override
  State<HomeVisualScreen> createState() => _HomeVisualScreenState();
}

class _Furniture {
  final String key;
  final String name;
  final double gx, gy, gw, gh;
  final int rotation;
  final String? action;
  const _Furniture(this.key, this.name, this.gx, this.gy, this.gw, this.gh,
      [this.rotation = 0, this.action]);
  factory _Furniture.fromMap(Map<String, dynamic> m) => _Furniture(
        m['key'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['gx'] as num?) ?? 0).toDouble(),
        ((m['gy'] as num?) ?? 0).toDouble(),
        ((m['gw'] as num?) ?? 1).toDouble(),
        ((m['gh'] as num?) ?? 1).toDouble(),
        HomeLayoutMath.clampRotation(((m['rotation'] as num?) ?? 0).toInt()),
        m['action'] as String?,
      );
}

class _Room {
  final String id;
  final String name;
  final List<_Furniture> furniture;
  const _Room(this.id, this.name, this.furniture);
  factory _Room.fromMap(Map<String, dynamic> m) => _Room(
        m['id'] as String? ?? '',
        m['name'] as String? ?? '',
        ((m['furniture'] as List?) ?? const [])
            .map((e) => _Furniture.fromMap(Map<String, dynamic>.from(e as Map)))
            .toList(),
      );
}

Map<String, String> _kActionLabels(AppLocalizations l10n) => {
  'sleep': l10n.actionSleep,
  'work': l10n.actionWork,
  'cook': l10n.actionCook,
  'eat': l10n.actionEat,
  'tv': l10n.actionTv,
  'read': l10n.actionRead,
  'shower': l10n.actionShower,
  'exercise': l10n.actionExercise,
  'music': l10n.actionMusic,
  'game': l10n.actionGame,
};

class _HomeVisualScreenState extends State<HomeVisualScreen>
    with SingleTickerProviderStateMixin {
  static const double _cw = 640;
  static const double _ch = 480;

  Map<String, dynamic>? _state;
  bool _loading = true;
  String? _error;
  int _characterId = 0;
  List<_Room> _rooms = const [];
  String _currentRoom = 'living';
  List<Map<String, dynamic>> _pets = const [];

  Offset _userPos = const Offset(240, 340);
  late final AnimationController _moveCtrl = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 500),
  );
  Animation<Offset>? _moveAnim;
  Timer? _dpadTimer;
  String? _dpadDir;

  String? _selected;
  String? _actionBubble;
  Timer? _bubbleTimer;
  final Map<String, String> _petState = {};
  final Map<String, ui.Image> _images = {};
  bool _imagesReady = false;

  // ── v3.2 家具自由摆放：拖动状态 ──
  String? _draggingKey;      // 正在拖动的家具 key
  Offset _dragGrab = Offset.zero;  // 抓取点相对家具左上角的偏移（逻辑像素）
  List<_Room> _serverRooms = const [];  // 服务器已知布局（保存失败回滚基准）
  DateTime _lastLayoutSaveAt = DateTime.fromMillisecondsSinceEpoch(0);
  Timer? _saveTimer;
  bool _dragHintVisible = false;

  // ── v3.3 家具编辑态（临时状态只在内存，点「完成」统一保存一次）──
  bool _editMode = false;
  String? _editingKey;                 // 编辑态中被编辑家具 key
  String? _editingRoomId;              // 被编辑家具所在房间（v1.2 世界地图跨房间定位）
  List<_Room> _editSessionStart = const [];  // 本次编辑会话开始快照（回退基准）

  // ── v1.2 世界地图：地图控制器 + 方向键每拍移动步长（世界 px）──
  final GlobalKey<LifeHomeWorldMapState> _worldKey =
      GlobalKey<LifeHomeWorldMapState>();
  static const double _worldDpadStep = 8.0;

  // ── v3.3 自由缩放 + 视角跟随（视图层变换，家具逻辑坐标 gx/gy 不变）──
  static const double _minViewScale = 0.6;
  static const double _maxViewScale = 2.5;
  static const Duration _followPause = Duration(seconds: 2);
  double _viewScale = 1.0;             // 初始适配屏幕（painter 自带 fit）
  Offset _viewOffset = Offset.zero;    // 视图平移（widget 坐标）
  Size _viewSize = Size.zero;          // 最近一次画布视图尺寸（build 时记录）
  DateTime _lastManualPanAt = DateTime.fromMillisecondsSinceEpoch(0);
  double _viewGestureStartScale = 1.0;
  Offset _viewGestureStartOffset = Offset.zero;
  Offset _viewGestureStartFocal = Offset.zero;

  // ── Aurora P4：家具点击波纹（点击屏幕位置 + 一次性扩散，reduceMotion 不绘制）──
  Offset? _ripplePos;
  Timer? _rippleTimer;

  static Future<ui.Image> _loadImage(String path) async {
    final data = await rootBundle.load(path);
    final codec = await ui.instantiateImageCodec(data.buffer.asUint8List());
    final frame = await codec.getNextFrame();
    return frame.image;
  }

  Future<void> _loadVisualAssets() async {
    final keys = <String>[
      'floor_living', 'floor_bedroom', 'floor_kitchen', 'floor_bathroom',
      'wall_living', 'wall_bedroom', 'wall_kitchen', 'wall_bathroom',
      'char_user', 'char_ai',
      'furn_bed', 'furn_sofa', 'furn_tv', 'furn_stove', 'furn_fridge',
      'furn_table', 'furn_desk', 'furn_bookshelf', 'furn_shower', 'furn_bathtub',
      'furn_wardrobe', 'furn_nightstand', 'furn_coffee', 'furn_chair',
      'furn_speaker', 'furn_game', 'furn_plant', 'furn_petbed', 'furn_sink',
      'furn_rug', 'furn_painting', 'furn_lamp', 'furn_shelf', 'furn_bin',
      'furn_clock',
    ];
    for (final k in keys) {
      try {
        _images[k] = await _loadImage('assets/life_visual/$k.png');
      } catch (_) {}
    }
    if (mounted) setState(() => _imagesReady = true);
  }

  @override
  void initState() {
    super.initState();
    _moveCtrl.addListener(() {
      final anim = _moveAnim;
      if (anim != null && mounted) {
        setState(() {
          _userPos = anim.value;
          _followUser();
        });
      }
    });
    _load();
    _loadVisualAssets();
    // 进入页面提示长按拖动（3 秒后自动消失；自定义布局来自后端，不做本地缓存）
    _dragHintVisible = true;
    Timer(const Duration(seconds: 3), () {
      if (mounted) setState(() => _dragHintVisible = false);
    });
  }

  @override
  void dispose() {
    _moveCtrl.dispose();
    _bubbleTimer?.cancel();
    _dpadTimer?.cancel();
    _saveTimer?.cancel();
    _rippleTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final r = await ApiClient().getLifeHomeState(characterId: _characterId);
      if (!mounted) return;
      setState(() {
        _state = r;
        _characterId = (r['character_id'] as int?) ?? 0;
        _rooms = ((r['rooms'] as List?) ?? const [])
            .map((e) => _Room.fromMap(Map<String, dynamic>.from(e as Map)))
            .toList();
        _serverRooms = _deepCopyRooms(_rooms);  // 自定义布局加载完成 → 回滚基准
        _pets = ((r['pets'] as List?) ?? const []).cast<Map<String, dynamic>>();
        _currentRoom = r['current_room'] as String? ?? 'living';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      final l10n = AppLocalizations.of(context)!;
      setState(() {
        _loading = false;
        // 2026-08-24：新机无角色时后端返回 404（no_character），显示友好空状态而非 DioException 原文
        if (e is DioException && e.response?.statusCode == 404) {
          _error = l10n.noCharacters;
        } else {
          _error = l10n.loadHomeFailed(e.toString());
        }
      });
    }
  }

  _Room get _room {
    for (final r in _rooms) {
      if (r.id == _currentRoom) return r;
    }
    return _rooms.isNotEmpty ? _rooms.first : const _Room('living', 'living', []);
  }

  // ── 触屏移动 ──
  /// widget 坐标 → 逻辑坐标：先按视图变换（缩放/平移）还原画布点，再做 fit 换算
  Offset _toLogic(Offset screen, Size size) {
    final p = (screen - _viewOffset) / _viewScale;
    final scale = math.min(size.width / _cw, size.height / _ch);
    final ox = (size.width - _cw * scale) / 2;
    final oy = (size.height - _ch * scale) / 2;
    return Offset((p.dx - ox) / scale, (p.dy - oy) / scale);
  }

  void _onTap(Offset screen, Size size) {
    final p = _toLogic(screen, size);
    for (final f in _room.furniture) {
      final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
      if (rect.contains(p)) {
        if (_editMode) {
          // 编辑态：点家具 → 进入「被编辑」（显示 回退/旋转/确定）
          setState(() {
            _editingKey = f.key;
            _selected = null;
          });
          return;
        }
        // Aurora P4：点击家具波纹（reduceMotion / 系统 disableAnimations 不绘制）
        if (!_homeMaybeReduceMotion(context)) {
          _rippleTimer?.cancel();
          setState(() => _ripplePos = screen);
          _rippleTimer = Timer(AppMotion.fast + const Duration(milliseconds: 60), () {
            if (mounted) setState(() => _ripplePos = null);
          });
        }
        setState(() => _selected = f.key);
        _showFurnitureDialog(f);
        return;
      }
    }
    if (_editMode) return;  // 编辑态点地面：不移动角色
    _moveTo(Offset(
      p.dx.clamp(20.0, _cw - 20),
      p.dy.clamp(20.0, _ch - 20),
    ));
  }

  void _moveTo(Offset target) {
    _lastManualPanAt = DateTime.fromMillisecondsSinceEpoch(0);  // 角色再移动 → 恢复视角跟随
    setState(() => _selected = null);
    _moveCtrl.stop();
    _moveAnim = Tween<Offset>(
      begin: _userPos,
      end: target,
    ).animate(CurvedAnimation(parent: _moveCtrl, curve: Curves.easeInOut));
    _moveCtrl
      ..duration = Duration(
        milliseconds: (200 + (_userPos - target).distance * 2).round(),
      )
      ..forward(from: 0);
  }

  // ── v3.2 家具自由摆放：长按 300ms 进入拖动态（短按仍弹交互弹窗）──
  _Furniture? _hitTestFurniture(Offset logic) {
    // 与绘制一致：按脚底 Y 从后往前（画面更靠前）优先命中
    final items = [..._room.furniture]
      ..sort((a, b) => ((b.gy + b.gh) * 40).compareTo((a.gy + a.gh) * 40));
    for (final f in items) {
      if (HomeLayoutMath.hitTestRect(logic,
          gx: f.gx, gy: f.gy, gw: f.gw, gh: f.gh)) {
        return f;
      }
    }
    return null;
  }

  void _onDragStart(Offset local, Size size) {
    final f = _hitTestFurniture(_toLogic(local, size));
    if (f == null) return;  // 长按地面：不进入拖动态
    setState(() {
      _draggingKey = f.key;
      _dragGrab = _toLogic(local, size) - Offset(f.gx * 40, f.gy * 40);
      _selected = null;
    });
  }

  void _onDragUpdate(Offset local, Size size) {
    final key = _draggingKey;
    if (key == null) return;
    var g = HomeLayoutMath.logicToGrid(_toLogic(local, size) - _dragGrab);
    for (final f in _room.furniture) {
      if (f.key == key) {
        g = HomeLayoutMath.clampGrid(g, f.gw, f.gh);
        break;
      }
    }
    setState(() => _updateFurniture(_currentRoom, key, gx: g.dx, gy: g.dy));
  }

  void _onDragEnd() {
    if (_draggingKey == null) return;
    setState(() => _draggingKey = null);
    if (_editMode) return;  // 编辑态：不实时保存，点「完成」统一保存一次
    _scheduleLayoutSave();
  }

  void _cancelDrag() {
    if (_draggingKey == null) return;
    setState(() => _draggingKey = null);
  }

  void _updateFurniture(String roomId, String key,
      {double? gx, double? gy, int? rotation}) {
    _rooms = [
      for (final r in _rooms)
        if (r.id == roomId)
          _Room(r.id, r.name, [
            for (final f in r.furniture)
              if (f.key == key)
                _Furniture(f.key, f.name, gx ?? f.gx, gy ?? f.gy, f.gw, f.gh,
                    rotation ?? f.rotation, f.action)
              else
                f,
          ])
        else
          r,
    ];
  }

  List<_Room> _deepCopyRooms(List<_Room> rooms) =>
      [for (final r in rooms) _Room(r.id, r.name, [...r.furniture])];

  /// 落位后保存整角色布局（节流：落位后 800ms 内不重复发请求；窗口内落位延后合并保存）
  void _scheduleLayoutSave() {
    _saveTimer?.cancel();
    final sinceLast = DateTime.now().difference(_lastLayoutSaveAt);
    if (sinceLast >= const Duration(milliseconds: 800)) {
      _saveLayout();
    } else {
      _saveTimer = Timer(const Duration(milliseconds: 800), _saveLayout);
    }
  }

  Future<void> _saveLayout() async {
    final l10n = AppLocalizations.of(context)!;
    _lastLayoutSaveAt = DateTime.now();
    final roomsPayload = [
      for (final r in _rooms)
        {
          'id': r.id,
          'name': r.name,
          'furniture': [
            for (final f in r.furniture)
              {
                'key': f.key,
                'name': f.name,
                'gx': f.gx,
                'gy': f.gy,
                'gw': f.gw,
                'gh': f.gh,
                'rotation': f.rotation,
                'action': f.action,
              },
          ],
        },
    ];
    try {
      await ApiClient().dio.put(
        '/api/v1/life-home/layout',
        data: {'character_id': _characterId, 'rooms': roomsPayload},
      );
      if (!mounted) return;
      setState(() {
        _serverRooms = _deepCopyRooms(_rooms);  // 保存成功 → 新回滚基准
        _actionBubble = l10n.homeLayoutSaved;
      });
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(seconds: 2), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _rooms = _deepCopyRooms(_serverRooms);  // 保存失败 → 回滚到服务器坐标
        _actionBubble = null;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(l10n.homeLayoutSaveFailed)),
      );
    }
  }

  // ── v3.3 家具编辑态 ──
  void _toggleEditMode() {
    if (_editMode) {
      // 再次点编辑按钮：退出编辑态（不保存，临时位置保留在内存）
      setState(() {
        _editMode = false;
        _editingKey = null;
        _editingRoomId = null;
      });
    } else {
      setState(() {
        _editMode = true;
        _editingKey = null;
        _editingRoomId = null;
        _editSessionStart = _deepCopyRooms(_rooms);  // 回退基准：本次编辑会话开始
      });
    }
  }

  /// 点「完成」：统一 PUT 保存一次（沿用节流/失败回滚逻辑），随后退出编辑态
  Future<void> _finishEdit() async {
    await _saveLayout();
    if (!mounted) return;
    setState(() {
      _editMode = false;
      _editingKey = null;
    });
  }

  /// 回退：该家具回到本次编辑会话开始时的位置/朝向，并退出被编辑
  void _revertEditing() {
    final key = _editingKey;
    if (key == null) return;
    final roomId = _editingRoomId ?? _currentRoom;  // v1.2 世界地图：按被编辑家具所在房间回退
    _Furniture? base;
    for (final r in _editSessionStart) {
      if (r.id != roomId) continue;
      for (final f in r.furniture) {
        if (f.key == key) base = f;
      }
    }
    setState(() {
      if (base != null) {
        _updateFurniture(roomId, key,
            gx: base.gx, gy: base.gy, rotation: base.rotation);
      }
      _editingKey = null;
      _editingRoomId = null;
    });
  }

  /// 旋转：循环切换方向（0=前 1=后 2=左 3=右；素材渲染后续换 8 向图），保持被编辑态
  void _rotateEditing() {
    final key = _editingKey;
    if (key == null) return;
    final roomId = _editingRoomId ?? _currentRoom;  // v1.2 世界地图：按被编辑家具所在房间旋转
    final f = _findFurniture(roomId, key);
    if (f == null) return;
    setState(() {
      _updateFurniture(roomId, key,
          rotation: HomeLayoutMath.nextRotation(f.rotation));
    });
  }

  /// 确定：保持当前临时位置/朝向，退出被编辑
  void _confirmEditing() {
    setState(() {
      _editingKey = null;
      _editingRoomId = null;
    });
  }

  // ── v3.3 自由缩放 + 视角跟随 ──
  void _onViewScaleStart(ScaleStartDetails d) {
    _viewGestureStartScale = _viewScale;
    _viewGestureStartOffset = _viewOffset;
    _viewGestureStartFocal = d.focalPoint;
  }

  void _onViewScaleUpdate(ScaleUpdateDetails d) {
    final newScale = (_viewGestureStartScale * d.scale)
        .clamp(_minViewScale, _maxViewScale);
    // 保持起始焦点下的画布点跟手（单指=平移，双指=捏合缩放）
    final world =
        (_viewGestureStartFocal - _viewGestureStartOffset) / _viewGestureStartScale;
    setState(() {
      _viewScale = newScale;
      _viewOffset = d.focalPoint - world * newScale;
    });
    _lastManualPanAt = DateTime.now();  // 手动拖动画布 → 暂停跟随
  }

  void _onViewScaleEnd() {
    _lastManualPanAt = DateTime.now();
  }

  /// 视角跟随：角色移动时画面平移保持角色可见（用户手动拖动画布后暂停一段时间）
  void _followUser() {
    if (_viewSize == Size.zero) return;
    if (DateTime.now().difference(_lastManualPanAt) < _followPause) return;
    final size = _viewSize;
    final f = math.min(size.width / _cw, size.height / _ch);
    final ox = (size.width - _cw * f) / 2;
    final oy = (size.height - _ch * f) / 2;
    // 角色在画布 fit 空间的坐标（视图变换前）
    final b = Offset(_userPos.dx * f + ox, _userPos.dy * f + oy);
    // 目标平移：角色位于视口中心（offset 不含当前 offset，直接由基点计算）
    var target = Offset(
      size.width / 2 - b.dx * _viewScale,
      size.height / 2 - b.dy * _viewScale,
    );
    // 钳制：画布仍覆盖视口中心（避免缩放下把画面拖出太远）
    target = Offset(
      target.dx.clamp(size.width / 2 - size.width * _viewScale, size.width / 2),
      target.dy.clamp(size.height / 2 - size.height * _viewScale, size.height / 2),
    );
    if ((target - _viewOffset).distance > 0.5) {
      _viewOffset = target;
    }
  }

  // ── 虚拟方向键 ──
  void _startDpad(String dir) {
    _dpadDir = dir;
    _lastManualPanAt = DateTime.fromMillisecondsSinceEpoch(0);  // 角色移动 → 恢复跟随
    _dpadTimer?.cancel();
    _dpadTimer = Timer.periodic(const Duration(milliseconds: 40), (_) {
      if (!mounted || _dpadDir == null) return;
      if (_state?['world'] != null) {
        // v1.2 世界地图：移动角色（世界 px），镜头由 LifeHomeWorldMap 保持跟随
        final st = _worldKey.currentState;
        if (st == null) return;
        switch (_dpadDir) {
          case 'up':
            st.moveBy(0, -_worldDpadStep);
            break;
          case 'down':
            st.moveBy(0, _worldDpadStep);
            break;
          case 'left':
            st.moveBy(-_worldDpadStep, 0);
            break;
          case 'right':
            st.moveBy(_worldDpadStep, 0);
            break;
        }
        return;
      }
      setState(() {
        switch (_dpadDir) {
          case 'up':
            _userPos = Offset(_userPos.dx, math.max(20, _userPos.dy - 7));
            break;
          case 'down':
            _userPos = Offset(_userPos.dx, math.min(_ch - 20, _userPos.dy + 7));
            break;
          case 'left':
            _userPos = Offset(math.max(20, _userPos.dx - 7), _userPos.dy);
            break;
          case 'right':
            _userPos = Offset(math.min(_cw - 20, _userPos.dx + 7), _userPos.dy);
            break;
        }
        _followUser();
      });
    });
  }

  void _stopDpad() {
    _dpadDir = null;
    _dpadTimer?.cancel();
  }

  // ── 交互按钮：找角色附近最近的可交互家具 ──
  void _interactNearby() {
    final l10n = AppLocalizations.of(context)!;
    if (_state?['world'] != null) {
      // v1.2 世界地图：由地图在角色周围找最近可交互家具并上抛
      final found = _worldKey.currentState?.interactNearby() ?? false;
      if (!found) {
        setState(() => _actionBubble = l10n.noNearbyFurniture);
        _bubbleTimer?.cancel();
        _bubbleTimer = Timer(const Duration(milliseconds: 1500), () {
          if (mounted) setState(() => _actionBubble = null);
        });
      }
      return;
    }
    _Furniture? nearest;
    var best = double.infinity;
    for (final f in _room.furniture) {
      if (f.action == null) continue;
      final cx = (f.gx + f.gw / 2) * 40;
      final cy = (f.gy + f.gh / 2) * 40;
      final d = (Offset(cx, cy) - _userPos).distance;
      if (d < 80 && d < best) {
        best = d;
        nearest = f;
      }
    }
    final n = nearest;
    if (n != null) {
      setState(() => _selected = n.key);
      _showFurnitureDialog(n);
    } else {
      setState(() => _actionBubble = l10n.noNearbyFurniture);
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(milliseconds: 1500), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    }
  }

  // ── v1.2 世界地图：家具点选 / 长按拖动（坐标已由地图换算为 房间id + 房间格坐标）──
  void _onWorldFurnitureTap(String roomId, String key) {
    if (_editMode) {
      // 编辑态：点家具 → 进入「被编辑」（显示 回退/旋转/确定）
      setState(() {
        _editingKey = key;
        _editingRoomId = roomId;
        _selected = null;
      });
      return;
    }
    final f = _findFurniture(roomId, key);
    if (f == null) return;
    setState(() => _selected = key);
    _showFurnitureDialog(f);
  }

  void _onWorldDragStart(String roomId, String key) {
    setState(() {
      _draggingKey = key;
      _selected = null;
    });
  }

  void _onWorldDragUpdate(String roomId, String key, double gx, double gy) {
    setState(() => _updateFurniture(roomId, key, gx: gx, gy: gy));
  }

  void _onWorldDragEnd(String roomId, String key, double gx, double gy) {
    setState(() => _draggingKey = null);
    if (_editMode) return;  // 编辑态：不实时保存，点「完成」统一保存一次
    _scheduleLayoutSave();
  }

  /// 全房间找家具（跨房间，供世界地图点选/旋转/回退定位）。
  _Furniture? _findFurniture(String roomId, String key) {
    for (final r in _rooms) {
      if (r.id != roomId) continue;
      for (final f in r.furniture) {
        if (f.key == key) return f;
      }
    }
    return null;
  }

  /// 把当前 _rooms 转为地图所需的 List<Map>（含实时家具坐标）。
  List<Map<String, dynamic>> _roomsToMaps() => [
        for (final r in _rooms)
          {
            'id': r.id,
            'name': r.name,
            'furniture': [
              for (final f in r.furniture)
                {
                  'key': f.key,
                  'name': f.name,
                  'gx': f.gx,
                  'gy': f.gy,
                  'gw': f.gw,
                  'gh': f.gh,
                  'rotation': f.rotation,
                  'action': f.action,
                },
            ],
          },
      ];

  // ── 家具交互面板（Aurora P4：AlertDialog → FloatingSheet）──
  Future<void> _showFurnitureDialog(_Furniture f) async {
    final l10n = AppLocalizations.of(context)!;
    await showFloatingSheet(
      context: context,
      expandable: false,
      maxHeightFraction: 0.55,
      title: f.name,
      child: Builder(
        builder: (sheetCtx) => Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (f.key == 'game')
              FilledButton(
                onPressed: () {
                  Navigator.pop(sheetCtx);
                  _openGameConsole();
                },
                child: Text(l10n.gameTitle),
              )
            else if (f.key == 'petbed')
              FilledButton.tonalIcon(
                onPressed: () {
                  Navigator.pop(sheetCtx);
                  _openPets();
                },
                icon: const Icon(Icons.pets),
                label: Text(l10n.petEntry),
              )
            else if (f.action != null)
              FilledButton(
                onPressed: () {
                  Navigator.pop(sheetCtx);
                  _runEvent(f.action!);
                },
                child: Text(_kActionLabels(l10n)[f.action] ?? l10n.interact),
              )
            else
              Text(
                l10n.furnitureInactive,
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 13, color: Colors.grey),
              ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () {
                Navigator.pop(sheetCtx);
                if (mounted) setState(() => _selected = null);
              },
              child: Text(l10n.cancel),
            ),
          ],
        ),
      ),
    );
    if (mounted) setState(() => _selected = null);
  }

  Future<void> _openPets() async {
    await Navigator.of(context).push(
      AppPageRoute(builder: (_) => const PetScreen()),
    );
    _load();
  }

  /// 群聊游戏 Phase 1：点击游戏机打开游戏面板（不再是简单体力加成）。
  Future<void> _openGameConsole() async {
    await Navigator.of(context).push(
      AppPageRoute(builder: (_) => const GameConsoleScreen()),
    );
  }

  Future<void> _runEvent(String action, {int? petId}) async {
    final l10n = AppLocalizations.of(context)!;
    try {
      final r = await ApiClient().postLifeHomeEvent(
        characterId: _characterId,
        action: action,
        petId: petId,
      );
      if (!mounted) return;
      setState(() {
        final p = r['player'] as Map<String, dynamic>?;
        final old = _state;
        if (p != null && old != null) {
          old['player'] = p;
        }
        final pet = r['pet'] as Map<String, dynamic>?;
        if (pet != null) {
          final pid = '${pet['id']}';
          for (var i = 0; i < _pets.length; i++) {
            if ('${_pets[i]['id']}' == pid) {
              _pets[i] = pet;
            }
          }
          _petState[pid] = action == 'pet_feed' ? 'eating' : 'playing';
          Timer(const Duration(seconds: 2), () {
            if (mounted) setState(() => _petState[pid] = 'idle');
          });
        }
        _actionBubble = petId != null
            ? '${_kActionLabels(l10n)[action] ?? l10n.interact}${l10n.actionDone}'
            : l10n.actionInProgress(_kActionLabels(l10n)[action] ?? l10n.interact);
      });
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(seconds: 2), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _actionBubble = l10n.opFailedErr(e.toString());
      });
      _bubbleTimer?.cancel();
      _bubbleTimer = Timer(const Duration(seconds: 2), () {
        if (mounted) setState(() => _actionBubble = null);
      });
    }
  }

  // ── v3.3 ① 标题：用户昵称的小家（有恋人 → 昵称与恋人名的小家）──
  String _appBarTitle(AppLocalizations l10n) {
    final user = _state?['user'] as Map<String, dynamic>?;
    final nickname = (user?['nickname'] as String?)?.trim() ?? '';
    final lover = (_state?['lover_name'] as String?)?.trim() ?? '';
    if (nickname.isNotEmpty) {
      if (lover.isNotEmpty && lover != nickname) {
        return l10n.homeTitleWithLover(nickname, lover);
      }
      return l10n.homeTitleMine(nickname);
    }
    // 旧后端未返回 user 信息时的兜底：角色名 → 通用标题
    return _state?['character_name'] as String? ?? l10n.homeTitle;
  }


  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Scaffold(
      appBar: AppBar(
        // Aurora P4 玻璃顶栏：半透明背景 + 0.5px 描边（不加 BackdropFilter）
        backgroundColor: isDark
            ? Colors.black.withValues(alpha: 0.30)
            : Colors.white.withValues(alpha: 0.55),
        elevation: 0,
        scrolledUnderElevation: 0,
        surfaceTintColor: Colors.transparent,
        shape: Border(
          bottom: BorderSide(
            color: isDark
                ? Colors.white.withValues(alpha: AppGlass.borderAlpha)
                : Colors.black.withValues(alpha: AppGlass.borderAlpha),
            width: 0.5,
          ),
        ),
        // #68 ④：AppBar 左侧改为侧抽屉按钮（与好友/朋友圈一致），编辑态退出走「完成/回退」
        leading: IconButton(
          tooltip: l10n.menu,
          icon: const Icon(Icons.menu),
          onPressed: AppDrawerController.toggle,
        ),
        title: Text(_appBarTitle(l10n)),
        actions: [
          if (_state != null)
            IconButton(
              tooltip: l10n.refresh,
              icon: const Icon(Icons.refresh),
              onPressed: _load,
            ),
        ],
      ),
      body: _loading
          ? const HomeVisualSkeleton()
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, style: const TextStyle(color: Colors.grey)),
                      const SizedBox(height: 12),
                      FilledButton.tonal(onPressed: _load, child: Text(l10n.retry)),
                    ],
                  ),
                )
              : Column(
                  children: [
                    _statusBar(),
                    // 小家大地图 v1.1（2026-08-26）：后端 world 载荷非空 → 世界画布视图；
                    // world==null（flag 关）保持旧独立房间视图（完全向后兼容）。
                    if (_state?['world'] == null) ...[
                      _roomTabs(),
                      Expanded(child: _roomView()),
                      _controlBar(),
                    ] else ...[
                      // issue #2/#3/#4：world 模式不再显示四房间 tab；
                      // 顶部换成一行工具栏（家具编辑 + 缩放），地图只占 Expanded 区域。
                      _worldToolbar(),
                      Expanded(
                        child: ClipRect(
                          clipBehavior: Clip.hardEdge,
                          child: Stack(
                            children: [
                              LifeHomeWorldMap(
                                key: _worldKey,
                                world: _state!['world'] as Map<String, dynamic>,
                                l10n: l10n,
                                roomNames: {
                                  for (final r in _rooms) r.id: r.name,
                                },
                                rooms: _roomsToMaps(),
                                images: _images,
                                editingRoom: _editingRoomId,
                                editingKey: _editingKey,
                                selected: _selected,
                                onFurnitureTap: _onWorldFurnitureTap,
                                onFurnitureDragStart: _onWorldDragStart,
                                onFurnitureDragUpdate: _onWorldDragUpdate,
                                onFurnitureDragEnd: _onWorldDragEnd,
                              ),
                              // v1.2 编辑态顶部提示条 + 被编辑家具操作栏（与旧 _roomView 一致）
                              if (_editMode)
                                Positioned(
                                  top: 0,
                                  left: 0,
                                  right: 0,
                                  child: LifeHomeEditHintBar(onDone: _finishEdit),
                                ),
                              if (_editMode && _editingKey != null)
                                Positioned(
                                  left: 0,
                                  right: 0,
                                  bottom: 0,
                                  child: LifeHomeEditActionBar(
                                    onRevert: _revertEditing,
                                    onRotate: _rotateEditing,
                                    onConfirm: _confirmEditing,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                      _controlBar(),
                    ],
                  ],
                ),
    );
  }

  Widget _statusBar() {
    final l10n = AppLocalizations.of(context)!;
    final p = _state?['player'] as Map<String, dynamic>?;
    final stamina = (p?['stamina'] as num?)?.toInt() ?? 70;
    final mood = (p?['mood'] as num?)?.toInt() ?? 50;
    final hunger = (p?['hunger'] as num?)?.toInt() ?? 70;
    final ai = _state?['ai'] as Map<String, dynamic>?;
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _bar(l10n.stamina, stamina, Colors.green),
              const SizedBox(width: 10),
              _bar(l10n.mood, mood, Colors.orange),
              const SizedBox(width: 10),
              _bar(l10n.petHunger, hunger, Colors.redAccent),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${ai?['name'] ?? ''}：${ai?['current_status'] ?? ''}',
            style: const TextStyle(fontSize: 12, color: Colors.grey),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  Widget _bar(String label, int value, Color color) {
    return Expanded(
      child: Row(
        children: [
          Text(label, style: const TextStyle(fontSize: 11)),
          const SizedBox(width: 4),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: (value / 100).clamp(0.0, 1.0),
                minHeight: 8,
                color: color,
                backgroundColor: color.withValues(alpha: 0.15),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _roomTabs() {
    if (_rooms.isEmpty) return const SizedBox.shrink();
    // v3.3 ②：四房间 Tab 居中一行 + 同行右端「家具编辑」按钮
    return LifeHomeRoomTabBar(
      rooms: [for (final r in _rooms) LifeHomeRoom(r.id, r.name)],
      currentRoomId: _currentRoom,
      editing: _editMode,
      onSelectRoom: _switchRoom,
      onEditTap: _toggleEditMode,
    );
  }

  /// issue #2/#3/#4：世界模式顶部工具栏（家具编辑 + 缩放），普通 Row，不遮上下栏。
  Widget _worldToolbar() {
    return LifeHomeWorldToolbar(
      editing: _editMode,
      onEditTap: _toggleEditMode,
      // 缩放：宿主经 GlobalKey 调地图公开方法（地图内悬浮按钮已移除）
      onZoomIn: () => _worldKey.currentState?.zoomIn(),
      onZoomOut: () => _worldKey.currentState?.zoomOut(),
      onResetView: () => _worldKey.currentState?.resetView(),
    );
  }

  void _switchRoom(String id) {
    setState(() {
      _currentRoom = id;
      _selected = null;
      _draggingKey = null;  // 拖动态中切房间：放弃当前拖动（不落位不保存）
      _editingKey = null;   // 编辑态切房间：退出被编辑（临时位置保留）
      _editingRoomId = null;
    });
  }

  Widget _roomView() {
    final l10n = AppLocalizations.of(context)!;
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(constraints.maxWidth, constraints.maxHeight);
        _viewSize = size;  // 记录视图尺寸（视角跟随用，build 期缓存）
        return Stack(
          children: [
            // 长按 300ms 进入家具拖动态（RawGestureDetector 自定义时长）；
            // 短按仍走 onTapUp：点家具弹交互弹窗（编辑态→进入被编辑）、点地面移动角色；
            // 单指拖动 / 双指捏合 → 视图缩放平移（v3.3 ⑤，手势在 Transform 之外拿 widget 坐标，
            // 命中/拖动坐标由 _toLogic 按同一视图变换换算）。
            RawGestureDetector(
              gestures: {
                LongPressGestureRecognizer:
                    GestureRecognizerFactoryWithHandlers<LongPressGestureRecognizer>(
                  () => LongPressGestureRecognizer(
                      duration: const Duration(milliseconds: 300)),
                  (instance) => instance
                    ..onLongPressStart = (d) {
                      _onDragStart(d.localPosition, size);
                    }
                    ..onLongPressMoveUpdate = (d) {
                      _onDragUpdate(d.localPosition, size);
                    }
                    ..onLongPressEnd = (_) {
                      _onDragEnd();
                    }
                    ..onLongPressCancel = _cancelDrag,
                ),
              },
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapUp: (d) => _onTap(d.localPosition, size),
                onScaleStart: _onViewScaleStart,
                onScaleUpdate: _onViewScaleUpdate,
                onScaleEnd: (_) => _onViewScaleEnd(),
                child: Transform(
                  transform: Matrix4.identity()
                    ..translateByVector3(Vector3(_viewOffset.dx, _viewOffset.dy, 0.0))
                    ..scaleByVector3(Vector3.all(_viewScale)),
                  child: CustomPaint(
                    size: Size.infinite,
                    painter: _RoomPainter(
                      roomId: _currentRoom,
                      furniture: _room.furniture,
                      userPos: _userPos,
                      aiStatus:
                          (_state?['ai'] as Map<String, dynamic>?)?['current_status']
                                  as String? ??
                              '',
                      selected: _selected,
                      dragging: _draggingKey,
                      editing: _editingKey,
                      bubble: _actionBubble,
                      images: _images,
                      imagesReady: _imagesReady,
                    ),
                  ),
                ),
              ),
            ),
            // Aurora P4：家具点击波纹（一次性扩散圆，IgnorePointer 不抢手势）
            if (_ripplePos != null)
              Positioned(
                left: _ripplePos!.dx - 44,
                top: _ripplePos!.dy - 44,
                width: 88,
                height: 88,
                child: const IgnorePointer(
                  key: Key('furnitureRipple'),
                  child: _FurnitureRipple(),
                ),
              ),
            // v3.3 ②③：编辑态顶部提示条 + 被编辑家具操作栏
            if (_editMode)
              Positioned(
                top: 0,
                left: 0,
                right: 0,
                child: LifeHomeEditHintBar(onDone: _finishEdit),
              ),
            if (_editMode && _editingKey != null)
              Positioned(
                left: 0,
                right: 0,
                bottom: 0,
                child: LifeHomeEditActionBar(
                  onRevert: _revertEditing,
                  onRotate: _rotateEditing,
                  onConfirm: _confirmEditing,
                ),
              ),
            // 2026-08-15 暂时隐藏宠物实体（挡住「宠物窝」入口；恢复时取消注释）
            // if (_currentRoom == 'living')
            //   for (var i = 0; i < _pets.length && i < 4; i++)
            if (_dragHintVisible && !_editMode)
              Positioned(
                left: 0,
                right: 0,
                bottom: 8,
                child: IgnorePointer(
                  child: Center(
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: Colors.black54,
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(
                        l10n.homeLayoutDragHint,
                        style: const TextStyle(
                            fontSize: 12, color: Colors.white),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }

  Widget _controlBar() {
    final l10n = AppLocalizations.of(context)!;
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      child: Row(
        children: [
          // 方向键（左/上/下/右）
          _dpad(),
          const Spacer(),
          // 交互按钮（模拟 E）
          Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              GestureDetector(
                onTapDown: (_) => _interactNearby(),
                child: Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.primary,
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.touch_app, color: Colors.white, size: 26),
                ),
              ),
              const SizedBox(height: 2),
              Text(l10n.interact, style: const TextStyle(fontSize: 10)),
            ],
          ),
        ],
      ),
    );
  }

  Widget _dpad() {
    Widget keyBtn(IconData icon, String dir) {
      return GestureDetector(
        onTapDown: (_) => _startDpad(dir),
        onTapUp: (_) => _stopDpad(),
        onTapCancel: _stopDpad,
        child: Container(
          width: 44,
          height: 44,
          margin: const EdgeInsets.all(2),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.9),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon, size: 22, color: Colors.grey.shade700),
        ),
      );
    }

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        keyBtn(Icons.chevron_left, 'left'),
        Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            keyBtn(Icons.keyboard_arrow_up, 'up'),
            keyBtn(Icons.keyboard_arrow_down, 'down'),
          ],
        ),
        keyBtn(Icons.chevron_right, 'right'),
      ],
    );
  }
}

// ── 像素房间绘制 ──
class _RoomPainter extends CustomPainter {
  final String roomId;
  final List<_Furniture> furniture;
  final Offset userPos;
  final String aiStatus;
  final String? selected;
  final String? dragging;
  final String? editing;
  final String? bubble;
  final Map<String, ui.Image> images;
  final bool imagesReady;

  _RoomPainter({
    required this.roomId,
    required this.furniture,
    required this.userPos,
    required this.aiStatus,
    required this.selected,
    required this.dragging,
    required this.editing,
    required this.bubble,
    required this.images,
    required this.imagesReady,
  });

  (Color, Color, Color) _theme() {
    switch (roomId) {
      case 'bedroom':
        return (const Color(0xFFE3D6C4), const Color(0xFFB8A080), const Color(0xFF7B6B55));
      case 'kitchen':
        return (const Color(0xFFE8E4DA), const Color(0xFFD0D0C0), const Color(0xFF888878));
      case 'bathroom':
        return (const Color(0xFFD6E8EC), const Color(0xFFA8C8D8), const Color(0xFF7898A8));
      default:
        return (const Color(0xFFE8DCC8), const Color(0xFFD9B98A), const Color(0xFF8B7355));
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final scale = math.min(size.width / 640.0, size.height / 480.0);
    canvas.save();
    canvas.translate(
      (size.width - 640 * scale) / 2,
      (size.height - 480 * scale) / 2,
    );
    canvas.scale(scale);

    final (wall, floor, wallDark) = _theme();
    // 地板平铺（40px 网格，480-60=420 高 -> 10.5 格，取 11 行覆盖）
    final floorImg = images['floor_$roomId'];
    final wallImg = images['wall_$roomId'];
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 11; j++) {
        if (floorImg != null) {
          canvas.drawImage(floorImg, Offset(i * 40.0, 60 + j * 40.0), Paint());
        }
      }
    }
    for (var i = 0; i < 16; i++) {
      for (var j = 0; j < 2; j++) {
        if (wallImg != null) {
          canvas.drawImage(wallImg, Offset(i * 40.0, j * 40.0), Paint());
        }
      }
    }
    // 兜底：素材未加载时用纯色 + 网格
    if (wallImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 0, 640, 60), Paint()..color = wall);
    }
    if (floorImg == null) {
      canvas.drawRect(const Rect.fromLTWH(0, 60, 640, 420), Paint()..color = floor);
      final grid = Paint()
        ..color = wallDark.withValues(alpha: 0.35)
        ..strokeWidth = 1;
      for (var i = 1; i < 16; i++) {
        canvas.drawLine(Offset(i * 40.0, 60), Offset(i * 40.0, 480), grid);
      }
      for (var j = 1; j < 11; j++) {
        canvas.drawLine(Offset(0, 60 + j * 40.0), Offset(640, 60 + j * 40.0), grid);
      }
    }
    // 窗户（叠在墙上）
    canvas.drawRect(
      const Rect.fromLTWH(430, 10, 120, 45),
      Paint()..color = const Color(0xFFBFE3FF),
    );
    canvas.drawLine(const Offset(490, 10), const Offset(490, 55),
        Paint()..color = AppColors.white..strokeWidth = 3);
    canvas.drawLine(const Offset(430, 32), const Offset(550, 32),
        Paint()..color = AppColors.white..strokeWidth = 3);

    // Aurora P4：柔和光影——光从窗户照入的径向渐变光斑（中心≈窗户中心，软边暖白，
    // 纯静态绘制，在家具/角色之前、不参与命中）
    canvas.drawRect(
      const Rect.fromLTWH(0, 0, 640, 480),
      Paint()
        ..shader = RadialGradient(
          colors: [
            const Color(0xFFFFF6DE).withValues(alpha: 0.12),
            const Color(0xFFFFF6DE).withValues(alpha: 0.0),
          ],
        ).createShader(Rect.fromCircle(center: const Offset(490, 32), radius: 280)),
    );

    // 按前后（脚底 Y）排序绘制：靠后的先画，实现前后遮挡
    final draws = <(double, void Function())>[];
    for (final f in furniture) {
      final bottomY = (f.gy + f.gh) * 40;
      draws.add((bottomY, () => _paintFurniture(canvas, f)));
    }
    draws.add((320, () => _paintCharacter(canvas, const Offset(220, 320), 'char_ai')));
    draws.add((userPos.dy, () => _paintCharacter(canvas, userPos, 'char_user')));
    draws.sort((a, b) => a.$1.compareTo(b.$1));
    for (final d in draws) {
      d.$2();
    }

    if (bubble != null && bubble!.isNotEmpty) {
      _paintBubble(canvas, userPos + const Offset(0, -46), bubble!);
    }

    canvas.restore();
  }

  void _paintFurniture(Canvas canvas, _Furniture f) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    final isDragging = dragging == f.key;
    if (isDragging) {
      // 拖动中：青色描边 + 半透明遮罩（视觉反馈）
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5
          ..color = const Color(0xFF00BCD4),
      );
    }
    if (selected == f.key) {
      canvas.drawRect(
        rect.inflate(3),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3
          ..color = const Color(0xFFFFC107),
      );
    }
    if (editing == f.key) {
      // 被编辑：橙色描边（与拖动态青色、选中弹窗琥珀色区分）
      canvas.drawRect(
        rect.inflate(4),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 4
          ..color = const Color(0xFFFF6D00),
      );
    }
    final wood = Paint()..color = const Color(0xFF9B7E5E);
    final img = images['furn_${f.key}'];
    if (img != null) {
      // 素材脚底对齐到格子底部（高度包含向上突出的部分）
      canvas.drawImage(
        img,
        Offset(f.gx * 40, (f.gy + f.gh) * 40 - img.height.toDouble()),
        Paint(),
      );
    } else {
      _paintFurnitureFallback(canvas, f, wood);
    }
    if (isDragging) {
      canvas.drawRect(rect, Paint()..color = const Color(0x59FFFFFF));
    }
    final tp = TextPainter(
      text: TextSpan(
        text: f.name,
        style: const TextStyle(fontSize: 11, color: Color(0xFF5A4632)),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, Offset(f.gx * 40, f.gy * 40 - 14));
  }

  void _paintFurnitureFallback(Canvas canvas, _Furniture f, Paint wood) {
    final rect = Rect.fromLTWH(f.gx * 40, f.gy * 40, f.gw * 40, f.gh * 40);
    switch (f.key) {
      case 'bed':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(6)),
          Paint()..color = const Color(0xFF7FA8C9),
        );
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 6, 30, 22),
          Paint()..color = AppColors.white,
        );
        break;
      case 'sofa':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect, const Radius.circular(8)),
          Paint()..color = const Color(0xFF8B6FA8),
        );
        break;
      case 'tv':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF222831));
        canvas.drawRect(rect.deflate(8), Paint()..color = const Color(0xFF9BE8FF));
        break;
      case 'stove':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 2; i++) {
          canvas.drawCircle(
            Offset(f.gx * 40 + 26 + i * 30, f.gy * 40 + 20),
            11,
            Paint()..color = AppColors.textStrong,
          );
        }
        break;
      case 'fridge':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF9AA5B1));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 36),
          Paint()..color = const Color(0xFF6B7684)..strokeWidth = 2,
        );
        break;
      case 'table':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'desk':
        canvas.drawRect(rect, wood);
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 10, f.gy * 40 + 8, 20, 14),
          Paint()..color = const Color(0xFFE8E2D8),
        );
        break;
      case 'bookshelf':
        canvas.drawRect(rect, wood);
        for (var i = 0; i < 3; i++) {
          canvas.drawLine(
            Offset(f.gx * 40 + 2, f.gy * 40 + 14 + i * 14),
            Offset(f.gx * 40 + 38, f.gy * 40 + 14 + i * 14),
            Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
          );
        }
        break;
      case 'shower':
        canvas.drawRect(rect, Paint()..color = const Color(0xFF9DD8F0));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 12),
          8,
          Paint()..color = AppColors.accentBlue,
        );
        break;
      case 'petbed':
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 6, f.gy * 40 + 14, 28, 18),
          Paint()..color = const Color(0xFFE8A0A0),
        );
        break;
      case 'wardrobe':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFFA8825F));
        canvas.drawLine(
          Offset(f.gx * 40 + 20, f.gy * 40 + 4),
          Offset(f.gx * 40 + 20, f.gy * 40 + 72),
          Paint()..color = const Color(0xFF6B4F33)..strokeWidth = 2,
        );
        break;
      case 'nightstand':
      case 'coffee':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFB98A5A));
        break;
      case 'chair':
        canvas.drawRect(rect.deflate(6), Paint()..color = const Color(0xFFA8825F));
        break;
      case 'speaker':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFF4A4E69));
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 18),
          7,
          Paint()..color = const Color(0xFF22223B),
        );
        break;
      case 'game':
        canvas.drawRect(rect.deflate(3), Paint()..color = const Color(0xFF2A2A3A));
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 8, f.gy * 40 + 10, 24, 16),
          Paint()..color = const Color(0xFF7BE0FF),
        );
        break;
      case 'bathtub':
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(3), const Radius.circular(10)),
          Paint()..color = AppColors.white,
        );
        canvas.drawRRect(
          RRect.fromRectAndRadius(rect.deflate(10), const Radius.circular(6)),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'sink':
        canvas.drawRect(rect.deflate(4), Paint()..color = const Color(0xFFE8E2D8));
        canvas.drawOval(
          Rect.fromLTWH(f.gx * 40 + 12, f.gy * 40 + 10, 16, 10),
          Paint()..color = const Color(0xFF9DD8F0),
        );
        break;
      case 'plant':
        canvas.drawRect(
          Rect.fromLTWH(f.gx * 40 + 14, f.gy * 40 + 24, 12, 10),
          Paint()..color = const Color(0xFFB07A3E),
        );
        canvas.drawCircle(
          Offset(f.gx * 40 + 20, f.gy * 40 + 14),
          8,
          Paint()..color = const Color(0xFF3E9B4F),
        );
        break;
      default:
        canvas.drawRect(rect, wood);
    }
  }

  void _paintCharacter(Canvas canvas, Offset p, String imgKey) {
    final img = images[imgKey];
    if (img != null) {
      // 精灵底部对齐到脚点 p
      canvas.drawImage(
        img,
        Offset(p.dx - img.width / 2, p.dy - img.height.toDouble()),
        Paint(),
      );
      return;
    }
    final color = imgKey == 'char_ai'
        ? const Color(0xFFE05A5A)
        : AppColors.accentBlue;
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy - 26, 16, 14),
        Paint()..color = const Color(0xFFF2C48D));
    canvas.drawRect(Rect.fromLTWH(p.dx - 10, p.dy - 12, 20, 16), Paint()..color = color);
    canvas.drawRect(Rect.fromLTWH(p.dx - 8, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
    canvas.drawRect(Rect.fromLTWH(p.dx + 1, p.dy + 4, 7, 10),
        Paint()..color = const Color(0xFF3A4A5A));
  }

  void _paintBubble(Canvas canvas, Offset p, String text) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(fontSize: 12, color: AppColors.textStrong),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final rect = Rect.fromLTWH(
      p.dx - tp.width / 2 - 8,
      p.dy - 24,
      tp.width + 16,
      tp.height + 10,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(8)),
      Paint()..color = AppColors.white,
    );
    tp.paint(canvas, Offset(p.dx - tp.width / 2, p.dy - 20));
  }

  @override
  bool shouldRepaint(covariant _RoomPainter old) =>
      old.roomId != roomId ||
      old.userPos != userPos ||
      old.selected != selected ||
      old.dragging != dragging ||
      old.editing != editing ||
      old.bubble != bubble ||
      old.furniture != furniture ||
      old.imagesReady != imagesReady ||
      old.images != images;
}


/// Aurora P4：家具点击波纹——一次扩散圆（scale 1→2.2 + opacity 0.45→0，
/// AppMotion.fast + emphasized）。仅在家具命中且未开启 reduceMotion 时由宿主挂载。
class _FurnitureRipple extends StatelessWidget {
  const _FurnitureRipple();

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.primary;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0.0, end: 1.0),
      duration: AppMotion.fast,
      curve: AppMotion.emphasized,
      builder: (context, t, _) {
        return Transform.scale(
          scale: 1.0 + 1.2 * t,
          child: Container(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(
                color: color.withValues(alpha: 0.45 * (1 - t)),
                width: 2,
              ),
            ),
          ),
        );
      },
    );
  }
}
