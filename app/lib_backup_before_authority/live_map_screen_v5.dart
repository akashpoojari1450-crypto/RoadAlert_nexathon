import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import '../api.dart';

const _userAgent = 'RoadAlertApp/0.1 (student project)';

/// Demo traffic colours are FAKE (same pattern on every route).
/// Keep this false until real traffic data is connected.
const bool kShowDemoTraffic = false;

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

/// DEMO ONLY. [f] is how far along the route we are (0 to 1).
Traffic _demoLevel(double f) {
  if (f > 0.30 && f < 0.42) return Traffic.slow;
  if (f >= 0.42 && f < 0.55) return Traffic.heavy;
  if (f >= 0.55 && f < 0.62) return Traffic.slow;
  if (f > 0.80 && f < 0.88) return Traffic.slow;
  return Traffic.free;
}

class _Place {
  final String name;
  final LatLng point;
  _Place(this.name, this.point);
}

class _Segment {
  final List<LatLng> points;
  final Traffic level;
  final double startM;
  _Segment(this.points, this.level, this.startM);
}

class _RouteOption {
  final List<LatLng> points;
  final double distanceM;
  final double durationS;
  final List<_Segment> segments;
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
  List<_Incident> _incidents = [];
  Timer? _incidentTimer;
  bool _incidentsLoaded = false;

  _RouteOption? get _current => _routes.isEmpty ? null : _routes[_selected];

  @override
  void initState() {
    super.initState();
    _startTracking();
    _incidentTimer =
        Timer.periodic(const Duration(seconds: 30), (_) => _loadIncidents());
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
      _sub = Geolocator.getPositionStream(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
          distanceFilter: 5,
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
      final uri = Uri.https('nominatim.openstreetmap.org', '/search', {
        'q': q,
        'format': 'jsonv2',
        'limit': '5',
        'viewbox':
            '${me.longitude - 1},${me.latitude + 1},${me.longitude + 1},${me.latitude - 1}',
      });
      final res = await http
          .get(uri, headers: {'User-Agent': _userAgent})
          .timeout(const Duration(seconds: 15));
      if (res.statusCode != 200) throw 'search failed (${res.statusCode})';
      final list = jsonDecode(res.body) as List;
      final places = list
          .map((e) => _Place(
                e['display_name'] as String,
                LatLng(double.parse(e['lat'] as String),
                    double.parse(e['lon'] as String)),
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

  List<_Segment> _buildSegments(List<LatLng> route) {
    if (route.length < 2) return [];
    const dist = Distance();
    final cum = <double>[0];
    for (var i = 1; i < route.length; i++) {
      cum.add(cum[i - 1] + dist.as(LengthUnit.Meter, route[i - 1], route[i]));
    }
    final total = cum.last;
    if (total <= 0) return [];

    final segs = <_Segment>[];
    var current = _demoLevel(((cum[0] + cum[1]) / 2) / total);
    var pts = <LatLng>[route[0]];
    var startM = 0.0;
    for (var i = 1; i < route.length; i++) {
      final level = _demoLevel(((cum[i - 1] + cum[i]) / 2) / total);
      if (level != current) {
        segs.add(_Segment(pts, current, startM));
        pts = <LatLng>[route[i - 1]];
        startM = cum[i - 1];
        current = level;
      }
      pts.add(route[i]);
    }
    segs.add(_Segment(pts, current, startM));
    return segs;
  }

  double? get _heavyAheadM {
    final cur = _current;
    if (cur == null) return null;
    for (final s in cur.segments) {
      if (s.level == Traffic.heavy) return s.startM;
    }
    return null;
  }

  _RouteOption _makeOption(List<LatLng> coords, double d, double t) {
    return _RouteOption(
      coords,
      d,
      t,
      kShowDemoTraffic ? _buildSegments(coords) : <_Segment>[],
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
      var source = 'OSRM';
      if (kOrsKey.isNotEmpty) {
        try {
          options = await _fetchOrs(me, place.point);
          source = 'openrouteservice';
        } catch (e) {
          // trip too long (over 100 km), bad key, or service down:
          // fall back so the user still gets a route
          options = await _fetchOsrm(me, place.point);
          source = 'OSRM (openrouteservice failed: $e)';
        }
      } else {
        options = await _fetchOsrm(me, place.point);
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
      if (kShowDemoTraffic && cur.segments.isNotEmpty) {
        lines.add(Polyline(
          points: cur.points,
          strokeWidth: 10,
          color: Colors.white,
        ));
        for (final s in cur.segments) {
          lines.add(Polyline(
            points: s.points,
            strokeWidth: 6,
            color: _colorFor(s.level),
          ));
        }
      } else {
        lines.add(Polyline(
          points: cur.points,
          strokeWidth: 7,
          color: Colors.blue.shade700,
          borderStrokeWidth: 2,
          borderColor: Colors.white,
        ));
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
            onMapReady: () => _mapReady = true,
            onPositionChanged: (camera, hasGesture) {
              if (hasGesture && _following) {
                setState(() => _following = false);
              }
            },
          ),
          children: [
            TileLayer(
              urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
              userAgentPackageName: 'com.example.roadalert_app',
              retinaMode: RetinaMode.isHighDensity(context),
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
            onPressed: () {
              setState(() => _following = true);
              _controller.move(me, 16);
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

  Widget _buildRouteCard() {
    final cur = _current!;
    final km = cur.distanceM / 1000;
    final mins = (cur.durationS / 60).round();
    final heavy = kShowDemoTraffic ? _heavyAheadM : null;
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
                          onSelected: (_) => setState(() => _selected = i),
                        ),
                      ),
                  ],
                ),
              )
            else
              const Text('No other routes found for this trip.',
                  style: TextStyle(fontSize: 12, color: Colors.grey)),
            if (heavy != null) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFFFCE8E6),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber,
                        color: _heavyColor, size: 20),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'Heavy traffic ahead in ${(heavy / 1000).toStringAsFixed(1)} km (demo)',
                        style: const TextStyle(
                            color: Color(0xFF8C1D18),
                            fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 8),
            if (kShowDemoTraffic)
              Row(
                children: [
                  _legendItem(_freeColor, 'Clear'),
                  const SizedBox(width: 12),
                  _legendItem(_slowColor, 'Slow'),
                  const SizedBox(width: 12),
                  _legendItem(_heavyColor, 'Heavy'),
                  const Spacer(),
                  const Text('Demo data',
                      style: TextStyle(fontSize: 11, color: Colors.grey)),
                ],
              )
            else
              Text('Times do not include live traffic yet. Routes: $_source',
                  style: const TextStyle(fontSize: 11, color: Colors.grey)),
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
