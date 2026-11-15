"""Optional VLC comparison player, isolated from the Encoder process."""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


class PairControls:
    """Shared user commands; no periodic playback-rate adjustments."""
    def __init__(self, backup, encoded):
        self.players = (backup, encoded)
        self.offset_ms = 0
        self.playing = False
        self.audio_side = 1
        self.volume_percent = 100
        self.audio_tracks = [None, None]
        self.preferred_audio_tracks = [None, None]
        self.subtitle_tracks = [None, None]
        self.last_audio_side = 1

    def seek(self, milliseconds):
        low = max(0, -self.offset_ms)
        lengths = [p.get_length() for p in self.players]
        limits = [n - offset for n, offset in zip(lengths, (self.offset_ms, 0)) if n > 0]
        high = max(low, min(limits) - 1) if limits else max(low, milliseconds)
        position = max(low, min(int(milliseconds), high))
        self.players[0].set_time(position + self.offset_ms)
        self.players[1].set_time(position)
        return position

    def pause(self):
        for player in self.players:
            player.set_pause(1)
        self.playing = False

    def resume(self):
        for player in self.players:
            player.set_pause(0)
        self.playing = True

    def audio(self, side):
        self.audio_side = side
        if side in (0, 1):
            self.last_audio_side = side
        self.apply_audio()

    def toggle_mute(self):
        self.audio(self.last_audio_side if self.audio_side == -1 else -1)

    def choose_audio_track(self, side, track):
        available = self.players[side].audio_get_track_description() or []
        if track < 0 or track not in {identifier for identifier, _label in available}:
            raise ValueError("That audio track is no longer available")
        self.preferred_audio_tracks[side] = track
        self.audio_tracks[side] = track
        self.apply_audio()

    def choose_subtitle_track(self, side, track):
        available = self.players[side].video_get_spu_description() or []
        if track != -1 and track not in {identifier for identifier, _label in available}:
            raise ValueError("That subtitle track is no longer available")
        if self.players[side].video_set_spu(track) == -1:
            raise RuntimeError("VLC could not select that subtitle track")
        self.subtitle_tracks[side] = track

    def restore_subtitles(self):
        for player, track in zip(self.players, self.subtitle_tracks):
            if track is not None:
                player.video_set_spu(track)

    def volume(self, value):
        self.volume_percent = max(0, min(100, int(float(value))))
        self.apply_audio()

    def apply_audio(self):
        # Mute alone is unreliable on some VLC/Windows output backends.
        # Disable the unselected audio track itself. Do not set its volume:
        # some backends share a Windows audio-session volume between players.
        for index, player in enumerate(self.players):
            current = player.audio_get_track()
            if (isinstance(current, int) and current >= 0
                    and self.preferred_audio_tracks[index] is None):
                self.audio_tracks[index] = current
            if index != self.audio_side and current != -1:
                player.audio_set_track(-1)
        if self.audio_side not in (0, 1):
            return
        player = self.players[self.audio_side]
        current = player.audio_get_track()
        preferred = self.preferred_audio_tracks[self.audio_side]
        if current == -1 or (preferred is not None and current != preferred):
            track = preferred if preferred is not None else self.audio_tracks[self.audio_side]
            if track is None:
                tracks = player.audio_get_track_description() or []
                track = next((identifier for identifier, _label in tracks if identifier >= 0), None)
            if track is not None:
                player.audio_set_track(track)
                self.audio_tracks[self.audio_side] = track
        player.audio_set_mute(False)
        player.audio_set_volume(self.volume_percent)


def reported_audio_status(player):
    """Read VLC's actual state rather than echoing the requested selection."""
    try:
        track = player.audio_get_track()
        if track == -1:
            return "off", "AUDIO OFF — VLC audio track disabled"
        if not isinstance(track, int) or track < 0:
            return "unknown", "Audio state unavailable"
        mute, volume = player.audio_get_mute(), player.audio_get_volume()
        playback = str(player.get_state()).split(".")[-1]
        detail = f"track {track} · volume {volume}% · {playback}"
        if mute in (True, 1):
            return "off", f"MUTED — {detail}"
        if volume == 0:
            return "off", f"SILENT — {detail}"
        if mute not in (False, 0) or not isinstance(volume, int) or volume < 0:
            return "unknown", f"Audio state unconfirmed — track {track}"
        if playback != "Playing":
            return "off", f"Audio enabled, {playback} — track {track} · volume {volume}%"
        return "on", f"AUDIO ON — {detail}"
    except Exception:
        return "unknown", "VLC audio state unavailable"


