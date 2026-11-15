## Instalasi
- `npm install`
- `npm start`

## Tes

`npm test` menjalankan test runner bawaan Node (`node --test`) pada
`tests/aggregate.test.js`. Tes memakai situs palsu (injeksi `getData`) sehingga
tidak bergantung pada jaringan, mencakup: situs yang melempar error,
mengembalikan `undefined`, array kosong, semua situs gagal (status 500),
pembuktian eksekusi paralel lewat pengukuran waktu, serta uji level HTTP
untuk route `/all` dan pendaftarannya di manifest `/`.

## Library yang digunakan
- axios
- cheerio
- express
- moment

## API

### Agregasi semua situs

`GET /all` mengambil daftar berita dari kelima situs (Tribunnews, CNN Indonesia,
Tempo, Kompas, Liputan6) secara **bersamaan (concurrent)** dalam satu request.
Fungsi ini hanya memakai ulang `getData` masing-masing situs tanpa menulis ulang
parser-nya.

Respon dikelompokkan per situs di field `results`, dengan ringkasan di `summary`:

- `success`: field `data` berisi array artikel dan `count` jumlahnya.
- `empty`: situs merespons tetapi tidak ada artikel yang bisa diparsing
  (ditandai eksplisit dengan `status: "empty"` dan `message`, bukan dianggap error).
- `error`: field `error` memakai format `message`/`status`/`code` yang sama
  dengan `helpers.errorResult`, lengkap dengan `name` dan `path` situs agar
  mudah dilacak. Ini mencakup situs yang `getData`-nya melempar exception
  maupun yang mengembalikan `undefined`/`null`.

Aturan status HTTP:

- Sebagian situs gagal tetap **200** selama masih ada situs yang sukses/empty.
- Hanya bila kelima situs gagal, endpoint mengembalikan **500** dengan
  rincian error per situs.

Contoh struktur:

```json
{
  "message": "Agregasi daftar berita dari lima situs berita Indonesia",
  "summary": { "total": 5, "success": 3, "empty": 1, "failed": 1, "all_failed": false },
  "results": [
    { "name": "Tribunnews", "path": "/tribunnews", "status": "success", "count": 20, "data": [] },
    { "name": "CNN Indonesia", "path": "/cnnindonesia", "status": "empty", "count": 0, "data": [], "message": "..." },
    { "name": "Tempo", "path": "/tempo", "status": "error", "error": { "message": "...", "status": 503, "code": "SERVICE_UNAVAILABLE" } }
  ]
}
```

Catatan waktu tunggu: agregator tidak menambah lapisan timeout/retry sendiri.
Setiap situs tetap tunduk pada strategi yang sudah ada — `fetchHtml` dengan
timeout 20 detik dan 2 kali retry (empat situs HTML), serta `fetchApi` Tempo
dengan timeout axios 20 detik. Karena request berjalan paralel, total waktu
terbatas oleh situs terlama, bukan jumlah dari kelima situs. Tradeoff-nya:
satu situs yang menggantung tetap bisa menahan request agregat sampai anggaran
timeout/retry situs itu habis, sehingga kebijakan retry tetap terpusat di satu
tempat.

### Daftar endpoint per situs

```json
{
  "message": "Semua data hasil dari scraping",
  "api": [
    {
      "name": "Tribunnews",
      "all": "/tribunnews",
      "section": [
        "/tribunnews/news",
        "/tribunnews/regional",
        "/tribunnews/nasional",
        "/tribunnews/internasional",
        "/tribunnews/bisnis",
        "/tribunnews/sport",
        "/tribunnews/seleb",
        "/tribunnews/lifestyle",
        "/tribunnews/kesehatan"
      ],
      "detail": "/tribunnews/:slug"
    },
    {
      "name": "CNN Indonesia",
      "all": "/cnnindonesia",
      "section": [
        "/cnnindonesia/nasional",
        "/cnnindonesia/internasional",
        "/cnnindonesia/ekonomi",
        "/cnnindonesia/olahraga",
        "/cnnindonesia/hiburan",
        "/cnnindonesia/gaya-hidup",
        "/cnnindonesia/teknologi"
      ],
      "detail": "/cnnindonesia/:slug"
    },
    {
      "name": "Tempo",
      "all": "/tempo",
      "section": [
        "/tempo/nasional",
        "/tempo/dunia",
        "/tempo/metro",
        "/tempo/bisnis",
        "/tempo/olahraga",
        "/tempo/bola",
        "/tempo/gaya",
        "/tempo/travel",
        "/tempo/otomotif",
        "/tempo/tekno"
      ],
      "detail": "/tempo/:slug"
    },
    {
      "name": "Kompas",
      "all": "/kompas",
      "section": [
        "/kompas/megapolitan",
        "/kompas/regional",
        "/kompas/nasional",
        "/kompas/global",
        "/kompas/money",
        "/kompas/bola",
        "/kompas/tekno",
        "/kompas/lifestyle",
        "/kompas/health",
        "/kompas/otomotif"
      ],
      "detail": "/kompas/:slug"
    },
    {
      "name": "Liputan 6",
      "all": "/liputan6",
      "section": [
        "/liputan6/news",
        "/liputan6/crypto",
        "/liputan6/saham",
        "/liputan6/bisnis",
        "/liputan6/bola",
        "/liputan6/showbiz",
        "/liputan6/tekno",
        "/liputan6/cek-fakta",
        "/liputan6/islami",
        "/liputan6/regional"
      ],
      "detail": "/liputan6/:slug"
    }
  ]
}
```

## Format URL Detail

Gunakan nilai `slug` dari response list, lalu gabungkan dengan prefix provider:

| Provider | Contoh |
| --- | --- |
| Tribunnews | `/tribunnews/regional/7906894/aksi-demo-...` |
| CNN Indonesia | `/cnnindonesia/nasional/20261104201643-12-1423405/sidang-militer-...` |
| Tempo | `/tempo/nasional/read/129470/lewat-festival-film-...` |
| Kompas | `/kompas/health/read/26K04094103168/kenapa-usia-30-tahun-...` |
| Liputan 6 | `/liputan6/news/read/8328487/pramono-lantik-3418-pns-...` |

Catatan:

- Route detail Tribunnews mendukung format baru (`/<kanal>/<id>/<slug>`) dan format lama (`/<kanal>/<tahun>/<bulan>/<hari>/<slug>`).
- Route detail Kompas mendukung ID panjang (`/read/2026/11/04/21093051/...`) dan ID pendek seperti kanal health (`/read/26K04094103168/...`).
- Daftar berita Tempo diambil dari `api.tempo.co`. Jika halaman artikel Tempo diblokir Cloudflare, response detail tetap berisi metadata dari API dengan `"partial": true` dan field `note`.

## Support

<a href="https://trakteer.id/bagood/tip" target="_blank"><img id="wse-buttons-preview" src="https://cdn.trakteer.id/images/embed/trbtn-red-1.png" height="40" style="border:0px;height:40px;" alt="Trakteer Saya"></a>