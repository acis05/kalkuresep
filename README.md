# KalkuResep Cloud v2.0 — Railway Deployment

Aplikasi MVP FastAPI + PostgreSQL, web UI, registrasi/login, cookie session, resep, bahan, scaling, baker %, HPP bahan, paket Free (10 resep) dan Pro (tanpa batas resep), panel admin untuk perubahan paket manual. **Belum ada payment gateway**; harga Pro promo Rp179.000/tahun hanya informasi pemasaran, bukan tagihan otomatis.

## Deploy dari GitHub ke Railway

1. Ekstrak ZIP ini lalu upload **isi folder ini** ke root repository GitHub (pastikan `Dockerfile`, `railway.toml`, `app/`, `static/` ada di root). Jangan upload ZIP-nya sebagai satu file.
2. Railway > **New Project** > **Deploy from GitHub repo**, pilih repository KalkuResep.
3. Railway > **+ New** > **Database** > **PostgreSQL**.
4. Di service aplikasi > **Variables**, buat:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (Railway reference variable; ganti `Postgres` jika nama service database berbeda)
   - `ADMIN_EMAIL` = email admin Anda
   - `ADMIN_PASSWORD` = password acak kuat minimal 16 karakter, unik
   - `COOKIE_SECURE` = `true`
   - `FREE_RECIPE_LIMIT` = `10`
