import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import '../api.dart';
import '../location.dart';

List<dynamic> thin(List<dynamic> g, int max) {
  if (g.length <= max) return g;
  final step = (g.length / max).ceil();
  final out = <dynamic>[];
  for (var i = 0; i < g.length; i += step) {
    out.add(g[i]);
  }
  out.add(g.last);
  return out;
}

class JourneyScreen extends StatefulWidget {
  const JourneyScreen({super.key});
  @override
  State<JourneyScreen> createState() => _JourneyScreenState();
}

class _JourneyScreenState extends State<JourneyScreen> {
  final _destName = TextEditingController();
  final _destCoords = TextEditingController(); // filled in when a place is chosen
  String _kind = 'commute';

  // place search
  bool _searching = false;
  String? _searchMsg;
  String? _chosen;
  List<Map<String, dynamic>> _places = [];

  bool _busy = false;
  String? _error;
  String? _routeInfo;

  // active journey
  int? _journeyId;
  bool _visibleToEmployer = false;
  DateTime? _startedAt;
  Position? _last;
  int _sent = 0;
  final List<Map<String, dynamic>> _buffer = [];
  StreamSubscription<Position>? _sub;
  Timer? _uploader;
  Timer? _clock;
  Timer? _sampler;

  @override
  void initState() {
    super.initState();
    _resumeIfActive();
  }

  @override
  void dispose() {
    _stopTracking();
    super.dispose();
  }

  Future<void> _resumeIfActive() async {
    try {
      final list = await Api.instance.myJourneys();
      final active = list.cast<Map>().where((j) => j['status'] == 'active');
      if (active.isNotEmpty && mounted) {
        final j = active.first;
        setState(() {
          _journeyId = j['id'] as int;
          _visibleToEmployer = j['tracking_visible_to_employer'] == true;
          _startedAt = DateTime.tryParse('${j['started_at']}')?.toLocal();
        });
        _startTracking();
      }
    } catch (_) {}
  }

  (double, double)? _parseCoords(String s) {
    final parts = s.split(RegExp(r'[,\s]+')).where((e) => e.isNotEmpty).toList();
    if (parts.length != 2) return null;
    final a = double.tryParse(parts[0]);
    final b = double.tryParse(parts[1]);
    if (a == null || b == null || a.abs() > 90 || b.abs() > 180) return null;
    return (a, b);
  }

  // ---------- destination search by name ----------

