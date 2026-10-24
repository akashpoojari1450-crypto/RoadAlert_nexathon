import 'dart:async';
import 'package:flutter/material.dart';
import '../api.dart';
import 'login_screen.dart';
import 'hardware_simulation_screen.dart';

/// The employer dashboard, inside the app (same data as the /employer/ web page).
class EmployerHome extends StatefulWidget {
  const EmployerHome({super.key});
  @override
  State<EmployerHome> createState() => _EmployerHomeState();
}

class _EmployerHomeState extends State<EmployerHome> {
  int _hours = 12;
  List<Map<String, dynamic>> _rows = [];
  bool _loading = true;
  String? _error;
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

  Future<void> _load({bool quiet = false}) async {
    if (!quiet) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final raw = await Api.instance.employerDashboard(hours: _hours);
      final rows =
          raw.map((e) => Map<String, dynamic>.from(e as Map)).toList();
      if (!mounted) return;
      setState(() {
        _rows = rows;
        _error = null;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.status == 401
            ? 'Your session ended. Log out and sign in again.'
            : e.message;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _logout() async {
    await Api.instance.logout();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()));
  }

  Color _statusColor(String status) {
    switch (status) {
      case 'Normal':
        return const Color(0xFF2E9E4F);
      case 'Road Block':
        return const Color(0xFFD93025);
      default:
        return const Color(0xFFF5A623);
    }
  }

  @override
  Widget build(BuildContext context) {
    final company = Api.instance.user?['company'];
    final affected = _rows.where((r) => r['status'] != 'Normal').length;
    return Scaffold(
      appBar: AppBar(
        title: Text(company == null ? 'Employee travel' : '$company'),
        actions: [
          IconButton(
              tooltip: 'Hardware & Camera Node',
              onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                      builder: (_) => const HardwareSimulationScreen())),
              icon: const Icon(Icons.memory)),
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
            Text('Employee travel',
                style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 4),
            Text('Journeys shared by employees. Updates every 30 seconds.',
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(child: _stat('${_rows.length}', 'Journeys shown')),
                const SizedBox(width: 12),
                Expanded(child: _stat('$affected', 'Affected by traffic')),
              ],
            ),
            const SizedBox(height: 12),
            Card(
              color: Theme.of(context).colorScheme.secondaryContainer,
              child: const Padding(
                padding: EdgeInsets.all(12),
                child: Text(
                  'Employees are shown only while they are travelling and have '
                  'agreed to share. Anyone who has not opted in, or whose trip '
                  'is not covered by your tracking policy, does not appear here.',
                ),
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                const Text('Show last '),
                DropdownButton<int>(
                  value: _hours,
                  items: const [
                    DropdownMenuItem(value: 1, child: Text('1 hour')),
                    DropdownMenuItem(value: 6, child: Text('6 hours')),
                    DropdownMenuItem(value: 12, child: Text('12 hours')),
                    DropdownMenuItem(value: 24, child: Text('24 hours')),
                    DropdownMenuItem(value: 72, child: Text('3 days')),
                  ],
                  onChanged: (v) {
                    if (v == null) return;
                    setState(() => _hours = v);
                    _load();
                  },
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_error != null)
              Text(_error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error))
            else if (_rows.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: Text('No shared journeys in this period.')),
              )
            else
              for (final r in _rows) _rowCard(r),
          ],
        ),
      ),
    );
  }

  Widget _stat(String value, String label) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value,
                style: Theme.of(context)
                    .textTheme
                    .headlineMedium
                    ?.copyWith(fontWeight: FontWeight.bold)),
            Text(label, style: Theme.of(context).textTheme.bodySmall),
          ],
        ),
      ),
    );
  }

  Widget _rowCard(Map<String, dynamic> r) {
    final status = '${r['status'] ?? ''}';
    final delay = r['delay_min'];
    final evidence = '${r['evidence'] ?? ''}';
    final color = _statusColor(status);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text('${r['employee'] ?? ''}',
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: color.withAlpha(40),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(status,
                      style: TextStyle(
                          color: color, fontWeight: FontWeight.w600)),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text('${r['route'] ?? ''}'),
            const SizedBox(height: 4),
            Text('Delay: $delay min   -   Verification: ${r['verification'] ?? ''}',
                style: Theme.of(context).textTheme.bodySmall),
            if (evidence.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(evidence),
            ],
            if (r['photo_url'] != null && '${r['photo_url']}'.isNotEmpty) ...[
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.network(
                  Api.instance.resolveUrl('${r['photo_url']}'),
                  height: 160,
                  width: double.infinity,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => const SizedBox.shrink(),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
