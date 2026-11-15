import os
import re
from unidecode import unidecode

class HelperUtils():
    # Characters that are not legal inside a Windows file/directory name.
    _INVALID_FILE_NAME_CHARS = set('<>:"/\\|?*')

    @staticmethod
    def _check_path_valid(directory: str, file_name: str) -> bool:
        '''
        Checks if the path given in export() is valid.

        This method is only responsible for path-level validation:
            1. The directory must exist on disk.
            2. The file name must be a legal, non-empty name (no path
               separators or other reserved characters).
            3. Whether a file already exists at the target location.

        It deliberately does NOT validate the export method (`how`) against
        the file extension. That matching is handled centrally by
        ``ObjectTree.export`` so the same extension rule cannot be enforced
        twice with conflicting logic.

            Args:
                directory (str): directory path
                file_name (str): desired file_name
            Returns:
                bool: True if a file with the same path already exists.
                The reason is that SAP gives the options "generate" and
                "replace" when saving a file; the caller must choose
                "replace" when the file already exists.
        '''
        if not isinstance(directory, str) or not isinstance(file_name, str):
            raise ValueError('Directory and file name must be strings')

        if not os.path.isdir(directory):
            raise ValueError(f"Directory: {directory} does not exist")

        if not file_name or file_name.strip() == '':
            raise ValueError('File name cannot be empty')

        # A file name must not contain path separators or reserved
        # characters, otherwise it would not resolve to a file inside
        # the given directory.
        if any(char in HelperUtils._INVALID_FILE_NAME_CHARS for char in file_name):
            raise ValueError(
                f"File name: {file_name} is not valid. "
                "It must not contain path separators or any of "
                '< > : " / \\ | ? *'
            )

        file_path = os.path.abspath(os.path.join(directory, file_name))
        return os.path.exists(file_path)
    
    @staticmethod
    def _clean_field_text(name: str) -> str:
        cleaned_name = re.sub(r'[-/&]+', ' ', name)
        cleaned_name = unidecode(string=cleaned_name).lower()
        cleaned_name = re.sub(r'[^A-Za-z0-9 ]', '', cleaned_name)
        return cleaned_name.replace(' ', '_')
        
    @staticmethod
    def _modify_repeat_name(field_text: str, repeat_name_dict: dict):
        """Modify a field text to add a frequency suffix if it's repeated"""
        repeat_name_dict[field_text] = repeat_name_dict.get(field_text, 0) + 1
        name_freq = repeat_name_dict[field_text]
        if name_freq > 1:
            field_text = field_text + f'_{name_freq}'
        
        return field_text, repeat_name_dict
    
    @staticmethod
    def _find_shell_export(session, sap_shell_id):
        try:
            session.findById(sap_shell_id).pressToolbarContextButton("&NAVIGATION_PROFILE_TOOLBAR_EXPAND")
        except Exception:
            pass # Expand button already expanded or doesn't exist

        try:
            session.findById(sap_shell_id).pressToolbarContextButton("&MB_EXPORT")
            session.findById(sap_shell_id).SelectContextMenuItem("&XXL")

        except Exception:
            raise ValueError('Export not option')