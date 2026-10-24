import 'package:flutter/material.dart';
import '../api.dart';
import 'login_screen.dart';

/// Authority home. The incident list, Confirm/Resolve buttons and monthly
/// reports need new server endpoints, so this is a placeholder for now.
class AuthorityHome extends StatelessWidget {
  const AuthorityHome({super.key});

  Future<void> _logout(BuildContext context) async {
    await Api.instance.logout();
    if (!context.mounted) return;
    Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()));
  }

  @override
  Widget build(BuildContext context) {
    final name = '${Api.instance.user?['username'] ?? ''}';
    return Scaffold(
      appBar: AppBar(
        title: const Text('Traffic authority'),
        actions: [
          IconButton(
              tooltip: 'Log out',
              onPressed: () => _logout(context),
              icon: const Icon(Icons.logout)),
        ],
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.account_balance, size: 56),
              const SizedBox(height: 12),
              Text('Signed in as $name',
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 12),
              const Text(
                'Live incidents, Confirm / Resolve actions and the monthly '
                'report are coming in the next update.',
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
