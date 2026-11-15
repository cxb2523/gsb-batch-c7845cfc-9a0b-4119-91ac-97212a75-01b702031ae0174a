<p align="center">
  <img src="assets/banner.png" alt="BTC Address Sorter by Type banner" width="100%" />
</p>

# ⚡ BTC ADDRESS SORTER – BY TYPE

**BTC Address Sorter by Type** – developed by BitMorphX, is a high-performance BTC address sorter for ultra-large datasets.  
It classifies Bitcoin addresses into Legacy (P2PKH), SegWit (P2SH), and Bech32 (Native SegWit) formats using optimized multiprocessing and low-memory streaming.

---

## ⚙️ Features

- ⚡ Handles 50M+ BTC addresses per run  
- 🧠 Smart detection: P2PKH, P2SH, Bech32  
- 🔄 True multiprocessing (multi-CPU support)  
- 🧵 Adjustable threading  
- 🧊 Very low RAM usage  
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

### Option 1 – Run via Python:
```bash
python btc_address_sorter_by_type.py
```

### Option 2 – Windows launcher:
```cmd
start_btc_address_sorter_by_type.bat
```

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
├── btc_address_sorter_by_type.py
├── start_btc_address_sorter_by_type.bat
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
