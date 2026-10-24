import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';

const _userAgent = 'RoadAlertApp/0.1 (student project)';

/// openrouteservice key (free at openrouteservice.org). Paste it between the
/// quotes. For testing only: later the key moves to the RoadAlert server.
/// While it is empty, the app uses the free OSRM service (one route only).
const String kOrsKey = '';

const _freeColor = Color(0xFF2E9E4F);
const _slowColor = Color(0xFFF5A623);
const _heavyColor = Color(0xFFD93025);

enum Traffic { free, slow, heavy }

Color _colorFor(Traffic t) {
  switch (t) {
    case Traffic.free:
      return _freeColor;
    case Traffic.slow:
      return _slowColor;
    case Traffic.heavy:
      return _heavyColor;
  }
}

enum MapStyle { standard, satellite, terrain, dark }

class _StyleInfo {
  final String label;
  final IconData icon;
  final String url;
  final int maxNativeZoom;
  final String? overlayUrl; // place names drawn over satellite imagery
  final String attribution;
  const _StyleInfo(this.label, this.icon, this.url,
      this.maxNativeZoom, this.overlayUrl, this.attribution);
}

// Free tile sources. Fine for development; check each provider's terms (or
// switch to a provider with a plan) before publishing the app.
// flutter_map v8 removed the subdomains parameter, so {s} must be replaced
// with a fixed subdomain letter in the URL.
const Map<MapStyle, _StyleInfo> _styles = {
  MapStyle.standard: _StyleInfo(
    'Default',
    Icons.map,
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    19,
    null,
    '© OpenStreetMap contributors',
  ),
  MapStyle.satellite: _StyleInfo(
    'Satellite',
    Icons.satellite_alt,
    'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    18,
    'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    'Imagery © Esri, Maxar, Earthstar Geographics',
  ),
  MapStyle.terrain: _StyleInfo(
    'Terrain',
    Icons.terrain,
    'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
    17,
    null,
    '© OpenStreetMap contributors, SRTM | Style © OpenTopoMap (CC-BY-SA)',
  ),
  MapStyle.dark: _StyleInfo(
    'Dark',
    Icons.dark_mode,
    'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    16,
    'https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
    '© Esri, HERE, Garmin, © OpenStreetMap contributors',
  ),
};

class _Place {
  final String name;
  final LatLng point;
  _Place(this.name, this.point);
}

class _Segment {
  final List<LatLng> points;
  final Traffic level;
  final double startM; // metres from the start of the route
  final double? speedKmh;
  _Segment(this.points, this.level, this.startM, [this.speedKmh]);
}

class _RouteOption {
  final List<LatLng> points;
  final double distanceM;
  final double durationS;
  List<_Segment> segments;
  bool trafficLoaded = false;
  _RouteOption(this.points, this.distanceM, this.durationS, this.segments);
}

/// A road report from the RoadAlert server (accident, flood, ...).
class _Incident {
  final int id;
  final String type;
  final String label;
  final String road;
  final LatLng point;
  final String verification;
  final int confidence;
  final int reports;
  final int delayMin;
  final DateTime? startedAt;
  final double distanceM;
  final bool resolved;
  final String? photoUrl;
  final String? description;

  _Incident({
    required this.id,
    required this.type,
    required this.label,
    required this.road,
    required this.point,
    required this.verification,
    required this.confidence,
    required this.reports,
    required this.delayMin,
    required this.startedAt,
    required this.distanceM,
    required this.resolved,
    this.photoUrl,
    this.description,
  });

  factory _Incident.from(Map<String, dynamic> m) {
    return _Incident(
      id: (m['id'] as num).toInt(),
      type: (m['incident_type'] ?? '').toString(),
      label: (m['type_label'] ?? m['incident_type'] ?? 'Report').toString(),
      road: (m['road_name'] ?? '').toString(),
      point: LatLng(
          (m['latitude'] as num).toDouble(), (m['longitude'] as num).toDouble()),
      verification: (m['verification_label'] ?? '').toString(),
      confidence: (m['confidence'] as num?)?.round() ?? 0,
      reports: (m['reports'] as num?)?.toInt() ?? 1,
      delayMin: (m['estimated_delay_min'] as num?)?.round() ?? 0,
      startedAt:
          DateTime.tryParse((m['started_at'] ?? '').toString())?.toLocal(),
      distanceM: (m['distance_m'] as num?)?.toDouble() ?? 0,
      resolved: m['resolved_at'] != null,
      photoUrl: m['photo_url'] as String?,
      description: m['description'] as String?,
    );
  }
}

