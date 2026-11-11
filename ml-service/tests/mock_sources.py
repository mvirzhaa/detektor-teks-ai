"""
Server sumber TIRUAN untuk menguji modul Cek Kemiripan tanpa internet.

  python tests/mock_sources.py 8099

Menyediakan:
  /oai                     → endpoint OAI-PMH (seperti OJS) dengan paging resumptionToken
  /article/view/<n>        → halaman artikel dengan meta citation_pdf_url
  /article/download/<n>/1  → PDF artikel
  /sitemap.xml, /berita/n  → situs berita dengan sitemap
  /web/<n>                 → halaman blog (hanya ditemukan lewat pencarian)
  /search?q=..&format=json → pencarian tiruan berformat SearXNG
  /robots.txt              → melarang /rahasia/
"""
import io
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ARTICLES = {
    1: ("Penguatan Pendidikan Karakter di Sekolah Dasar", "Rahmawati, Siti; Pratama, Budi", "2023",
        "Pendidikan karakter merupakan usaha sadar dan terencana untuk menanamkan nilai-nilai kebaikan kepada "
        "peserta didik sehingga mereka mampu bersikap jujur, disiplin, dan bertanggung jawab dalam kehidupan "
        "sehari-hari. Penelitian ini menggunakan pendekatan kualitatif dengan teknik pengumpulan data berupa "
        "observasi, wawancara mendalam, dan studi dokumentasi di tiga sekolah dasar negeri. Hasil penelitian "
        "menunjukkan bahwa keteladanan guru menjadi faktor paling dominan dalam membentuk karakter siswa, "
        "diikuti oleh pembiasaan kegiatan keagamaan setiap pagi dan keterlibatan orang tua dalam memantau "
        "perilaku anak di rumah. Kendala utama yang dihadapi sekolah adalah keterbatasan waktu pembelajaran "
        "serta belum adanya instrumen penilaian sikap yang terstandar sehingga perkembangan karakter siswa "
        "sulit diukur secara objektif. Oleh karena itu sekolah perlu menyusun rubrik penilaian sikap yang "
        "sederhana namun konsisten digunakan oleh seluruh guru kelas."),
    2: ("Sistem Peringatan Dini Banjir Berbasis Internet of Things", "Hidayat, Rizki", "2022",
        "Banjir merupakan bencana yang sering terjadi di wilayah perkotaan akibat tingginya curah hujan dan "
        "buruknya sistem drainase. Penelitian ini merancang sistem peringatan dini banjir menggunakan sensor "
        "ultrasonik untuk mengukur ketinggian air sungai secara berkala. Data ketinggian air dikirim melalui "
        "modul wifi ke server kemudian ditampilkan pada dasbor web dan dikirimkan sebagai notifikasi kepada "
        "warga ketika ketinggian air melewati ambang batas siaga. Pengujian selama tiga puluh hari menunjukkan "
        "tingkat akurasi pengukuran sensor mencapai sembilan puluh enam persen dengan rata-rata waktu "
        "pengiriman notifikasi kurang dari lima detik."),
    3: ("Transformasi Digital UMKM Pascapandemi", "Lestari, Dewi", "2024",
        "Usaha mikro kecil dan menengah dituntut untuk beradaptasi dengan perubahan perilaku konsumen yang "
        "semakin mengandalkan transaksi daring. Studi ini menganalisis faktor yang memengaruhi adopsi "
        "pemasaran digital pada pelaku usaha di Kabupaten Bogor. Hasil analisis regresi menunjukkan bahwa "
        "literasi digital, dukungan komunitas, dan persepsi kemudahan penggunaan berpengaruh signifikan "
        "terhadap keputusan pelaku usaha untuk memasarkan produk melalui media sosial dan lokapasar."),
}
NEWS = {
    1: ("Pemerintah Kota Luncurkan Program Sekolah Ramah Anak", "2026-10-01",
        "Pemerintah kota resmi meluncurkan program sekolah ramah anak yang menyasar seratus sekolah dasar pada "
        "tahun ini. Program tersebut menekankan lingkungan belajar yang aman, bebas perundungan, dan "
        "mendorong partisipasi aktif orang tua dalam kegiatan sekolah. Kepala dinas pendidikan menyampaikan "
        "bahwa evaluasi program akan dilakukan setiap semester dengan melibatkan komite sekolah."),
    2: ("Curah Hujan Tinggi, Warga Diminta Waspada Banjir", "2026-11-02",
        "Badan meteorologi memprakirakan curah hujan tinggi akan terjadi sepanjang pekan depan sehingga warga "
        "yang tinggal di bantaran sungai diminta meningkatkan kewaspadaan terhadap potensi banjir. Petugas "
        "telah menyiagakan pompa air dan perahu karet di sejumlah titik rawan genangan."),
}
WEB = {
    1: ("Pengertian Pembelajaran Daring dan Manfaatnya",
        "Pembelajaran daring adalah proses belajar mengajar yang dilakukan tanpa tatap muka secara langsung "
        "dengan memanfaatkan jaringan internet sebagai media penghubung antara guru dan siswa. Model ini "
        "memberikan fleksibilitas waktu dan tempat sehingga siswa dapat mengakses materi kapan saja, namun "
        "menuntut kemandirian belajar yang tinggi serta ketersediaan perangkat dan kuota yang memadai."),
    2: ("Tips Menjaga Kesehatan Mental Mahasiswa",
        "Menjaga kesehatan mental bagi mahasiswa sama pentingnya dengan menjaga prestasi akademik. Tidur yang "
        "cukup, olahraga ringan secara teratur, dan berbagi cerita dengan teman yang dipercaya dapat membantu "
        "mengurangi tekanan selama masa perkuliahan."),
}
SECRET = "Halaman ini dilarang oleh robots.txt dan tidak boleh diindeks oleh crawler mana pun juga sama sekali."


