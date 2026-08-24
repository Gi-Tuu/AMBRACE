import 'package:flutter/foundation.dart';
import '../models/diary_entry.dart';
import '../services/api_client.dart';

class DiaryProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  List<DiaryEntry> _entries = [];
  bool _loading = false;
  String? _error;

  List<DiaryEntry> get entries => _entries;
  bool get loading => _loading;
  String? get error => _error;

  Future<void> loadDiary(int characterId) async {
    _loading = true; _error = null; notifyListeners();
    try {
      _entries = await _api.getDiary(characterId);
    } catch (e) { _error = e.toString(); }
    _loading = false; notifyListeners();
  }

  void clear() { _entries = []; _error = null; notifyListeners(); }
}
