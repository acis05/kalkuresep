# KalkuResep Android — WebView

URL: https://kalkuresep.acisapps.id/

## Cara membuat APK (paling mudah)
1. Instal Android Studio (versi terbaru) di Windows/macOS/Linux.
2. Ekstrak ZIP, pilih **Open** dan buka folder `KalkuResep_Android_WebView`.
3. Tunggu Gradle Sync; gunakan JDK 17 yang disertakan Android Studio dan pasang Android SDK 35 jika diminta.
4. Pilih **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
5. File debug APK ada di `app/build/outputs/apk/debug/app-debug.apk` setelah build sukses.
6. Untuk distribusi resmi, pilih **Build > Generate Signed Bundle / APK** dan simpan keystore secara aman.

## Fitur
- WebView HTTPS ke domain KalkuResep; cookie login dan localStorage tersedia.
- Upload foto resep dan file Excel via Android file picker.
- Download file dari URL HTTPS melalui DownloadManager dengan cookie login.
- Tautan di luar domain dibuka di aplikasi eksternal.
- Tombol Back untuk riwayat WebView.
- Tampilan status bar dan ikon bertema Golden Kitchen.

## Catatan penting
- Ini **source project**, bukan APK siap instal. Build belum dapat dijalankan di lingkungan pembuat karena Android SDK/Gradle tidak tersedia.
- DownloadManager menyimpan laporan ke folder khusus aplikasi di penyimpanan eksternal. Akses melalui notifikasi unduhan atau pengelola file yang mendukung folder aplikasi; untuk folder Downloads umum perlu implementasi MediaStore/SAF.
- Jika aplikasi web membuat unduhan memakai `blob:` atau fetch JavaScript, unduhan tersebut belum didukung oleh DownloadManager. Perlu penanganan tambahan setelah diuji di perangkat.
- Aplikasi memerlukan internet dan layanan Railway aktif. Pastikan sertifikat HTTPS valid.
- Uji login, upload, PDF/Excel, logout, dan navigasi pada perangkat sebelum distribusi.


## v2 — Mobile-first + Back Navigation
- Android WebView uses `KalkuResepAndroid/2.0` user-agent suffix.
- Website source v2.14 is REQUIRED for the dedicated Android bottom navigation and SPA history support. Deploy it to Railway first.
- Browser version retains its original UI because the Android CSS is gated by user-agent.
- Hardware Back navigates the SPA menu history. At root, a confirmation dialog asks before exiting.
- Scroll the bottom navigation horizontally for additional menus.
- Test login, recipe editing, file uploads, PDF/Excel downloads and back navigation on a real Android device.