def make_pdf(title, authors, body):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    buf = io.BytesIO()
    st = getSampleStyleSheet()
    SimpleDocTemplate(buf, pagesize=A4).build([Paragraph(title, st["Title"]), Paragraph(authors, st["Normal"]),
                                               Paragraph(body, st["BodyText"]), Paragraph(body.split(". ")[0] + ".", st["BodyText"])])
    return buf.getvalue()


PDF_CACHE = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def base(self):
        return f"http://{self.headers['Host']}"

    def do_GET(self):  # noqa: C901
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/robots.txt":
            return self.send(200, "User-agent: *\nDisallow: /rahasia/\nSitemap: " + self.base() + "/sitemap.xml\n", "text/plain")
        if p == "/oai":
            return self.oai(q)
        m = re.match(r"^/article/view/(\d+)$", p)
        if m and int(m.group(1)) in ARTICLES:
            n = int(m.group(1))
            t, a, y, body = ARTICLES[n]
            metas = "".join(f'<meta name="citation_author" content="{x.strip()}">' for x in a.split(";"))
            return self.send(200, f"""<html><head><title>{t}</title><meta name="citation_title" content="{t}">{metas}
<meta name="citation_publication_date" content="{y}"><meta name="citation_pdf_url" content="{self.base()}/article/download/{n}/1">
</head><body><nav>Beranda | Arsip | Tentang Jurnal</nav><h1>{t}</h1><p>Abstrak: {body.split('. ')[0]}.</p></body></html>""")
        m = re.match(r"^/article/download/(\d+)/1$", p)
        if m and int(m.group(1)) in ARTICLES:
            n = int(m.group(1))
            if n not in PDF_CACHE:
                PDF_CACHE[n] = make_pdf(ARTICLES[n][0], ARTICLES[n][1], ARTICLES[n][3])
            return self.send(200, PDF_CACHE[n], "application/pdf")
        if p == "/sitemap.xml":
            urls = "".join(f"<url><loc>{self.base()}/berita/{n}</loc><lastmod>{NEWS[n][1]}</lastmod></url>" for n in NEWS)
            urls += f"<url><loc>{self.base()}/rahasia/1</loc></url>"
            return self.send(200, f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                             "application/xml")
        m = re.match(r"^/berita/(\d+)$", p)
        if m and int(m.group(1)) in NEWS:
            t, d, body = NEWS[int(m.group(1))]
            return self.send(200, f"<html><head><title>{t}</title></head><body><header>Portal Berita Kota</header>"
                                  f"<article><h1>{t}</h1><time>{d}</time><p>{body}</p></article><footer>Hak cipta</footer></body></html>")
        m = re.match(r"^/web/(\d+)$", p)
        if m and int(m.group(1)) in WEB:
            t, body = WEB[int(m.group(1))]
            return self.send(200, f"<html><head><title>{t}</title></head><body><article><h1>{t}</h1><p>{body}</p></article></body></html>")
        if p.startswith("/rahasia/"):
            return self.send(200, f"<html><body><p>{SECRET}</p></body></html>")
        if p == "/search":
            return self.search(q)
        return self.send(404, "tidak ditemukan", "text/plain")

    def search(self, q):
        phrase = q.get("q", "").strip('"').lower()
        norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()  # noqa: E731
        res = []
        pages = [(f"/web/{n}", t, b) for n, (t, b) in WEB.items()] + [(f"/berita/{n}", t, b) for n, (t, _, b) in NEWS.items()]
        for path, t, b in pages:
            if norm(phrase) and norm(phrase) in norm(b):
                res.append({"url": self.base() + path, "title": t, "content": b[:150]})
        return self.send(200, json.dumps({"query": phrase, "results": res}), "application/json")

    def oai(self, q):
        frm = q.get("from")
        token = q.get("resumptionToken")
        start = int(token) if token else 1
        ids = [n for n in sorted(ARTICLES) if not frm or ARTICLES[n][2] >= frm[:4]]
        if not ids:
            return self.send(200, '<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                                  '<error code="noRecordsMatch">No records</error></OAI-PMH>', "text/xml")
        page = [n for n in ids if n >= start][:2]
        recs = ""
        for n in page:
            t, a, y, body = ARTICLES[n]
            creators = "".join(f"<dc:creator>{x.strip()}</dc:creator>" for x in a.split(";"))
            recs += f"""<record><header><identifier>oai:mock:article/{n}</identifier><datestamp>{y}-06-01T00:00:00Z</datestamp></header>
<metadata><oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{t}</dc:title>{creators}<dc:date>{y}</dc:date><dc:description>{body.split('. ')[0]}.</dc:description>
<dc:identifier>{self.base()}/article/view/{n}</dc:identifier><dc:relation>{self.base()}/article/view/{n}/1</dc:relation>
</oai_dc:dc></metadata></record>"""
        nxt = [n for n in ids if n > page[-1]]
        tok = f"<resumptionToken>{nxt[0]}</resumptionToken>" if nxt else "<resumptionToken/>"
        return self.send(200, f'<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                              f"<ListRecords>{recs}{tok}</ListRecords></OAI-PMH>", "text/xml")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"Sumber tiruan berjalan di http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