def comparison_colors(theme):
    return ({"bg": "#1e1f22", "panel": "#27292d", "field": "#303238",
             "fg": "#f2f2f2", "muted": "#c7c9ce", "accent": "#4f9cf9",
             "select": "#365f91", "border": "#454850"} if theme == "dark" else
            {"bg": "#f0f0f0", "panel": "#ffffff", "field": "#ffffff",
             "fg": "#202124", "muted": "#404246", "accent": "#2678d4",
             "select": "#cde4ff", "border": "#b8b8b8"})


def load_vlc():
    # python-vlc also detects installed VLC through the Windows registry.
    # Keep DLL-directory handles alive for this isolated player's lifetime.
    handles = []
    if os.name == "nt":
        from .vlc_runtime import find_windows_vlc
        folder = find_windows_vlc()
        if folder is None:
            raise RuntimeError("A matching VLC runtime was not found. Install VLC matching the application's architecture.")
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        os.environ["VLC_PLUGIN_PATH"] = str(folder / "plugins")
        if hasattr(os, "add_dll_directory"):
            handles.append(os.add_dll_directory(str(folder)))
    try:
        import vlc
        instance = vlc.Instance("--no-video-title-show", "--avcodec-hw=any")
        if instance is None:
            raise RuntimeError("VLC could not initialize")
        return instance, handles
    except Exception as exc:
        raise RuntimeError(
            "The comparison player requires VLC 3.x matching the application's "
            "architecture (normally 64-bit). Install VLC, then try again. "
            "Portable VLC can be placed in a VLC folder beside the application.\n\n"
            f"Details: {exc}") from exc


