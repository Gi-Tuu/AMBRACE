import 'package:flutter/foundation.dart';
import '../models/character.dart';
import '../services/api_client.dart';

class CharactersProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  List<AICharacter> _characters = [];
  bool _loading = false;
  String? _error;

  List<AICharacter> get characters => _characters;
  bool get loading => _loading;
  String? get error => _error;

  Future<void> loadCharacters() async {
    _loading = true; _error = null; notifyListeners();
    try {
      _characters = await _api.getCharacters();
    } catch (e) { _error = e.toString(); }
    _loading = false; notifyListeners();
  }

  Future<AICharacter?> createCharacter(Map<String, dynamic> data) async {
    try {
      final char = await _api.createCharacter(data);
      _characters.add(char);
      notifyListeners();
      return char;
    } catch (e) { _error = e.toString(); return null; }
  }

  Future<AICharacter?> updateCharacter(int id, Map<String, dynamic> data) async {
    try {
      final char = await _api.updateCharacter(id, data);
      final idx = _characters.indexWhere((c) => c.id == id);
      if (idx >= 0) _characters[idx] = char;
      notifyListeners();
      return char;
    } catch (e) { _error = e.toString(); return null; }
  }

  Future<bool> deleteCharacter(int id) async {
    try {
      await _api.deleteCharacter(id);
      _characters.removeWhere((c) => c.id == id);
      notifyListeners();
      return true;
    } catch (e) { _error = e.toString(); return false; }
  }

  void addCharacter(AICharacter char) {
    _characters.add(char);
    notifyListeners();
  }
}