5. Railway > service aplikasi > **Settings / Networking** > **Generate Domain**; setelah berfungsi, pasang custom domain dan DNS sesuai instruksi Railway.
6. Buka `https://domain-anda` dan masuk memakai akun admin yang dibuat dari `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Admin dibuat hanya bila belum ada user dengan email tersebut saat startup.
7. Di panel Admin, ubah paket Free / Pro secara manual. Pembayaran dan masa berlaku Pro belum otomatis ditangani.

**PENTING**: Jika `DATABASE_URL` tidak diset, aplikasi memakai SQLite di filesystem container yang dapat hilang saat redeploy. Untuk Railway publik WAJIB PostgreSQL. Jangan commit file `.env` asli. Pastikan service tidak diberi domain sampai variabel dan database siap.

## Jalankan lokal

```bash
python -m venv .venv
# aktifkan virtual environment sesuai OS
pip install -r requirements.txt
# salin .env.example ke .env lalu muat env variables, atau export variabel secara manual
uvicorn app.main:app --reload
```

Buka http://127.0.0.1:8000. Untuk lokal gunakan `COOKIE_SECURE=false` agar sesi bekerja melalui HTTP.

## Keamanan dan batas MVP

- Data bahan/resep disaring berdasarkan `owner_id` pada backend, bukan hanya frontend.
- Session cookie HttpOnly, SameSite Strict, Secure di HTTPS; middleware memeriksa Origin untuk mutasi dengan cookie.
- **Belum siap untuk peluncuran publik besar**: perlu rate limiting login/registrasi, verifikasi email, reset password, migrasi Alembic, audit log admin, pengujian isolasi data otomatis, backup terjadwal + restore drill, monitoring, dan proteksi abuse.
- Kalkulasi saat ini HPP **bahan saja**; belum termasuk kemasan, overhead dan tenaga kerja.
- Pro saat ini tidak memiliki expiry date atau billing otomatis. Penjualan tahunan memerlukan pencatatan tanggal mulai/berakhir, kebijakan renewal, dan proses penurunan paket.
- Gunakan `DATABASE_URL` private dari service PostgreSQL Railway, bukan URL publik.
- Jangan menggunakan contoh password pada produksi.

## Update v2.1 — HPP lengkap, ekspor, masa aktif Pro

- Tambahan input biaya **kemasan, tenaga kerja, overhead per batch resep dasar**. Saat target scaling berubah, biaya batch ikut dikalikan faktor scaling. Kalkulasi kini mengembalikan `ingredient_cost`, `extra_costs`, `total_cost`, `hpp_per_unit`, margin dan laba per unit.
- Pada hasil kalkulasi, tersedia **Unduh PDF** dan **Unduh Excel**. Export memerlukan login dan memeriksa kepemilikan resep.
- Admin yang memilih paket Pro akan menetapkan **masa aktif 365 hari** dari waktu aktivasi. Setelah tanggal habis, akun otomatis dianggap Free dalam validasi kuota (data resep yang melebihi kuota tidak dihapus). Admin dapat memperpanjang dengan aktivasi ulang. Admin sendiri tidak kedaluwarsa.
- Aplikasi mencoba migrasi tambahan kolom dari database v2.0 saat startup. **Backup PostgreSQL terlebih dahulu sebelum update**; untuk rilis produksi selanjutnya gunakan migrasi Alembic terkelola.
- Rate limit login/registrasi dasar berbasis memori ditambahkan. **Belum cukup untuk multi-replica atau perlindungan serangan publik**; sebelum peluncuran umum gunakan rate limiting berbasis Redis/proxy, verifikasi email, reset password, audit log, observabilitas, dan backup teruji.
- Belum ada pembayaran otomatis, pemberitahuan masa aktif, atau manajemen pajak. Harga promo bukan mekanisme penagihan.

### Upgrade Railway dari v2.0

1. Backup database PostgreSQL dan pastikan variabel `DATABASE_URL` menunjuk ke database persisten yang benar.
2. Upload seluruh source v2.1 ke repository GitHub yang sudah terhubung ke Railway.
3. Railway akan melakukan rebuild; pastikan `health` menjadi sehat.
4. Login sebagai admin, buka panel Admin dan pilih Pro pada akun yang ingin diaktifkan 365 hari.
5. Uji satu resep dengan biaya tambahan, kalkulasi dan ekspor PDF/Excel.

**Perhatian**: Aktivasi Pro v2.0 lama yang tidak memiliki tanggal kedaluwarsa (`NULL`) masih diperlakukan sebagai Pro tanpa kedaluwarsa agar pelanggan lama tidak tiba-tiba turun paket. Admin dapat mengaktifkan ulang untuk menetapkan 365 hari.


## Perubahan v2.2 — Free dan Pro
- Free: maksimal **5 resep**, **30 bahan**, **3 unduhan PDF per bulan kalender UTC**; tidak bisa ekspor Excel, duplikasi resep, riwayat harga, atau membandingkan HPP.
- Pro: resep/bahan tanpa batas kuota produk, ekspor PDF/Excel, duplikasi resep, riwayat harga bahan, dan perbandingan HPP (maksimal 100 resep per permintaan). Fair-use tetap diperlukan pada deployment publik.
- Pembatasan diverifikasi di API. Perubahan harga bahan lewat endpoint PUT akan tercatat pada histori.
- Tabel tambahan dibuat saat startup: `ingredient_price_history` dan `export_usage`. **Backup PostgreSQL sebelum upgrade.**
- Tidak ada payment gateway; admin tetap mengaktifkan Pro manual selama 365 hari.
- Catatan: versi ini MVP, bukan siap produksi publik. Belum ada verifikasi email, reset password, Redis rate limiter, Alembic migrations, maupun backup otomatis.


## Update v2.3 — Login, UI, Admin
- Memperbaiki tombol login/registrasi yang sebelumnya menggunakan `onclick` inline dan diblokir Content-Security-Policy. Semua aksi kini memakai event listener JavaScript eksternal.
- UI modern responsif: halaman autentikasi, sidebar, dashboard, resep, bahan, kalkulator, perbandingan HPP, admin.
- Admin: isi `ADMIN_EMAIL` dan `ADMIN_PASSWORD` (minimal 16 karakter) di Railway Variables **sebelum deploy pertama**. Startup membuat akun admin jika email belum terdaftar. Login memakai email/password itu, kemudian klik **Admin Panel** pada sidebar. Jika email admin sudah terdaftar sebagai pengguna biasa, startup menolak promosi otomatis demi keamanan; gunakan email admin lain atau migrasi akun dengan verifikasi manual.
- `COOKIE_SECURE=true` untuk HTTPS Railway; untuk localhost HTTP gunakan `COOKIE_SECURE=false`.
- Setelah push GitHub, lakukan hard refresh browser (Ctrl+Shift+R) jika masih memuat JS lama.
- Paket Pro 365 hari masih diaktifkan manual setelah pembayaran diverifikasi.
- Perubahan UI ini tidak memerlukan migrasi database tambahan. Backup database sebelum deploy tetap disarankan.
- Catatan keamanan: aplikasi belum dilengkapi verifikasi email, reset password, rate limiting terdistribusi, backup otomatis, dan audit log admin. Jangan menerima pembayaran publik sebelum pengujian dan hardening produksi.

## Pembaruan v2.4 — satuan, konversi dan admin

- Menu **Master Satuan** untuk menambah satuan milik akun sendiri: tentukan jenis (berat/volume/jumlah) dan faktor ke basis **g/ml/pcs**. Contoh `ons` faktor `100` (100 g), `dus` faktor `24` (24 pcs). Tidak bisa mengonversi berat ke volume tanpa data densitas.
- Di **Bahan Baku**, isi **jumlah pembelian**, **satuan beli**, **satuan pakai**, dan **harga total pembelian**. Misal tepung: 1 kg seharga Rp14.000, dipakai 100 g, maka HPP bahan Rp1.400. Baris resep dapat memilih satuan yang kompatibel.
- Data bahan lama dipertahankan: harga lama dianggap harga per 1 satuan lama. Backup database sebelum deploy.
- **Admin bootstrap:** `ADMIN_EMAIL` dan `ADMIN_PASSWORD` minimal 16 karakter, hanya membuat akun admin bila belum ada. Mengubah `ADMIN_PASSWORD` saja tidak mengubah password admin lama.
- Untuk mereset password **akun yang sudah admin**: tambahkan `ADMIN_SYNC_PASSWORD=true` sementara pada Railway Variables dan lakukan redeploy/restart. Setelah berhasil login, **hapus** variabel itu (atau set `false`) agar password tidak terus di-reset pada setiap restart. Ini tidak menaikkan akun biasa menjadi admin. Jangan bagikan password/log ke siapa pun.
- Jika email admin sudah pernah didaftarkan sebagai akun biasa, startup akan gagal dengan pesan konflik. Gunakan `ADMIN_EMAIL` lain yang belum dipakai atau lakukan migrasi terverifikasi. Jangan langsung mengubah role akun biasa dari input publik.
- Untuk login admin, gunakan halaman login biasa. Setelah masuk sebagai admin, menu **Admin Panel** muncul di sidebar.
- Pastikan `DATABASE_URL=${{Postgres.DATABASE_URL}}` mengarah ke database Railway yang benar dan `COOKIE_SECURE=true` untuk HTTPS. Jika login tetap gagal, periksa Railway **Deploy Logs** dan **HTTP Logs**, terutama status 401/403/500; jangan kirim password atau isi secret.

**Batasan:** satuan custom tidak boleh dihapus selama digunakan; harga pembelian dihitung berdasarkan kuantitas kemasan, bukan harga per satuan pakai. Fitur edit satuan/bahan yang sudah tersimpan belum tersedia dalam UI; buat entri baru bila perlu. Gunakan staging dan backup sebelum pembaruan production.


## Perubahan v2.5
- Tombol Edit pada daftar resep, memuat seluruh bahan dan biaya tambahan ke editor, menyimpan melalui PUT, serta tombol Batal Edit.
- Perbandingan HPP: status loading, pesan untuk Free, keadaan kosong, dan tabel perbandingan HPP Pro yang lebih informatif.
- Perbandingan HPP harus memakai satuan hasil yang sebanding; tidak mengubah model database.
- Deploy: backup PostgreSQL, upload isi folder proyek ke root repo GitHub, commit, deploy Railway, lalu hard refresh browser.

## KalkuResep Cloud v2.6 — Fitur Pro baru

Enam fitur Pro: **sub-resep**, **simulasi harga bahan**, **Smart Selling Price**, **analisis food cost**, **laporan profitabilitas**, dan **import Excel via template dengan pratinjau dan konfirmasi**. Fitur lama v2.5 tetap tersedia.

- **Sub-resep**: buka **Resep Saya**, tambah komponen sub-resep (hanya Pro), simpan. Harga dihitung rekursif dari hasil resep anak; siklus ditolak pada kalkulasi. Satuan komponen mengikuti satuan hasil sub-resep.
- **Analisis Bisnis**: sidebar → **Analisis Bisnis** untuk rekomendasi harga jual (simpan), simulasi perubahan harga bahan tanpa menyimpan perubahan, dan laporan profitabilitas/food cost. Laporan menghitung **laba kotor per unit**, bukan laba bersih atau proyeksi volume penjualan.
- **Import Excel**: sidebar → **Import Excel** → unduh template → isi sheet `Bahan` dan `Resep` → unggah `.xlsx` maksimal 2 MB → periksa pratinjau → konfirmasi. Import **hanya menambahkan** data; nama bahan baru harus unik (tidak membedakan kapital). Baris dengan `kode_resep` sama menjadi satu resep. Gunakan satuan dari Master Satuan. Hapus baris contoh sebelum impor data asli.
- Semua endpoint `/api/pro/*` memerlukan sesi Pro aktif, bukan sekadar disembunyikan dari menu Free.
- Database memakai `create_all` untuk tabel baru. **Backup PostgreSQL sebelum upgrade**. Untuk produksi berskala besar, gunakan migrasi Alembic, object storage untuk pratinjau import, Redis untuk rate limiting, serta pengujian keamanan dan backup terjadwal.
- Import Excel menggunakan library `openpyxl` yang sudah tercantum dalam `requirements.txt`.

**Catatan**: `ADMIN_SYNC_PASSWORD=true` hanya sementara untuk reset password akun yang sudah berstatus admin; setelah berhasil, ubah ke `false`.


## v2.7
- Edit master bahan: nama, kuantitas beli, satuan beli/pakai, harga, riwayat perubahan harga.
- Resep dengan bahan terhapus tetap dapat dihitung dari bahan yang masih ada. Respons kalkulasi memuat `missing_ingredients` dan UI memberi peringatan. Nilai HPP setelah penghapusan tidak mencakup bahan hilang dan bukan HPP produksi lengkap.
- Saat edit resep, referensi bahan terhapus dilewati; simpan untuk membersihkannya.


## v2.8 — Simulasi penjualan (Pro)
Di Analisis Bisnis, pilih resep yang sudah memiliki harga jual, isi qty terjual, lalu hitung estimasi omzet, total HPP, laba kotor, margin. Tombol export Excel dan PDF tersedia setelah simulasi berhasil. Angka adalah proyeksi, bukan transaksi aktual. Akses seluruh endpoint dibatasi Pro dan data milik pengguna yang login.
