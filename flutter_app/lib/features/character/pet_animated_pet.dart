// F7-c-1（2026-08-31）自 features/character/pet_screen.dart 拆分迁入；逻辑逐字节保持。
import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:vector_math/vector_math_64.dart' show Vector3;
import '../../theme/tokens.dart';
import '../../models/pet.dart';
import '../../services/api_client.dart';
import 'pet_fx.dart' show petMaybeReduceMotion;

class AnimatedPet extends StatefulWidget {
  final Pet pet;
  final String? action;
  final int actionSeq;
  const AnimatedPet({super.key, required this.pet, this.action, required this.actionSeq});

  @override
  State<AnimatedPet> createState() => AnimatedPetState();
}

class AnimatedPetState extends State<AnimatedPet> with TickerProviderStateMixin {
  late final AnimationController _idle;
  late final AnimationController _burst;
  late int _lastSeq;
  _PetPose _pose = _PetPose.idle;
  Timer? _poseTimer;

  static const int _actionMs = 3000;    // 行为帧停留 3 秒后按精力高低切回睡觉/待机

  // Aurora P4：reduceMotion / 系统 disableAnimations 时待机循环停止为静态帧
  //（_idle 置 0 → 呼吸/浮动/眨眼/AttentionHalo 全部定格；点击 burst 弹跳保留为必要反馈，
  //  粒子在 reduceMotion 时省略）
  bool _reduceMotion = false;

