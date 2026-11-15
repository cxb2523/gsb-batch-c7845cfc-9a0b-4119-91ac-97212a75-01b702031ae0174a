<p align="center">
  <img src="assets/banner.png" alt="BTC Address Sorter by Type banner" width="100%" />
</p>

# ⚡ BTC ADDRESS SORTER – BY TYPE

**BTC Address Sorter by Type** – developed by BitMorphX, is a high-performance BTC address sorter for ultra-large datasets.  
It classifies Bitcoin addresses into Legacy (P2PKH), SegWit (P2SH), and Bech32 (Native SegWit) formats using optimized multiprocessing and low-memory streaming.

---

## ⚙️ Features

- ⚡ Handles 50M+ BTC addresses per run
- 🌊 True streaming: input is read line by line in fixed-size batches —
  the whole file is never loaded into RAM and worker processes never hold
  the full result set
- 🧠 Smart detection: P2PKH, P2SH, Bech32
- 🔄 True multiprocessing (multi-CPU support, 1–CPU-core clamp)
- 🧵 Adjustable threading
- 🧊 Very low RAM usage
- 💾 Resumable runs via `btc_sorter_progress.json` (append after the last
  committed line; fresh restart with output overwrite when the input changes)
- 🛑 Fail-safe writes: any batch write error stops further writes, keeps
  already written data, leaves progress at the last committed line and
  exits non-zero
- 📊 Real-time progress via `tqdm`
- 🎨 Colorful CLI output with `colorama`
- 🖼️ ASCII art intro with `pyfiglet`

---

## 📁 File Overview

- `btc_address_sorter_by_type.py` – Main sorter script  
- `start_btc_address_sorter_by_type.bat` – Windows launcher  
- `.vscode/`  
  - `settings.json` – Editor config  
  - `launch.json` – Run/debug configuration  
  - `tasks.json` – Task automation  
  - `extensions.json` – Recommended extensions  
- `assets/`  
  - `banner.png` – Project banner  
- `README.md` – This documentation  
- `LICENSE` – Apache 2.0 License  
- `NOTICE` – Legal and attribution info  
- `ETHICS` – Responsible use statement  
- `RELEASE_v1.0.0.md` – Initial release notes  
- `RELEASE_v2.0.0.md` – Current release notes  
- `requirements.txt` – Python packages

---

## 🛠️ Dependencies

```
colorama
tqdm
pyfiglet
```

Install with:

```bash
pip install -r requirements.txt
```

> Python 3.8+ is recommended.

---

## 🚀 Usage

### Option 1 – Run via Python (interactive, original behavior):
```bash
python btc_address_sorter_by_type.py
```
With no CLI arguments the script prompts for the input file and thread
count exactly like the previous version.

### Option 2 – Windows launcher:
```cmd
start_btc_address_sorter_by_type.bat
```

### Option 3 – CLI arguments (recommended for automation / large runs):
```bash
python btc_address_sorter_by_type.py \
  --input btc_address.txt \
  --output-dir . \
  --threads 8 \
  --batch-size 10000
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `-i, --input` | `btc_address.txt` | Input address file, one address per line |
| `-o, --output-dir` | `.` | Directory for the three output files and the progress file |
| `-t, --threads` | CPU count | Worker processes, clamped to `1..CPU cores` |
| `-b, --batch-size` | `10000` | Lines classified and written per batch |
| `-p, --progress-file` | `<output-dir>/btc_sorter_progress.json` | Progress file path |
| `--max-batches N` | `0` (no limit) | Planned stop after N batches; rerun to resume |

---

## 📥 Input Format

Place all addresses in a `.txt` file (e.g. `btc_address.txt`), one per line:

```
1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa
3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy
bc1qw508d6qe...
```

---

## 📤 Output

After processing, the following files are generated:

- `1xxxx.txt` → Legacy P2PKH addresses (`1...`)  
- `3xxxx.txt` → SegWit-wrapped P2SH addresses (`3...`)  
- `bcxxxx.txt` → Native SegWit Bech32 addresses (`bc1...`)

Addresses are appended batch by batch (one address per line, UTF-8).
Empty lines and unrecognized lines are **never** written to an output
file — they are only counted.

---

## 📈 Progress file & resume behavior

Every run maintains `btc_sorter_progress.json` next to the output files.
It is written atomically (temp file + replace) **after each successfully
flushed batch** and contains:

```json
{
  "version": 1,
  "input_file": "C:\\data\\btc_address.txt",
  "input_size": 1234567,
  "input_mtime": 1731234567.89,
  "processed_lines": 50000,
  "input_offset": 1850000,
  "counts": { "1": 20000, "3": 15000, "bc": 15000 },
  "empty_lines": 12,
  "unrecognized_lines": 34,
  "output_offsets": { "1": 720000, "3": 540000, "bc": 590000 },
  "batch_size": 10000,
  "threads": 8,
  "completed": false,
  "updated_at": "2026-11-16T08:00:00+00:00"
}
```

On the next run the script compares the input's **size and modification
time** with the values stored in the progress file and prints the chosen
mode in the terminal:

- **`[MODE] RESUME`** — progress file exists and the input is unchanged:
  processing continues after `processed_lines`, output files are opened
  in append mode (any uncommitted partial tail past the recorded output
  offsets is truncated first, so resume cannot duplicate data).
- **`[MODE] FRESH RESTART`** — no progress file, or the input file changed
  (different size/mtime/path): processing restarts from line 0, the old
  output files are truncated/overwritten and all counters reset. The
  reason is printed alongside the banner.

A run whose progress is already marked `completed` exits immediately
without rewriting anything.

---

## 🛑 Write failures and exit codes

The main process is the only writer. A batch is committed in this order:
write → flush → `fsync` → update progress file. Therefore the progress
file never advances past data that is not durably on disk.

If any write fails (unwritable output directory, disk full, permission
error, …):

1. all further writes stop immediately;
2. already written batches stay on disk untouched;
3. the progress file remains at the last committed batch — the failed
   batch's lines are not counted;
4. the process exits with status **1** (setup errors such as a missing
   input file exit **2**; a successful run exits **0**).

Fix the output location and simply rerun: resume mode picks up exactly
after the last committed line.

Observe the outcome yourself:

```bash
# exit code
echo $?                       # Git Bash / POSIX
echo %ERRORLEVEL%             # Windows cmd

