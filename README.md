# Medical OCR Data Extraction

Automated the extraction of medical data from scanned documents using **Optical Character Recognition (OCR)** and regular expressions, enabling efficient data processing and analysis.

## 📄 Overview
This project focuses on automating the extraction of medical data from scanned documents. By leveraging OCR technology, the script reads text from images and applies regular expressions to extract specific data fields. It is designed to handle various types of medical reports, such as IPS (Intravascular Pressure System) and EFR (Electrocardiogram Frequency Response).

## 🛠️ Technical Highlights

- **Programming Language:** Python
- **Libraries:**
  - ```pytesseract```
  - ```Pandas```
  - ```NumPy```
  - ```langdetect```
  - ```re```
- **Reporting:** RMarkdown

## 🧰 Challenges and Solutions

- **Language Variability**: One of the challenges was dealing with documents in multiple languages. The solution was to use langdetect to identify the language and then apply the appropriate regular expressions.
- **Complex Data Fields**: Some medical data fields required intricate regular expressions for accurate extraction. This required a deep understanding of both medical terminologies and regular expression capabilities.
- **Data Quality**: Scanned documents can have varying quality, affecting OCR performance. The project allows for custom Tesseract configurations to handle such cases.

## 🧯 Per-file failure handling (batch resilience)

IPS and EFR are settled **one file at a time**. Each file independently goes
through conversion, OCR/extraction and persistence; a failure only affects that
file and never aborts the batch.

- `ImageConverter.file_to_image` writes each file's pages into a dedicated
  temporary directory (`ocr_convert_*`) and returns the image paths. A failed
  conversion raises `ConversionError` and removes the directory, so an
  unreadable PDF can never leave or reuse a previous file's `IMG_0.png`.
- Files whose detected language is not `FR`/`EN` (or `FR` for EFR, whose
  regexes are French only) are skipped with a recorded reason.
- When a required field is missing (regex miss → `NaN`), the whole file is
  rejected with the list of missing fields instead of crashing on fixed-index
  dict access.
- The name → `UniquePatientID` mapping is shared across all files and both
  pipelines, so multiple reports of the same patient reuse one ID; anonymous
  reports (missing name) get distinct IDs without polluting the mapping.
- Every run also writes a failure manifest next to the success workbooks:
  - `Export/IPS/IPS_FICHIERS_ECHECS.xlsx`
  - `Export/EFR/EFR_FICHIERS_ECHECS.xlsx`

  with columns `FILENAME`, `SOURCE_TYPE` (`IPS`/`EFR`) and `FAILURE_REASON`.
  Re-runs overwrite the exports, so failed files never appear in the success
  tables. Acceptance tests live in `../tests/acceptance_test.py` and stub the
  poppler/tesseract boundary so they run without those binaries.

## 👨‍⚕️ Collaboration and Statistical Analysis

This project involved regular collaboration with medical professionals to identify and prioritize critical data fields relevant for medical diagnoses and treatment plans. Their expertise guided the selection of key metrics to focus on during data extraction and analysis. A comprehensive RMarkdown document was also developed to perform statistical tests on the extracted data, featuring interactive graphs powered by Plotly to aid decision-making. Due to confidentiality, the RMarkdown file is not included in this repository.
