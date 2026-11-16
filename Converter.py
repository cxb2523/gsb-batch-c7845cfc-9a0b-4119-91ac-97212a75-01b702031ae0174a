from pdf2image import convert_from_path
from pdf2image.exceptions import PDFPageCountError
import tempfile
import os
import shutil
from tqdm import tqdm


class ConversionError(Exception):
    """Raised when a document cannot be converted into an image."""


class ImageConverter:

    def __init__(self, poppler_path):
        self.poppler_path = poppler_path

    def file_to_image(self, file_path):
        """
        Conversion to image format(.PNG).

        The output images are written into a dedicated temporary directory
        created for *this specific file*, so the result is always bound to the
        file currently being processed: a failed or missing conversion can
        never expose images left over from a previous file.

        :param file_path: PDF file to convert in IMAGE format
        :return: list of paths of the generated PNG images (first page first)
        :raises ConversionError: if the document cannot be converted
        """
        output_dir = tempfile.mkdtemp(prefix='ocr_convert_')
        try:
            try:
                images = convert_from_path(file_path, poppler_path=self.poppler_path)
            except PDFPageCountError as exc:
                raise ConversionError(
                    f'PDF conversion failed (unreadable/corrupt PDF): {file_path}'
                ) from exc
            except Exception as exc:  # pdf2image/popler can raise other errors too
                raise ConversionError(
                    f'PDF conversion failed ({type(exc).__name__}): {file_path}'
                ) from exc

            if not images:
                raise ConversionError(f'PDF conversion produced no image: {file_path}')

            image_paths = []
            for i, image in enumerate(images):
                image_path = os.path.join(output_dir, f'IMG_{i}.png')
                image.save(image_path, 'PNG')
                image_paths.append(image_path)
            return image_paths
        except ConversionError:
            # Do not leave an empty/partial directory (or stale images) behind.
            shutil.rmtree(output_dir, ignore_errors=True)
            raise

    @staticmethod
    def cleanup_images(image_paths):
        """Remove the temporary directory holding the images of one file."""
        if not image_paths:
            return
        output_dir = os.path.dirname(image_paths[0])
        if os.path.basename(output_dir).startswith('ocr_convert_'):
            shutil.rmtree(output_dir, ignore_errors=True)


class DocToPDF:

    def __init__(self, directory):
        # Imported lazily so the rest of the pipeline stays usable on machines
        # without Word/pywin32 (and so this module can be imported headless).
        import win32com.client as win32
        self.word = win32.Dispatch('Word.Application')
        self.DIRECTORY = directory

    def convert_files(self, doc_files: list, pdf_files: list):
        for doc_file in tqdm(doc_files, desc='[+] Conversion fichiers .DOC -> .PDF', total=len(doc_files)):
            pdf_file = os.path.splitext(doc_file)[0] + ".pdf"
            if pdf_file in pdf_files:
                continue

            doc_path = os.path.join(self.DIRECTORY, doc_file)
            doc = self.word.Documents.Open(doc_path)
            pdf_path = os.path.join(self.DIRECTORY, pdf_file)
            doc.SaveAs(pdf_path, FileFormat=17)
            doc.Close()
            pdf_files.append(pdf_file)

        self.word.Quit()
