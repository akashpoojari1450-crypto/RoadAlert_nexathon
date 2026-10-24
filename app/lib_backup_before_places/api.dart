import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiException implements Exception {
  final String message;
  final int? status;
  ApiException(this.message, [this.status]);
  @override
  String toString() => message;
}

/// One shared instance talks to the RoadAlert Django backend.
class Api {
  Api._();
  static final Api instance = Api._();

  String baseUrl = 'http://10.0.2.2:8000'; // Android emulator -> your PC
  String? token;
  Map<String, dynamic>? user;

  Future<void> load() async {
    final p = await SharedPreferences.getInstance();
    baseUrl = p.getString('baseUrl') ?? baseUrl;
    token = p.getString('token');
  }

  Future<void> _save() async {
    final p = await SharedPreferences.getInstance();
    await p.setString('baseUrl', baseUrl);
    if (token == null) {
      await p.remove('token');
    } else {
      await p.setString('token', token!);
    }
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Token $token',
      };

  Future<dynamic> _call(String method, String path,
      {Object? body, Map<String, String>? query}) async {
    final uri = Uri.parse(baseUrl + path).replace(queryParameters: query);
    http.Response r;
    try {
      final req = http.Request(method, uri)..headers.addAll(_headers);
      if (body != null) req.body = jsonEncode(body);
      r = await http.Response.fromStream(
          await req.send().timeout(const Duration(seconds: 20)));
    } catch (e) {
      throw ApiException('Cannot reach the server at $baseUrl. Is it running?');
    }
    dynamic data;
    if (r.body.isNotEmpty) {
      try {
        data = jsonDecode(utf8.decode(r.bodyBytes));
      } catch (_) {
        data = null;
      }
    }
    if (r.statusCode >= 400) {
      String msg = 'Request failed (${r.statusCode})';
      if (data is Map) {
        if (data['detail'] != null) {
          msg = data['detail'].toString();
        } else if (data['non_field_errors'] != null) {
          msg = (data['non_field_errors'] as List).join(' ');
        } else if (data.isNotEmpty) {
          msg = data.entries.map((e) => '${e.key}: ${e.value}').join('\n');
        }
      }
      if (r.statusCode == 401) {
        token = null;
        await _save();
      }
      throw ApiException(msg, r.statusCode);
    }
    return data;
  }

  // ---- auth ----
  Future<void> login(String server, String username, String password) async {
    baseUrl = server.trim().replaceAll(RegExp(r'/+$'), '');
    token = null;
    final d = await _call('POST', '/api/auth/login/',
        body: {'username': username, 'password': password});
    token = d['token'] as String;
    await _save();
    await me();
  }

  /// Public sign-up. The server always creates a passenger account.
  Future<void> register(
      String server, String username, String password, String email) async {
    baseUrl = server.trim().replaceAll(RegExp(r'/+$'), '');
    token = null;
    final d = await _call('POST', '/api/auth/register/', body: {
      'username': username,
      'password': password,
      if (email.isNotEmpty) 'email': email,
    });
    token = d['token'] as String;
    user = Map<String, dynamic>.from(d['user'] as Map);
    await _save();
  }

  Future<Map<String, dynamic>> me() async {
    user = Map<String, dynamic>.from(await _call('GET', '/api/auth/me/'));
    return user!;
  }

  Future<void> logout() async {
    token = null;
    user = null;
    await _save();
  }

  Future<Map<String, dynamic>> consent() async =>
      Map<String, dynamic>.from(await _call('GET', '/api/auth/consent/'));

  Future<Map<String, dynamic>> setConsent(bool granted) async =>
      Map<String, dynamic>.from(
          await _call('POST', '/api/auth/consent/', body: {'granted': granted}));

  // ---- journeys ----
  Future<Map<String, dynamic>?> plan(
      double oLat, double oLng, double dLat, double dLng) async {
    try {
      final d = await _call('POST', '/api/routes/plan/', body: {
        'origin': {'lat': oLat, 'lng': oLng},
        'destination': {'lat': dLat, 'lng': dLng},
      });
      return Map<String, dynamic>.from(d);
    } on ApiException {
      return null; // routing is optional; journey can still start
    }
  }

  Future<Map<String, dynamic>> startJourney(Map<String, dynamic> body) async =>
      Map<String, dynamic>.from(
          await _call('POST', '/api/journeys/start/', body: body));

  Future<void> sendPoints(int id, List<Map<String, dynamic>> points) =>
      _call('POST', '/api/journeys/$id/points/', body: {'points': points});

  Future<void> endJourney(int id) => _call('POST', '/api/journeys/$id/end/');

  Future<List<dynamic>> myJourneys() async =>
      List<dynamic>.from(await _call('GET', '/api/journeys/'));

  // ---- incidents ----
  Future<Map<String, dynamic>> report(Map<String, dynamic> body) async =>
      Map<String, dynamic>.from(
          await _call('POST', '/api/incidents/report/', body: body));

  Future<List<dynamic>> nearby(double lat, double lng, {int radius = 5000}) async =>
      List<dynamic>.from(await _call('GET', '/api/incidents/nearby/',
          query: {'lat': '$lat', 'lng': '$lng', 'radius_m': '$radius'}));

  // ---- employer ----
  /// Journeys shared by employees (server applies consent and policy rules).
  Future<List<dynamic>> employerDashboard({int hours = 12}) async =>
      List<dynamic>.from(await _call('GET', '/api/employer/dashboard/',
          query: {'hours': '$hours'}));

  // ---- authority ----
  Future<Map<String, dynamic>> monthlyReport(int year, int month) async =>
      Map<String, dynamic>.from(await _call(
          'GET', '/api/incidents/reports/monthly/',
          query: {'year': '$year', 'month': '$month'}));

  /// Sets the incident to the highest verification level (authority only).
  Future<void> confirmIncident(int id) =>
      _call('POST', '/api/incidents/$id/confirm/');

  Future<void> resolveIncident(int id) =>
      _call('POST', '/api/incidents/$id/resolve/');

  // ---- notifications ----
  Future<List<dynamic>> inbox() async =>
      List<dynamic>.from(await _call('GET', '/api/notifications/'));

  Future<void> markRead(int id) => _call('POST', '/api/notifications/$id/read/');
}
