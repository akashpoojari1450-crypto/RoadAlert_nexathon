import 'package:flutter/material.dart';
import '../api.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  Map<String, dynamic>? _consent;
  String? _error;
  bool _busy = false;

  bool get _isEmployee => Api.instance.user?['role'] == 'employee';

  @override
  void initState() {
    super.initState();
    if (_isEmployee) _load();
  }

  Future<void> _load() async {
    try {
      final c = await Api.instance.consent();
      if (mounted) setState(() => _consent = c);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _toggle(bool value) async {
    if (value) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Share journeys with your employer?'),
          content: const Text(
              'While you are travelling, your employer can see your route status, '
              'delay and the evidence for it. They never see you outside a journey '
              'you start, and personal journeys are never shared. '
              'You can turn this off at any time and it takes effect immediately.'),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(c, false),
                child: const Text('Cancel')),
            FilledButton(
                onPressed: () => Navigator.pop(c, true),
                child: const Text('I agree')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() => _busy = true);
    try {
      final c = await Api.instance.setConsent(value);
      if (mounted) setState(() => _consent = c);
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final u = Api.instance.user ?? {};
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        ListTile(
          leading: const CircleAvatar(child: Icon(Icons.person)),
          title: Text('${u['username'] ?? ''}'),
          subtitle: Text('${u['role'] ?? ''}'
              '${u['company'] != null ? ' · ${u['company']}' : ''}'),
        ),
        const Divider(),
        if (_isEmployee) ...[
          SwitchListTile(
            title: const Text('Share journeys with employer'),
            subtitle: Text(_consent == null
                ? (_error ?? 'Loading…')
                : (_consent!['tracking_consent'] == true
                    ? 'On. Applies to journeys you start from now on.'
                    : 'Off. Your employer cannot see your journeys.')),
            value: _consent?['tracking_consent'] == true,
            onChanged: (_consent == null || _busy) ? null : _toggle,
          ),
        ] else
          const ListTile(
            leading: Icon(Icons.info_outline),
            title: Text('Tracking consent applies to employee accounts only.'),
          ),
        ListTile(
          leading: const Icon(Icons.dns),
          title: const Text('Server'),
          subtitle: Text(Api.instance.baseUrl),
        ),
      ],
    );
  }
}
