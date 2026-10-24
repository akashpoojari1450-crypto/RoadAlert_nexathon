import 'dart:io';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
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
  final ImagePicker _picker = ImagePicker();
  XFile? _image;

  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _result;

  Future<void> _pickImage(ImageSource source) async {
    try {
      final img = await _picker.pickImage(
        source: source,
        maxWidth: 1600,
        maxHeight: 1600,
        imageQuality: 80,
      );
      if (img != null) {
        setState(() => _image = img);
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not open camera/gallery: $e')),
      );
    }
  }

  void _showImagePickerOptions() {
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Wrap(
          children: [
            ListTile(
              leading: const Icon(Icons.camera_alt),
              title: const Text('Take a photo with camera'),
              onTap: () {
                Navigator.pop(ctx);
                _pickImage(ImageSource.camera);
              },
            ),
            ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('Choose from photo gallery'),
              onTap: () {
                Navigator.pop(ctx);
                _pickImage(ImageSource.gallery);
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final pos = await currentPosition();
      final r = await Api.instance.report(
        {
          'incident_type': _type,
          'latitude': pos.latitude,
          'longitude': pos.longitude,
          'speed_kmh': pos.speed < 0 ? 0.0 : pos.speed * 3.6,
          if (_road.text.trim().isNotEmpty) 'road_name': _road.text.trim(),
          if (_desc.text.trim().isNotEmpty) 'description': _desc.text.trim(),
        },
        imagePath: _image?.path,
      );
      setState(() {
        _result = r;
        _image = null;
        _road.clear();
        _desc.clear();
      });
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
        const Text('Your live GPS location is attached automatically.'),
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
              labelText: 'Road name (optional)',
              hintText: 'e.g. NH-66 near Airport Road',
              border: OutlineInputBorder()),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _desc,
          maxLines: 2,
          decoration: const InputDecoration(
              labelText: 'Details / Description (optional)',
              border: OutlineInputBorder()),
        ),
        const SizedBox(height: 12),

        // Photo Attachment Section
        if (_image == null) ...[
          OutlinedButton.icon(
            onPressed: _showImagePickerOptions,
            icon: const Icon(Icons.add_a_photo),
            label: const Text('Add Photo Evidence (Verification Level 5)'),
            style: OutlinedButton.styleFrom(
              padding: const EdgeInsets.symmetric(vertical: 14),
            ),
          ),
        ] else ...[
          Card(
            clipBehavior: Clip.antiAlias,
            child: Stack(
              children: [
                Image.file(
                  File(_image!.path),
                  height: 180,
                  width: double.infinity,
                  fit: BoxFit.cover,
                ),
                Positioned(
                  top: 8,
                  right: 8,
                  child: CircleAvatar(
                    backgroundColor: Colors.black54,
                    radius: 18,
                    child: IconButton(
                      icon: const Icon(Icons.close, color: Colors.white, size: 18),
                      onPressed: () => setState(() => _image = null),
                      tooltip: 'Remove photo',
                    ),
                  ),
                ),
                Positioned(
                  bottom: 8,
                  left: 8,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.green.shade800.withAlpha(220),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: const Text(
                      '✓ Photo attached (Level 5 Evidence)',
                      style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],

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
            color: Colors.green.shade50,
            child: ListTile(
              leading: const Icon(Icons.check_circle, color: Colors.green, size: 36),
              title: Text(
                'Report received (${_result!['type_label']})',
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
              subtitle: Text(
                'Verification: Level ${_result!['verification_level']} - ${_result!['verification_label']}\n'
                'Confidence: ${_result!['confidence']}%\n'
                'Visible to Employers and Traffic Authorities.',
              ),
            ),
          ),
        ],
      ],
    );
  }
}
