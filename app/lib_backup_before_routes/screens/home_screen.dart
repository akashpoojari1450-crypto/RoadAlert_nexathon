import 'package:flutter/material.dart';
import '../api.dart';
import 'login_screen.dart';
import 'journey_screen.dart';
import 'report_screen.dart';
import 'alerts_screen.dart';
import 'profile_screen.dart';
import 'live_map_screen.dart';
import 'employer_home.dart';
import 'authority_home.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _tab = 0;
  String? _role;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadRole();
  }

  /// The server tells us what kind of account this is.
  Future<void> _loadRole() async {
    setState(() => _loading = true);
    try {
      final u = Api.instance.user ?? await Api.instance.me();
      _role = u['role'] as String?;
    } catch (_) {
      _role = null;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _logout() async {
    await Api.instance.logout();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()));
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    switch (_role) {
      case 'employer':
        return const EmployerHome();
      case 'authority':
      case 'admin':
        return const AuthorityHome();
      case 'employee':
      case 'passenger':
        return _userTabs();
      default:
        return Scaffold(
          appBar: AppBar(title: const Text('RoadAlert')),
          body: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Could not load your account.'),
                  const SizedBox(height: 12),
                  FilledButton(onPressed: _loadRole, child: const Text('Try again')),
                  TextButton(onPressed: _logout, child: const Text('Log out')),
                ],
              ),
            ),
          ),
        );
    }
  }

  Widget _userTabs() {
    // IndexedStack keeps the journey screen (and its GPS tracking) alive
    // while the user switches tabs.
    return Scaffold(
      appBar: AppBar(
        title: const Text('RoadAlert'),
        actions: [
          IconButton(
              tooltip: 'Log out',
              onPressed: _logout,
              icon: const Icon(Icons.logout)),
        ],
      ),
      body: IndexedStack(index: _tab, children: const [
        JourneyScreen(),
        LiveMapScreen(),
        ReportScreen(),
        AlertsScreen(),
        ProfileScreen(),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.navigation), label: 'Journey'),
          NavigationDestination(icon: Icon(Icons.map), label: 'Map'),
          NavigationDestination(icon: Icon(Icons.warning_amber), label: 'Report'),
          NavigationDestination(
              icon: Icon(Icons.notifications), label: 'Alerts'),
          NavigationDestination(icon: Icon(Icons.person), label: 'Me'),
        ],
      ),
    );
  }
}