  Future<void> _searchPlaces() async {
    final q = _destName.text.trim();
    if (q.isEmpty) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _searching = true;
      _searchMsg = null;
      _places = [];
      _chosen = null;
      _destCoords.clear();
    });
    try {
      double? lat;
      double? lng;
      try {
        // prefer places near the user when a position is known
        final p = await Geolocator.getLastKnownPosition();
        if (p != null) {
          lat = p.latitude;
          lng = p.longitude;
        }
      } catch (_) {}
      final list = await Api.instance.searchPlaces(q, lat: lat, lng: lng);
      final places = list
          .map((e) => <String, dynamic>{
                'name': e['name'] as String,
                'lat': (e['lat'] as num).toDouble(),
                'lng': (e['lng'] as num).toDouble(),
              })
          .toList();
      if (!mounted) return;
      setState(() {
        _places = places;
        _searchMsg =
            places.isEmpty ? 'No places found. Try another name.' : null;
      });
    } catch (e) {
      if (mounted) setState(() => _searchMsg = 'Search error: $e');
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  void _choosePlace(Map<String, dynamic> p) {
    final full = p['name'] as String;
    final short = full.split(',').first.trim();
    setState(() {
      _destName.text = short;
      _destCoords.text = '${p['lat']}, ${p['lng']}';
      _chosen = full;
      _places = [];
      _searchMsg = null;
      _error = null;
    });
  }

  Future<void> _start() async {
    final dest = _parseCoords(_destCoords.text);
    if (dest == null) {
      setState(() => _error =
          'Type a place name, tap the search arrow, then tap one of the results.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _routeInfo = null;
    });
    try {
      final pos = await currentPosition();
      final plan =
          await Api.instance.plan(pos.latitude, pos.longitude, dest.$1, dest.$2);
      List<dynamic>? geometry;
      int? expected;
      if (plan != null) {
        final routes = plan['routes'] as List;
        final rec = routes[plan['recommended'] as int] as Map;
        geometry = thin(rec['geometry'] as List, 400);
        expected = rec['effective_duration_min'] as int?;
        _routeInfo = '${rec['distance_km']} km · about $expected min';
        final alert = plan['alert'];
        if (alert is Map) {
          _routeInfo =
              '$_routeInfo\n⚠ ${alert['title']} (+${alert['estimated_delay_min']} min)';
        }
      }
      final j = await Api.instance.startJourney({
        'kind': _kind,
        'origin_name': 'Current location',
        'destination_name': _destName.text.trim(),
        'origin_lat': pos.latitude,
        'origin_lng': pos.longitude,
        'dest_lat': dest.$1,
        'dest_lng': dest.$2,
        if (geometry != null) 'route_geometry': geometry,
        if (expected != null) 'expected_minutes': expected,
      });
      setState(() {
        _journeyId = j['id'] as int;
        _visibleToEmployer = j['tracking_visible_to_employer'] == true;
        _startedAt = DateTime.now();
        _sent = 0;
      });
      _startTracking();
    } catch (e) {
      setState(() => _error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _startTracking() {
    _sub?.cancel();
    _sub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high, distanceFilter: 10),
    ).listen((p) {
      _last = p;
      _buffer.add({
        'latitude': p.latitude,
        'longitude': p.longitude,
        // geolocator speed is m/s; the backend wants km/h. Negative = unknown.
        'speed_kmh': p.speed < 0 ? 0.0 : p.speed * 3.6,
        'recorded_at': p.timestamp.toUtc().toIso8601String(),
      });
      if (mounted) setState(() {});
    });
    _uploader?.cancel();
    _uploader = Timer.periodic(const Duration(seconds: 15), (_) => _flush());
    _clock?.cancel();
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
    // A stationary phone emits nothing with distanceFilter, which would hide a
    // traffic jam. Sample the position every 20 s so standing still is recorded.
    _sampler?.cancel();
    _sampler = Timer.periodic(const Duration(seconds: 20), (t) async {
      if (_journeyId == null) {
        t.cancel();
        return;
      }
      try {
        final p = await Geolocator.getCurrentPosition(
            desiredAccuracy: LocationAccuracy.high);
        _last = p;
        _buffer.add({
          'latitude': p.latitude,
          'longitude': p.longitude,
          'speed_kmh': p.speed < 0 ? 0.0 : p.speed * 3.6,
          'recorded_at': p.timestamp.toUtc().toIso8601String(),
        });
      } catch (_) {}
    });
  }

  Future<void> _flush() async {
    final id = _journeyId;
    if (id == null || _buffer.isEmpty) return;
    final batch = List<Map<String, dynamic>>.from(_buffer.take(500));
    try {
      await Api.instance.sendPoints(id, batch);
      _buffer.removeRange(0, batch.length);
      _sent += batch.length;
    } on ApiException catch (e) {
      if (e.status == 400) _buffer.clear(); // journey ended elsewhere
      // other errors: keep points and retry next tick
    }
    if (mounted) setState(() {});
  }

  void _stopTracking() {
    _sub?.cancel();
    _uploader?.cancel();
    _clock?.cancel();
    _sampler?.cancel();
    _sub = null;
  }

  Future<void> _end() async {
    final id = _journeyId;
    if (id == null) return;
    setState(() => _busy = true);
    try {
      await _flush();
      await Api.instance.endJourney(id);
      _stopTracking();
      setState(() {
        _journeyId = null;
        _buffer.clear();
        _last = null;
      });
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String get _elapsed {
    final s = _startedAt;
    if (s == null) return '';
    final d = DateTime.now().difference(s);
    return '${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return _journeyId == null ? _form(context) : _active(context);
  }

  Widget _form(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Start a journey', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 12),
        TextField(
          controller: _destName,
          textInputAction: TextInputAction.search,
          onSubmitted: (_) => _searchPlaces(),
          onChanged: (_) {
            // typing again means the old choice no longer applies
            if (_chosen != null) {
              setState(() {
                _chosen = null;
                _destCoords.clear();
              });
            }
          },
          decoration: InputDecoration(
            labelText: 'Where do you want to go?',
            hintText: 'e.g. Mulki',
            border: const OutlineInputBorder(),
            suffixIcon: _searching
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : IconButton(
                    icon: const Icon(Icons.search),
                    onPressed: _searchPlaces,
                  ),
          ),
        ),
        if (_searchMsg != null) ...[
          const SizedBox(height: 8),
          Text(_searchMsg!,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
        if (_places.isNotEmpty) ...[
          const SizedBox(height: 8),
          Card(
            child: Column(
              children: [
                for (final p in _places)
                  ListTile(
                    dense: true,
                    leading: const Icon(Icons.place),
                    title: Text(p['name'] as String,
                        maxLines: 2, overflow: TextOverflow.ellipsis),
                    onTap: () => _choosePlace(p),
                  ),
              ],
            ),
          ),
        ],
        if (_chosen != null) ...[
          const SizedBox(height: 8),
          Card(
            child: ListTile(
              leading: const Icon(Icons.check_circle, color: Colors.green),
              title: const Text('Destination set'),
              subtitle:
                  Text(_chosen!, maxLines: 2, overflow: TextOverflow.ellipsis),
            ),
          ),
        ],
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _kind,
          decoration: const InputDecoration(
              labelText: 'Journey type', border: OutlineInputBorder()),
          items: const [
            DropdownMenuItem(value: 'commute', child: Text('Commute')),
            DropdownMenuItem(
                value: 'official_travel', child: Text('Official travel')),
            DropdownMenuItem(
                value: 'personal', child: Text('Personal (never shared)')),
          ],
          onChanged: (v) => setState(() => _kind = v ?? 'commute'),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: _busy ? null : _start,
          icon: _busy
              ? const SizedBox(
                  height: 18,
                  width: 18,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.play_arrow),
          label: const Text('Start journey'),
        ),
      ],
    );
  }

  Widget _active(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          color: _visibleToEmployer ? cs.tertiaryContainer : cs.surfaceContainerHighest,
          child: ListTile(
            leading: Icon(_visibleToEmployer ? Icons.visibility : Icons.visibility_off),
            title: Text(_visibleToEmployer
                ? 'Your employer can see this journey'
                : 'This journey is private'),
            subtitle: Text(_visibleToEmployer
                ? 'Turn off sharing in the Me tab (applies to the next journey).'
                : 'Sharing is off, or this journey type is not covered by your company policy.'),
          ),
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Journey in progress · $_elapsed',
                    style: Theme.of(context).textTheme.titleMedium),
                if (_routeInfo != null) ...[
                  const SizedBox(height: 6),
                  Text(_routeInfo!),
                ],
                const SizedBox(height: 8),
                Text(_last == null
                    ? 'Waiting for GPS…'
                    : 'Speed ${(_last!.speed < 0 ? 0 : _last!.speed * 3.6).toStringAsFixed(0)} km/h · '
                        '${_last!.latitude.toStringAsFixed(4)}, ${_last!.longitude.toStringAsFixed(4)}'),
                const SizedBox(height: 4),
                Text('Points uploaded: $_sent · waiting: ${_buffer.length}',
                    style: Theme.of(context).textTheme.bodySmall),
                const SizedBox(height: 4),
                Text('Keep the app open while travelling.',
                    style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!, style: TextStyle(color: cs.error)),
        ],
        const SizedBox(height: 16),
        FilledButton.tonalIcon(
          onPressed: _busy ? null : _end,
          icon: const Icon(Icons.stop),
          label: const Text('End journey'),
        ),
      ],
    );
  }
}
