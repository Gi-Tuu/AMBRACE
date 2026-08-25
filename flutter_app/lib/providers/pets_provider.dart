import 'package:flutter/foundation.dart';
import '../models/pet.dart';
import '../services/api_client.dart';

class PetsProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  List<Pet> _pets = [];
  int? _selectedId;
  bool _loading = false;
  String? _error;
  List<Map<String, dynamic>> _activities = [];
  int _activityPetId = 0;

  List<Pet> get pets => _pets;
  int? get selectedId => _selectedId;
  bool get loading => _loading;
  String? get error => _error;
  bool get hasAttention => _pets.any((p) => p.needAttention);
  List<Map<String, dynamic>> get activities => _activities;

  Pet? get selectedPet {
    for (final p in _pets) {
      if (p.id == _selectedId) return p;
    }
    return _pets.isNotEmpty ? _pets.first : null;
  }

  Future<void> loadPets() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final pets = await _api.getPets();
      _pets = pets;
      if (_selectedId == null || !_pets.any((p) => p.id == _selectedId)) {
        _selectedId = _pets.isNotEmpty ? _pets.first.id : null;
      }
    } catch (e) {
      _error = e.toString();
    }
    _loading = false;
    notifyListeners();
  }

  void selectPet(int id) {
    _selectedId = id;
    notifyListeners();
    loadActivities(id);
  }

  /// 加载宠物最近互动活动（互动展示区）
  Future<void> loadActivities(int petId) async {
    try {
      final acts = await _api.getPetActivities(petId);
      if (_activityPetId != petId) _activityPetId = petId;
      _activities = acts;
      notifyListeners();
    } catch (_) {
      // 静默：活动加载失败不影响宠物主界面
    }
  }

  /// 互动后刷新活动（短暂等待接口返回，活动列表跟随最新状态）
  Future<void> refreshActivities() async {
    final id = _selectedId;
    if (id != null) await loadActivities(id);
  }

  /// 遗弃宠物（硬删除）：成功后移除本地并刷新
  Future<bool> abandon(int petId) async {
    try {
      await _api.deletePet(petId);
      _pets = _pets.where((p) => p.id != petId).toList();
      if (_selectedId == petId) _selectedId = _pets.isNotEmpty ? _pets.first.id : null;
      notifyListeners();
      return true;
    } catch (e) {
      _error = e.toString();
      return false;
    }
  }

  Future<bool> adopt(String species, String name) async {
    try {
      final pet = await _api.adoptPet(species, name);
      _pets = [..._pets, pet];
      _selectedId = pet.id;
      notifyListeners();
      return true;
    } catch (e) {
      _error = e.toString();
      return false;
    }
  }

  Future<bool> interact(String action) async {
    final pet = selectedPet;
    if (pet == null) return false;
    try {
      final updated = await _api.petAction(pet.id, action);
      _replace(updated);
      return true;
    } catch (e) {
      _error = e.toString();
      return false;
    }
  }

  Future<bool> rename(String name) async {
    final pet = selectedPet;
    if (pet == null) return false;
    try {
      final updated = await _api.renamePet(pet.id, name);
      _replace(updated);
      return true;
    } catch (e) {
      _error = e.toString();
      return false;
    }
  }

  void _replace(Pet updated) {
    _pets = [for (final p in _pets) if (p.id == updated.id) updated else p];
    notifyListeners();
  }
}
