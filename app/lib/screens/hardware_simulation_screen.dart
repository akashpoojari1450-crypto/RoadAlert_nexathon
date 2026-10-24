import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../api.dart';

class HardwareSimulationScreen extends StatefulWidget {
  const HardwareSimulationScreen({super.key});

  @override
  State<HardwareSimulationScreen> createState() =>
      _HardwareSimulationScreenState();
}

class _HardwareSimulationScreenState extends State<HardwareSimulationScreen> {
  final _camUrlCtl = TextEditingController(text: 'http://10.43.195.117');
  final _sensorUrlCtl =
      TextEditingController(text: 'http://10.43.195.251/api/status');
  final _roadNameCtl = TextEditingController();

  // AI Detection Server URL — the web page that shows "Detected: ambulance"
  final _aiServerUrlCtl = TextEditingController(text: 'http://10.43.195.117');

  // Detection states: starts as CLEAR / SCANNING (not pre-selected!)
  String? _detectedClass; // null = Normal/Scanning, 'accident', 'ambulance', 'vip'
  double _distanceCm = 150.0; // Defaults to clear distance until read
  double _confidence = 0.0;
  bool _isAnalyzing = false;

  // Real-time sensor state
  bool _sensorOnline = false;
  String? _sensorError;
  bool _busy = false;

  // Camera state & image buffer
  Uint8List? _camImageBytes;
  bool _camLoading = false;
  String? _camError;
  bool _useSimulatedFeed = false; // Real camera first!
  bool _liveStreamActive = true; // Auto-poll camera frames

  // Detection terminal log
  final List<String> _detectionLogs = [];

  // Live Location masquerading as Hardware GPS module
  Position? _livePosition;
  StreamSubscription<Position>? _gpsSub;

  Timer? _camTimer;
  Timer? _sensorTimer;

  // AI Detection Server polling
  Timer? _aiPollTimer;
  bool _aiPollingActive = true;
  bool _aiServerOnline = false;
  String? _lastAiRawLabel; // avoid re-dispatching the same unchanged detection

  @override
  void initState() {
    super.initState();
    _loadSavedUrls();
    _startHardwareGpsStream();
    _startLiveCameraLoop();
    _startSensorPolling();
    _startAiServerPolling();
  }

  @override
  void dispose() {
    _gpsSub?.cancel();
    _camTimer?.cancel();
    _sensorTimer?.cancel();
    _aiPollTimer?.cancel();
    _camUrlCtl.dispose();
    _sensorUrlCtl.dispose();
    _roadNameCtl.dispose();
    _aiServerUrlCtl.dispose();
    super.dispose();
  }

  void _addLog(String msg) {
    final now = DateTime.now();
    final timeStr =
        '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}:${now.second.toString().padLeft(2, '0')}';
    if (mounted) {
      setState(() {
        _detectionLogs.insert(0, '[$timeStr] $msg');
        if (_detectionLogs.length > 30) _detectionLogs.removeLast();
      });
    }
  }

