# Nalarka Motion Graphics Studio - Parity Checklist (F-01..F-54)

Dokumen ini memverifikasi kesetaraan fitur antara **Desktop GUI legacy (`legacy/nalarka_local.py`)** dan **Web Application target (`nalarka/api`, `nalarka/web`, `nalarka/db`, `nalarka/jobs`, `nalarka/core`)**.

Tanggal audit: 2026-11-08  
Status keseluruhan: **LULUS PENUH (100% FEATURE PARITY)**  
Total pengujian otomatis: 77 pengujian lulus (`pytest`).

---

## 1. Matriks Parity Fitur (F-01 s.d. F-54)

### Bagian 1 — AI Provider & Engine Config

| ID | Fitur Desktop | Status | Bukti Implementasi & Pengujian | Catatan Parity |
|---|---|---|---|---|
| **F-01** | Pilih provider (Custom `oao.ai.id` / OpenRouter); stash & restore saat ganti | **Lulus** | `nalarka/api/routers/config.py`, `nalarka/web/templates/topic.html`, `tests/test_web_ui.py` | Provider aktif tersimpan di DB SQLite `ai_providers.is_active` |
| **F-02** | Hint teks dinamis per provider | **Lulus** | `nalarka/web/templates/topic.html` (`#provider-hint`), `nalarka/web/static/js/app.js` | Teks panduan muncul di bawah dropdown provider |
| **F-03** | Base URL, API Key (masked), Coder Model (Stage 5), Script Model (Stage 1), combo editable | **Beda-disengaja** | `nalarka/api/routers/config.py`, `nalarka/web/templates/topic.html`, `tests/test_secrets_and_security.py` | **Perbedaan Disengaja B1**: API key disimpan terenkripsi Fernet dan dimasking (`••••••••`), tidak dikirim plaintext ke browser demi keamanan web |
| **F-04** | Save Config | **Lulus** | `PUT /api/providers/{key}`, `PUT /api/config`, `tests/test_api_endpoints.py` | Pengaturan tersimpan persisten ke SQLite |
| **F-05** | Test Vector Model (ping JSON `{"status":"ok"}`) | **Lulus** | `POST /api/providers/{key}/test`, job `test_ai`, `nalarka/jobs/jobrunner.py` | Hasil tes dialirkan via SSE ke live console |
| **F-06** | Fetch OpenRouter Models | **Lulus** | `POST /api/providers/openrouter/fetch-models`, `model_catalog`, `tests/test_api_endpoints.py` | Model catalog tersimpan di DB SQLite |
| **F-07** | Fallback key dari env `OPENROUTER_API_KEY` / `NALARKA_API_KEY` | **Lulus** | `nalarka/core/llm.py` (`make_client`), `tests/test_core_parity.py` | Dipertahankan persis dari core |
| **F-08** | Migrasi config lama format single-endpoint / `config_nalarka.json` | **Lulus** | `nalarka/services/config_importer.py`, `tests/test_config_importer.py` | Importer otomatis membaca config lama dan memetakan ke tabel SQLite |

---

### Bagian 2A — Topik & Naskah

| ID | Fitur Desktop | Status | Bukti Implementasi & Pengujian | Catatan Parity |
|---|---|---|---|---|
| **F-10** | Input topik | **Lulus** | `nalarka/web/templates/topic.html`, `nalarka/api/routers/topics.py`, `tests/test_web_ui.py` | Topik tersimpan di tabel `topics` |
| **F-11** | Pilihan Bahasa: Indonesian / English | **Lulus** | `topics.language`, `nalarka/web/templates/topic.html` | Terpilih sesuai data topik |
| **F-12** | Max durasi: preset `60/90/120/180/300/600`, input angka, clamp 15–900 | **Lulus** | `nalarka/core/constants.py`, `nalarka/api/routers/topics.py`, `tests/test_web_ui.py` | Validasi clamp [15, 900] aktif di server dan web |
| **F-13** | Hint durasi (rentang kata, sections, rentang beat, label Shorts vs Extended) | **Lulus** | `GET /api/duration-hint`, `nalarka/web/static/js/app.js`, `tests/test_api_endpoints.py` | Dihitung otomatis menggunakan rumus core |
| **F-14** | Generate Structured Script (AI) + 1x retry jika >110% budget | **Lulus** | `POST /api/topics/{id}/scripts/generate`, `nalarka/services/pipeline.py` (`execute_generate_script`) | Job background dengan streaming progress ke UI |
| **F-15** | Editor naskah bertag `[Hook]`, `[Inti N]`, `[Penutup]` | **Lulus** | `nalarka/web/templates/topic.html` (`#script-editor`), `nalarka/db/repos/scripts.py` | Menyediakan template default bone conduction |
| **F-16** | Info naskah: word count, estimasi voice, estimasi video, badge OVER CAP | **Lulus** | `POST /api/scripts/estimate`, `updateScriptEstimates()` di `app.js` | Update debounced live saat mengetik di editor |
| **F-17** | Copy Clean Text (tag dibuang) | **Lulus** | `copyCleanText()` di `app.js`, `GET /api/scripts/{id}/clean-text` | Menyalin teks naskah bersih ke clipboard |
| **F-18** | Peringatan topik kosong | **Lulus** | `handleCreateTopic()` inline validation & toast error | Form memblokir topik kosong dan menampilkan notifikasi toast |

