import 'package:flutter/material.dart';
import '../api.dart';
import 'login_screen.dart';
import 'journey_screen.dart';
import 'report_screen.dart';
import 'alerts_screen.dart';
import 'profile_screen.dart';
import 'live_map_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _tab = 0;

  Future<void> _logout() async {
    await Api.instance.logout();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const LoginScreen()));
  }

  @override
  Widget build(BuildContext context) {
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
