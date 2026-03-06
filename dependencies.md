# PYTR Dependency Locations

Where to look when upgrading dependencies.

| What | Where to update |
|------|----------------|
| Python packages | `web/requirements.txt` |
| Python base image | `Dockerfile` (FROM line) |
| Deno version | `Dockerfile` (DENO_VERSION env) |
| ffmpeg, adb | `Dockerfile` (apt-get install) |
| Android SDK / Kotlin | `clients/android/build.gradle.kts` |
| WebOS | `clients/webos/` — pure JS, no build deps |