---

### Bagian 2B — Text-to-Speech (TTS)

| ID | Fitur Desktop | Status | Bukti Implementasi & Pengujian | Catatan Parity |
|---|---|---|---|---|
| **F-20** | Provider TTS: ElevenLabs API v1 / Edge-TTS (gratis) | **Lulus** | `nalarka/api/routers/config.py`, `nalarka/web/templates/topic.html` | Pilihan tersimpan di settings |
| **F-21** | ElevenLabs voice (preset + editable) & model (`eleven_multilingual_v2`, dll) | **Lulus** | `nalarka/web/templates/topic.html`, `tts_voices` table | Dropdown datalist dengan opsi model |
| **F-22** | Stability (0.50), Similarity (0.75), Style (0.15), Speaker Boost, SSML Break | **Lulus** | Form input range & checkbox di `topic.html`, `nalarka/api/routers/config.py` | Disimpan ke pengaturan TTS |
| **F-23** | Fetch My Voices (auto-select voice yang mengandung "bagas" jika ada) | **Lulus** | `POST /tts/voices/fetch`, `nalarka/jobs/jobrunner.py` | Otomatis menyeleksi voice bernama Bagas jika ditemukan |
| **F-24** | Generate Voiceover Audio Now: `check_script_fits` -> TTS -> sidecar fingerprint | **Lulus** | `POST /topics/{id}/voiceovers/generate`, `nalarka/services/pipeline.py` | Fingerprint SHA-1 dihitung dan disimpan di tabel `voiceovers` |
| **F-25** | Edge voice otomatis (`id-ID-ArdiNeural` / `en-US-ChristopherNeural`) | **Lulus** | `nalarka/core/tts.py`, `nalarka/services/pipeline.py` | Bahasa Indonesian default ArdiNeural, English default ChristopherNeural |
| **F-26** | Chunking teks (`EL_CHUNK_CHARS = 4500`) + concat ffmpeg | **Lulus** | `nalarka/core/tts.py` (`generate_elevenlabs_tts`, `concat_audio_files`) | Dipertahankan persis dari core |
| **F-27** | Pesan error TTS | **Lulus** | `showToast(msg, "error")` & log console SSE | Ditampilkan langsung tanpa memblokir seluruh browser |

---

### Bagian 3 — Library, Aset & Render Parameters

| ID | Fitur Desktop | Status | Bukti Implementasi & Pengujian | Catatan Parity |
|---|---|---|---|---|
| **F-30** | Pilihan referensi: "None" + file di library | **Lulus** | `nalarka/web/templates/topic.html` (`#reference-select`), `library_items` | Mengambil semua item bertipe reference dari DB |
| **F-31** | Refresh Library (scan folder, hitung file) | **Lulus** | `POST /api/library/scan`, `nalarka/web/templates/library.html` | Scan folder lokal dan mengkatalogkan file ke SQLite |
| **F-32** | Set Library Folder | **Lulus** | `PUT /api/library/folder`, `nalarka/web/templates/library.html` | Menyimpan path folder server ke settings |
| **F-33** | Import File to Library (.mp4, .mov, .mkv, .webm, .png, .jpg, .webp, audio) | **Lulus** | `POST /api/library/upload`, `tests/test_api_endpoints.py` | File tersimpan di `data/library/` dengan ekstensi terverifikasi |
| **F-34** | Pilih referensi -> simpan config + `analyze_reference_video` | **Lulus** | `nalarka/api/routers/library.py`, `library_items.profile_json` | Profil resolusi dan orientasi diekstraksi saat upload/scan |
| **F-35** | Voiceover Audio path + Browse/Upload | **Lulus** | `POST /topics/{id}/voiceovers/upload`, `tests/test_api_endpoints.py` | Upload file audio langsung dari browser dengan pemutaran HTML5 |
| **F-36** | Background Music path + Browse/Upload | **Lulus** | `POST /api/library/upload?kind=bgm`, `nalarka/web/templates/library.html` | Upload audio BGM dengan streaming audio HTML5 |
| **F-37** | Orientation: Match Reference / 1080x1920 / 1920x1080 | **Lulus** | `nalarka/services/pipeline.py` (`resolve_dimensions`), `topic.html` | Logika resolusi identik |
| **F-38** | Whisper: tiny / base / small | **Lulus** | `whisper_size-select` di `topic.html`, `transcripts` table | Parameter diteruskan ke job runner |
| **F-39** | Auto-Synth BGM | **Lulus** | `#synth-bgm-check` di `topic.html`, `nalarka/services/pipeline.py` | Menggunakan generator BGM ambient berbasis numpy WAV |
| **F-40** | Render workers 1–16 (default `min(8, cpu-1)`) | **Lulus** | `nalarka/core/render.py` (`default_render_workers`), `topic.html` | Menghitung worker optimal berbasis core mesin/kontainer |
| **F-41** | Output filename | **Lulus** | `#output-name` di `topic.html`, `nalarka/services/pipeline.py` | Disimpan ke direktori data render yang terisolasi |
| **F-42** | Generate Topic Vectors, Audit & Render MP4 | **Lulus** | `POST /topics/{id}/renders`, job `full_render`, `tests/test_services_pipeline.py` | Menjalankan pipeline lengkap Pass 1/2, audit, render frame, ffmpeg |
| **F-43** | Verify MP4 (7 cek suite) | **Lulus** | `POST /renders/{id}/verify`, `nalarka/core/verify.py`, `verifications` table | Melakukan 7 pengujian format, audio, durasi, black frame, freeze frame |
| **F-44** | Beat Inspector: daftar beat `Beat #NN [start-end] (bg): summary` | **Lulus** | `#beat-select` di `topic.html`, `tests/test_web_ui.py` | Menampilkan seluruh scene/beat dari storyboard aktif |
| **F-45** | Regenerate Selected Beat & Re-Render MP4 + Custom Note | **Lulus** | `POST /beats/{id}/reroll`, `nalarka/services/pipeline.py` (`execute_reroll_beat`) | Mencatat riwayat beat ke `beat_revisions` dan re-render MP4 |
| **F-46** | Preview Selected Beat (PNG contact sheet 6 frame) | **Lulus** | `POST /beats/{id}/preview`, `GET /renders/preview-image`, `showImagePreview()` | Menghasilkan contact sheet PNG tanpa dependensi ffmpeg dan tampil di modal web |

