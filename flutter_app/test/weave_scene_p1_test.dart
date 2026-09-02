// 织网 3D P1 · 可测逻辑单测（2026-08-24）
// 覆盖：WeaveTextureCache 的 LRU 淘汰 / contentKey 失效 / 降级；weaveShouldDegradeToDots；
// weaveCardContentKey；pickNearest3DScreen（3D 世界坐标投影到屏幕的最近邻命中兜底）。
// 说明：卡片纹理真正上传 GPU（Texture2D.fromImage）在 flutter 测试环境不可用（需 GPU），
// 故本测试只覆盖纯逻辑（LRU/降级/投屏拾取），GPU 纹理由真机/模拟器冒烟验证。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vector_math/vector_math.dart' show Vector3;

import 'package:ai_companion/features/weave/weave_scene_controller.dart';
import 'package:ai_companion/features/weave/weave_card_texture.dart';
import 'package:ai_companion/features/weave/weave_scene_view.dart' show pickNearest3DScreen;
import 'package:flutter_scene/scene.dart' show PerspectiveCamera;

WeaveSceneNode _node(int id, {String title = '标题', String summary = '摘要'}) {
  return WeaveSceneNode(
    id: id,
    characterId: id % 8,
    characterIds: [id % 8],
    title: title,
    summary: summary,
    importance: 0,
    mood: '',
    lat: 0.0,
    lon: 0.0,
  );
}

