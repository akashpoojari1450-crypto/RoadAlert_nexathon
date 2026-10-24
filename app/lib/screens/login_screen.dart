import 'package:flutter/material.dart';
import '../api.dart';
import 'home_screen.dart';

/// One login screen for all three kinds of account.
/// The server decides what each account is; this screen only checks that the
/// chosen button matches the account's role.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _server = TextEditingController(text: Api.instance.baseUrl);
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _email = TextEditingController();

  String _choice = 'user'; // user | employer | authority
  bool _signup = false;
  bool _busy = false;
  bool _hidePassword = true;
  String? _error;

  static const _descriptions = {
    'user': 'For employees and passengers.',
    'employer': 'See your employees\' shared journeys.',
    'authority': 'Traffic authority dashboard.',
  };

  bool _roleMatches(String role) {
    switch (_choice) {
      case 'user':
        return role == 'employee' || role == 'passenger';
      case 'employer':
        return role == 'employer';
      case 'authority':
        return role == 'authority' || role == 'admin';
    }
    return false;
  }

  String _choiceFor(String role) {
    switch (role) {
      case 'employer':
        return 'Employer';
      case 'authority':
      case 'admin':
        return 'Authority';
      default:
        return 'User';
    }
  }

  Future<void> _submit() async {
    final username = _username.text.trim();
    final password = _password.text;
    if (username.isEmpty || password.isEmpty) {
      setState(() => _error = 'Enter your username and password.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (_signup) {
        await Api.instance
            .register(_server.text, username, password, _email.text.trim());
      } else {
        await Api.instance.login(_server.text, username, password);
      }
      final role = '${Api.instance.user?['role'] ?? ''}';
      if (!_roleMatches(role)) {
        await Api.instance.logout();
        if (mounted) {
          setState(() => _error =
              'This is not a ${_choice == 'user' ? 'user' : _choice} account. '
              'Choose "${_choiceFor(role)}" above and sign in again.');
        }
        return;
      }
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
          MaterialPageRoute(builder: (_) => const HomeScreen()));
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Icon(Icons.traffic, size: 56),
                  const SizedBox(height: 8),
                  Text('RoadAlert',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.headlineMedium),
                  const SizedBox(height: 20),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'user', label: Text('User')),
                      ButtonSegment(value: 'employer', label: Text('Employer')),
                      ButtonSegment(
                          value: 'authority', label: Text('Authority')),
                    ],
                    selected: {_choice},
                    showSelectedIcon: false,
                    onSelectionChanged: (s) => setState(() {
                      _choice = s.first;
                      if (_choice != 'user') _signup = false;
                      _error = null;
                    }),
                  ),
                  const SizedBox(height: 8),
                  Text(_descriptions[_choice]!,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _server,
                    keyboardType: TextInputType.url,
                    decoration: const InputDecoration(
                      labelText: 'Server address',
                      helperText:
                          'Emulator: http://10.0.2.2:8000 - Real phone: http://<PC-IP>:8000',
                      helperMaxLines: 2,
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _username,
                    autocorrect: false,
                    decoration: const InputDecoration(
                        labelText: 'Username', border: OutlineInputBorder()),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _password,
                    obscureText: _hidePassword,
                    onSubmitted: (_) => _busy ? null : _submit(),
                    decoration: InputDecoration(
                      labelText: 'Password',
                      border: const OutlineInputBorder(),
                      suffixIcon: IconButton(
                        icon: Icon(_hidePassword
                            ? Icons.visibility
                            : Icons.visibility_off),
                        onPressed: () =>
                            setState(() => _hidePassword = !_hidePassword),
                      ),
                    ),
                  ),
                  if (_signup) ...[
                    const SizedBox(height: 12),
                    TextField(
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(
                          labelText: 'Email (optional)',
                          helperText: 'Password needs at least 8 characters.',
                          border: OutlineInputBorder()),
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(_error!, style: TextStyle(color: cs.error)),
                  ],
                  const SizedBox(height: 16),
                  FilledButton(
                    onPressed: _busy ? null : _submit,
                    child: _busy
                        ? const SizedBox(
                            height: 20,
                            width: 20,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : Text(_signup ? 'Create account' : 'Sign in'),
                  ),
                  if (_choice == 'user') ...[
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => setState(() {
                                _signup = !_signup;
                                _error = null;
                              }),
                      child: Text(_signup
                          ? 'I already have an account'
                          : 'Create a passenger account'),
                    ),
                    Text(
                      'Employees are added by their employer. Employer and '
                      'authority accounts are created by the administrator.',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