IconData _incidentIcon(String type) {
  final t = type.toLowerCase();
  if (t.contains('accident')) return Icons.car_crash;
  if (t.contains('landslide')) return Icons.terrain;
  if (t.contains('flood') || t.contains('water')) return Icons.water_drop;
  if (t.contains('repair') || t.contains('construction')) {
    return Icons.construction;
  }
  if (t.contains('closed') || t.contains('closure') || t.contains('block')) {
    return Icons.block;
  }
  if (t.contains('traffic') || t.contains('jam')) return Icons.traffic;
  if (t.contains('signal')) return Icons.traffic;
  if (t.contains('breakdown')) return Icons.car_repair;
  if (t.contains('festival') || t.contains('event')) return Icons.celebration;
  return Icons.warning_amber;
}

/// Higher confidence (more verified) = redder.
Color _incidentColor(int confidence) {
  if (confidence >= 70) return const Color(0xFFD93025);
  if (confidence >= 40) return const Color(0xFFF5A623);
  return const Color(0xFF8D6E63);
}

String _clock(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

class _IncidentPin extends StatelessWidget {
  final _Incident incident;
  const _IncidentPin(this.incident);

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: _incidentColor(incident.confidence),
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 2),
        boxShadow: const [BoxShadow(color: Colors.black38, blurRadius: 4)],
      ),
      child: Icon(_incidentIcon(incident.type), color: Colors.white, size: 22),
    );
  }
}

/// Live map: user's position, destination search by name, and up to three
/// driving routes to choose from.
class LiveMapScreen extends StatefulWidget {
  const LiveMapScreen({super.key});

  @override
  State<LiveMapScreen> createState() => _LiveMapScreenState();
}

class _LiveMapScreenState extends State<LiveMapScreen> {
  final MapController _controller = MapController();
  final TextEditingController _searchCtl = TextEditingController();
  StreamSubscription<Position>? _sub;

  LatLng? _me;
  bool _mapReady = false;
  bool _following = true;
  String? _error;

  bool _busy = false;
  String? _message;
  List<_Place> _results = [];
  _Place? _destination;
  List<_RouteOption> _routes = [];
  int _selected = 0;
  String _source = '';
  String? _alert;
  String? _trafficNote;
  MapStyle _style = MapStyle.standard;
  List<_Incident> _incidents = [];
  Timer? _incidentTimer;
  bool _incidentsLoaded = false;

  _RouteOption? get _current => _routes.isEmpty ? null : _routes[_selected];

  @override
  void initState() {
    super.initState();
    _startTracking();
    _loadStyle();
    _incidentTimer =
        Timer.periodic(const Duration(seconds: 30), (_) {
      _loadIncidents();
      if (_routes.isNotEmpty) _loadTraffic(_selected, force: true);
    });
  }

  // ---------- map style ----------

  Future<void> _loadStyle() async {
    try {
      final p = await SharedPreferences.getInstance();
      final name = p.getString('mapStyle');
      final found = MapStyle.values.where((v) => v.name == name);
      if (found.isNotEmpty && mounted) setState(() => _style = found.first);
    } catch (_) {}
  }

  Future<void> _setStyle(MapStyle v) async {
    setState(() => _style = v);
    try {
      final p = await SharedPreferences.getInstance();
      await p.setString('mapStyle', v.name);
    } catch (_) {}
  }

