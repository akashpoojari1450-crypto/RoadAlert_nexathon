import 'dart:async';
import 'package:flutter/material.dart';
import '../api.dart';
import 'login_screen.dart';

// The authority sees incidents around this point (Mangaluru), out to this radius.
const _regionLat = 12.9141;
const _regionLng = 74.8560;
const _radiusM = 200000;

const _months = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

/// Traffic authority dashboard, inside the app (same data as /authority/).
class AuthorityHome extends StatefulWidget {
  const AuthorityHome({super.key});
  @override
  State<AuthorityHome> createState() => _AuthorityHomeState();
}

class _AuthorityHomeState extends State<AuthorityHome> {
  int _year = DateTime.now().year;
  int _month = DateTime.now().month;

  List<Map<String, dynamic>> _incidents = [];
  Map<String, dynamic>? _report;
  bool _loading = true;
  String? _error;
  String? _reportError;
  int? _busyId;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) => _load(quiet: true));
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  String _msg(Object e) {
    if (e is ApiException) {
      return e.status == 401
          ? 'Your session ended. Log out and sign in again.'
          : e.message;
    }
    return '$e';
  }

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final raw = await Api.instance
          .nearby(_regionLat, _regionLng, radius: _radiusM);
      final items = raw
          .map((e) => Map<String, dynamic>.from(e as Map))
          .where((m) => m['resolved_at'] == null)
          .toList();
      // most verified first, then newest
      items.sort((a, b) {
        final la = (a['verification_level'] as num?) ?? 0;
        final lb = (b['verification_level'] as num?) ?? 0;
        final byLevel = lb.compareTo(la);
        if (byLevel != 0) return byLevel;
        return '${b['started_at']}'.compareTo('${a['started_at']}');
      });
      if (!mounted) return;
      setState(() {
        _incidents = items;
        _error = null;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = _msg(e);
        _loading = false;
      });
    }
    await _loadReport();
  }

  Future<void> _loadReport() async {
    try {
      final r = await Api.instance.monthlyReport(_year, _month);
      if (!mounted) return;
      setState(() {
        _report = r;
        _reportError = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _reportError = _msg(e));
    }
  }

  Future<void> _act(Map<String, dynamic> inc, bool confirm) async {
    final id = (inc['id'] as num).toInt();
    if (!confirm) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Resolve incident?'),
          content: Text(
              'Mark "${inc['type_label']}" as resolved. It will leave the live list.'),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('Resolve')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => _busyId = id);
    try {
      if (confirm) {
        await Api.instance.confirmIncident(id);
      } else {
        await Api.instance.resolveIncident(id);
      }
      await _load(quiet: true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_msg(e))));
      }
    } finally {
      if (mounted) setState(() => _busyId = null);
    }
  }

  Future<void> _logout() async {
    await Api.instance.logout();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()));
  }

  String _when(Object? iso) {
    final t = DateTime.tryParse('$iso')?.toLocal();
    if (t == null) return '';
    final hh = t.hour.toString().padLeft(2, '0');
    final mm = t.minute.toString().padLeft(2, '0');
    return '${t.day} ${_months[t.month - 1].substring(0, 3)}, $hh:$mm';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Traffic authority'),
        actions: [
          IconButton(
              tooltip: 'Refresh',
              onPressed: () => _load(),
              icon: const Icon(Icons.refresh)),
          IconButton(
              tooltip: 'Log out',
              onPressed: _logout,
              icon: const Icon(Icons.logout)),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () => _load(quiet: true),
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            _summary(),
            const SizedBox(height: 16),
            Text('Live incidents',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 4),
            Text(
                'Most verified first. Confirm sets the highest verification level.',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_error != null)
              Text(_error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error))
            else if (_incidents.isEmpty)
              const Padding(
                padding: EdgeInsets.all(16),
                child: Center(child: Text('No active incidents.')),
              )
            else
              for (final i in _incidents) _incidentCard(i),
            const SizedBox(height: 24),
            _monthly(),
          ],
        ),
      ),
    );
  }

  Widget _stat(String value, String label) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context)
                    .textTheme
                    .titleLarge
                    ?.copyWith(fontWeight: FontWeight.bold)),
            const SizedBox(height: 2),
            Text(label, style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }

  Widget _summary() {
    final r = _report;
    return Column(
      children: [
        Row(
          children: [
            Expanded(child: _stat('${_incidents.length}', 'Active incidents')),
            Expanded(
                child: _stat(
                    r == null ? '-' : '${r['total_incidents']}', 'This month')),
          ],
        ),
        Row(
          children: [
            Expanded(
                child: _stat(
                    r == null ? '-' : '${r['total_delay_hours']} h', 'Total delay')),
            Expanded(
                child: _stat(
                    r == null ? '-' : '${r['most_frequent_reason'] ?? '-'}',
                    'Most frequent reason')),
          ],
        ),
      ],
    );
  }

  Widget _incidentCard(Map<String, dynamic> i) {
    final id = (i['id'] as num).toInt();
    final level = (i['verification_level'] as num?)?.toInt() ?? 1;
    final road = '${i['road_name'] ?? ''}'.trim();
    final place = road.isNotEmpty
        ? road
        : '${(i['latitude'] as num).toStringAsFixed(4)}, ${(i['longitude'] as num).toStringAsFixed(4)}';
    final busy = _busyId == id;
    final confirmed = level >= 6;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text('${i['type_label'] ?? i['incident_type']}',
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                ),
                Text('${i['severity'] ?? ''}',
                    style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
            const SizedBox(height: 4),
            Text(place),
            const SizedBox(height: 4),
            Text(
                'Level $level - ${i['verification_label'] ?? ''} - ${i['confidence']}%',
                style: Theme.of(context).textTheme.bodySmall),
            Text(
                'Reports: ${i['reports']}   Delay: ${i['estimated_delay_min']} min   Since ${_when(i['started_at'])}',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            Row(
              children: [
                FilledButton.tonal(
                  onPressed: (busy || confirmed) ? null : () => _act(i, true),
                  child: Text(confirmed ? 'Confirmed' : 'Confirm'),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: busy ? null : () => _act(i, false),
                  child: const Text('Resolve'),
                ),
                if (busy) ...[
                  const SizedBox(width: 12),
                  const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2)),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _monthly() {
    final now = DateTime.now();
    final r = _report;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Monthly report', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        Row(
          children: [
            DropdownButton<int>(
              value: _month,
              items: [
                for (var m = 1; m <= 12; m++)
                  DropdownMenuItem(value: m, child: Text(_months[m - 1])),
              ],
              onChanged: (v) {
                if (v == null) return;
                setState(() => _month = v);
                _loadReport();
              },
            ),
            const SizedBox(width: 16),
            DropdownButton<int>(
              value: _year,
              items: [
                for (var y = now.year - 1; y <= now.year; y++)
                  DropdownMenuItem(value: y, child: Text('$y')),
              ],
              onChanged: (v) {
                if (v == null) return;
                setState(() => _year = v);
                _loadReport();
              },
            ),
          ],
        ),
        if (_reportError != null)
          Text(_reportError!,
              style: TextStyle(color: Theme.of(context).colorScheme.error))
        else if (r == null)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator()),
          )
        else ...[
          Row(
            children: [
              Expanded(child: _stat('${r['average_delay_min']} min', 'Average delay')),
              Expanded(child: _stat('${r['max_delay_min']} min', 'Longest delay')),
              Expanded(child: _stat('${r['authority_confirmed']}', 'Confirmed')),
            ],
          ),
          const SizedBox(height: 12),
          Text('By reason', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          _bars(r['by_reason'] as List? ?? const [], 'reason'),
          const SizedBox(height: 12),
          Text('Hotspots', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 4),
          _bars(r['hotspots'] as List? ?? const [], 'road'),
          const SizedBox(height: 4),
          Text('Each bar: number of incidents, then total delay hours.',
              style: Theme.of(context).textTheme.bodySmall),
        ],
      ],
    );
  }

  Widget _bars(List items, String nameKey) {
    if (items.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 8),
        child: Text('Nothing recorded for this month.'),
      );
    }
    final rows = items.map((e) => Map<String, dynamic>.from(e as Map)).toList();
    var maxCount = 1;
    for (final r in rows) {
      final c = (r['incidents'] as num?)?.toInt() ?? 0;
      if (c > maxCount) maxCount = c;
    }
    return Column(
      children: [
        for (final r in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(child: Text('${r[nameKey]}')),
                    Text('${r['incidents']} - ${r['total_delay_hours']} h'),
                  ],
                ),
                const SizedBox(height: 2),
                LinearProgressIndicator(
                  value: ((r['incidents'] as num?)?.toDouble() ?? 0) / maxCount,
                  minHeight: 6,
                  borderRadius: BorderRadius.circular(3),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