class CompareWindow:
    def __init__(self, root, instance, backup: Path, encoded: Path):
        self.root, self.instance = root, instance
        self.paths = (backup, encoded)
        self.players = []
        self.media = []
        self.detached = []
        self.closed = False
        self.loading = False
        self.seeking = False
        self.failure = None
        self.after_id = None
        root.title("Controls — Play Both - Compare")
        root.geometry("1400x650")
        root.minsize(760, 420)
        self.body = ttk.Frame(root)
        self.body.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Opening both videos…")
        self.audio_status_vars = [tk.StringVar(value="VLC audio state: opening") for _ in range(2)]
        self.hide_identity = tk.BooleanVar(value=False)
        self.identity_labels = []
        self.panels = self.make_panels((self.body, self.body))
        for path in self.paths:
            media = instance.media_new_path(str(path))
            player = instance.media_player_new()
            player.set_media(media)
            self.media.append(media)
            self.players.append(player)
        self.controls = PairControls(*self.players)
        self.style = ttk.Style(root)
        self.bar = ttk.Frame(root, padding=8)
        self.bar.pack(fill="x")
        self.play_button = ttk.Button(self.bar, text="Pause both", command=self.toggle)
        self.play_button.pack(side="left")
        ttk.Button(self.bar, text="−10 s", command=lambda: self.jump(-10000)).pack(side="left")
        ttk.Button(self.bar, text="+10 s", command=lambda: self.jump(10000)).pack(side="left")
        ttk.Button(self.bar, text="Resync", command=lambda: self.jump(0)).pack(side="left")
        self.layout_button = ttk.Button(self.bar, text="Separate windows", command=self.toggle_layout)
        self.layout_button.pack(side="left", padx=8)
        ttk.Label(self.bar, text="Backup offset (ms):").pack(side="left")
        self.offset = tk.StringVar(value="0")
        spin = ttk.Spinbox(self.bar, from_=-60000, to=60000, increment=40,
                          width=9, textvariable=self.offset, command=self.apply_offset)
        spin.pack(side="left")
        spin.bind("<Return>", lambda event: self.apply_offset())
        ttk.Button(self.bar, text="Apply", command=self.apply_offset).pack(side="left")
        self.audio_bar = ttk.Frame(root, padding=(8, 0, 8, 4))
        self.audio_bar.pack(fill="x")
        self.audio_choice = tk.StringVar(value="Encoded")
        for side, title in enumerate(("Backup", "Encoded")):
            box = ttk.LabelFrame(self.audio_bar, text=title + " audio", padding=6)
            box.pack(side="left", fill="x", expand=True, padx=4)
            ttk.Radiobutton(box, text="Listen to " + title, value=title,
                            variable=self.audio_choice, command=self.set_audio).pack(side="left", padx=4)
            ttk.Label(box, textvariable=self.audio_status_vars[side],
                      style=("BackupAudio.TLabel" if side == 0 else "EncodedAudio.TLabel"),
                      wraplength=470).pack(side="left", fill="x", expand=True, padx=6)
        volume_bar = ttk.Frame(root, padding=(12, 0, 12, 4))
        volume_bar.pack(fill="x")
        self.mute_button = ttk.Button(volume_bar, text="Mute Both", command=self.mute_both)
        self.mute_button.pack(side="left")
        ttk.Label(volume_bar, text="Volume:").pack(side="left", padx=(12, 4))
        volume = ttk.Scale(volume_bar, from_=0, to=100, length=220, command=self.change_volume)
        volume.set(100)
        volume.pack(side="left")
        self.volume_text = tk.StringVar(value="100%")
        ttk.Label(volume_bar, textvariable=self.volume_text, width=6).pack(side="left")
        ttk.Label(volume_bar, text="Indicators show VLC-reported state, not sound levels").pack(side="left", padx=12)
        ttk.Label(volume_bar, text="Theme:").pack(side="right")
        try:
            from .config import load_config
            theme = load_config(None).theme
        except Exception:
            theme = "dark"
        self.theme_choice = tk.StringVar(value=theme if theme in {"dark", "light"} else "dark")
        theme_picker = ttk.Combobox(volume_bar, values=("dark", "light"),
                                   textvariable=self.theme_choice, state="readonly", width=7)
        theme_picker.pack(side="right", padx=5)
        theme_picker.bind("<<ComboboxSelected>>", lambda event: self.apply_theme())
        ttk.Checkbutton(root, text="Hide video identities and filenames",
                        variable=self.hide_identity, command=self.update_identities).pack(anchor="w", padx=12)
        self.timeline = ttk.Scale(root, from_=0, to=1)
        self.timeline.pack(fill="x", padx=10)
        self.timeline.bind("<ButtonPress-1>", lambda event: self.begin_seek())
        self.timeline.bind("<ButtonRelease-1>", lambda event: self.end_seek())
        self.timeline.bind("<KeyRelease>", lambda event: self.end_seek())
        ttk.Label(root, textvariable=self.status, padding=8).pack(fill="x")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<space>", self.space)
        self.apply_theme()
        self.refresh_audio_status()
        self.attach()
        self.start_at(0, True)
        self.tick()

    def make_panels(self, parents):
        panels = []
        self.identity_labels = []
        for index, parent in enumerate(parents):
            container = ttk.Frame(parent, padding=3)
            container.pack(side="left", fill="both", expand=True)
            title = "ORIGINAL BACKUP" if index == 0 else "ENCODED VIDEO"
            heading = ttk.Label(container, text=title)
            heading.pack()
            filename = ttk.Label(container, text=self.paths[index].name, wraplength=550)
            filename.pack()
            self.identity_labels.append((heading, filename))
            ttk.Label(container, textvariable=self.audio_status_vars[index],
                      style=("BackupAudio.TLabel" if index == 0 else "EncodedAudio.TLabel")).pack()
            canvas = tk.Frame(container, background="black", width=600, height=340)
            canvas.pack(fill="both", expand=True)
            panels.append((container, canvas))
            for widget in (container, canvas, *container.winfo_children()):
                widget.bind("<Button-3>", lambda event, side=index: self.track_menu(side, event))
        self.update_identities()
        return panels

    def update_identities(self):
        hidden = self.hide_identity.get()
        for side, (heading, filename) in enumerate(self.identity_labels):
            heading.configure(text=f"Video {side + 1}" if hidden else
                              ("ORIGINAL BACKUP" if side == 0 else "ENCODED VIDEO"))
            filename.configure(text="" if hidden else self.paths[side].name)
        for side, window in enumerate(self.detached):
            label = f"Video {side + 1}" if hidden else ("Original" if side == 0 else "Encoded video")
            window.title(label + " — use the shared controls")

    def attach(self):
        self.root.update_idletasks()
        for player, (_, canvas) in zip(self.players, self.panels):
            handle = canvas.winfo_id()
            if os.name == "nt":
                player.set_hwnd(handle)
            elif sys.platform.startswith("linux"):
                player.set_xwindow(handle)
            else:
                raise RuntimeError("This comparison prototype supports Windows and X11")
            player.video_set_mouse_input(False)
            player.video_set_key_input(False)

    def start_at(self, position, playing):
        self.loading = True
        self.play_button.configure(state="disabled")
        self.layout_button.configure(state="disabled")
        for player in self.players:
            self.set_audio()
            if player.play() == -1:
                self.failure = "VLC could not open a video. Close this player and check the files in VLC."
                self.status.set(self.failure)
                self.loading = False
                return
        self.ready_attempts = 0
        def ready():
            if self.closed:
                return
            self.ready_attempts += 1
            if all(p.is_seekable() and p.get_length() > 0 for p in self.players):
                self.controls.pause()
                self.controls.seek(position)
                self.set_audio()
                self.controls.restore_subtitles()
                if playing:
                    self.controls.resume()
                self.loading = False
                self.play_button.configure(state="normal", text="Pause both" if playing else "Play both")
                self.layout_button.configure(state="normal")
                return
            if self.ready_attempts >= 150:
                self.controls.pause()
                self.loading = False
                self.failure = "Could not prepare both videos within 30 seconds. Close and retry; files are unchanged."
                self.status.set(self.failure)
                return
            self.ready_id = self.root.after(200, ready)
        self.ready_id = self.root.after(100, ready)

    def toggle(self):
        if self.loading:
            return
        if self.controls.playing:
            self.controls.pause()
        else:
            self.controls.resume()
        self.play_button.configure(text="Pause both" if self.controls.playing else "Play both")

    def space(self, event):
        if event.widget.winfo_class() not in {"TSpinbox", "TCombobox", "TEntry"}:
            self.toggle()
            return "break"

    def jump(self, delta):
        if not self.loading:
            self.controls.seek(max(0, self.players[1].get_time()) + delta)

    def begin_seek(self):
        self.seeking = True

    def end_seek(self):
        if not self.loading:
            self.controls.seek(self.timeline.get())
        self.seeking = False

    def apply_offset(self):
        try:
            value = int(self.offset.get())
            if abs(value) > 60000:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Offset", "Enter an offset between −60000 and +60000 milliseconds.", parent=self.root)
            return
        self.controls.offset_ms = value
        self.jump(0)

    def set_audio(self):
        self.controls.audio({"Backup": 0, "Encoded": 1, "Muted": -1}[self.audio_choice.get()])
        self.refresh_mute_button()
        self.refresh_audio_status()

    def mute_both(self):
        self.controls.toggle_mute()
        self.audio_choice.set({-1: "Muted", 0: "Backup", 1: "Encoded"}[self.controls.audio_side])
        self.refresh_mute_button()
        self.refresh_audio_status()

    def refresh_mute_button(self):
        muted = self.controls.audio_side == -1
        self.mute_button.configure(text="Unmute Both" if muted else "Mute Both",
                                   style="Muted.TButton" if muted else "TButton")

    def track_menu(self, side, event):
        if self.loading:
            return "break"
        old = getattr(self, "context_menu", None)
        if old is not None:
            old.destroy()
        colors = comparison_colors(self.theme_choice.get())
        def new_menu(parent):
            return tk.Menu(parent, tearoff=False, background=colors["panel"], foreground=colors["fg"],
                           activebackground=colors["select"], activeforeground=colors["fg"],
                           disabledforeground=colors["muted"])
        menu = new_menu(self.root)
        self.context_menu = menu
        menu.add_command(label=(f"Video {side + 1}" if self.hide_identity.get() else
                                ("Original" if side == 0 else "Encoded video")), state="disabled")
        menu.add_separator()
        player = self.players[side]
        audio = new_menu(menu)
        selected = self.controls.preferred_audio_tracks[side]
        if selected is None:
            selected = self.controls.audio_tracks[side]
        audio_var = tk.IntVar(master=self.root, value=selected if selected is not None else -1)
        subtitle_var = tk.IntVar(master=self.root, value=player.video_get_spu())
        self.track_menu_vars = (audio_var, subtitle_var)
        def label(identifier, name):
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            return str(name or f"Track {identifier}")
        tracks = [(identifier, name) for identifier, name in
                  (player.audio_get_track_description() or []) if identifier >= 0]
        for identifier, name in tracks:
            audio.add_radiobutton(label=label(identifier, name), value=identifier, variable=audio_var,
                                  command=lambda track=identifier: self.change_track(side, "audio", track))
        if not tracks:
            audio.add_command(label="No audio tracks available", state="disabled")
        menu.add_cascade(label="Audio track", menu=audio)
        subtitles = new_menu(menu)
        subtitles.add_radiobutton(label="Off", value=-1, variable=subtitle_var,
                                 command=lambda: self.change_track(side, "subtitle", -1))
        for identifier, name in player.video_get_spu_description() or []:
            if identifier >= 0:
                subtitles.add_radiobutton(label=label(identifier, name), value=identifier, variable=subtitle_var,
                                         command=lambda track=identifier: self.change_track(side, "subtitle", track))
        menu.add_cascade(label="Subtitles", menu=subtitles)
        menu.add_separator()
        menu.add_command(label="Listen to this video",
                         command=lambda: self.listen_to(side))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def listen_to(self, side):
        self.audio_choice.set("Backup" if side == 0 else "Encoded")
        self.set_audio()

    def change_track(self, side, kind, track):
        try:
            if kind == "audio":
                self.controls.choose_audio_track(side, track)
            else:
                self.controls.choose_subtitle_track(side, track)
            self.refresh_audio_status()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot change track", str(exc), parent=self.root)

    def change_volume(self, value):
        self.controls.volume(value)
        if hasattr(self, "volume_text"):
            self.volume_text.set(f"{self.controls.volume_percent}%")

    def refresh_audio_status(self):
        if not hasattr(self, "style"):
            return
        dark = self.theme_choice.get() == "dark"
        for side, player in enumerate(self.players):
            state, text = reported_audio_status(player)
            self.audio_status_vars[side].set(text)
            color = (("#60d394" if dark else "#176b35") if state == "on" else
                     ("#ffca5c" if dark else "#a55d00") if state == "unknown" else
                     comparison_colors(self.theme_choice.get())["muted"])
            name = "BackupAudio.TLabel" if side == 0 else "EncodedAudio.TLabel"
            self.style.configure(name, foreground=color)

    def apply_theme(self):
        colors = comparison_colors(self.theme_choice.get())
        self.style.theme_use("clam")
        self.style.configure(".", background=colors["bg"], foreground=colors["fg"],
                             fieldbackground=colors["field"])
        for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
            self.style.configure(name, background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TRadiobutton", background=colors["bg"], foreground=colors["fg"])
        self.style.map("TRadiobutton", background=[("active", colors["panel"])],
                       foreground=[("active", colors["fg"])])
        self.style.configure("TButton", background=colors["panel"], foreground=colors["fg"], padding=5)
        self.style.map("TButton", background=[("active", colors["select"])],
                       foreground=[("disabled", colors["muted"])])
        self.style.configure("Muted.TButton", background="#a53838", foreground="#ffffff", padding=5)
        self.style.map("Muted.TButton", background=[("active", "#bd4646")],
                       foreground=[("active", "#ffffff")])
        self.refresh_mute_button()
        for name in ("TCombobox", "TSpinbox"):
            self.style.configure(name, fieldbackground=colors["field"], foreground=colors["fg"],
                                 background=colors["panel"], arrowcolor=colors["fg"],
                                 insertcolor=colors["fg"])
            self.style.map(name, fieldbackground=[("readonly", colors["field"])],
                           foreground=[("readonly", colors["fg"])])
        self.style.configure("Horizontal.TScale", background=colors["bg"], troughcolor=colors["field"])
        self.style.configure("Horizontal.TProgressbar", background=colors["accent"], troughcolor=colors["field"])
        self.root.option_add("*TCombobox*Listbox.background", colors["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["select"])
        for window in [self.root, *self.detached]:
            window.configure(background=colors["bg"])

    def toggle_layout(self):
        if self.loading:
            return
        position, playing = max(0, self.players[1].get_time()), self.controls.playing
        self.controls.pause()
        # LibVLC requires the old HWND to stay alive until playback stops.
        for player in self.players:
            player.stop()
        for container, canvas in self.panels:
            container.destroy()
        if self.detached:
            for window in self.detached:
                window.destroy()
            self.detached = []
            parents = (self.body, self.body)
            self.body.pack(fill="both", expand=True, before=self.bar)
            self.root.minsize(760, 420)
            self.root.geometry("1400x650")
            self.layout_button.configure(text="Separate windows")
        else:
            self.body.pack_forget()
            self.root.state("normal")
            self.root.update_idletasks()
            width = max(self.bar.winfo_reqwidth(), self.audio_bar.winfo_reqwidth(), 760)
            height = self.root.winfo_reqheight()
            self.root.minsize(width, height)
            self.root.geometry(f"{width}x{height}")
            for label in ("Original", "Encoded video"):
                window = tk.Toplevel(self.root)
                window.title(label + " — use the shared controls")
                window.geometry("960x600")
                window.protocol("WM_DELETE_WINDOW", self.toggle_layout)
                window.bind("<space>", self.space)
                self.detached.append(window)
            parents = self.detached
            self.layout_button.configure(text="Side by side")
        self.panels = self.make_panels(parents)
        self.apply_theme()
        self.attach()
        self.start_at(position, playing)

    def tick(self):
        if self.closed:
            return
        # Audio tracks can initialize/reset asynchronously on load, seek or
        # surface recreation. Reassert selection without altering video timing.
        self.controls.apply_audio()
        self.refresh_audio_status()
        if not self.loading and not self.failure:
            current = max(0, self.players[1].get_time())
            length = max(1, self.players[1].get_length())
            self.timeline.configure(to=length)
            if not self.seeking:
                self.timeline.set(current)
            times = [max(0, p.get_time()) / 1000 for p in self.players]
            self.status.set(f"Backup: {times[0]:.2f}s   |   Encoded: {times[1]:.2f}s / {length / 1000:.2f}s"
                            f"   |   Backup offset: {self.controls.offset_ms:+d} ms")
            if any(str(p.get_state()).split(".")[-1] in {"Ended", "Error"} for p in self.players):
                self.controls.pause()
                self.play_button.configure(text="Play both")
                self.status.set("A video ended or reported a playback error. Files are unchanged; close and reopen to restart.")
        self.after_id = self.root.after(200, self.tick)

    def close(self):
        self.closed = True
        for identifier in (self.after_id, getattr(self, "ready_id", None)):
            if identifier:
                self.root.after_cancel(identifier)
        for player in self.players:
            player.stop()
            player.release()
        for media in self.media:
            media.release()
        self.instance.release()
        self.root.destroy()


def main(args=None):
    args = sys.argv[1:] if args is None else args
    root = tk.Tk()
    root.withdraw()
    try:
        if len(args) != 2:
            raise ValueError("Provide the original backup and encoded video paths")
        backup, encoded = (Path(value).resolve() for value in args)
        if not backup.is_file() or not encoded.is_file() or backup == encoded:
            raise ValueError("Two different existing video files are required")
        instance, dll_handles = load_vlc()
        root.deiconify()
        CompareWindow(root, instance, backup, encoded)
    except Exception as exc:
        messagebox.showerror("Cannot open comparison player", str(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
