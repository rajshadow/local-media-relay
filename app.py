"""Windows folder picker and controls. The window owns the relay session."""
import ipaddress
import os
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

import settings
from relay import Relay
from ssdp import lan_ip


class App:
    def __init__(self, window):
        self.window = window
        self.relay = None
        self.udn = 'uuid:' + str(uuid.uuid4())
        self.window.title('Local Media Relay')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        panel = ttk.Frame(window, padding=16)
        panel.grid(sticky='nsew')
        ttk.Label(panel, text='Folders to share on your local network').grid(sticky='w')
        self.folders = tk.Listbox(panel, width=76, height=6, exportselection=False)
        self.folders.grid(row=1, columnspan=3, sticky='ew', pady=5)
        self.add = ttk.Button(panel, text='Add folder', command=self.add_folder)
        self.add.grid(row=2, column=0, sticky='w')
        self.remove = ttk.Button(panel, text='Remove selected', command=self.remove_folder)
        self.remove.grid(row=2, column=1, sticky='w')
        ttk.Label(panel, text='PC IPv4 address').grid(row=3, column=0, sticky='w', pady=(12, 2))
        self.host = tk.StringVar(value=lan_ip())
        self.address = ttk.Entry(panel, textvariable=self.host, width=18)
        self.address.grid(row=3, column=1, sticky='w', pady=(12, 2))
        self.toggle_button = ttk.Button(panel, text='Start sharing', command=self.toggle)
        self.toggle_button.grid(row=4, sticky='w', pady=10)
        ttk.Button(panel, text='Open logs', command=self.open_logs).grid(row=4, column=1, sticky='w')
        self.status = tk.StringVar(value='Stopped')
        ttk.Label(panel, textvariable=self.status, wraplength=520).grid(row=5, columnspan=3, sticky='w')
        ttk.Label(panel, text='On your TV or Quest DLNA player, open network media sources.').grid(
            row=6, columnspan=3, sticky='w', pady=(8, 0))
        self.load_settings()
        self.refresh_status()

    def load_settings(self):
        try:
            folders, self.udn = settings.load()
            self.folders.insert(tk.END, *folders)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.status.set(f'Could not load settings: {exc}')

    def save_folders(self, folders):
        try:
            settings.save(folders, self.udn)
        except OSError as exc:
            messagebox.showerror('Could not save folders', str(exc), parent=self.window)
            return
        self.folders.delete(0, tk.END)
        self.folders.insert(tk.END, *folders)

    def add_folder(self):
        folder = filedialog.askdirectory(mustexist=True)
        folders = self.folders.get(0, tk.END)
        if folder and folder not in folders:
            self.save_folders((*folders, folder))

    def remove_folder(self):
        selected = self.folders.curselection()
        if selected:
            self.save_folders([folder for index, folder in enumerate(self.folders.get(0, tk.END))
                               if index not in selected])

    def open_logs(self):
        try:
            settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(settings.LOG_DIR)
        except OSError as exc:
            messagebox.showerror('Could not open logs', str(exc), parent=self.window)

    def toggle(self):
        if self.relay:
            self.stop()
            return
        folders = self.folders.get(0, tk.END)
        try:
            host = ipaddress.IPv4Address(self.host.get().strip())
            if host.is_unspecified or host.is_multicast or host.is_loopback:
                raise ValueError('Enter the IPv4 address of the PC adapter connected to your LAN.')
            self.relay = Relay(folders, str(host), self.udn)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Could not start sharing', str(exc), parent=self.window)
            return
        self.toggle_button.config(text='Stop sharing')
        for control in (self.add, self.remove, self.address):
            control.config(state='disabled')
        self.update_status()

    def update_status(self):
        if self.relay:
            if self.relay.caddy.process.poll() is not None:
                self.stop()
                self.status.set('Stopped because Caddy exited. Click Start sharing to retry.')
                return
            self.status.set(self.relay.discovery.error or
                            f'Sharing as Local Media Relay | Caddy {self.relay.base} | SSDP UDP 1900')

    def refresh_status(self):
        self.update_status()
        self.window.after(1000, self.refresh_status)

    def stop(self):
        self.relay.stop()
        self.relay = None
        self.toggle_button.config(text='Start sharing')
        for control in (self.add, self.remove, self.address):
            control.config(state='normal')
        self.status.set('Stopped')

    def close(self):
        if self.relay:
            self.stop()
        self.window.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    App(window)
    window.mainloop()
