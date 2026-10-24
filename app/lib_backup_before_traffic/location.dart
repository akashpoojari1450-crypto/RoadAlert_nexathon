import 'package:geolocator/geolocator.dart';

/// Asks for permission if needed and returns the current position.
Future<Position> currentPosition() async {
  if (!await Geolocator.isLocationServiceEnabled()) {
    throw Exception('Turn on location (GPS) on your phone.');
  }
  var perm = await Geolocator.checkPermission();
  if (perm == LocationPermission.denied) {
    perm = await Geolocator.requestPermission();
  }
  if (perm == LocationPermission.denied ||
      perm == LocationPermission.deniedForever) {
    throw Exception('Location permission is needed. Enable it in app settings.');
  }
  return Geolocator.getCurrentPosition(
      desiredAccuracy: LocationAccuracy.high);
}
