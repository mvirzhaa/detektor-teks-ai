# Panduan Deployment Server (UIKA Bogor)

Panduan ini mendokumentasikan langkah deployment dan konfigurasi **Detektor Teks AI & Cek Kemiripan** pada server:
- **Domain**: `https://utility.uika-bogor.ac.id/detector-text`
- **Reverse Proxy**: Nginx
- **Port Container di Host**: `3002`

---

## 1. Arsitektur Port di Server `utility.uika-bogor.ac.id`

| Port Host | Aplikasi | Path URL |
|---|---|---|
| `3000` | OlahPDF | `https://utility.uika-bogor.ac.id/pdf/` |
| `3001` | Presensi App | `https://utility.uika-bogor.ac.id/presensi` |
| **`3002`** | **Detektor Teks AI** | **`https://utility.uika-bogor.ac.id/detector-text/`** |
| `8080` | UIKA Form (Root) | `https://utility.uika-bogor.ac.id/` |

---

## 2. Langkah Setup Pertama Kali di Server

Masuk sebagai user `root` via SSH:

```bash
# 1. Pindah ke direktori web
cd /var/www

# 2. Clone repositori
git clone https://github.com/mvirzhaa/detektor-teks-ai.git
cd detektor-teks-ai

# 3. Buat file .env dari template
cp .env.example .env
nano .env
```

Pastikan konfigurasi kunci berikut terisi di `.env`:
```env
POSTGRES_DB=detektor
POSTGRES_USER=detektor
POSTGRES_PASSWORD=ganti-password-kuat-anda

# Port 3002 agar tidak bentrok dengan OlahPDF (3000) dan Presensi (3001)
WEB_PORT=3002

# Subpath URL
BASE_PATH=/detector-text

# Token keamanan admin untuk aksi model & sumber data
LEARN_TOKEN=ganti-dengan-token-rahasia-kampus

# Penyedia pencarian web gratis tanpa kuota (SearXNG self-hosted)
SEARCH_PROVIDER=searxng
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=uika-searxng-secret-987654321
BRAVE_API_KEY=
```

---

## 3. Konfigurasi Nginx di Server

Buka file konfigurasi Nginx:
```bash
nano /etc/nginx/sites-enabled/utility.uika-bogor.ac.id
```

Sisipkan blok `# 4. Detektor Teks AI` di atas blok `location /`:

```nginx
    # 4. Detektor Teks AI
    location = /detector-text {
        return 301 /detector-text/;
    }
    location /detector-text/ {
        proxy_pass http://127.0.0.1:3002/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /detector-text;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
```

Uji sintaks dan reload Nginx:
```bash
nginx -t && systemctl reload nginx
```

---

## 4. Menjalankan Layanan Docker

Di dalam direktori `/var/www/detektor-teks-ai`:

```bash
# Build dan jalankan di latar belakang
docker compose up -d --build

# Periksa status semua container
docker compose ps
```

---

## 5. Pembaruan Aplikasi (Update Code)

Setiap ada perubahan kode yang di-push ke branch `main`, cukup jalankan di server:

```bash
cd /var/www/detektor-teks-ai
git pull origin main
docker compose up -d --build
```

---

## 6. Perintah Pemeliharaan & Monitoring

| Kebutuhan | Perintah |
|---|---|
| **Cek status container** | `docker compose ps` |
| **Log API Web** | `docker compose logs -f web` |
| **Log Worker Cek Kemiripan & Crawler** | `docker compose logs -f sim-worker` |
| **Log Layanan ML Python** | `docker compose logs -f ml` |
| **Restart seluruh layanan** | `docker compose restart` |
| **Backup Database** | `docker compose exec -T db pg_dump -U detektor detektor \| gzip > backup_$(date +%F).sql.gz` |

