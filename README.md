# Detektor Teks AI

Aplikasi web dengan dua modul:

1. **Deteksi AI**: mempelajari dan mendeteksi apakah sebuah teks ditulis oleh AI atau oleh manusia.
2. **Cek Kemiripan** (ala Turnitin, buka `/kemiripan`): membandingkan dokumen dengan **internet** (pencarian web langsung) dan **repositori jurnal/skripsi internal** yang di-crawl **setiap malam**. Lihat [Modul Cek Kemiripan](#modul-cek-kemiripan).

Fitur modul Deteksi AI:

- **Mode Pengecekan**: tempel teks atau unggah dokumen (PDF, DOCX, TXT, MD, HTML). Hasilnya berupa probabilitas buatan AI, putusan, sorotan per bagian teks, indikator gaya bahasa, serta kata/frasa yang paling memengaruhi keputusan.
- **Mode Belajar**: ajari model dengan contoh berlabel (teks, dokumen, atau impor CSV/JSONL). Model **langsung belajar** dari setiap contoh (*incremental learning*) dan **dilatih ulang penuh** secara berkala, sehingga terus berkembang.
- **Umpan balik**: di Mode Cek, hasil yang salah bisa dikoreksi, dan koreksi itu menjadi data latih.
- **Dataset, Model, Riwayat**: kelola data latih, pantau akurasi dan versi model (bisa *rollback*), lihat riwayat pengecekan.

## Arsitektur

```
 Browser ──► web (Node.js / Express) ──► ml-service (Python / FastAPI + scikit-learn)
              │  UI, REST API, upload,        │  ekstraksi dokumen, fitur, prediksi,
              │  token, riwayat, antrean      │  incremental learning, versi model
              ▼                               ▼
            PostgreSQL ◄──────────────────── sim-worker (Python)  ──► internet: API pencarian,
            samples · checks · model_versions │  antrean cek kemiripan      repositori OAI-PMH,
            sim_sources · sim_documents ·     │  crawling setiap malam      sitemap, RSS
            sim_fingerprints · sim_checks …   ▼  penjadwal
```

| Komponen | Teknologi | Tugas |
|---|---|---|
| `web/` | Node.js 22, Express, pg, multer, helmet | Antarmuka web, API publik, validasi, rate limit, token belajar, simpan sampel & riwayat |
| `ml-service/` | Python 3.13, FastAPI, scikit-learn, pypdf, python-docx | Ekstraksi teks dokumen, fitur, model, pelatihan, evaluasi, versi model |
| `sim-worker` | Python (image yang sama), httpx, trafilatura, lxml | Pemeriksaan kemiripan, pencarian web, crawling terjadwal |
| `db` | PostgreSQL 16 | Dataset latih, riwayat, versi model, indeks fingerprint kemiripan |

Layanan ML **tidak dibuka ke luar**; hanya server web yang dapat mengaksesnya.

## Menjalankan

### Dengan Docker (disarankan)

```bash
cp .env.example .env        # ubah POSTGRES_PASSWORD dan isi LEARN_TOKEN untuk produksi
docker compose up -d --build
# buka http://localhost:3000
```

### Tanpa Docker

Butuh Node ≥ 20, Python ≥ 3.11, dan PostgreSQL ≥ 14.

```bash
# 1) Database
createuser detektor -P && createdb detektor -O detektor

# 2) Layanan ML
cd ml-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://detektor:PASSWORD@localhost:5432/detektor
uvicorn app.main:app --port 8000 --workers 1

# 3) Worker Cek Kemiripan (terminal lain, folder ml-service, env sama + SEARCH_PROVIDER dsb.)
python -m app.similarity.worker

# 4) Web (terminal lain)
cd web && npm ci
DATABASE_URL=postgresql://detektor:PASSWORD@localhost:5432/detektor ML_URL=http://localhost:8000 npm start
```

Tabel database dibuat otomatis saat web pertama kali berjalan (`web/src/db/schema.sql` dan `schema-similarity.sql`).

> Layanan ML harus berjalan dengan **1 worker**, karena model aktif disimpan di memori proses. Untuk beban tinggi, pisahkan peran prediksi dan pelatihan, lalu muat ulang model dari file versi.

## Modul Cek Kemiripan

Halaman `/kemiripan` (tombol **Cek Kemiripan** di kanan atas). Cara kerjanya meniru Turnitin.

### Alur pemeriksaan

1. **Kirim dokumen** (PDF/DOCX/TXT/MD/HTML atau tempel teks). Pemeriksaan masuk antrean dan diproses `sim-worker` di latar belakang. Progresnya tampil langsung.
2. Worker memecah dokumen menjadi **shingle 5 kata**, meng-*hash*-nya, lalu memilih fingerprint dengan **winnowing**. Setiap salinan sepanjang ≥ 8 kata dijamin tertangkap.
3. **Indeks lokal**: fingerprint dicocokkan dengan semua dokumen yang pernah di-crawl atau diunggah (repositori jurnal, situs, cache web, tugas mahasiswa).
4. **Pencarian web**: hingga `SEARCH_MAX_QUERIES` kalimat paling khas (tersebar merata di dokumen) dicari sebagai frasa persis. Halaman hasilnya (HTML/PDF) diunduh, diindeks, dan disimpan sebagai cache selama `WEB_CACHE_DAYS` hari.
5. **Penyelarasan** kata per kata dengan setiap kandidat. Perubahan kecil (1–3 kata diganti/disisipkan) tetap dihitung satu kecocokan.
6. **Laporan**:
   - **Indeks Kemiripan** = kata yang cocok ÷ total kata.
   - Persentase per kategori (Sumber internet, Publikasi, Tugas mahasiswa).
   - Daftar **sumber utama**: setiap kata hanya dihitung untuk satu sumber, yaitu sumber terbesar lebih dulu.
   - Sorotan berwarna bernomor di dokumen. Klik untuk melihat teks sumber aslinya dan tautannya.

Di laporan Anda dapat:
- **mengecualikan sumber** (persentase langsung dihitung ulang);
- mengubah filter (kecualikan daftar pustaka/kutipan, panjang kecocokan minimum, jenis sumber) lalu memeriksa ulang. Hasil pencarian web sebelumnya dipakai ulang agar tidak menghabiskan kuota API;
- **mencetak/menyimpan PDF**.

Centang **"Simpan ke repositori tugas"** agar dokumen menjadi pembanding untuk pemeriksaan berikutnya. Fitur ini menangkap salin-menyalin antarmahasiswa, seperti *Student Paper Repository* di Turnitin.

### Sumber data & crawling setiap malam

Di tab **Sumber Data**, tambahkan sumber:

| Jenis | Untuk | Contoh URL |
|---|---|---|
| OAI-PMH | Jurnal **OJS**, repositori **EPrints**/**DSpace** | OJS 3: `https://journal.kampus.ac.id/index.php/<jurnal>/oai` · EPrints: `https://repository.kampus.ac.id/cgi/oai2` · DSpace 7: `https://…/server/oai/request` |
| Sitemap | Situs berita, blog, portal | `https://www.situs.com/` (sitemap dicari dari robots.txt) |
| Telusur web | Situs tanpa sitemap | URL awal + kedalaman (`depth`) |
| RSS/Atom | Feed berita/blog | `https://www.situs.com/feed` |

Unggah manual juga tersedia untuk arsip PDF/DOCX tanpa OAI-PMH.

- **Jadwal**: worker mengantrekan crawl untuk semua sumber berjadwal *setiap malam* pada `CRAWL_TIME` (default 01:00 WIB). Sumber *mingguan* di-crawl pada `CRAWL_WEEKLY_DAY`. Pada waktu yang sama, cache web yang lebih lama dari `WEB_CACHE_DAYS` dibersihkan. Bila worker sempat mati pada jam tersebut, crawl dijalankan begitu worker hidup kembali di hari yang sama.
- **Bertahap**: OAI-PMH hanya meminta rekaman sejak panen terakhir (parameter `from`), sitemap hanya mengambil halaman dengan `lastmod` baru, dan halaman lain diperiksa dengan `ETag`/`Last-Modified`. Rekaman yang dihapus di repositori ikut dihapus dari indeks.
- **Teks lengkap jurnal**: diambil dari PDF artikel lewat meta `citation_pdf_url` (dipakai OJS, EPrints, DSpace). Bila gagal, dipakai judul + abstrak.
- **Batas per malam**: `CRAWL_MAX_DOCS` atau `max_docs` per sumber. Sisanya dilanjutkan malam berikutnya.
- **Sopan & aman**: mematuhi `robots.txt` (RFC 9309) dan *crawl-delay*, memberi jeda per host (`CRAWL_DELAY_SEC`), membatasi ukuran unduhan, dan **menolak alamat IP privat** (anti-SSRF). Untuk repositori di jaringan internal kampus, setel `CRAWL_ALLOW_PRIVATE=true`.
- Konfigurasi lanjutan per sumber (JSON), misalnya `{"set": "jti", "max_docs": 300}`, `{"include": "/berita/", "exclude": "/tag/"}`, `{"depth": 2}`, `{"full_harvest": true}`.
- Setiap crawl tercatat di **Riwayat crawl** lengkap dengan lognya.

### Penyedia pencarian web

Atur `SEARCH_PROVIDER` di `.env`:

| Nilai | Keterangan |
|---|---|
| `brave` | Brave Search API (`BRAVE_API_KEY`). Berbayar per kueri. |
| `serpapi` | Hasil Google via SerpAPI (`SERPAPI_KEY`). Berbayar. |
| `google_cse` | Google Custom Search JSON API. Sudah **tertutup untuk pelanggan baru** dan dijadwalkan **berhenti 1 Januari 2027**. Hanya untuk yang sudah punya kunci. |
| `searxng` | **Gratis**, self-hosted: `docker compose --profile searxng up -d`. Mesin pencari publik di belakangnya dapat membatasi permintaan bila volumenya besar. |
| `none` | Tanpa pencarian web; hanya indeks lokal (repositori + situs yang di-crawl). |

Perkiraan biaya: satu dokumen memakai hingga `SEARCH_MAX_QUERIES` kueri (default 10). Bing Search API tidak didukung karena sudah dihentikan Microsoft pada Agustus 2025.

### Catatan hukum & etika

- Teks dokumen yang di-crawl disimpan di database agar sumber bisa ditampilkan berdampingan di laporan. Crawl hanya sumber yang Anda berhak indeks (repositori sendiri, akses terbuka, atau dengan izin). Cache web dihapus otomatis setelah `WEB_CACHE_DAYS`.
- Menyimpan tugas mahasiswa ke repositori sebaiknya didasari kebijakan institusi dan diketahui mahasiswa.
- Persentase kemiripan **bukan vonis plagiarisme**. Kutipan sah, istilah baku, dan nama lembaga dapat ikut cocok. Selalu tinjau sumber satu per satu.
- Pemeriksa ini mendeteksi **salinan dan parafrase ringan**. Parafrase berat dan terjemahan belum terdeteksi (lihat Pengembangan lanjutan).

## Cara pakai

1. **Mulai dari Mode Belajar.** Sebelum ada minimal 5 contoh per kelas (`MIN_SAMPLES_PER_CLASS`), Mode Cek memakai **heuristik gaya bahasa** sebagai perkiraan kasar dan menampilkan peringatan.
2. Tambahkan contoh **manusia** dan **AI** dalam jumlah seimbang. Untuk dataset besar, gunakan impor CSV/JSONL (lihat `data/template-dataset.csv`) atau CLI.
3. Pantau tab **Model**:
   - *Akurasi online* adalah persentase contoh baru yang ditebak benar **sebelum** dipelajari. Ini ukuran jujur untuk perkembangan model.
   - *Evaluasi data uji* adalah metrik dari latih ulang penuh terakhir (20% data disisihkan sebagai data uji).
   - Versi lama dapat diaktifkan kembali (*rollback*) bila versi baru memburuk.
4. Di **Mode Cek**, gunakan tombol "Sebenarnya buatan AI / manusia" untuk mengoreksi hasil.

### Impor massal via CLI

```bash
# Docker
docker compose cp dataset.csv ml:/tmp/dataset.csv
docker compose exec ml python -m app.cli import /tmp/dataset.csv --text-col text --label-col label

# Tanpa Docker (dari folder ml-service, dengan DATABASE_URL terisi)
python -m app.cli import dataset.csv
python -m app.cli evaluate data-uji-terpisah.csv   # uji model aktif pada data baru
python -m app.cli retrain
python -m app.cli status
```

Kolom teks dikenali otomatis (`text`, `teks`, `content`, `isi`, …). Label juga dikenali otomatis: `ai/manusia`, `ai/human`, `1/0`, `generated`, dll. Duplikat diabaikan berdasarkan hash teks.

## Cara kerja model

**Representasi teks** (seluruhnya *stateless* sehingga mendukung belajar incremental):

1. **N-gram kata (1–2)** via `HashingVectorizer`: pilihan kata dan frasa.
2. **N-gram karakter (3–5)**: morfologi, ejaan, kebiasaan mengetik.
3. **25 fitur stilometri** (`ml-service/app/features.py`), antara lain variasi panjang kalimat (*burstiness*), keragaman kosakata (MATTR), frasa transisi formal, frasa khas AI, bahasa informal/gaul, kata ganti orang pertama, tanda baca, struktur daftar/poin, dan pengulangan frasa. Daftar kata mencakup bahasa Indonesia dan Inggris.

**Klasifier**: `SGDClassifier(loss="log_loss")` (regresi logistik yang mendukung `partial_fit`).

**Belajar incremental**: setiap contoh baru dipelajari bersama *replay* acak contoh lama (16 per kelas) agar model tidak "lupa", dengan bobot kelas seimbang. Dokumen panjang juga dipotong menjadi segmen berlabel sama (augmentasi), karena Mode Cek menilai teks per segmen.

**Latih ulang penuh** (manual, otomatis setiap `AUTO_RETRAIN_EVERY` contoh, atau setelah impor besar):
pembagian latih/uji 80/20 bertingkat → pemilihan `alpha` terbaik pada data validasi → evaluasi (akurasi, presisi, recall, F1, ROC-AUC, matriks kebingungan) → model final dilatih dengan seluruh data → disimpan sebagai versi baru. Contoh yang masuk selama pelatihan berlangsung dipelajari secara incremental sesudahnya.

**Prediksi**: skor dokumen utuh digabung dengan rata-rata skor per segmen (±70 kata). Bila sebagian segmen sangat AI dan sebagian sangat manusia, putusannya **"Campuran"**.

## Membangun dataset yang baik

Model ini hanya sebaik datanya. Saran:

- **Teks manusia**: tulisan yang pasti dibuat sebelum era ChatGPT (sebelum akhir 2022), misalnya arsip tugas/esai/skripsi lama, berita, atau blog lama, dengan izin penggunaan yang jelas.
- **Teks AI**: buat dari **topik dan prompt yang sama** dengan teks manusia, menggunakan **beberapa model AI** (ChatGPT, Gemini, Claude, Llama, dll.) dan berbagai gaya, termasuk yang diminta "menulis seperti mahasiswa". Ini mencegah model hanya belajar membedakan topik.
- Seimbangkan jumlah, panjang, dan topik kedua kelas. Simpan sebagian data sebagai **data uji terpisah** (`python -m app.cli evaluate`).
- Dataset publik (misalnya HC3 atau M4 yang multibahasa) bisa menjadi tambahan. Periksa lisensi dan cakupan bahasanya terlebih dahulu.

## API

| Metode | Endpoint | Keterangan | Token |
|---|---|---|---|
| POST | `/api/check` | Cek teks (`text`) atau dokumen (`file`, multipart) | – |
| POST | `/api/check/:id/feedback` | `{label: "ai"\|"manusia"}` → jadi data latih | ✓ |
| GET | `/api/history`, `/api/history/:id` | Riwayat pengecekan | – |
| POST | `/api/learn/text` | `{text, label, note?}` | ✓ |
| POST | `/api/learn/files` | multipart `files[]` + `label` | ✓ |
| POST | `/api/learn/import` | multipart `file` (CSV/TSV/JSON/JSONL) | ✓ |
| GET | `/api/samples`, `/api/samples/export.csv` | Daftar & ekspor dataset | – |
| PATCH/DELETE | `/api/samples/:id` | Ubah label / hapus sampel | ✓ |
| GET | `/api/model/status`, `/api/model/versions` | Status & versi model | – |
| POST | `/api/model/retrain` | Latih ulang penuh | ✓ |
| POST | `/api/model/versions/:id/activate` | Aktifkan versi (rollback) | ✓ |
| GET | `/api/health`, `/api/stats` | Kesehatan layanan & statistik | – |
| POST | `/api/sim/checks` | Kirim dokumen untuk cek kemiripan (`file`/`text`, `title`, `author`, opsi) | –* |
| GET | `/api/sim/checks`, `/api/sim/checks/:id`, `/api/sim/checks/:id/status` | Daftar, laporan, progres | – |
| POST | `/api/sim/checks/:id/rerun` | Periksa ulang dengan filter baru | – |
| GET/POST/PATCH/DELETE | `/api/sim/sources`, `/api/sim/sources/:id` | Kelola sumber data | ✓ (ubah) |
| POST | `/api/sim/sources/:id/crawl` | Crawl sekarang | ✓ |
| GET | `/api/sim/runs`, `/api/sim/runs/:id` | Riwayat & log crawl | – |
| GET/DELETE | `/api/sim/documents` | Telusuri/hapus dokumen terindeks | ✓ (hapus) |
| POST | `/api/sim/repository/upload` | Unggah dokumen ke repositori internal | ✓ |
| GET | `/api/sim/overview` | Ringkasan indeks, status worker & jadwal | – |

\* Token dibutuhkan bila `save_to_repository=true`. "Token" berarti header `x-learn-token` wajib diisi bila `LEARN_TOKEN` diatur. Di web, token diisi lewat ikon gembok.

## Konfigurasi

Lihat `.env.example`. Yang terpenting: `LEARN_TOKEN` (cegah orang sembarangan "meracuni" model), `AUTO_RETRAIN_EVERY`, `MIN_SAMPLES_PER_CLASS`, dan `STORE_CHECK_TEXT=false` bila teks yang dicek tidak boleh disimpan utuh.

## Pengujian

```bash
cd ml-service
DATABASE_URL=postgresql://detektor:detektor@localhost:5432/detektor_test python -m pytest -q tests
```

Untuk mencoba modul Cek Kemiripan **tanpa internet**, jalankan server sumber tiruan (repositori OJS, situs berita bersitemap, dan mesin pencari berformat SearXNG):

```bash
pip install reportlab
python tests/mock_sources.py 8099
# lalu jalankan worker dengan:
SEARCH_PROVIDER=searxng SEARXNG_URL=http://127.0.0.1:8099 SIM_ALLOW_PRIVATE_FETCH=true python -m app.similarity.worker
# dan tambahkan sumber OAI-PMH http://127.0.0.1:8099/oai serta sitemap http://127.0.0.1:8099/
# (untuk sumber lokal, setel juga CRAWL_ALLOW_PRIVATE=true)
```

Uji ini memakai data sintetis dan hanya memastikan **alurnya** benar (belajar → prediksi → latih ulang → rollback). Uji ini bukan ukuran akurasi.

## Keterbatasan

- **Tidak ada detektor AI yang 100% akurat.** Hasil aplikasi ini adalah *indikasi probabilistik*. Jangan jadikan satu-satunya dasar keputusan akademik atau sanksi; gunakan sebagai bahan percakapan dan pertimbangan bersama bukti lain (riwayat draf, wawancara, dll.).
- Penulis yang gaya tulisnya sangat formal dan rapi dapat salah ditandai sebagai AI. Sebaliknya, teks AI yang sudah diparafrase atau disunting manusia dapat lolos. Perhatikan kartu **matriks kebingungan**, terutama jumlah *manusia yang salah ditandai AI*.
- Teks pendek (< 50 kata) kurang dapat diandalkan.
- PDF hasil scan (gambar) belum didukung; perlu OCR.

## Pengembangan lanjutan

- **Cek Kemiripan**: deteksi parafrase berat dan terjemahan dengan *embedding* kalimat multibahasa + `pgvector`; impor Common Crawl untuk domain `.ac.id`/`.go.id`; integrasi Crossref/OpenAlex untuk metadata jurnal nasional; partisi tabel `sim_fingerprints` bila indeks melewati puluhan juta baris.

- Tambahkan fitur **perplexity** dari model bahasa (misalnya model GPT-2 berbahasa Indonesia). Ini sinyal yang kuat, tetapi membutuhkan PyTorch dan sumber daya lebih besar.
- Setelah dataset mencapai ribuan contoh, *fine-tune* model transformer (misalnya IndoBERT) sebagai model kedua dan gabungkan skornya.
- OCR (Tesseract) untuk PDF hasil scan, autentikasi/SSO untuk pengguna, dan peran admin terpisah untuk Mode Belajar.