# progress counters after every batch
cat out/btc_sorter_progress.json
```

---

## 🧪 Acceptance tests

The self-contained end-to-end suite (real subprocesses, isolated temp
directories, no extra dependencies) covers normal classification with
blank/illegal lines, resume-append, fresh restart after an input change,
unwritable output paths, simulated mid-run write failure with recovery,
and multiprocessing equivalence:

```bash
python -I tests/test_sorter_acceptance.py
```

The write-failure case uses the test hook `BTC_SORTER_FAIL_AFTER_BATCHES=N`
(fails on the N-th batch) in addition to an unwritable output path.

---

## ⚡ Performance Tips

- Set threads to match your CPU core count  
- SSD is strongly recommended for faster I/O  
- Works on 8GB RAM and higher  
- CLI-first, headless-server ready  

---

## 📂 Project Structure

```text
btc_address_sorter_by_type/
├── assets/
│   └── banner.png
├── .vscode/
│   ├── settings.json
│   ├── launch.json
│   ├── tasks.json
│   └── extensions.json
├── tests/
│   └── test_sorter_acceptance.py   # End-to-end acceptance suite
├── btc_address_sorter_by_type.py
├── start_btc_address_sorter_by_type.bat
├── 1xxxx.txt / 3xxxx.txt / bcxxxx.txt   # Generated outputs
├── btc_sorter_progress.json              # Generated resume state
├── LICENSE
├── NOTICE
├── ETHICS
├── README.md
├── RELEASE_v1.0.0.md
├── RELEASE_v2.0.0.md
└── requirements.txt
```

---

## ⚠️ DISCLAIMER

This software is provided strictly for **educational, analytical, and research purposes only**.

The author **does not promote or condone** any unethical behavior, unauthorized access, or abuse of blockchain systems or cryptographic tools.

This project **does not include or generate any real private keys** linked to actual cryptocurrency holdings.  
It is designed to operate in **offline environments** or for simulation/testing purposes.

**The author accepts no liability** for any damages, losses, or illegal use resulting from this software.  
All responsibility lies solely with the end user.

> **Use responsibly. Learn ethically. Contribute honestly.**

---

## ⚖️ Ethical Use

This tool is created strictly for **research and educational purposes**.  
See [ETHICS](./ETHICS.md) for the full statement.

---

## 📜 License

Licensed under the [Apache 2.0 License](./LICENSE)

---

## 📣 NOTICE

See [`NOTICE`](./NOTICE) for important information about attribution, DMCA protection, and reuse permissions.

---

## 🍱 Support

★ **Bitcoin (BTC)**  
`1MorphXyhHpgmYSfvwUpWojphfLTjrNXc7`

★ **Monero (XMR)**  
`86VAmEogaZF5WDwR3SKtEC6HSEUh6JPA1gVGcny68XmSJ1pYBbGLmdzEB1ZzGModLBXkG3WbRv12mSKv4KnD8i9w7VTg2uu`

★ **Dash (DASH)**  
`XtNuNfgaEXFKhtfxAKuDkdysxUqaZm7TDX`

**We also value early privacy coins such as:**  
★ **Bytecoin (BCN)**  
`bcnZNMyrDrweQgoKH6zpWaE2kW1VZRsX3aDEqnxBVEQfjNnPK6vvNMNRPA4S7YxfhsStzyJeP16woK6G7cRBydZm2TvLFB2eeR`

🙏 *Thank you for supporting independent research and ethical technology.*

---

## 👤 Author & Contact

🔗 GitHub: https://github.com/BitMorphX  
✉️ Email: BitMorphX@proton.me  
💬 Telegram: https://t.me/BitMorphX

> _“I morph bits, not to break, but to understand.”_  
> — **BitMorphX**

---

© BitMorphX – All rights reserved.
