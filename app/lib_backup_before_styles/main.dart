import 'package:flutter/material.dart';
import 'api.dart';
import 'screens/login_screen.dart';
import 'screens/home_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Api.instance.load();
  Map<String, dynamic>? user;
  if (Api.instance.token != null) {
    try {
      user = await Api.instance.me();
    } catch (_) {
      user = null;
    }
  }
  runApp(RoadAlertApp(loggedIn: user != null));
}

class RoadAlertApp extends StatelessWidget {
  final bool loggedIn;
  const RoadAlertApp({super.key, required this.loggedIn});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'RoadAlert',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFFE8590C),
        useMaterial3: true,
      ),
      home: loggedIn ? const HomeScreen() : const LoginScreen(),
    );
  }
}
