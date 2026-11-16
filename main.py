import os
import numpy as np
import pandas as pd
from tqdm import tqdm
from PIL import Image
from Converter import ImageConverter, DocToPDF, ConversionError
from Extractor import Extractor, IPSExtractor, EFRExtractor
from DataManager import IPSDataManager, EFRDataManager, UniquePatientID
from Export import Export

desired_width = 320
pd.set_option('display.width', desired_width)
pd.set_option('display.max_columns', None)
pd.set_option("display.max_colwidth", None)

IPS_DIRECTORY = r'C:\Users\33634\Desktop\DONNEES\IPS'
EFR_DIRECTORY = r'C:\Users\33634\Desktop\DONNEES\EFR'
# IPS_DIRECTORY = 'E:/IPS/'
# EFR_DIRECTORY = 'E:/EFR/'
POPPLER_PATH = r'C:\Program Files\poppler-0.68.0\bin'
TESSERACT_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

LANGUAGES = ['FR', 'EN']

# Columns of the failure manifest exported next to the result workbooks.
FAILURE_COLUMNS = ['FILENAME', 'SOURCE_TYPE', 'FAILURE_REASON']

# (index in the per-file extracted dict list, keys that must be present
# and non-empty). A file missing any of these is rejected, not partially loaded.
IPS_REQUIRED_FIELDS = [
    (0, ['PATIENT_NAME']),
    (1, ['DATE_NAISSANCE', 'AGE', 'SEXE']),
    (2, ['RAPPORT_ID']),
    (3, ['DATE_TEST']),
    (4, ['IPS_D', 'IPS_G']),
    (5, ['BRAS_SYS', 'BRAS_DIA']),
    (6, ['BPM']),
]

EFR_REQUIRED_FIELDS = [
    (0, ['PATIENT_NAME']),
    (1, ['AGE', 'SEXE']),
    (3, ['DATE_TEST']),
    (4, ['PARAM_1']),
    (5, ['PRE_1']),
    (6, ['POST_1']),
]


def _is_missing(value):
    """True for None / NaN / pandas NA (regex misses come back as np.nan)."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _find_missing_fields(record_maps, required_fields):
    """Return labels of required fields absent or NaN in the extracted data."""
    missing = []
    for index, keys in required_fields:
        for key in keys:
            if index >= len(record_maps) or key not in record_maps[index] \
                    or _is_missing(record_maps[index][key]):
                missing.append(f'{key}@[{index}]')
    return missing


def _record_failure(failures, filename, source_type, reason):
    failures.append({
        'FILENAME': filename,
        'SOURCE_TYPE': source_type,
        'FAILURE_REASON': reason,
    })
    print(f'[!] {source_type} - {filename}: {reason}')


def process_ips(ips_directory, image_converter, extractor, ips_extractor,
                ips_data_manager, unique_id, all_patients_id, export):
    """Process every IPS PDF independently; one file's failure never aborts the batch."""
    if not os.path.isdir(ips_directory):
        raise FileNotFoundError(f'IPS directory does not exist: {ips_directory}')

    ips_all_files = sorted(f for f in os.listdir(ips_directory)
                           if f.lower().endswith('pdf'))
    failures = []
    general_rows = []
    detailed_rows = []

    for filename in tqdm(ips_all_files, desc='[+] Extraction IPS', total=len(ips_all_files)):
        f = os.path.join(ips_directory, filename)
        # Every conversion gets its own fresh temporary directory, so the
        # images can only belong to this file (no IMG_0.png residue).
        image_paths = []
        try:
            image_paths = image_converter.file_to_image(file_path=f)
            img = Image.open(image_paths[0])
            language = extractor.detect_language(image=img)

            if language not in LANGUAGES:
                _record_failure(failures, filename, 'IPS',
                                f'Unsupported language: {language} (only FR/EN are handled)')
                continue

            current_patient_data = []
            for item in ips_extractor.regex_roi_dict:
                roi_image = img.crop(ips_extractor.regex_roi_dict[item]['ROI'])
                results = extractor.extract_data_from_roi(
                    roi_image=roi_image, item=item, language=language,
                    extractor_obj=ips_extractor)
                current_patient_data.append(results)

            # Reject the whole file (with the list of missing fields) instead
            # of indexing into NaN dicts and crashing the whole batch.
            missing = _find_missing_fields(current_patient_data, IPS_REQUIRED_FIELDS)
            if missing:
                _record_failure(failures, filename, 'IPS',
                                'Missing fields: ' + ', '.join(missing))
                continue

            # Check if patient is already in data (mapping shared across files
            # and across the IPS/EFR pipelines):
            try:
                patient_name = current_patient_data[0]['PATIENT_NAME'].upper().replace(' ', '')
            except AttributeError:
                patient_name = np.nan
            patient_id = unique_id.assign_patient_id(
                patient_name=patient_name,
                all_patients_id=all_patients_id
            )

            # Add unique ID value and filename:
            current_patient_data[0]['PATIENT_ID'] = patient_id
            current_patient_data[0]['FILENAME'] = filename

            # General information row:
            general_rows.append({
                'FILENAME': current_patient_data[0]['FILENAME'],
                'PATIENT_NAME': current_patient_data[0]['PATIENT_NAME'],
                'PATIENT_ID': current_patient_data[0]['PATIENT_ID'],
                'DATE_NAISSANCE': current_patient_data[1]['DATE_NAISSANCE'],
                'AGE': current_patient_data[1]['AGE'],
                'SEXE': current_patient_data[1]['SEXE'],
                'RAPPORT_ID': current_patient_data[2]['RAPPORT_ID'],
                'DATE_TEST': current_patient_data[3]['DATE_TEST'],
            })

            # Detailed information row:
            detailed_dict = {}
            for d in current_patient_data[3:]:
                detailed_dict.update(d)
            detailed_dict.update({'PATIENT_ID': patient_id, 'BORNE_INF': 1.0, 'BORNE_SUP': 1.4})
            detailed_rows.append(detailed_dict)

        except ConversionError as exc:
            _record_failure(failures, filename, 'IPS', str(exc))
        except Exception as exc:  # isolate any per-file failure
            _record_failure(failures, filename, 'IPS',
                            f'Unexpected error: {type(exc).__name__}: {exc}')
        finally:
            # Always remove this file's images, success or failure.
            image_converter.cleanup_images(image_paths)

    ips_general_info_df = pd.DataFrame(general_rows,
                                       columns=ips_data_manager.df_general.columns)
    ips_detailed_info_df = pd.DataFrame(detailed_rows,
                                        columns=ips_data_manager.df_detailed.columns)
    ips_failures_df = pd.DataFrame(failures, columns=FAILURE_COLUMNS)

    ips_general_info_df = ips_data_manager.format_general_table(df=ips_general_info_df)
    ips_detailed_info_df = ips_data_manager.format_detailed_table(df=ips_detailed_info_df)

    # Export IPS (success tables keep their original structure, plus a failure manifest):
    all_ips_dfs = {
        'IPS_INFORMATIONS_GENERALES': (ips_general_info_df, True, {'F': 30}),
        'IPS_VALEURS_TESTS': (ips_detailed_info_df, False),
        'IPS_FICHIERS_ECHECS': (ips_failures_df, False),
    }
    export.export_data(all_ips_dfs)

    return {
        'source': 'IPS',
        'total': len(ips_all_files),
        'success': len(general_rows),
        'failed': len(failures),
        'failures_df': ips_failures_df,
    }


