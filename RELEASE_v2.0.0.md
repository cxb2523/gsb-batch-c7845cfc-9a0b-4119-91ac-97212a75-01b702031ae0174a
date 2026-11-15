# 📎 RELEASE NOTES – BTC Address Sorter by Type  
**Version:** 2.0.0  
**Release Date:** 2025-06-25

---

## 🚀 Overview

**BTC Address Sorter by Type** – developed by BitMorphX, is a high-performance BTC address classifier designed for ultra-large Bitcoin address datasets.

The tool scans and classifies Bitcoin addresses (Legacy, P2SH, Bech32) using multiprocessing for maximum performance and low memory footprint — ideal for research, forensic analysis, and blockchain data wrangling.

---

## 🔧 Core Features

- 🧠 Supports all major Bitcoin address types:  
  - Legacy (P2PKH) – starts with `1`  
  - SegWit-wrapped P2SH – starts with `3`  
  - Native SegWit Bech32 – starts with `bc1`  

- 🚀 Fast multiprocessing architecture (Process + Queue)  
- 📈 Real-time progress tracking via `tqdm`  
- 🧵 Adjustable thread count  
- 🎨 CLI color output with `colorama`  
- 🖼️ ASCII branding via `pyfiglet`  
- 🧊 Efficient memory usage – suitable for 50M+ line files  

---

## ✅ Included in v2.0.0

- ✅ Rebranded as **BTC Address Sorter by Type**  
- ✅ `README.md` completely rewritten for clarity & consistency  
- ✅ License changed from **MIT to Apache 2.0**  
- ✅ `.vscode/` folder with development configuration files  
- ✅ `assets/banner.png` added  
- ✅ `RELEASE_v1.0.0.md` and `RELEASE_v2.0.0.md` release notes included  
- ✅ Standardization of file structure and documentation  
- ✅ Removal of deprecated elements (e.g., references to `demo.gif`)  

---

## ⚖️ License Change Notice

As of version 2.0.0, the project is officially licensed under the [Apache 2.0 License](./LICENSE).  
Version 1.0.0 was previously distributed under the MIT license.  
This change ensures clearer permissions and long-term sustainability for open-source contribution.

---

## 🛠 Technical Details

- **Language:** Python 3.8+  
- **Platform:** Cross-platform CLI (Linux, Windows, macOS)  
- **Input:** One address per line in `.txt` format  
- **Output:** Classified `.txt` files by address type  
- **Interface:** Interactive terminal with multiprocessing backend  

---

## 📌 Related Files

- [btc_address_sorter_by_type.py](./btc_address_sorter_by_type.py)  
- [start_btc_address_sorter_by_type.bat](./start_btc_address_sorter_by_type.bat)  
- [.vscode/extensions.json](.vscode/extensions.json)  
- [assets/banner.png](./assets/banner.png)  
- [README.md](./README.md)  
- [LICENSE](./LICENSE) – Apache 2.0  
- [NOTICE](./NOTICE)  
- [ETHICS](./ETHICS.md)  
- [RELEASE_v1.0.0.md](./RELEASE_v1.0.0.md)  
- [RELEASE_v2.0.0.md](./RELEASE_v2.0.0.md)  
- [requirements.txt](./requirements.txt)

---

## 📜 License  
Licensed under the [Apache 2.0 License](./LICENSE) by **BitMorphX**

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
