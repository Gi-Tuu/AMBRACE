import 'package:dio/dio.dart';
import '../api_client.dart';

/// PhoneDesktopApi：小手机桌面领域 API（extension 挂到 ApiClient）
extension PhoneDesktopApi on ApiClient {
  Future<Map<String, dynamic>> getPhoneLayouts(int characterId) async {
    final r = await dio.get(
      '/api/v1/phone-desktop/layouts',
      queryParameters: {'character_id': characterId},
    );
    return r.data as Map<String, dynamic>;
  }

  Future<void> savePhoneLayouts(
    int characterId,
    List<Map<String, dynamic>> apps, {
    String? wallpaper,
  }) async {
    await dio.put(
      '/api/v1/phone-desktop/layouts',
      queryParameters: {'character_id': characterId},
      data: {'apps': apps, 'wallpaper': wallpaper},
    );
  }

  Future<Map<String, dynamic>> getPhonePhotos() async {
    final r = await dio.get('/api/v1/phone-desktop/photos');
    return r.data as Map<String, dynamic>;
  }

  Future<String> uploadPhonePhoto(String filePath) async {
    final form = FormData.fromMap({
      'image': await MultipartFile.fromFile(filePath),
    });
    final r = await dio.post('/api/v1/phone-desktop/photos', data: form);
    return (r.data as Map<String, dynamic>)['url'] as String;
  }

  Future<void> deletePhonePhoto(String source, String filename) async {
    await dio.delete(
      '/api/v1/phone-desktop/photos',
      queryParameters: {'source': source, 'filename': filename},
    );
  }

  Future<String> savePhonePhoto(String filename) async {
    final r = await dio.post(
      '/api/v1/phone-desktop/photos/save',
      data: {'filename': filename},
    );
    return (r.data as Map<String, dynamic>)['url'] as String;
  }

  Future<List<Map<String, dynamic>>> getCalendarNotes(
    int characterId, {
    String? month,
  }) async {
    final r = await dio.get(
      '/api/v1/phone-desktop/calendar-notes',
      queryParameters: {
        'character_id': characterId,
        if (month != null) 'month': month,
      },
    );
    return parseListItems(r.data, 'notes', (j) => j as Map<String, dynamic>);
  }

  Future<void> addCalendarNote(int characterId, String date, String text) async {
    await dio.post('/api/v1/phone-desktop/calendar-notes', data: {
      'character_id': characterId,
      'date': date,
      'text': text,
    });
  }

  Future<void> deleteCalendarNote(int noteId) async {
    await dio.delete('/api/v1/phone-desktop/calendar-notes/$noteId');
  }

  Future<List<Map<String, dynamic>>> getBrowserHistory(int characterId) async {
    final r = await dio.get(
      '/api/v1/phone-desktop/browser-history',
      queryParameters: {'character_id': characterId},
    );
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  Future<void> addBrowserHistory(int characterId, String query) async {
    await dio.post('/api/v1/phone-desktop/browser-history', data: {
      'character_id': characterId,
      'query': query,
    });
  }

  Future<void> deleteBrowserHistory(int histId) async {
    await dio.delete('/api/v1/phone-desktop/browser-history/$histId');
  }

  Future<Map<String, dynamic>> searchWeb(String query) async {
    // 搜索走 Playwright 冷启动（约 6-15s，最坏 30s+），放宽超时避免 Dio 提前中止
    final r = await dio.get(
      '/api/v1/phone-desktop/search',
      queryParameters: {'q': query},
      options: Options(
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 75),
      ),
    );
    return r.data as Map<String, dynamic>;
  }

  Future<List<Map<String, dynamic>>> getPhoneMemos(int characterId) async {
    final r = await dio.get(
      '/api/v1/phone-desktop/memos',
      queryParameters: {'character_id': characterId},
    );
    return parseListItems(r.data, 'items', (j) => j as Map<String, dynamic>);
  }

  Future<void> addPhoneMemo(int characterId, String text) async {
    await dio.post('/api/v1/phone-desktop/memos', data: {
      'character_id': characterId,
      'text': text,
    });
  }

  Future<void> deletePhoneMemo(int memoId) async {
    await dio.delete('/api/v1/phone-desktop/memos/$memoId');
  }

  Future<String> getPhoneWeather() async {
    final r = await dio.get('/api/v1/phone-desktop/weather');
    return (r.data as Map<String, dynamic>)['line'] as String? ?? '';
  }
}