def process_efr(efr_directory, efr_pdf_files, image_converter, extractor, efr_extractor,
                efr_data_manager, unique_id, all_patients_id, export):
    """Process every EFR PDF independently; one file's failure never aborts the batch."""
    efr_pdf_files = sorted(efr_pdf_files)
    failures = []
    general_rows = []
    detailed_rows = []

    for filename in tqdm(efr_pdf_files, desc='[+] Extraction EFR', total=len(efr_pdf_files)):
        f = os.path.join(efr_directory, filename)
        image_paths = []
        try:
            image_paths = image_converter.file_to_image(file_path=f)
            img = Image.open(image_paths[0])
            language = extractor.detect_language(image=img)

            if language not in LANGUAGES:
                _record_failure(failures, filename, 'EFR',
                                f'Unsupported language: {language} (only FR/EN are handled)')
                continue
            if language != 'FR':
                # The EFR extractor only defines French regexes (REGEX_FR).
                _record_failure(failures, filename, 'EFR',
                                f'Language {language} not supported by the EFR extractor (only FR)')
                continue

            current_patient_data = []

            # Création du tableau des informations générales du patient:
            for item in efr_extractor.regex_roi_dict:
                roi_image = img.crop(efr_extractor.regex_roi_dict[item]['ROI'])
                results = extractor.extract_data_from_roi(
                    roi_image=roi_image, item=item, language=language,
                    extractor_obj=efr_extractor,
                    dotall=True if item == 'INTERPRETATION' else False)
                current_patient_data.append(results)

            # Merge of 'NOM' and 'PRENOM':
            for dict_data in current_patient_data:
                if 'NOM' in dict_data and 'PRENOM' in dict_data:
                    if not _is_missing(dict_data['NOM']) and not _is_missing(dict_data['PRENOM']):
                        dict_data['PATIENT_NAME'] = dict_data.pop('NOM') + ' ' + dict_data.pop('PRENOM')
                    else:
                        dict_data['PATIENT_NAME'] = np.nan

            # Reject the file if a key field could not be extracted, instead
            # of letting a fixed-index lookup abort the whole batch:
            missing = _find_missing_fields(current_patient_data, EFR_REQUIRED_FIELDS)
            if missing:
                _record_failure(failures, filename, 'EFR',
                                'Missing fields: ' + ', '.join(missing))
                continue

            # Check if patient is already in data (mapping shared across files):
            try:
                patient_name = current_patient_data[0]['PATIENT_NAME'].upper().replace(' ', '')
            except AttributeError:
                patient_name = np.nan
            patient_id = unique_id.assign_patient_id(
                patient_name=patient_name,
                all_patients_id=all_patients_id
            )
            current_patient_data.extend([{'FILENAME': filename}, {'PATIENT_ID': patient_id}])

            # General informations data:
            general_data = [current_patient_data[0], current_patient_data[1], current_patient_data[3],
                            current_patient_data[7], current_patient_data[8]]
            general_dict = {}
            for d in general_data:
                general_dict.update(d)
            general_rows.append(general_dict)

            # Detailed informations data:
            detailed_dict = {}
            keys_to_extract = [2, 3, 4, 5, 6, 8]
            for index in keys_to_extract:
                d = current_patient_data[index]
                if 'PATIENT_ID' in d:
                    detailed_dict['PATIENT_ID'] = str(d['PATIENT_ID'])
                detailed_dict.update(d)
            detailed_rows.append(detailed_dict)

        except ConversionError as exc:
            _record_failure(failures, filename, 'EFR', str(exc))
        except Exception as exc:  # isolate any per-file failure
            _record_failure(failures, filename, 'EFR',
                            f'Unexpected error: {type(exc).__name__}: {exc}')
        finally:
            image_converter.cleanup_images(image_paths)

    efr_general_info_df = pd.DataFrame(general_rows,
                                       columns=efr_data_manager.df_general.columns)
    efr_detailed_df = pd.DataFrame(detailed_rows)
    efr_failures_df = pd.DataFrame(failures, columns=FAILURE_COLUMNS)

    # Pivot data to rows (1 parameter per row), then to columns (1 patient per row).
    efr_detailed_rows_df = efr_data_manager.detailed_table_to_rows(efr_detailed_df)
    efr_detailed_cols_df = efr_data_manager.detailed_table_to_cols(efr_detailed_rows_df)

    efr_general_info_df = efr_data_manager.format_general_table(efr_general_info_df)
    efr_detailed_rows_df = efr_data_manager.format_detailed_rows_table(efr_detailed_rows_df)
    efr_detailed_cols_df = efr_data_manager.format_detailed_cols_table(efr_detailed_cols_df)

    # Export EFR (success tables keep their original structure, plus a failure manifest):
    all_efr_dfs = {
        'EFR_INFORMATIONS_GENERALES': (efr_general_info_df, False),
        'EFR_VALEURS_TESTS_LIGNES': (efr_detailed_rows_df, False),
        'EFR_VALEURS_TESTS_COLONNES': (efr_detailed_cols_df, False),
        'EFR_FICHIERS_ECHECS': (efr_failures_df, False),
    }
    export.export_data(all_efr_dfs)

    return {
        'source': 'EFR',
        'total': len(efr_pdf_files),
        'success': len(general_rows),
        'failed': len(failures),
        'failures_df': efr_failures_df,
    }


