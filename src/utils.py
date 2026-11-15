import os
from tkinter import messagebox
from typing import Union

LOG_MODE = ('Disable logging','Enable logging')
APP_TITLE = 'Text2Excel'
ENCODING = 'utf-8-sig'
LOG_DEFAULT = 'log ...'

# Extensions that are considered text files when a directory is selected.
TEXT_FILE_EXTENSIONS = (
    '.txt', '.csv', '.log', '.json', '.xml', '.html', '.htm',
    '.md', '.ini', '.cfg', '.conf', '.yaml', '.yml', '.tsv',
    '.py', '.js', '.java', '.c', '.h', '.cpp', '.hpp',
)

FILE_TYPES = [
    ('All Files','*.*'),
    ('Excel Files','*.xlsx;*.xlsm;*.xltx;*.xltm'),
    ('CSV Files','*.csv')
]

class WithLogging:
    with_logging = True

MENU_COLOR_ARGS = {'activebackground' : '#00c8ff', 'activeforeground' : 'black'}
EXACT_CB_GRID_ARGS = {'row' : 0, 'column' : 1, 'sticky' : 's', 'pady' : (0,10), 'padx' : (0,20)}

def show_error(err : Union[Exception,str]) -> None:
    messagebox.showerror(title=APP_TITLE, message=err)

def show_info(message : str) -> None:
    messagebox.showinfo(title=APP_TITLE, message=message)

def list_text_files(directory : str) -> list[str]:
    """Return the text files inside *directory* (non recursive), sorted by name.

    Only regular files whose extension is in :data:`TEXT_FILE_EXTENSIONS`
    are returned. Anything else -- binaries, subdirectories, files with no
    recognised text extension -- is ignored by design.
    """
    text_files = []

    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in TEXT_FILE_EXTENSIONS:
            text_files.append(path)

    return text_files
