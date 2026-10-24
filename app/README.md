# RoadAlert app (employee, v0.1)

Screens: sign in, Journey (live GPS upload), Report, Alerts, Me (consent).

## One-time setup (Windows PowerShell)
1. Install Flutter: https://docs.flutter.dev/get-started/install/windows and run `flutter doctor`.
2. In this folder run:

       flutter create . --project-name roadalert_app --org com.roadalert --platforms android
       flutter pub get

   `flutter create .` generates the android/ folder without touching lib/ or pubspec.yaml.
3. Add location permissions to `android/app/src/main/AndroidManifest.xml`,
   inside `<manifest>` and above `<application>`:

       <uses-permission android:name="android.permission.INTERNET"/>
       <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
       <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>

4. Allow plain-HTTP to your dev server: in the same file, add
   `android:usesCleartextTraffic="true"` to the `<application ...>` tag.

## Backend side
Django only accepts localhost by default. Edit `config/settings.py`:

    ALLOWED_HOSTS = ["*"]      # development only

and run the server so other devices can reach it:

    python manage.py runserver 0.0.0.0:8000

## Run
- Android emulator: server address `http://10.0.2.2:8000`
- Real phone on the same Wi-Fi: `http://<your-PC-IP>:8000` (find it with `ipconfig`)
  Allow Python through Windows Firewall if it asks.

Run: `flutter run`

## Test accounts
Employee consent only works for role "employee". The demo seed has demo_alice,
demo_bob, demo_carol (employees, password demo12345) - run
`python manage.py seed_demo --reset --accounts-only` first.
Turn on sharing in the Me tab, start a journey, then watch the employer page
(demo_employer) in the browser.

## Not yet built
Photo/video/voice attachments, place search, background tracking (keep the app
open for now), Firebase push, map view, SOS.
