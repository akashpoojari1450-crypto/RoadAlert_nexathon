import 'package:flutter/material.dart';
import '../api.dart';
import '../location.dart';

const Map<String, String> incidentTypes = {
  'heavy_traffic': 'Heavy traffic',
  'accident': 'Accident',
  'road_closure': 'Road closure',
  'road_repair': 'Road repair / construction',
  'breakdown': 'Vehicle breakdown',
  'signal_failure': 'Traffic signal failure',
  'flood': 'Flood / waterlogging',
  'heavy_rain': 'Heavy rain',
  'landslide': 'Landslide',
  'festival': 'Festival / public event',
  'strike': 'Strike / protest',
  'vip_movement': 'VIP movement',
  'maintenance': 'Bridge/road maintenance',
  'police_restriction': 'Police/security restrictions',
};

class ReportScreen extends StatefulWidget {
  const ReportScreen({super.key});
  @override
  State<ReportScreen> createState() => _ReportScreenState();
}

class _ReportScreenState extends State<ReportScreen> {
  String _type = 'heavy_traffic';
  final _road = TextEditingController();
  final _desc = TextEditingController();
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result;

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final pos = await currentPosition();
      final r = await Api.instance.report({
        'incident_type': _type,
        'latitude': pos.latitude,
        'longitude': pos.longitude,
        'speed_kmh': pos.speed < 0 ? 0.0 : pos.speed * 3.6,
        if (_road.text.trim().isNotEmpty) 'road_name': _road.text.trim(),
        if (_desc.text.trim().isNotEmpty) 'description': _desc.text.trim(),
      });
      setState(() => _result = r);
    } catch (e) {
      setState(() => _error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Report a road problem',
            style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 4),
        const Text('Your current GPS location is attached automatically.'),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          value: _type,
          isExpanded: true,
          decoration: const InputDecoration(
              labelText: 'What is happening?', border: OutlineInputBorder()),
          items: incidentTypes.entries
              .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
              .toList(),
          onChanged: (v) => setState(() => _type = v ?? _type),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _road,
          decoration: const InputDecoration(
              labelText: 'Road name (optional)', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _desc,
          maxLines: 3,
          decoration: const InputDecoration(
              labelText: 'Details (optional)', border: OutlineInputBorder()),
        ),
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: _busy ? null : _submit,
          icon: _busy
              ? const SizedBox(
                  height: 18,
                  width: 18,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : const Icon(Icons.send),
          label: const Text('Send report'),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
        ],
        if (_result != null) ...[
          const SizedBox(height: 16),
          Card(
            child: ListTile(
              leading: const Icon(Icons.check_circle, color: Colors.green),
              title: const Text('Report received'),
              subtitle: Text(
                  '${_result!['type_label']} · ${_result!['reports']} report(s)\n'
                  'Verification: ${_result!['verification_label']}'),
            ),
          ),
        ],
      ],
    );
  }
}