  @override
  void initState() {
    super.initState();
    _idle = AnimationController(vsync: this, duration: const Duration(seconds: 4))..repeat();
    _burst = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400), value: 1);
    _lastSeq = widget.actionSeq;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final rm = MediaQuery.disableAnimationsOf(context) || petMaybeReduceMotion(context);
    if (rm != _reduceMotion) {
      setState(() => _reduceMotion = rm);
      if (rm) {
        _idle.stop();
        _idle.value = 0;
      } else if (!_idle.isAnimating) {
        _idle.repeat();
      }
    }
  }

  @override
  void didUpdateWidget(covariant AnimatedPet oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.action != null && widget.actionSeq != _lastSeq) {
      _lastSeq = widget.actionSeq;
      _burst.forward(from: 0);
      // 触发对应行为切换状态帧：喂食->进食 / 玩耍->玩耍 / 清洁->行走 / 其他->待机
      _poseTimer?.cancel();
      final next = switch (widget.action) {
        'feed' => _PetPose.eating,
        'play' => _PetPose.playing,
        'clean' => _PetPose.walking,
        _ => _PetPose.idle,
      };
      setState(() => _pose = next);
      if (next != _PetPose.idle) {
        _poseTimer = Timer(const Duration(milliseconds: _actionMs), () {
          if (mounted) setState(() => _pose = _PetPose.idle);
        });
      }
    }
  }

  @override
  void dispose() {
    _poseTimer?.cancel();
    _idle.dispose();
    _burst.dispose();
    super.dispose();
  }

  /// 宠物形象素材相对路径（按物种派生，已养宠物同样生效）
  String _asset(String name) =>
      ApiClient().resolveUrl('/uploads/pets_assets/${widget.pet.species}/$name');

  /// 当前应显示的状态帧：行为帧优先；无行为且精力低 -> 睡觉
  _PetPose _effectivePose() {
    if (_pose != _PetPose.idle) return _pose;
    if (_PetMotionState.from(widget.pet).sleepy) return _PetPose.sleeping;
    return _PetPose.idle;
  }

  Widget _poseImage(_PetPose pose) {
    switch (pose) {
      case _PetPose.idle:
        return Image.network(_asset('idle.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: AppColors.textTertiary)));
      case _PetPose.eating:
        return Image.network(_asset('eating.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: AppColors.textTertiary)));
      case _PetPose.playing:
        return Image.network(_asset('playing.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: AppColors.textTertiary)));
      case _PetPose.walking:
        return Image.network(_asset('walking.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: AppColors.textTertiary)));
      case _PetPose.sleeping:
        return Image.network(_asset('sleeping.png'), fit: BoxFit.contain, gaplessPlayback: true, errorBuilder: (c, e, s) => Center(child: Icon(Icons.pets, size: 80, color: AppColors.textTertiary)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final pet = widget.pet;
    final pose = _effectivePose();
    return SizedBox(
      width: 220,
      height: 220,
      child: AnimatedBuilder(
        animation: Listenable.merge([_idle, _burst]),
        builder: (context, _) {
          final t = _idle.value;
          final motion = _PetMotionState.from(pet).compute(t);
          final burstP = _burst.value;
          final bursting = _burst.isAnimating;
          double hop = 0;
          double bounceScale = 1.0;
          if (bursting) {
            // 竖向弹跳：前 30% 段上抛再落回
            if (burstP < 0.3) {
              hop = -20 * math.sin(math.pi * (burstP / 0.3));
            }
            // 柔和缩放：0→1 全程轻微放大到 1.10 再回落，与弹跳组合成"点击宠物"的柔和反馈
            bounceScale = 1.0 + 0.10 * math.sin(math.pi * burstP);
          }
          return Stack(
            alignment: Alignment.center,
            children: [
              if (pet.needAttention) _AttentionHalo(t: _reduceMotion ? 0 : t),
              Transform(
                alignment: Alignment.bottomCenter,
                transform: Matrix4.identity()
                  ..translateByVector3(Vector3(0.0, motion.dy + hop, 0.0))
                  ..rotateZ(motion.rotate)
                  ..scaleByVector3(Vector3(motion.scaleX * bounceScale, motion.scaleY * bounceScale, 1.0)),
                child: Container(
                  width: 190,
                  height: 190,
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceAlt,
                    borderRadius: BorderRadius.circular(24),
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(16),
                    child: _poseImage(pose),
                  ),
                ),
              ),
              // Aurora P4：粒子在 reduceMotion 时省略（弹跳/缩放反馈保留）
              if (bursting && !_reduceMotion)
                _ParticleBurst(progress: burstP, action: widget.action ?? ''),
            ],
          );
        },
      ),
    );
  }
}

/// 宠物行为状态帧：待机 / 进食 / 玩耍 / 行走 / 睡觉（静态图，行为帧 3 秒后按精力回落）
enum _PetPose { idle, eating, playing, walking, sleeping }

class _PetMotion {
  final double scaleX;
  final double scaleY;
  final double rotate;
  final double dy;
  const _PetMotion({required this.scaleX, required this.scaleY, required this.rotate, required this.dy});
}

class _PetMotionState {
  final bool lively;
  final bool droopy;
  final bool sad;
  final bool sleepy;

  const _PetMotionState({required this.lively, required this.droopy, required this.sad, required this.sleepy});

  factory _PetMotionState.from(Pet pet) {
    return _PetMotionState(
      lively: pet.mood >= 60 && pet.energy >= 60 && pet.hunger >= 60,
      droopy: pet.hunger < 30 || pet.energy < 30,
      sad: pet.mood < 30,
      sleepy: pet.energy < 30,
    );
  }

  _PetMotion compute(double t) {
    final base = t * 2 * math.pi;
    double bFreq = 1.0, bAmp = 0.03;
    if (sleepy) {
      bFreq = 0.5;
      bAmp = 0.016;
    } else if (droopy) {
      bFreq = 0.7;
      bAmp = 0.02;
    }
    if (lively) {
      bFreq = 1.8;
      bAmp = 0.05;
    }
    final breath = 1 + bAmp * math.sin(base * bFreq);
    final sec = t * 4;
    final bp = (sec % 3.2) / 3.2;
    const closed = 0.05;
    double blink = 1;
    if (bp < closed) {
      blink = 1 - 0.92 * Curves.easeInOut.transform(bp / closed);
    } else if (bp < closed * 2) {
      blink = 0.08 + 0.92 * Curves.easeInOut.transform((bp - closed) / closed);
    }
    double sFreq = 1.0, sAmp = 0.02;
    if (sad) {
      sFreq = 0.5;
      sAmp = 0.011;
    }
    if (droopy && !lively) {
      sAmp = 0.014;
    }
    if (lively) {
      sFreq = 2.2;
      sAmp = 0.032;
    }
    final rotate = sAmp * math.sin(base * sFreq) + (droopy ? 0.04 : 0);
    double dy = 2.5 * math.sin(base);
    if (lively) {
      dy = 5.5 * math.sin(base * 2);
    } else if (sleepy) {
      dy = 1.2 * math.sin(base * 0.5);
    } else if (droopy) {
      dy = 1.5 * math.sin(base * 0.7) + 3;
    }
    return _PetMotion(scaleX: breath, scaleY: breath * blink, rotate: rotate, dy: dy);
  }
}

class _AttentionHalo extends StatelessWidget {
  final double t;
  const _AttentionHalo({required this.t});

  @override
  Widget build(BuildContext context) {
    final pulse = (math.sin(t * 2 * math.pi * 2) + 1) / 2;
    return Container(
      width: 190 + 22 * pulse,
      height: 190 + 22 * pulse,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: Colors.orange.withValues(alpha: 0.08 + 0.07 * pulse),
      ),
    );
  }
}

class _ParticleData {
  final String emoji;
  final double dx;
  final double dy;
  final double size;
  final double delay;
  const _ParticleData({required this.emoji, required this.dx, required this.dy, required this.size, required this.delay});
}

class _ParticleBurst extends StatelessWidget {
  final double progress;
  final String action;
  const _ParticleBurst({required this.progress, required this.action});

  @override
  Widget build(BuildContext context) {
    final List<String> emojis = action == 'feed'
        ? const ['❤️', '🍖', '🍎', '🧀']
        : action == 'play'
            ? const ['⭐', '✨', '🎈', '🌟']
            : action == 'clean'
                ? const ['💧', '🫧', '✨', '💦']
                : const ['✨'];
    final rnd = math.Random(7);
    final particles = List.generate(12, (i) {
      final angle = rnd.nextDouble() * math.pi;
      final dist = 60 + rnd.nextDouble() * 110;
      final dx = math.cos(angle) * dist * (rnd.nextBool() ? 1 : -1);
      final dy = -(40 + rnd.nextDouble() * 130);
      return _ParticleData(
        emoji: emojis[i % emojis.length],
        dx: dx,
        dy: dy,
        size: 16 + rnd.nextDouble() * 16,
        delay: rnd.nextDouble() * 0.22,
      );
    });
    return Stack(
      children: [
        for (final pt in particles)
          Positioned(
            left: 110 - pt.size / 2,
            top: 110 - pt.size / 2,
            child: Transform.translate(
              offset: _offset(pt),
              child: Opacity(
                opacity: _opacity(pt),
                child: Text(pt.emoji, style: TextStyle(fontSize: pt.size)),
              ),
            ),
          ),
      ],
    );
  }

  Offset _offset(_ParticleData pt) {
    final p = (progress - pt.delay) / (1 - pt.delay);
    if (p <= 0) return Offset.zero;
    final q = Curves.easeOutCubic.transform(p.clamp(0.0, 1.0).toDouble());
    return Offset(pt.dx * q, pt.dy * q);
  }

  double _opacity(_ParticleData pt) {
    final p = (progress - pt.delay) / (1 - pt.delay);
    if (p <= 0) return 0;
    final q = p.clamp(0.0, 1.0).toDouble();
    if (q < 0.18) return q / 0.18;
    return (1 - q) / 0.82;
  }
}

class StatBar extends StatelessWidget {
  final IconData icon;
  final String label;
  final int value;
  const StatBar({super.key, required this.icon, required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    final color = value < 30
        ? AppColors.error
        : value < 60
            ? AppColors.warning
            : AppColors.success;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Icon(icon, size: 20, color: AppColors.textMuted),
          const SizedBox(width: 8),
          SizedBox(width: 52, child: Text(label, style: const TextStyle(fontSize: 13))),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: LinearProgressIndicator(
                value: value.clamp(0, 100) / 100.0,
                minHeight: 10,
                backgroundColor: AppColors.surfaceAlt,
                color: color,
              ),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(width: 30, child: Text("$value", textAlign: TextAlign.right, style: const TextStyle(fontSize: 13))),
        ],
      ),
    );
  }
}