def main(ips_directory=IPS_DIRECTORY, efr_directory=EFR_DIRECTORY,
         poppler_path=POPPLER_PATH, tesseract_path=TESSERACT_PATH):
    image_converter = ImageConverter(poppler_path=poppler_path)
    extractor = Extractor(path_to_tesseract=tesseract_path)
    ips_extractor = IPSExtractor()
    efr_extractor = EFRExtractor()
    ips_data_manager = IPSDataManager()
    efr_data_manager = EFRDataManager()
    unique_id = UniquePatientID()
    export = Export()

    # Name -> ID mapping shared by every file and both pipelines, so several
    # reports of the same patient always resolve to the same UniquePatientID.
    all_patients_id = {}

    efr_doc_files = [f for f in os.listdir(efr_directory) if f.lower().endswith('doc')] \
        if os.path.isdir(efr_directory) else []
    efr_pdf_files = [f for f in os.listdir(efr_directory) if f.lower().endswith('pdf')] \
        if os.path.isdir(efr_directory) else []

    # .DOC -> .PDF conversion is best-effort: a Word failure must not prevent
    # the already-available PDFs from being processed.
    if efr_doc_files:
        try:
            convert = DocToPDF(directory=efr_directory)
            convert.convert_files(doc_files=efr_doc_files, pdf_files=efr_pdf_files)
        except Exception as exc:
            print(f'[!] .DOC -> .PDF conversion aborted: {type(exc).__name__}: {exc}')

    ips_summary = process_ips(
        ips_directory=ips_directory,
        image_converter=image_converter,
        extractor=extractor,
        ips_extractor=ips_extractor,
        ips_data_manager=ips_data_manager,
        unique_id=unique_id,
        all_patients_id=all_patients_id,
        export=export,
    )

    efr_summary = process_efr(
        efr_directory=efr_directory,
        efr_pdf_files=efr_pdf_files,
        image_converter=image_converter,
        extractor=extractor,
        efr_extractor=efr_extractor,
        efr_data_manager=efr_data_manager,
        unique_id=unique_id,
        all_patients_id=all_patients_id,
        export=export,
    )

    return {'IPS': ips_summary, 'EFR': efr_summary}


if __name__ == '__main__':
    summaries = main()
    for source, summary in summaries.items():
        print(f"[{source}] total={summary['total']} "
              f"success={summary['success']} failed={summary['failed']}")