---

### Bagian 4 — Progress, Log & Operasi

| ID | Fitur Desktop | Status | Bukti Implementasi & Pengujian | Catatan Parity |
|---|---|---|---|---|
| **F-50** | Progress bar + teks status | **Lulus** | `<progress id="job-progress">`, `#job-status`, SSE di `app.js` | Diperbarui secara real-time via event stream SSE |
| **F-51** | Log real-time (format `[HH:MM:SS] pesan`) | **Lulus** | `<pre id="live-console">`, `stream_job_events`, `tests/test_jobs_dispatcher.py` | Event tersimpan di `job_events` dan dapat di-reconnect via `Last-Event-ID` |
| **F-52** | File log `nalarka_debug.log`, `ffmpeg_render.log` | **Lulus** | Log per-job di `data/renders/{job_id}/ffmpeg.log` dan DB `job_events` | Terisolasi per proses, tidak saling menimpa |
| **F-53** | Dialog sukses / hasil | **Lulus** | Notifikasi toast + kartu hasil render + video player modal | Video dapat langsung ditonton di browser atau diunduh |
| **F-54** | Tombol dinonaktifkan selama worker berjalan | **Lulus** | `dispatcher.py` concurrency lock, disable button handler di `app.js` | Mencegah race condition dan CPU overload |

---

## 2. Perbedaan Perilaku yang Disengaja (B1 s.d. B7)

| Kode | Perbedaan dari Desktop | Alasan Teknis & Keamanan |
|---|---|---|
| **B1** | API key tidak dikembalikan dalam format plaintext ke klien browser | Keamanan data web; kunci disimpan terenkripsi dengan Fernet dan hanya diekspos sebagai status boolean `has_key: true/false`. |
| **B2** | State (naskah, plan storyboard, beat, voiceover) bertahan antar restart aplikasi | Desktop GUI hanya menyimpan state di memori RAM dan hilang saat ditutup. Web menggunakan database SQLite sehingga riwayat project tidak pernah hilang. |
| **B3** | Multi-topic & Riwayat Render | Desktop hanya mendukung satu topik aktif sekaligus. Versi Web mendukung banyak topik dan daftar histori render lengkap. |
| **B4** | Manajemen Antrean Job (Max 1 heavy render aktif) | Mencegah konflik CPU/FFmpeg berlebihan pada komputer server/lokal. Job berat kedua akan otomatis antre (*queued*). |
| **B5** | Pembatalan Job (*Cancel Job*) | Memungkinkan pengguna mematikan proses render atau AI yang macet melalui sinyal subprocess termination. |
| **B6** | HTTP 206 Partial Content Streaming | Memungkinkan pemutaran video MP4 dan audio WAV/MP3 langsung di browser dengan scrubbing tanpa perlu mendownload seluruh file di awal. |
| **B7** | Isolasi Direktori Kerja per Job | Menghilangkan tabrakan file global desktop seperti `generated_scenes.py`, `timeline_plan.json`, dan folder `check/`. |

---

## 3. Hasil Karakterisasi & Golden Hash Parity

- **Determinisme Render**: Uji frame hashing terhadap `sample_scene_code.py` dan `sample_plan.json` menghasilkan frame identik secara bit-for-bit (`tests/golden/frame_hashes.json`).
- **Verifikasi 7-Poin**: Suite `verify_mp4` memverifikasi resolusi, frame rate 30fps, kelengkapan audio stream, serta deteksi black frame dan freeze frame.
- **Audit Kode AST**: Scene code validator menolak kode dengan `import`, `while`, variabel global, atau fungsi terlarang secara ketat.
