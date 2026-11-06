"""Windows folder picker and controls. The window owns the relay session."""
import ipaddress
import os
import queue
import threading
import tkinter as tk
import uuid
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import settings
from access import allowed_addresses
from client_history import HISTORY_DAYS, local_ipv4_addresses, recent_clients
from relay import Relay
from ssdp import lan_ip


class App:
    def __init__(self, window):
        self.window = window
        self.relay = None
        self.history_queue = queue.Queue()
        self.history_loading = False
        self.closed = False
        self.seen_clients = {}
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
        self.restricted = tk.BooleanVar(value=False)
        self.client_ips = tk.StringVar()
        self.restrict_control = ttk.Checkbutton(
            panel, text='Only these IPv4 addresses', variable=self.restricted,
            command=self.update_access_controls,
        )
        self.restrict_control.grid(row=4, column=0, sticky='w', pady=(8, 0))
        self.clients_entry = ttk.Entry(panel, textvariable=self.client_ips, width=44)
        self.clients_entry.grid(row=4, column=1, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Label(panel, text='Separate addresses with commas. Access settings save when sharing starts.').grid(
            row=5, columnspan=3, sticky='w')
        self.toggle_button = ttk.Button(panel, text='Start sharing', command=self.toggle)
        self.toggle_button.grid(row=6, sticky='w', pady=10)
        ttk.Button(panel, text='Open logs', command=self.open_logs).grid(row=6, column=1, sticky='w')
        self.status = tk.StringVar(value='Stopped')
        ttk.Label(panel, textvariable=self.status, wraplength=520).grid(row=7, columnspan=3, sticky='w')
        ttk.Label(panel, text='Open network media sources in your DLNA player.').grid(
            row=8, columnspan=3, sticky='w', pady=(8, 0))
        history = ttk.LabelFrame(panel, text='Previously seen clients', padding=8)
        history.grid(row=9, columnspan=3, sticky='ew', pady=(12, 0))
        self.clients = ttk.Treeview(
            history, columns=('ip', 'guess', 'activity', 'seen'), show='headings', height=5,
            selectmode='extended',
        )
        for column, label, width in (
            ('ip', 'IP address', 115), ('guess', 'Device guess', 200),
            ('activity', 'Activity seen', 125), ('seen', 'Last seen', 145),
        ):
            self.clients.heading(column, text=label)
            self.clients.column(column, width=width)
        self.clients.grid(row=0, column=0, columnspan=2, sticky='ew')
        scrollbar = ttk.Scrollbar(history, orient='vertical', command=self.clients.yview)
        scrollbar.grid(row=0, column=2, sticky='ns')
        self.clients.configure(yscrollcommand=scrollbar.set)
        self.clients.bind('<<TreeviewSelect>>', self.select_client)
        self.refresh_clients_button = ttk.Button(history, text='Refresh', command=self.refresh_clients)
        self.refresh_clients_button.grid(row=1, column=0, sticky='w', pady=5)
        self.add_clients_button = ttk.Button(
            history, text='Add selected IPs', command=self.add_selected_clients, state='disabled',
        )
        self.add_clients_button.grid(row=1, column=1, sticky='e')
        self.reported_client = tk.StringVar()
        ttk.Label(history, text='Reported client').grid(row=2, column=0, sticky='w')
        ttk.Entry(history, textvariable=self.reported_client, state='readonly').grid(
            row=3, column=0, columnspan=2, sticky='ew')
        self.history_note = tk.StringVar()
        ttk.Label(history, textvariable=self.history_note, wraplength=580).grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(5, 0))
        ttk.Label(history, text='This PC is hidden. Grouped by IP; device guesses and addresses can change.').grid(
            row=5, column=0, columnspan=2, sticky='w')
        self.load_settings()
        self.update_access_controls()
        self.refresh_status()
        self.refresh_clients()

    def load_settings(self):
        try:
            folders, self.udn = settings.load()
            self.folders.insert(tk.END, *folders)
            addresses = settings.load_access()
            self.restricted.set(addresses is not None)
            self.client_ips.set(', '.join(addresses or ()))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.restricted.set(True)
            self.status.set(f'Could not load settings: {exc}')

    def update_access_controls(self):
        self.clients_entry.config(state='normal' if self.restricted.get() else 'disabled')

    def save_folders(self, folders):
        try:
            settings.save(folders, self.udn)
        except (OSError, ValueError) as exc:
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

    def refresh_clients(self):
        if self.closed or self.history_loading:
            return
        self.history_loading = True
        self.refresh_clients_button.config(state='disabled')
        self.history_note.set('Reading recent access logs…')
        selected_host = self.host.get().strip()

        def read_history():
            try:
                excluded = local_ipv4_addresses(selected_host)
                result = recent_clients(settings.LOG_DIR, exclude_addresses=excluded)
                self.history_queue.put((result, None))
            except Exception as error:
                self.history_queue.put((None, str(error)))

        threading.Thread(target=read_history, daemon=True).start()
        self.window.after(75, self.finish_client_refresh)

    def finish_client_refresh(self):
        if self.closed:
            return
        try:
            result, error = self.history_queue.get_nowait()
        except queue.Empty:
            self.window.after(75, self.finish_client_refresh)
            return
        self.history_loading = False
        self.refresh_clients_button.config(state='normal')
        if error is not None:
            self.history_note.set(f'Could not read client history: {error}')
            return
        clients, unreadable = result
        self.clients.delete(*self.clients.get_children())
        self.seen_clients = {}
        for index, client in enumerate(clients):
            identifier = str(index)
            self.seen_clients[identifier] = client
            seen = datetime.fromtimestamp(client.last_seen).strftime('%Y-%m-%d %H:%M')
            self.clients.insert('', 'end', iid=identifier, values=(client.address, client.guess, client.activity, seen))
        note = f'{len(clients)} addresses from recent log tails (up to {HISTORY_DAYS} days). Media requests do not confirm playback.'
        if unreadable:
            note += f' {unreadable} log files could not be read.'
        if not clients:
            note += ' Connect a player while sharing, then refresh.'
        self.history_note.set(note)
        self.select_client()

    def select_client(self, event=None):
        selection = self.clients.selection()
        evidence = ''
        if len(selection) == 1:
            evidence = self.seen_clients[selection[0]].evidence
        elif selection:
            evidence = 'Multiple entries selected'
        self.reported_client.set(evidence)
        enabled = bool(selection) and self.relay is None
        self.add_clients_button.config(state='normal' if enabled else 'disabled')

    def add_selected_clients(self):
        if self.relay is not None:
            return
        selection = self.clients.selection()
        if not selection:
            return
        try:
            current = allowed_addresses(self.client_ips.get()) if self.client_ips.get().strip() else ()
            selected = [self.seen_clients[identifier].address for identifier in selection]
            addresses = allowed_addresses([*current, *selected])
        except ValueError as error:
            messagebox.showerror('Could not add addresses', str(error), parent=self.window)
            return
        self.client_ips.set(', '.join(addresses))
        self.restricted.set(True)
        self.update_access_controls()
        self.history_note.set('Selected IPs added. Access settings save when sharing starts.')

    def toggle(self):
        if self.relay:
            self.stop()
            return
        folders = self.folders.get(0, tk.END)
        try:
            host = ipaddress.IPv4Address(self.host.get().strip())
            if host.is_unspecified or host.is_multicast or host.is_loopback:
                raise ValueError('Enter the IPv4 address of the PC adapter connected to your LAN.')
            addresses = allowed_addresses(self.client_ips.get()) if self.restricted.get() else None
            settings.save(folders, self.udn)
            settings.save_access(addresses)
            self.relay = Relay(folders, str(host), self.udn, allowed_clients=addresses)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Could not start sharing', str(exc), parent=self.window)
            return
        self.toggle_button.config(text='Stop sharing')
        for control in (self.add, self.remove, self.address, self.restrict_control, self.clients_entry):
            control.config(state='disabled')
        self.select_client()
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
        for control in (self.add, self.remove, self.address, self.restrict_control):
            control.config(state='normal')
        self.update_access_controls()
        self.status.set('Stopped')
        self.select_client()
        self.refresh_clients()

    def close(self):
        self.closed = True
        if self.relay:
            self.stop()
        self.window.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    App(window)
    window.mainloop()