  void _pickStyle() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('Map style',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            ),
            for (final v in MapStyle.values)
              ListTile(
                leading: Icon(_styles[v]!.icon),
                title: Text(_styles[v]!.label),
                trailing: v == _style ? const Icon(Icons.check) : null,
                onTap: () {
                  Navigator.pop(ctx);
                  _setStyle(v);
                },
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Widget _attributionLabel() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(200),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(_styles[_style]!.attribution,
          style: const TextStyle(fontSize: 10, color: Colors.black87)),
    );
  }

  // ---------- live location ----------

  Future<void> _startTracking() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        _fail('Location is turned off on this device.');
        return;
      }
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        _fail('Location permission is needed to show the live map.');
        return;
      }

      // 1. Immediately use last known GPS position so map renders instantly
      try {
        final last = await Geolocator.getLastKnownPosition();
        if (last != null) _onPosition(last);
      } catch (_) {}

      // 2. Query fresh high-accuracy live GPS
      try {
        final fresh = await Geolocator.getCurrentPosition(
          desiredAccuracy: LocationAccuracy.high,
        );
        _onPosition(fresh);
      } catch (_) {}

      // 3. Continuously stream live GPS coordinates in real-time
      _sub = Geolocator.getPositionStream(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
          distanceFilter: 0,
        ),
      ).listen(_onPosition, onError: (e) => _fail('Location error: $e'));
    } catch (e) {
      _fail('Could not start location: $e');
    }
  }

  void _fail(String message) {
    if (mounted) setState(() => _error = message);
  }

  void _onPosition(Position p) {
    if (!mounted) return;
    final here = LatLng(p.latitude, p.longitude);
    setState(() {
      _me = here;
      _error = null;
    });
    if (!_incidentsLoaded) {
      _incidentsLoaded = true;
      _loadIncidents();
    }
    if (_mapReady && _following) {
      _controller.move(here, _controller.camera.zoom);
    }
  }

  // ---------- destination search ----------

  Future<void> _search() async {
    final q = _searchCtl.text.trim();
    final me = _me;
    if (q.isEmpty || me == null) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _busy = true;
      _message = null;
      _results = [];
    });
    try {
      final list = await Api.instance
          .searchPlaces(q, lat: me.latitude, lng: me.longitude);
      final places = list
          .map((e) => _Place(
                e['name'] as String,
                LatLng((e['lat'] as num).toDouble(),
                    (e['lng'] as num).toDouble()),
              ))
          .toList();
      if (!mounted) return;
      setState(() {
        _results = places;
        _message = places.isEmpty ? 'No places found. Try another name.' : null;
      });
    } catch (e) {
      if (mounted) setState(() => _message = 'Search error: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ---------- routes ----------

  double? get _heavyAheadM {
    final cur = _current;
    if (cur == null) return null;
    for (final s in cur.segments) {
      if (s.level == Traffic.heavy) return s.startM;
    }
    return null;
  }

  double _distanceTo(_RouteOption r, int vertex) {
    const dist = Distance();
    var d = 0.0;
    for (var i = 1; i <= vertex && i < r.points.length; i++) {
      d += dist.as(LengthUnit.Meter, r.points[i - 1], r.points[i]);
    }
    return d;
  }

  /// Live traffic on one route, from RoadAlert users' own GPS speeds.
  /// Stretches without enough data are simply not coloured.
  Future<void> _loadTraffic(int index, {bool force = false}) async {
    if (index < 0 || index >= _routes.length) return;
    final route = _routes[index];
    if (route.trafficLoaded && !force) return;
    try {
      final n = route.points.length;
      final step = n <= 2000 ? 1 : (n / 2000).ceil();
      final idx = <int>[for (var i = 0; i < n; i += step) i];
      if (idx.last != n - 1) idx.add(n - 1);
      final geom = <List<double>>[
        for (final i in idx) [route.points[i].longitude, route.points[i].latitude],
      ];
      final data = await Api.instance.traffic(geom);
      final windows = data['windows'] as List? ?? const [];
      final segs = <_Segment>[];
      for (final w in windows) {
        final m = Map<String, dynamic>.from(w as Map);
        final a = (m['from'] as num).toInt();
        final b = (m['to'] as num).toInt();
        if (a < 0 || b >= idx.length || b <= a) continue;
        final from = idx[a];
        final to = idx[b];
        Traffic level;
        switch ('${m['level']}') {
          case 'heavy':
            level = Traffic.heavy;
            break;
          case 'slow':
            level = Traffic.slow;
            break;
          default:
            level = Traffic.free;
        }
        segs.add(_Segment(route.points.sublist(from, to + 1), level,
            _distanceTo(route, from), (m['speed_kmh'] as num?)?.toDouble()));
      }
      route.segments = segs;
      route.trafficLoaded = true;
      if (mounted) setState(() => _trafficNote = null);
    } catch (e) {
      if (mounted) setState(() => _trafficNote = 'live traffic unavailable');
    }
  }

  _RouteOption _makeOption(List<LatLng> coords, double d, double t) {
    return _RouteOption(
      coords,
      d,
      t,
      <_Segment>[],
    );
  }

  List<LatLng> _toLatLngs(List raw) {
    return raw
        .map((c) => LatLng((c[1] as num).toDouble(), (c[0] as num).toDouble()))
        .toList();
  }

  /// openrouteservice: up to 3 alternatives, trips up to 100 km.
  Future<List<_RouteOption>> _fetchOrs(LatLng me, LatLng to) async {
    final res = await http
        .post(
          Uri.parse(
              'https://api.openrouteservice.org/v2/directions/driving-car/geojson'),
          headers: {
            'Authorization': kOrsKey,
            'Content-Type': 'application/json; charset=utf-8',
            'Accept': 'application/json, application/geo+json',
          },
          body: jsonEncode({
            'coordinates': [
              [me.longitude, me.latitude],
              [to.longitude, to.latitude],
            ],
            'alternative_routes': {
              'target_count': 3,
              'share_factor': 0.8,
              'weight_factor': 2.0,
            },
          }),
        )
        .timeout(const Duration(seconds: 25));
    if (res.statusCode != 200) throw 'openrouteservice ${res.statusCode}';
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final features = data['features'] as List?;
    if (features == null || features.isEmpty) throw 'no route returned';
    final out = <_RouteOption>[];
    for (final f in features) {
      final coords = _toLatLngs(f['geometry']['coordinates'] as List);
      final summary = f['properties']['summary'];
      out.add(_makeOption(coords, (summary['distance'] as num).toDouble(),
          (summary['duration'] as num).toDouble()));
    }
    return out;
  }

  /// OSRM public server: usually one route only.
  Future<List<_RouteOption>> _fetchOsrm(LatLng me, LatLng to) async {
    final uri = Uri.parse('https://router.project-osrm.org/route/v1/driving/'
        '${me.longitude},${me.latitude};${to.longitude},${to.latitude}'
        '?overview=full&geometries=geojson&alternatives=3');
    final res = await http
        .get(uri, headers: {'User-Agent': _userAgent})
        .timeout(const Duration(seconds: 20));
    if (res.statusCode != 200) throw 'route failed (${res.statusCode})';
    final data = jsonDecode(res.body) as Map<String, dynamic>;
    final list = data['routes'] as List?;
    if (list == null || list.isEmpty) throw 'no driving route found';
    final out = <_RouteOption>[];
    for (final item in list) {
      final r = item as Map<String, dynamic>;
      out.add(_makeOption(
        _toLatLngs(r['geometry']['coordinates'] as List),
        (r['distance'] as num).toDouble(),
        (r['duration'] as num).toDouble(),
      ));
    }
    return out;
  }

  /// Routes from the RoadAlert server (its routing service plus live incidents).
  Future<List<_RouteOption>> _fetchServer(LatLng me, LatLng to) async {
    final plan = await Api.instance
        .planRoutes(me.latitude, me.longitude, to.latitude, to.longitude);
    final routes = plan['routes'] as List? ?? const [];
    if (routes.isEmpty) throw 'no driving route found';
    final out = <_RouteOption>[];
    for (final item in routes) {
      final r = Map<String, dynamic>.from(item as Map);
      final coords = _toLatLngs(r['geometry'] as List);
      final km = double.tryParse('${r['distance_km']}') ?? 0;
      final mins =
          double.tryParse('${r['effective_duration_min'] ?? r['duration_min']}') ??
              0;
      out.add(_makeOption(coords, km * 1000, mins * 60));
    }
    // the server says which route it recommends: show that one first
    final rec = (plan['recommended'] as num?)?.toInt() ?? 0;
    if (rec > 0 && rec < out.length) {
      out.insert(0, out.removeAt(rec));
    }
    final alert = plan['alert'];
    _alert = alert is Map
        ? '${alert['title']} (+${alert['estimated_delay_min']} min)'
        : null;
    return out;
  }

  Future<void> _pick(_Place place) async {
    final me = _me;
    if (me == null) return;
    setState(() {
      _busy = true;
      _message = null;
      _results = [];
    });
    try {
      List<_RouteOption> options;
      String source;
      try {
        options = await _fetchServer(me, place.point);
        source = 'RoadAlert server';
      } catch (serverError) {
        // fall back to a routing service called straight from the phone
        _alert = null;
        try {
          if (kOrsKey.isNotEmpty) {
            options = await _fetchOrs(me, place.point);
            source = 'openrouteservice (server failed: $serverError)';
          } else {
            options = await _fetchOsrm(me, place.point);
            source = 'OSRM (server failed: $serverError)';
          }
        } catch (_) {
          throw serverError;
        }
      }
      if (!mounted) return;
      setState(() {
        _destination = place;
        _routes = options;
        _selected = 0;
        _source = source;
        _following = false;
      });
      _controller.fitCamera(CameraFit.bounds(
        bounds: LatLngBounds.fromPoints(
            [me, for (final o in options) ...o.points]),
        padding: const EdgeInsets.fromLTRB(40, 170, 40, 260),
      ));
      _loadTraffic(0);
    } catch (e) {
      if (mounted) setState(() => _message = 'Route error: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ---------- road reports ----------

  Future<void> _loadIncidents({bool announce = false}) async {
    final me = _me;
    if (me == null) return;
    String status;
    try {
      final raw = await Api.instance
          .nearby(me.latitude, me.longitude, radius: 20000);
      final items = <_Incident>[];
      for (final e in raw) {
        try {
          final inc = _Incident.from(Map<String, dynamic>.from(e as Map));
          if (!inc.resolved) items.add(inc);
        } catch (_) {
          // skip a report we cannot read
        }
      }
      if (!mounted) return;
      setState(() => _incidents = items);
      status = items.isEmpty
          ? 'No reports within 20 km.'
          : '${items.length} report(s) within 20 km.';
    } catch (e) {
      status = 'Could not load reports: $e';
    }
    if (announce && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(status)),
      );
    }
  }

  void _showIncident(_Incident i) {
    showModalBottomSheet(
      context: context,
      builder: (_) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(_incidentIcon(i.type), color: _incidentColor(i.confidence)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(i.label,
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (i.road.isNotEmpty) Text(i.road),
            if (i.description != null && i.description!.trim().isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('Note: ${i.description!}',
                  style: const TextStyle(fontStyle: FontStyle.italic)),
            ],
            if (i.photoUrl != null && i.photoUrl!.isNotEmpty) ...[
              const SizedBox(height: 10),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  Api.instance.resolveUrl(i.photoUrl!),
                  height: 180,
                  width: double.infinity,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                ),
              ),
            ],
            const SizedBox(height: 8),
            Text('Verification: ${i.verification}'),
            Text('Confidence: ${i.confidence}%  •  Reports: ${i.reports}'),
            Text('Estimated delay: ${i.delayMin} min'),
            if (i.startedAt != null) Text('Started: ${_clock(i.startedAt!)}'),
            Text('Distance from you: ${(i.distanceM / 1000).toStringAsFixed(1)} km'),
          ],
        ),
      ),
    );
  }

  void _clearRoute() {
    setState(() {
      _destination = null;
      _alert = null;
      _trafficNote = null;
      _routes = [];
      _selected = 0;
      _results = [];
      _message = null;
      _searchCtl.clear();
      _following = true;
    });
    final me = _me;
    if (me != null) _controller.move(me, 16);
  }

  @override
  void dispose() {
    _sub?.cancel();
    _incidentTimer?.cancel();
    _controller.dispose();
    _searchCtl.dispose();
    super.dispose();
  }

  // ---------- UI ----------

  @override
  Widget build(BuildContext context) {
    return Scaffold(body: _buildBody());
  }

  List<Polyline> _routeLines() {
    final lines = <Polyline>[];
    // alternatives first, in grey, so the chosen route is drawn on top
    for (var i = 0; i < _routes.length; i++) {
      if (i == _selected) continue;
      lines.add(Polyline(
        points: _routes[i].points,
        strokeWidth: 5,
        color: Colors.grey.shade600,
        borderStrokeWidth: 2,
        borderColor: Colors.white,
      ));
    }
    final cur = _current;
    if (cur != null) {
      // white casing, blue road, then coloured stretches where live data exists
      lines.add(Polyline(points: cur.points, strokeWidth: 10, color: Colors.white));
      lines.add(Polyline(
          points: cur.points, strokeWidth: 6, color: Colors.blue.shade700));
      for (final seg in cur.segments) {
        lines.add(Polyline(
            points: seg.points, strokeWidth: 6, color: _colorFor(seg.level)));
      }
    }
    return lines;
  }

  Widget _buildBody() {
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_error!, textAlign: TextAlign.center),
        ),
      );
    }

    final me = _me;
    if (me == null) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(),
            SizedBox(height: 12),
            Text('Finding your location...'),
          ],
        ),
      );
    }

    final hasRoute = _destination != null && _routes.isNotEmpty;

    return Stack(
      children: [
        FlutterMap(
          mapController: _controller,
          options: MapOptions(
            initialCenter: me,
            initialZoom: 16,
            onMapReady: () {
              _mapReady = true;
              if (_me != null) {
                _controller.move(_me!, 16);
              }
            },
            onPositionChanged: (camera, hasGesture) {
              if (hasGesture && _following) {
                setState(() => _following = false);
              }
            },
          ),
          children: [
            TileLayer(
              key: ValueKey(_style),
              urlTemplate: _styles[_style]!.url,
              maxNativeZoom: _styles[_style]!.maxNativeZoom,
              userAgentPackageName: 'com.example.roadalert_app',
            ),
            if (_styles[_style]!.overlayUrl != null)
              TileLayer(
                key: ValueKey('${_style.name}-labels'),
                urlTemplate: _styles[_style]!.overlayUrl!,
                maxNativeZoom: 18,
                userAgentPackageName: 'com.example.roadalert_app',
              ),
            if (hasRoute) PolylineLayer(polylines: _routeLines()),
            MarkerLayer(
              markers: [
                for (final i in _incidents)
                  Marker(
                    point: i.point,
                    width: 44,
                    height: 44,
                    child: GestureDetector(
                      onTap: () => _showIncident(i),
                      child: _IncidentPin(i),
                    ),
                  ),
                if (_destination != null)
                  Marker(
                    point: _destination!.point,
                    width: 40,
                    height: 40,
                    alignment: Alignment.topCenter,
                    child: const Icon(Icons.location_on,
                        color: Colors.red, size: 40),
                  ),
                Marker(
                  point: me,
                  width: 30,
                  height: 30,
                  child: const _MeDot(),
                ),
              ],
            ),
          ],
        ),
        Positioned(
          top: 12,
          left: 12,
          right: 12,
          child: SafeArea(child: _buildSearchCard()),
        ),
        if (hasRoute)
          Positioned(
            left: 12,
            right: 12,
            bottom: 12,
            child: _buildRouteCard(),
          ),
        Positioned(
          right: 16,
          bottom: hasRoute ? 350 : 136,
          child: FloatingActionButton.small(
            heroTag: null,
            tooltip: 'Map style',
            onPressed: _pickStyle,
            child: const Icon(Icons.layers),
          ),
        ),
        if (!hasRoute)
          Positioned(
            left: 8,
            bottom: 6,
            right: 80,
            child: Align(
              alignment: Alignment.bottomLeft,
              child: _attributionLabel(),
            ),
          ),
        Positioned(
          right: 16,
          bottom: hasRoute ? 300 : 86,
          child: FloatingActionButton.small(
            heroTag: null,
            tooltip: 'Refresh road reports',
            onPressed: () => _loadIncidents(announce: true),
            child: const Icon(Icons.refresh),
          ),
        ),
        Positioned(
          right: 16,
          bottom: hasRoute ? 230 : 16,
          child: FloatingActionButton(
            heroTag: null,
            onPressed: () async {
              setState(() => _following = true);
              try {
                final p = await Geolocator.getCurrentPosition(
                  desiredAccuracy: LocationAccuracy.high,
                  timeLimit: const Duration(seconds: 4),
                );
                _onPosition(p);
              } catch (_) {}
              if (_me != null) _controller.move(_me!, 16);
            },
            child: Icon(
              _following ? Icons.my_location : Icons.location_searching,
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSearchCard() {
    return Card(
      elevation: 4,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _searchCtl,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => _search(),
            decoration: InputDecoration(
              hintText: 'Where do you want to go?',
              prefixIcon: const Icon(Icons.search),
              border: InputBorder.none,
              contentPadding: const EdgeInsets.symmetric(vertical: 14),
              suffixIcon: _busy
                  ? const Padding(
                      padding: EdgeInsets.all(12),
                      child: SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    )
                  : IconButton(
                      icon: const Icon(Icons.arrow_forward),
                      onPressed: _search,
                    ),
            ),
          ),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
              child: Text(_message!, style: const TextStyle(color: Colors.red)),
            ),
          if (_results.isNotEmpty)
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 220),
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final p in _results)
                    ListTile(
                      dense: true,
                      leading: const Icon(Icons.place),
                      title: Text(p.name,
                          maxLines: 2, overflow: TextOverflow.ellipsis),
                      onTap: () => _pick(p),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  String _chipLabel(int i) {
    final r = _routes[i];
    final mins = (r.durationS / 60).round();
    final km = (r.distanceM / 1000).toStringAsFixed(1);
    if (i == 0) return 'Fastest  $mins min  $km km';
    final diff = ((r.durationS - _routes[0].durationS) / 60).round();
    final sign = diff >= 0 ? '+' : '';
    return 'Route ${i + 1}  $mins min  $km km  ($sign$diff min)';
  }

  Widget _legendItem(Color c, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(color: c, shape: BoxShape.circle),
        ),
        const SizedBox(width: 4),
        Text(label, style: const TextStyle(fontSize: 12)),
      ],
    );
  }

  Widget _banner(String text) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: const Color(0xFFFCE8E6),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_amber, color: _heavyColor, size: 20),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(
                  color: Color(0xFF8C1D18), fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildRouteCard() {
    final cur = _current!;
    final km = cur.distanceM / 1000;
    final mins = (cur.durationS / 60).round();
    final heavy = _heavyAheadM;
    final status = cur.segments.isEmpty
        ? 'No live traffic data on this route'
        : 'Live traffic from RoadAlert users';
    return Card(
      elevation: 4,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        _destination!.name.split(',').first,
                        style: const TextStyle(
                            fontWeight: FontWeight.bold, fontSize: 16),
                      ),
                      const SizedBox(height: 4),
                      Text('${km.toStringAsFixed(1)} km  •  $mins min'),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Clear route',
                  onPressed: _clearRoute,
                  icon: const Icon(Icons.close),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (_routes.length > 1)
              SizedBox(
                height: 44,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  children: [
                    for (var i = 0; i < _routes.length; i++)
                      Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: ChoiceChip(
                          label: Text(_chipLabel(i)),
                          selected: i == _selected,
                          onSelected: (_) {
                            setState(() => _selected = i);
                            _loadTraffic(i);
                          },
                        ),
                      ),
                  ],
                ),
              )
            else
              const Text('No other routes found for this trip.',
                  style: TextStyle(fontSize: 12, color: Colors.grey)),
            if (_alert != null) ...[
              const SizedBox(height: 8),
              _banner(_alert!),
            ],
            if (heavy != null) ...[
              const SizedBox(height: 8),
              _banner(
                  'Heavy traffic ahead in ${(heavy / 1000).toStringAsFixed(1)} km'),
            ],
            const SizedBox(height: 8),
            Wrap(
              spacing: 12,
              children: [
                _legendItem(_freeColor, 'Clear'),
                _legendItem(_slowColor, 'Slow'),
                _legendItem(_heavyColor, 'Heavy'),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              '$status. Routes: $_source${_trafficNote == null ? '' : ' - $_trafficNote'}',
              style: const TextStyle(fontSize: 11, color: Colors.grey),
            ),
            const SizedBox(height: 2),
            Text(_styles[_style]!.attribution,
                style: const TextStyle(fontSize: 10, color: Colors.grey)),
          ],
        ),
      ),
    );
  }
}

class _MeDot extends StatelessWidget {
  const _MeDot();

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.blue,
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 3),
        boxShadow: const [BoxShadow(color: Colors.black26, blurRadius: 6)],
      ),
    );
  }
}
