"""Independent bounded logs and retention controls for each application tab."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
import subprocess
import threading

from .config import application_directory

_lock = threading.RLock()

def settings_path():
    return application_directory() / 'log_settings.json'

def read_settings(tab):
    try:
        values = json.loads(settings_path().read_text(encoding='utf-8')).get(tab, {})
        return {'max_mb': max(1, min(1000, int(values.get('max_mb', 10)))),
                'backups': max(1, min(100, int(values.get('backups', 5))))}
    except (OSError, ValueError, TypeError):
        return {'max_mb': 10, 'backups': 5}

def log_directory():
    # Honor the existing Cleaner log-folder setting for the shared log folder.
    try:
        data = json.loads((application_directory() / 'cleaner_config.json').read_text(encoding='utf-8'))
        folder = Path(data.get('log_directory', 'logs'))
        return folder if folder.is_absolute() else application_directory() / folder
    except (OSError,ValueError,TypeError):
        return application_directory() / 'logs' 

def get_logger(tab):
    with _lock:
        logger = logging.getLogger('video_optimizer.' + tab)
        if not logger.handlers or Path(logger.handlers[0].baseFilename).parent != log_directory():
            configure(tab)
        return logger

def configure(tab):
    with _lock:
        values = read_settings(tab)
        folder = log_directory(); folder.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger('video_optimizer.' + tab)
        for handler in list(logger.handlers):
            logger.removeHandler(handler); handler.close()
        handler = RotatingFileHandler(folder / (tab + '.log'),
                    maxBytes=values['max_mb'] * 1024 * 1024, backupCount=values['backups'], encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate = False
        # A lowered retention count also removes excess rotated logs now.
        for path in folder.glob(tab + '.log.*'):
            if path.suffix[1:].isdigit() and int(path.suffix[1:]) > values['backups']:
                path.unlink(missing_ok=True)
        return logger

def save_settings(tab, max_mb, backups):
    max_mb, backups = int(max_mb), int(backups)
    if not 1 <= max_mb <= 1000 or not 1 <= backups <= 100:
        raise ValueError('Log size must be 1–1000 MB; older logs to keep must be 1–100.')
    with _lock:
        try:
            data = json.loads(settings_path().read_text(encoding='utf-8'))
        except (OSError, ValueError): data = {}
        data[tab] = {'max_mb': max_mb, 'backups': backups}
        target = settings_path(); temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8'); temporary.replace(target)
        configure(tab)

def write(tab, message):
    try: get_logger(tab).info('%s', str(message)[:65536])
    except OSError: pass  # Logging failure must not break file operations.

def settings_dialog(parent, tab):
    import tkinter as tk
    from tkinter import ttk, messagebox
    window = tk.Toplevel(parent); window.title(tab.title() + ' Logging Settings')
    window.transient(parent.winfo_toplevel())
    values = read_settings(tab)
    size, count = tk.StringVar(value=str(values['max_mb'])), tk.StringVar(value=str(values['backups']))
    for row, (label, variable) in enumerate((('Maximum log size (MB)',size), ('Older logs to keep',count))):
        ttk.Label(window,text=label).grid(row=row,column=0,padx=12,pady=8,sticky='w')
        ttk.Entry(window,textvariable=variable,width=8).grid(row=row,column=1,padx=12)
    def open_folder():
        folder=log_directory();folder.mkdir(parents=True,exist_ok=True)
        if os.name=='nt': os.startfile(str(folder))
        else: subprocess.Popen(['xdg-open',str(folder)])
    def save():
        try: save_settings(tab,size.get(),count.get())
        except (ValueError,OSError) as exc:
            messagebox.showerror('Logging settings',str(exc),parent=window);return
        window.destroy()
    ttk.Button(window,text='Open Log Folder',command=open_folder).grid(row=2,column=0,padx=12,pady=10)
    ttk.Button(window,text='Save',command=save).grid(row=2,column=1,padx=12)