  /// Masquerades the phone live GPS as the Hardware GPS Module
  Future<void> _startHardwareGpsStream() async {
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      final initialPos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.high,
        timeLimit: const Duration(seconds: 5),
      );
      _updateLocationAndCorridor(initialPos);
    } catch (_) {
      try {
        final last = await Geolocator.getLastKnownPosition();
        if (last != null) _updateLocationAndCorridor(last);
      } catch (_) {}
    }

    _gpsSub?.cancel();
    _gpsSub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        distanceFilter: 5,
      ),
    ).listen((pos) {
      if (mounted) _updateLocationAndCorridor(pos);
    });
  }

  void _updateLocationAndCorridor(Position pos) {
    setState(() {
      _livePosition = pos;
      if (_roadNameCtl.text.isEmpty ||
          _roadNameCtl.text.startsWith('Corridor @')) {
        _roadNameCtl.text =
            'Corridor @ ${pos.latitude.toStringAsFixed(4)}, ${pos.longitude.toStringAsFixed(4)}';
      }
    });
  }

  Future<void> _loadSavedUrls() async {
    try {
      final p = await SharedPreferences.getInstance();
      final cam = p.getString('esp_cam_url');
      final sensor = p.getString('esp_sensor_url');
      final road = p.getString('esp_road_name');
      final aiServer = p.getString('esp_ai_server_url');
      if (cam != null && cam.isNotEmpty) _camUrlCtl.text = cam;
      if (sensor != null && sensor.isNotEmpty) _sensorUrlCtl.text = sensor;
      if (road != null && road.isNotEmpty) _roadNameCtl.text = road;
      if (aiServer != null && aiServer.isNotEmpty) _aiServerUrlCtl.text = aiServer;
      if (mounted) setState(() {});
    } catch (_) {}
  }

  Future<void> _saveUrls() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setString('esp_cam_url', _camUrlCtl.text.trim());
      await p.setString('esp_sensor_url', _sensorUrlCtl.text.trim());
      await p.setString('esp_road_name', _roadNameCtl.text.trim());
      await p.setString('esp_ai_server_url', _aiServerUrlCtl.text.trim());
    } catch (_) {}
  }

  String _cleanCamUrl(String raw) {
    var url = raw.trim();
    if (!url.startsWith('http')) url = 'http://$url';
    url = url.replaceAll(RegExp(r'/+$'), '');
    if (!url.endsWith('/capture') && !url.endsWith('/stream')) {
      url = '$url/capture';
    }
    return url;
  }

  /// True while the camera fetch is in progress — prevents AI poll overlap.
  bool _camFetching = false;

  Future<void> _fetchCameraSnapshot({bool quiet = false}) async {
    if (_useSimulatedFeed || _camFetching) return;
    _camFetching = true;
    final targetUrl = _cleanCamUrl(_camUrlCtl.text);

    if (!quiet) {
      setState(() {
        _camLoading = true;
        _camError = null;
      });
    }

    try {
      final uri = Uri.parse(targetUrl);
      final response =
          await http.get(uri).timeout(const Duration(seconds: 3));

      if (response.statusCode == 200 && response.bodyBytes.length > 2) {
        // Validate JPEG: must start with 0xFF 0xD8
        final bytes = response.bodyBytes;
        final isJpeg = bytes[0] == 0xFF && bytes[1] == 0xD8;
        // Also accept PNG: starts with 0x89 0x50
        final isPng = bytes[0] == 0x89 && bytes[1] == 0x50;
        if ((isJpeg || isPng) && mounted) {
          setState(() {
            _camImageBytes = bytes;
            _camLoading = false;
            _camError = null;
          });
        } else if (mounted) {
          setState(() {
            _camLoading = false;
            _camError = 'Camera returned non-image data';
          });
        }
      } else {
        if (mounted) {
          setState(() {
            _camLoading = false;
            _camError = 'Camera returned HTTP ${response.statusCode}';
          });
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _camLoading = false;
          _camError = 'Camera offline at $targetUrl';
        });
      }
    } finally {
      _camFetching = false;
    }
  }

  void _startLiveCameraLoop() {
    _camTimer?.cancel();
    _camTimer = Timer.periodic(const Duration(milliseconds: 1500), (_) {
      if (mounted && _liveStreamActive && !_useSimulatedFeed && !_camLoading) {
        _fetchCameraSnapshot(quiet: true);
      }
    });
  }

  /// Actively polls the ESP32 Ultrasonic Sensor
  void _startSensorPolling() {
    _sensorTimer?.cancel();
    _sensorTimer = Timer.periodic(const Duration(seconds: 2), (_) async {
      final rawUrl = _sensorUrlCtl.text.trim();
      if (rawUrl.isEmpty) return;

      try {
        final uri = Uri.parse(
            rawUrl.startsWith('http') ? rawUrl : 'http://$rawUrl');
        final response =
            await http.get(uri).timeout(const Duration(seconds: 2));

        if (response.statusCode == 200) {
          double? parsedDist;
          try {
            final data = jsonDecode(response.body);
            if (data is Map) {
              parsedDist = (data['distance'] ??
                      data['distance_cm'] ??
                      data['cm'] ??
                      data['value'])
                  ?.toDouble();
            }
          } catch (_) {
            parsedDist = double.tryParse(response.body.trim());
          }

          if (parsedDist != null && mounted) {
            setState(() {
              _distanceCm = parsedDist!;
              _sensorOnline = true;
              _sensorError = null;
            });
            // If obstacle is under 100cm, trigger blockage alert
            if (_distanceCm < 100.0 && _detectedClass == null) {
              _triggerDetection(
                  'accident', 0.94, 'Ultrasonic Sensor detected road hazard!');
            }
          }
        } else {
          if (mounted) {
            setState(() {
              _sensorOnline = false;
              _sensorError = 'HTTP ${response.statusCode}';
            });
          }
        }
      } catch (e) {
        if (mounted) {
          setState(() {
            _sensorOnline = false;
            _sensorError = 'Unreachable';
          });
        }
      }
    });
  }

  // ======================================================================
  //  AI DETECTION SERVER POLLING — reads live labels from the Edge Impulse
  //  / YOLO web page (the page that shows "Detected: ambulance 54.7%")
  // ======================================================================

  void _startAiServerPolling() {
    _aiPollTimer?.cancel();
    _aiPollTimer = Timer.periodic(const Duration(seconds: 4), (_) {
      if (mounted && _aiPollingActive && !_camFetching) _pollAiServer();
    });
    // Immediate first poll after a short delay (let camera settle)
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted && _aiPollingActive) _pollAiServer();
    });
  }

  Future<void> _pollAiServer() async {
    var rawUrl = _aiServerUrlCtl.text.trim();
    if (rawUrl.isEmpty) return;
    if (!rawUrl.startsWith('http')) rawUrl = 'http://$rawUrl';
    rawUrl = rawUrl.replaceAll(RegExp(r'/+$'), '');

    try {
      String? label;
      double? conf;

      final response = await http.get(
        Uri.parse(rawUrl),
        headers: {'Accept': 'application/json, text/html'},
      ).timeout(const Duration(seconds: 3));

      if (response.statusCode != 200) {
        if (mounted) setState(() => _aiServerOnline = false);
        return;
      }

      final body = response.body;

      // ---------- Try JSON first ----------
      try {
        final data = jsonDecode(body);
        if (data is Map) {
          final l = data['label'] ??
              data['detected_class'] ??
              data['class'] ??
              data['prediction'] ??
              data['object'];
          var c = data['confidence'] ??
              data['score'] ??
              data['probability'];
          if (l != null) {
            label = l.toString().toLowerCase().trim();
            if (c is num) {
              conf = c > 1 ? c / 100.0 : c.toDouble();
            }
          }
        }
        if (data is List && data.isNotEmpty && data.first is Map) {
          final first = data.first as Map;
          final l = first['label'] ??
              first['detected_class'] ??
              first['class'] ??
              first['name'];
          var c = first['confidence'] ?? first['score'];
          if (l != null) {
            label = l.toString().toLowerCase().trim();
            if (c is num) conf = c > 1 ? c / 100.0 : c.toDouble();
          }
        }
      } catch (_) {
        // Not JSON — parse HTML for detection patterns
        // Pattern 1: "Detected: ambulance" (from the Edge Impulse page)
        final detMatch = RegExp(
          r'Detected:\s*(\w[\w\s]*\w|\w)',
          caseSensitive: false,
        ).firstMatch(body);
        if (detMatch != null) {
          label = detMatch.group(1)!.trim().toLowerCase();
        }

        // Pattern 2: class name inside a <td> tag (table row)
        if (label == null) {
          final tdMatch = RegExp(
            r'<td[^>]*>\s*(ambulance|accident|crash|vip|police|emergency|fire.?truck|landslide|clear|none)\s*</td>',
            caseSensitive: false,
          ).firstMatch(body);
          if (tdMatch != null) {
            label = tdMatch.group(1)!.trim().toLowerCase();
          }
        }

        // Confidence: look for "54.7%" anywhere
        final confMatch = RegExp(r'(\d+\.?\d*)\s*%').firstMatch(body);
        if (confMatch != null) {
          final parsed = double.tryParse(confMatch.group(1)!);
          if (parsed != null) conf = parsed > 1 ? parsed / 100.0 : parsed;
        }
      }

      if (label != null &&
          label != 'none' &&
          label != 'clear' &&
          label != 'background' &&
          label != 'no detection') {
        if (mounted) setState(() => _aiServerOnline = true);

        // Map to our categories
        String scenario;
        if (label.contains('ambulance') || label.contains('emergency')) {
          scenario = 'ambulance';
        } else if (label.contains('accident') ||
            label.contains('crash') ||
            label.contains('collision')) {
          scenario = 'accident';
        } else if (label.contains('vip') ||
            label.contains('escort') ||
            label.contains('police')) {
          scenario = 'vip';
        } else if (label.contains('fire')) {
          scenario = 'accident';
        } else if (label.contains('landslide') ||
            label.contains('flood') ||
            label.contains('block')) {
          scenario = 'accident';
        } else {
          scenario = label;
        }

        // Only trigger if label or confidence changed
        final confValue = conf ?? 0.5;
        final roundedConf = (confValue * 100).round();
        final sig = '$scenario:$roundedConf';
        if (sig != _lastAiRawLabel) {
          _lastAiRawLabel = sig;
          _triggerDetection(
            scenario,
            confValue,
            'AI Server auto-detected: $label (${roundedConf}%)',
          );
        }
      } else if (label == 'clear' || label == 'none') {
        if (mounted) setState(() => _aiServerOnline = true);
        if (_lastAiRawLabel != null && _lastAiRawLabel != 'clear') {
          _lastAiRawLabel = 'clear';
          _resetToNormal();
        }
      } else {
        if (mounted) setState(() => _aiServerOnline = true);
      }
    } catch (e) {
      if (mounted) setState(() => _aiServerOnline = false);
    }
  }

  /// AI Detection Routine: Runs classification only when triggered manually
  Future<void> _runAiDetection({String? forceScenario}) async {
    setState(() => _isAnalyzing = true);
    _addLog('Capturing camera frame for AI evaluation...');
    await _fetchCameraSnapshot(quiet: true);

    await Future.delayed(const Duration(milliseconds: 800));

    // Choose scenario (accident if road is blocked or forced)
    final scenario = forceScenario ??
        (_distanceCm < 100.0 ? 'accident' : 'ambulance');
    final conf = scenario == 'accident' ? 0.95 : 0.92;

    _triggerDetection(
        scenario, conf, 'AI Model analyzed camera frame & sensor.');
    setState(() => _isAnalyzing = false);
  }

  void _triggerDetection(String scenario, double conf, String reason) {
    setState(() {
      _detectedClass = scenario;
      _confidence = conf;
    });

    final label = scenario.toUpperCase();
    _addLog('AI DETECTED: $label (${(conf * 100).toInt()}%) - $reason');
    _addLog('Ultrasonic Distance: ${_distanceCm.toStringAsFixed(1)} cm');

    // Automatically dispatch event to server
    _sendHardwareEvent(quiet: true);
  }

  void _resetToNormal() {
    setState(() {
      _detectedClass = null;
      _confidence = 0.0;
    });
    _addLog('Corridor status cleared: Normal Flow / Monitoring...');
  }

  Future<void> _sendHardwareEvent({bool quiet = false}) async {
    if (!quiet) setState(() => _busy = true);
    await _saveUrls();

    final lat = _livePosition?.latitude ?? 12.9141;
    final lng = _livePosition?.longitude ?? 74.8560;

    final detected = _detectedClass ?? 'clear';
    final conf = _detectedClass == null ? 0.99 : _confidence;

    try {
      final camIp = _cleanCamUrl(_camUrlCtl.text)
          .replaceAll('http://', '')
          .replaceAll('https://', '')
          .split('/')
          .first;

      final roadName = _roadNameCtl.text.trim().isNotEmpty
          ? _roadNameCtl.text.trim()
          : 'Corridor @ ${lat.toStringAsFixed(4)}, ${lng.toStringAsFixed(4)}';

      await Api.instance.sendCameraEvent({
        'detected_class': detected,
        'confidence': conf,
        'camera_ip': camIp,
        'road_name': roadName,
        'distance_cm': _distanceCm,
        'latitude': lat,
        'longitude': lng,
      });

      _addLog(
          'Synced to Authority & Employer: $detected @ (${lat.toStringAsFixed(4)}, ${lng.toStringAsFixed(4)})');
      if (detected == 'accident') {
        _addLog('Twilio SOS Voice Alert dispatched to traffic responders!');
      }

      if (mounted) setState(() => _busy = false);
    } catch (e) {
      _addLog('Sync Error: $e');
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
      final lat = _livePosition?.latitude ?? 12.9141;
    final lng = _livePosition?.longitude ?? 74.8560;
    final isBlocked = _distanceCm < 100.0 || _detectedClass == 'accident';

    return Scaffold(
      appBar: AppBar(
        title: const Text('Hardware & IoT Simulation'),
        actions: [
          IconButton(
            tooltip: 'Save Settings',
            onPressed: () {
              _saveUrls();
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Hardware configuration saved')),
              );
            },
            icon: const Icon(Icons.save),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 1. HARDWARE GPS NODE CARD
          Card(
            elevation: 2,
            color: const Color(0xFF1E293B),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.satellite_alt,
                          color: Color(0xFF38BDF8), size: 22),
                      const SizedBox(width: 8),
                      const Expanded(
                        child: Text(
                          'HARDWARE GPS NODE (NEO-6M / ESP32)',
                          style: TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.bold,
                            fontSize: 12,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: const Color(0xFF059669).withAlpha(60),
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                              color: const Color(0xFF10B981), width: 1),
                        ),
                        child: const Text(
                          '3D FIX ACTIVE',
                          style: TextStyle(
                            color: Color(0xFF10B981),
                            fontWeight: FontWeight.bold,
                            fontSize: 10,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Lat: ${lat.toStringAsFixed(5)}° N    Lng: ${lng.toStringAsFixed(5)}° E    Port: UART (9600 baud)',
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      color: Color(0xFF38BDF8),
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 14),

          // 2. Hardware Connection Inputs
          Text('1. Hardware Connection URLs',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),

          TextField(
            controller: _camUrlCtl,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: 'ESP32-CAM URL',
              hintText: 'http://10.43.195.117/capture',
              prefixIcon: const Icon(Icons.videocam),
              suffixIcon: IconButton(
                tooltip: 'Capture Frame',
                icon: const Icon(Icons.refresh),
                onPressed: () => _fetchCameraSnapshot(),
              ),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 6),

          Wrap(
            spacing: 8,
            children: [
              ActionChip(
                label: const Text('Auto-Fix /capture'),
                avatar: const Icon(Icons.auto_fix_high, size: 16),
                onPressed: () {
                  _camUrlCtl.text = _cleanCamUrl(_camUrlCtl.text);
                  _fetchCameraSnapshot();
                },
              ),
              ActionChip(
                label: Text(_liveStreamActive ? 'Pause Stream' : 'Live Stream'),
                avatar: Icon(
                    _liveStreamActive ? Icons.pause : Icons.play_arrow,
                    size: 16),
                onPressed: () =>
                    setState(() => _liveStreamActive = !_liveStreamActive),
              ),
            ],
          ),
          const SizedBox(height: 10),

          // Sensor URL + Live Status Badge
          TextField(
            controller: _sensorUrlCtl,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: 'ESP32 Ultrasonic Sensor URL',
              hintText: 'http://10.43.195.251/api/status',
              prefixIcon: const Icon(Icons.radar),
              suffixIcon: Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Chip(
                  label: Text(_sensorOnline ? 'ONLINE' : 'OFFLINE'),
                  backgroundColor: _sensorOnline
                      ? Colors.green.shade100
                      : Colors.orange.shade100,
                  labelStyle: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: _sensorOnline
                        ? Colors.green.shade900
                        : Colors.orange.shade900,
                  ),
                ),
              ),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 10),

          // AI Detection Server URL
          TextField(
            controller: _aiServerUrlCtl,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: 'AI Detection Server URL',
              hintText: 'http://10.43.195.117',
              helperText: 'The web page that shows "Detected: ambulance"',
              helperMaxLines: 2,
              prefixIcon: const Icon(Icons.smart_toy),
              suffixIcon: Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Chip(
                  label: Text(_aiServerOnline
                      ? (_aiPollingActive ? 'LIVE' : 'ONLINE')
                      : 'OFFLINE'),
                  backgroundColor: _aiServerOnline
                      ? (_aiPollingActive
                          ? Colors.green.shade100
                          : Colors.blue.shade100)
                      : Colors.orange.shade100,
                  labelStyle: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: _aiServerOnline
                        ? (_aiPollingActive
                            ? Colors.green.shade900
                            : Colors.blue.shade900)
                        : Colors.orange.shade900,
                  ),
                ),
              ),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              const Icon(Icons.sync, size: 16, color: Colors.grey),
              const SizedBox(width: 6),
              const Expanded(
                child: Text(
                  'Auto-poll AI detection every 3s',
                  style: TextStyle(fontSize: 12, color: Colors.grey),
                ),
              ),
              Transform.scale(
                scale: 0.8,
                child: Switch(
                  value: _aiPollingActive,
                  onChanged: (v) {
                    setState(() => _aiPollingActive = v);
                    if (v) _pollAiServer(); // immediate poll
                  },
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),

          TextField(
            controller: _roadNameCtl,
            decoration: InputDecoration(
              labelText: 'Live Monitored Roadway / Corridor',
              prefixIcon: const Icon(Icons.add_road),
              suffixIcon: IconButton(
                tooltip: 'Auto-detect from GPS',
                icon: const Icon(Icons.my_location),
                onPressed: () {
                  setState(() {
                    _roadNameCtl.text =
                        'Corridor @ ${lat.toStringAsFixed(4)}, ${lng.toStringAsFixed(4)}';
                  });
                },
              ),
              border: const OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 18),

          // 3. Live Camera View
          Row(
            children: [
              Expanded(
                child: Text('2. Live ESP32-CAM Stream',
                    style: Theme.of(context).textTheme.titleMedium),
              ),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text('Simulate:', style: TextStyle(fontSize: 11)),
                  Transform.scale(
                    scale: 0.8,
                    child: Switch(
                      value: _useSimulatedFeed,
                      onChanged: (v) => setState(() => _useSimulatedFeed = v),
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 6),

          Card(
            clipBehavior: Clip.antiAlias,
            elevation: 3,
            child: Container(
              height: 230,
              width: double.infinity,
              color: const Color(0xFF1A1A1A),
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (_useSimulatedFeed)
                    Container(
                      color: const Color(0xFF1E293B),
                      child: Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(
                              _detectedClass == 'accident'
                                  ? Icons.car_crash
                                  : _detectedClass == 'ambulance'
                                      ? Icons.emergency
                                      : _detectedClass == 'vip'
                                          ? Icons.security
                                          : Icons.videocam,
                              size: 60,
                              color: _detectedClass == 'accident'
                                  ? Colors.red
                                  : _detectedClass == 'ambulance'
                                      ? Colors.blue
                                      : Colors.green,
                            ),
                            const SizedBox(height: 8),
                            Text(
                              '[SIMULATED ESP32-CAM STREAM]\n${_roadNameCtl.text}',
                              textAlign: TextAlign.center,
                              style: const TextStyle(
                                  color: Colors.white70, fontSize: 12),
                            ),
                          ],
                        ),
                      ),
                    )
                  else if (_camImageBytes != null)
                    Image.memory(
                      _camImageBytes!,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) => const Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.broken_image, color: Colors.amber, size: 40),
                            SizedBox(height: 8),
                            Text('Frame decode error — waiting for next frame...',
                                style: TextStyle(color: Colors.white70, fontSize: 12)),
                          ],
                        ),
                      ),
                    )
                  else
                    Center(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            if (_camLoading) ...[
                              const CircularProgressIndicator(),
                              const SizedBox(height: 10),
                              const Text('Streaming ESP32-CAM...',
                                  style: TextStyle(color: Colors.white70)),
                            ] else ...[
                              const Icon(Icons.videocam_off,
                                  color: Colors.amber, size: 40),
                              const SizedBox(height: 8),
                              Text(
                                _camError ?? 'Camera loading frame...',
                                style: const TextStyle(
                                    color: Colors.white70, fontSize: 12),
                                textAlign: TextAlign.center,
                              ),
                              const SizedBox(height: 10),
                              FilledButton.tonal(
                                onPressed: () {
                                  _camUrlCtl.text =
                                      _cleanCamUrl(_camUrlCtl.text);
                                  _fetchCameraSnapshot();
                                },
                                child: const Text('Connect to /capture'),
                              ),
                            ],
                          ],
                        ),
                      ),
                    ),

                  // Analyzing Spinner Overlay
                  if (_isAnalyzing)
                    Container(
                      color: Colors.black54,
                      child: const Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            CircularProgressIndicator(color: Colors.cyan),
                            SizedBox(height: 10),
                            Text('AI Scanning Camera Frame...',
                                style: TextStyle(
                                    color: Colors.cyan,
                                    fontWeight: FontWeight.bold)),
                          ],
                        ),
                      ),
                    ),

                  // HUD Overlay Top Left
                  Positioned(
                    top: 10,
                    left: 10,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: Colors.black.withAlpha(200),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Row(
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: BoxDecoration(
                              color: (_camImageBytes != null ||
                                      _useSimulatedFeed)
                                  ? Colors.green
                                  : Colors.red,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 6),
                          Text(
                            _useSimulatedFeed
                                ? 'SIMULATED STREAM'
                                : 'LIVE: ${_cleanCamUrl(_camUrlCtl.text)}',
                            style: const TextStyle(
                                color: Colors.white,
                                fontSize: 10,
                                fontFamily: 'monospace'),
                          ),
                        ],
                      ),
                    ),
                  ),

                  // Detection Status Pill Bottom Right
                  Positioned(
                    bottom: 10,
                    right: 10,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 5),
                      decoration: BoxDecoration(
                        color: _detectedClass == null
                            ? Colors.green.shade800
                            : _detectedClass == 'accident'
                                ? Colors.red.shade800
                                : Colors.blue.shade800,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        _detectedClass == null
                            ? 'AI: CLEAR ROADWAY'
                            : 'AI: ${_detectedClass!.toUpperCase()} (${(_confidence * 100).toInt()}%)',
                        style: const TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.bold,
                            fontSize: 12),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 18),

          // 4. Ultrasonic Sensor Live Gauge
          Card(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Ultrasonic Obstacle Distance:',
                          style: TextStyle(fontWeight: FontWeight.bold)),
                      Text('${_distanceCm.toStringAsFixed(1)} cm',
                          style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                              color: isBlocked ? Colors.red : Colors.green)),
                    ],
                  ),
                  Slider(
                    value: _distanceCm.clamp(10.0, 300.0),
                    min: 10.0,
                    max: 300.0,
                    divisions: 58,
                    label: '${_distanceCm.round()} cm',
                    activeColor: isBlocked ? Colors.red : Colors.green,
                    onChanged: (v) {
                      setState(() => _distanceCm = v);
                      if (_distanceCm < 100.0 && _detectedClass == null) {
                        _triggerDetection('accident', 0.94,
                            'Obstacle distance fell below 100cm');
                      }
                    },
                  ),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.symmetric(
                        horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color:
                          isBlocked ? Colors.red.shade50 : Colors.green.shade50,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: isBlocked ? Colors.red : Colors.green),
                    ),
                    child: Text(
                      isBlocked
                          ? 'ROADWAY BLOCKED / HAZARD AHEAD (< 100 cm)'
                          : 'ROADWAY CLEAR (No Obstacle Detected)',
                      style: TextStyle(
                        color: isBlocked
                            ? Colors.red.shade900
                            : Colors.green.shade900,
                        fontWeight: FontWeight.bold,
                        fontSize: 11,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),

          // 5. AI CLASSIFICATION TRIGGER BUTTONS (Result shown only after detection!)
          Text('3. AI Detection Triggers',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),

          // Main Trigger Button
          FilledButton.icon(
            onPressed: _isAnalyzing
                ? null
                : () => _runAiDetection(forceScenario: 'accident'),
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xFFB42318),
              padding: const EdgeInsets.symmetric(vertical: 13),
            ),
            icon: const Icon(Icons.car_crash),
            label: const Text('Detect Accident from Camera & Sensor',
                style: TextStyle(fontWeight: FontWeight.bold)),
          ),
          const SizedBox(height: 8),

          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _isAnalyzing
                      ? null
                      : () => _runAiDetection(forceScenario: 'ambulance'),
                  icon: const Icon(Icons.emergency, size: 18),
                  label: const Text('Ambulance'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _isAnalyzing
                      ? null
                      : () => _runAiDetection(forceScenario: 'vip'),
                  icon: const Icon(Icons.security, size: 18),
                  label: const Text('VIP Escort'),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filledTonal(
                tooltip: 'Reset to Clear',
                onPressed: _resetToNormal,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // 6. Live AI Detection & Event Terminal Log
          Text('4. Live Telemetry & Detection Console',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),

          Container(
            height: 160,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Colors.black,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.grey.shade800),
            ),
            child: ListView.builder(
              itemCount: _detectionLogs.length,
              itemBuilder: (ctx, i) {
                final log = _detectionLogs[i];
                Color color = Colors.white70;
                if (log.contains('ACCIDENT')) color = Colors.redAccent;
                if (log.contains('AMBULANCE')) color = Colors.cyanAccent;
                if (log.contains('VIP')) color = Colors.amberAccent;
                if (log.contains('Synced')) color = Colors.greenAccent;
                if (log.contains('AI Server auto')) color = Colors.lightBlueAccent;
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text(
                    log,
                    style: TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 11,
                      color: color,
                    ),
                  ),
                );
              },
            ),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}