void main() {
  group('weaveShouldDegradeToDots', () {
    test('等于阈值不降级，超过阈值降级（纯色圆点）', () {
      expect(weaveShouldDegradeToDots(kWeaveTextureDegradeAbove), isFalse);
      expect(weaveShouldDegradeToDots(kWeaveTextureDegradeAbove + 1), isTrue);
      expect(weaveShouldDegradeToDots(0), isFalse);
    });
  });

  group('weaveCardContentKey', () {
    test('相同标题/摘要内容一致；内容任一变化键变化', () {
      final a = _node(1, title: 't', summary: 's');
      final same = _node(1, title: 't', summary: 's');
      final diffTitle = _node(1, title: 't2', summary: 's');
      final diffSummary = _node(1, title: 't', summary: 's2');
      expect(weaveCardContentKey(a), weaveCardContentKey(same));
      expect(weaveCardContentKey(a), isNot(weaveCardContentKey(diffTitle)));
      expect(
          weaveCardContentKey(a), isNot(weaveCardContentKey(diffSummary)));
    });
  });

  group('WeaveTextureCache LRU', () {
    test('超过上限自动淘汰最久未用条目', () async {
      final built = <int>[];
      final cache = WeaveTextureCache<String>(
        maxEntries: 2,
        build: (n) async {
          built.add(n.id);
          return 'tex${n.id}';
        },
      );
      await cache.warm([_node(1), _node(2), _node(3)], degrade: false);
      expect(built, [1, 2, 3], reason: '三个都应构建过');
      expect(cache.size, 2, reason: '上限 2，插入 3 个应淘汰 1 个');
      // 最久未用的 1 被淘汰；2/3 仍在
      expect(cache.lookup(1, weaveCardContentKey(_node(1))), isNull);
      expect(cache.lookup(2, weaveCardContentKey(_node(2))), isNotNull);
      expect(cache.lookup(3, weaveCardContentKey(_node(3))), isNotNull);
    });

    test('MRU 访问提升：被访问的条目不会先被淘汰', () async {
      final cache = WeaveTextureCache<String>(
        maxEntries: 2,
        build: (n) async => 'tex${n.id}',
      );
      await cache.warm([_node(1), _node(2)], degrade: false);
      // 访问 1 → 1 变最新；再插入 3 → 淘汰最久未用的 2
      expect(cache.lookup(1, weaveCardContentKey(_node(1))), isNotNull);
      await cache.warm([_node(3)], degrade: false);
      expect(cache.lookup(2, weaveCardContentKey(_node(2))), isNull,
          reason: '2 未再访问，应为最久未用被淘汰');
      expect(cache.lookup(1, weaveCardContentKey(_node(1))), isNotNull);
    });

    test('contentKey 失效：同 id 内容变化后旧的缓存视为失效', () async {
      final cache = WeaveTextureCache<String>(
        maxEntries: 5,
        build: (n) async => 'tex:${n.title}',
      );
      await cache.warm([_node(1, title: 'a', summary: 's')], degrade: false);
      expect(cache.lookup(1, 'a|s|||'), isNotNull);
      // 内容变化（key 不同）→ 旧缓存失效
      expect(cache.lookup(1, 'b|s|||'), isNull);
      expect(cache.contains(1, 'b|s|||'), isFalse,
          reason: '旧条目应被丢弃，等待重建');
    });

    test('degrade=true 时不构建、清空缓存', () async {
      var built = 0;
      final cache = WeaveTextureCache<String>(
        maxEntries: 5,
        build: (n) async {
          built++;
          return 'tex${n.id}';
        },
      );
      await cache.warm([_node(1), _node(2)], degrade: false);
      expect(cache.size, 2);
      await cache.warm([_node(1), _node(2), _node(3)], degrade: true);
      expect(cache.size, 0, reason: '降级应清空纹理池');
      expect(cache.ensure(_node(4), degrade: true), isNull);
      expect(built, 2, reason: '降级后不再触发构建');
    });
  });

  group('pickNearest3DScreen（3D 坐标投屏最近邻）', () {
    const size = Size(400, 400);
    final camera = PerspectiveCamera(
      position: Vector3(0, 0, -6),
      target: Vector3.zero(),
    );

    test('命中投影中心附近的最近节点', () {
      final items = <({int id, Vector3 world})>[
        (id: 1, world: Vector3(0, 0, 0)),
        (id: 2, world: Vector3(1.4, 0, 0)),
      ];
      // 原点节点投影到屏幕中心
      final center = camera.worldToScreen(Vector3.zero(), size)!;
      final picked = pickNearest3DScreen(
        offset: center,
        size: size,
        camera: camera,
        items: items,
        worldOf: (e) => e.world,
        idOf: (e) => e.id,
        hitRadiusPx: 44,
      );
      expect(picked, 1);
    });

    test('点击某个节点投影处返回该节点（放大命中半径覆盖近失）', () {
      final items = <({int id, Vector3 world})>[
        (id: 1, world: Vector3(0, 0, 0)),
        (id: 2, world: Vector3(1.4, 0, 0)),
      ];
      final s2 = camera.worldToScreen(items[1].world, size)!;
      final picked = pickNearest3DScreen(
        offset: s2,
        size: size,
        camera: camera,
        items: items,
        worldOf: (e) => e.world,
        idOf: (e) => e.id,
        hitRadiusPx: 44,
      );
      expect(picked, 2);
    });

    test('相机之后的节点 worldToScreen 为 null，不被作为候选项', () {
      // 相机在 z=-6 朝 +Z；z=-8 的点在相机后方。
      final behind = (id: 9, world: Vector3(0, 0, -8));
      expect(camera.worldToScreen(behind.world, size), isNull);
      final picked = pickNearest3DScreen(
        offset: const Offset(200, 200),
        size: size,
        camera: camera,
        items: <({int id, Vector3 world})>[behind],
        worldOf: (e) => e.world,
        idOf: (e) => e.id,
        hitRadiusPx: 60,
      );
      expect(picked, isNull);
    });

    test('命中半径之外不命中', () {
      final items = <({int id, Vector3 world})>[
        (id: 1, world: Vector3(1.4, 0, 0)),
      ];
      final s1 = camera.worldToScreen(items[0].world, size)!;
      final picked = pickNearest3DScreen(
        offset: s1 + const Offset(100, 0), // 远离节点投影处
        size: size,
        camera: camera,
        items: items,
        worldOf: (e) => e.world,
        idOf: (e) => e.id,
        hitRadiusPx: 44,
      );
      expect(picked, isNull);
    });
  });
}
