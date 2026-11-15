# 📎 RELEASE NOTES – BTC Address Sorter by Type  
**Version:** 1.0.0  
**Release Date:** 2025-05-13

---

## 🚀 Overview

**BTC Address Sorter by Type** – developed by BitMorphX, is an ultra-fast command-line tool that classifies Bitcoin addresses into types: Legacy (P2PKH), SegWit-wrapped (P2SH), and Bech32 (Native SegWit).

Built for extremely large address lists, this tool enables high-speed classification with low memory usage — making it suitable for blockchain researchers, forensic analysts, and data engineers.

---

## 🔧 Core Features

- 🔍 Full detection support:
  - Legacy (P2PKH) – starts with `1`
  - Nested SegWit (P2SH) – starts with `3`
  - Native SegWit (Bech32) – starts with `bc1`

- 🚀 Streaming-based input (line-by-line, low RAM)
- 🧵 True multiprocessing using `Process` + `Queue`
- 📊 `tqdm`-powered live progress bar
- 🎨 Colorized CLI output using `colorama`
- 🖼️ ASCII banner branding via `pyfiglet`

---

## ✅ Included in v1.0.0

- ✅ Initial implementation of `btc_address_sorter_by_type.py`  
- ✅ Basic Windows launcher: `start_btc_address_sorter_by_type.bat`  
- ✅ CLI-based configuration (file name, thread count)  
- ✅ Output classification into:
  - `1xxxx.txt` (P2PKH)
  - `3xxxx.txt` (P2SH)
  - `bcxxxx.txt` (Bech32)  
- ✅ Dependencies: `colorama`, `pyfiglet`, `tqdm`

---

## 🛠 Technical Details

- **Language:** Python 3.8+  
- **Platform:** Cross-platform CLI (Linux, Windows, macOS)  
- **Input:** One address per line in `.txt` format  
- **Output:** Plain `.txt` files by address type  
- **Interface:** Interactive terminal  

---

## 📌 Related Files

- [btc_address_sorter_by_type.py](./btc_address_sorter_by_type.py) – Main sorting script  
- [start_btc_address_sorter_by_type.bat](./start_btc_address_sorter_by_type.bat) – Windows launcher  
- [requirements.txt](./requirements.txt) – Dependency list  
- [README.md](./README.md) – Project documentation  
- [LICENSE](./LICENSE) – MIT license (v1.0.0)  
- [NOTICE](./NOTICE) – Legal attributions  
- [ETHICS](./ETHICS.md) – Responsible use statement  
- [RELEASE_v1.0.0.md](./RELEASE_v1.0.0.md) – This file  

---

## 📜 License  
Licensed under the **MIT License** for version 1.0.0

> *Note: The license has been changed to Apache 2.0 starting from version 2.0.0.*

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

---

## 👤 Author & Contact

🔗 GitHub: https://github.com/BitMorphX  
✉️ Email: BitMorphX@proton.me  
💬 Telegram: https://t.me/BitMorphX

> _“I morph bits, not to break, but to understand.”_  
> — **BitMorphX**

---

© BitMorphX – All rights reserved.
