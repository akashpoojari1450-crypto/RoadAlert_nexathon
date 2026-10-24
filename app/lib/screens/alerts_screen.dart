import 'package:flutter/material.dart';
import '../api.dart';
import '../location.dart';

class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key});
  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  List<dynamic> _inbox = [];
  List<dynamic> _nearby = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final inbox = await Api.instance.inbox();
      List<dynamic> nearby = [];
      try {
        final pos = await currentPosition();
        nearby = await Api.instance.nearby(pos.latitude, pos.longitude);
      } catch (_) {
        // location unavailable: still show the inbox
      }
      if (mounted) {
        setState(() {
          _inbox = inbox;
          _nearby = nearby;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _read(Map n) async {
    if (n['read_at'] != null) return;
    try {
      await Api.instance.markRead(n['id'] as int);
      _refresh();
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Text(_error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          Text('Warnings for you', style: Theme.of(context).textTheme.titleMedium),
          if (_inbox.isEmpty && !_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('No warnings yet.'),
            ),
          ..._inbox.map((n) {
            final unread = n['read_at'] == null;
            return Card(
              child: ListTile(
                onTap: () => _read(n),
                leading: Icon(Icons.warning_amber,
                    color: unread ? Colors.orange : Colors.grey),
                title: Text('${n['title']}',
                    style: TextStyle(
                        fontWeight: unread ? FontWeight.bold : FontWeight.normal)),
                subtitle: Text('${n['body']}'),
              ),
            );
          }),
          const SizedBox(height: 16),
          Text('Reported near you (5 km)',
              style: Theme.of(context).textTheme.titleMedium),
          if (_nearby.isEmpty && !_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text('Nothing reported nearby, or location is off.'),
            ),
          ..._nearby.map((i) => Card(
                child: ListTile(
                  leading: const Icon(Icons.report_problem),
                  title: Text(
                      '${i['type_label']}${(i['road_name'] ?? '') != '' ? ' · ${i['road_name']}' : ''}'),
                  subtitle: Text(
                      '${i['distance_m']} m away · ${i['verification_label']}'
                      '${(i['estimated_delay_min'] ?? 0) > 0 ? ' · ~${i['estimated_delay_min']} min delay' : ''}'),
                ),
              )),
        ],
      ),
    );
  }
}
