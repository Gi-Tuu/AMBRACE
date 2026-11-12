import 'package:flutter/foundation.dart';
import '../models/moment.dart';
import '../models/moment_comment.dart';
import '../services/api_client.dart';

class MomentsProvider extends ChangeNotifier {
  final ApiClient _api = ApiClient();

  List<Moment> _moments = [];
  final Map<int, List<MomentComment>> _comments = {};
  Map<String, List<Map<String, dynamic>>> _archiveDays = {};
  bool _loading = false;
  bool _archiveMode = false;
  String? _error;

  List<Moment> get moments => _moments;
  Map<int, List<MomentComment>> get comments => _comments;
  Map<String, List<Map<String, dynamic>>> get archiveDays => _archiveDays;
  bool get loading => _loading;
  bool get archiveMode => _archiveMode;
  String? get error => _error;

  void toggleArchiveMode() { _archiveMode = !_archiveMode; notifyListeners(); }

  Future<void> loadMoments() async {
    _loading = true; _error = null; notifyListeners();
    try {
      final moments = await _api.getMoments();
      _moments = moments;
      for (var m in moments) {
        try { _comments[m.id] = await _api.getComments(m.id); } catch (_) {}
      }
    } catch (e) { _error = e.toString(); }
    _loading = false; notifyListeners();
  }

  Future<void> loadArchive() async {
    _loading = true; _error = null; notifyListeners();
    try {
      final data = await _api.getMomentsArchive();
      final days = (data["days"] as List?) ?? (data["moments"] as List?) ?? [];
      _archiveDays = {};
      for (var day in days) {
        final dateStr = day is Map ? (day["date"] as String?) ?? "" : "";
        final momentList = day is Map ? (day["moments"] as List?) ?? [] : [];
        if (dateStr.isNotEmpty) {
          _archiveDays[dateStr] = momentList.cast<Map<String, dynamic>>();
        }
      }
    } catch (e) { _error = e.toString(); }
    _loading = false; notifyListeners();
  }

  Future<void> loadComments(int momentId) async {
    try {
      _comments[momentId] = await _api.getComments(momentId);
      notifyListeners();
    } catch (e) { _error = e.toString(); }
  }

  Future<bool> likeMoment(int momentId) async {
    try {
      await _api.likeMoment(momentId);
      return true;
    } catch (e) { _error = e.toString(); return false; }
  }

  Future<bool> postComment(int momentId, String content, {int? parentId}) async {
    try {
      await _api.postComment(momentId, content, parentId: parentId);
      await loadComments(momentId);
      return true;
    } catch (e) { _error = e.toString(); return false; }
  }

  Future<bool> publishMoment(int characterId) async {
    try {
      final result = await _api.publishMoment(characterId);
      await loadMoments();
      return result["success"] == true;
    } catch (e) { _error = e.toString(); return false; }
  }

  Future<bool> publishUserMoment(String content) async {
    try {
      await _api.publishUserMoment(content);
      await loadMoments();
      return true;
    } catch (e) { _error = e.toString(); return false; }
  }
}
