"""
Main application window for PNG Background Remover.
Uses CustomTkinter for the UI and a background thread + queue for processing.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

import processor

# --------------------------------------------------------------------------
# Appearance
# --------------------------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# --------------------------------------------------------------------------
# Worker (module-level so it's easily picklable and testable)
# --------------------------------------------------------------------------

def _worker_func(file_pairs: list, q: queue.Queue, settings: dict):
    """
    Runs on a background thread. Processes each (input, output) pair and
    puts progress dicts onto the queue. Sends a 'done' message at the end.
    """
    total = len(file_pairs)
    errors = 0
    for i, (inp, out) in enumerate(file_pairs):
        result = processor.process_image(inp, out, **settings)
        if result['status'] == 'error':
            errors += 1
        q.put({
            'type': 'progress',
            'current': i + 1,
            'total': total,
            'filename': os.path.basename(inp),
            'result': result,
        })
    q.put({'type': 'done', 'total': total, 'errors': errors})


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class BgRemoverApp(ctk.CTk):

    _CARDS_PER_ROW = 4
    _THUMB_SIZE = 150

    def __init__(self):
        super().__init__()

        self.title("PNG Background Remover v1.0")
        self.geometry("980x800")
        self.minsize(720, 580)
        self.resizable(True, True)

        self._folder_var = tk.StringVar()
        self._selected_files = None
        self._processing = False
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._last_output_dir: str | None = None

        # PhotoImage refs — must be kept alive to prevent GC
        self._photo_refs: list = []
        # Ordered list of output paths for arrow navigation
        self._processed_paths: list = []
        self._viewer_index: int = 0
        # Full-size viewer overlay state
        self._viewer_frame: ctk.CTkFrame | None = None
        self._viewer_photo = None

        # Card grid tracking
        self._card_row = 0
        self._card_col = 0

        # Advanced settings vars
        self._adv_dark_tol  = tk.DoubleVar(value=10)
        self._adv_light_tol = tk.DoubleVar(value=20)
        self._adv_blur      = tk.DoubleVar(value=2.0)
        self._adv_visible   = False

        self._build_ui()
        self.bind('<Escape>', lambda e: self._close_viewer())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        tabs = ctk.CTkTabview(self)
        tabs.grid(row=0, column=0, padx=8, pady=8, sticky='nsew')

        tabs.add("Background Remover")
        tabs.add("Grid Slicer")

        self._build_remover_tab(tabs.tab("Background Remover"))
        GridSlicerTab(tabs.tab("Grid Slicer")).pack(fill='both', expand=True)

    def _build_remover_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(4, weight=1)

        # --- Row 0: folder selection ---
        top = ctk.CTkFrame(parent, corner_radius=8)
        top.grid(row=0, column=0, padx=12, pady=(12, 4), sticky='ew')
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Input:", width=50, anchor='w').grid(
            row=0, column=0, padx=(10, 4), pady=10)
        self._folder_entry = ctk.CTkEntry(
            top, textvariable=self._folder_var, state='readonly',
            placeholder_text="Select a folder or individual PNG file(s)…")
        self._folder_entry.grid(row=0, column=1, padx=4, pady=10, sticky='ew')
        ctk.CTkButton(top, text="Folder…", width=80,
                      command=self._browse_folder).grid(row=0, column=2, padx=(4, 2), pady=10)
        ctk.CTkButton(top, text="File(s)…", width=80,
                      command=self._browse_files).grid(row=0, column=3, padx=(2, 10), pady=10)

        # --- Row 1: controls bar ---
        ctrl = ctk.CTkFrame(parent, corner_radius=8)
        ctrl.grid(row=1, column=0, padx=12, pady=4, sticky='ew')
        ctrl.grid_columnconfigure(1, weight=1)

        self._start_btn = ctk.CTkButton(
            ctrl, text="Start Processing", width=140, command=self._start_processing)
        self._start_btn.grid(row=0, column=0, padx=(10, 8), pady=10)

        self._status_label = ctk.CTkLabel(
            ctrl, text="Select a folder or file(s), then click Start.", anchor='w')
        self._status_label.grid(row=0, column=1, padx=4, pady=10, sticky='ew')

        self._open_folder_btn = ctk.CTkButton(
            ctrl, text="Open Output Folder", width=150,
            command=self._open_output_folder, state='disabled')
        self._open_folder_btn.grid(row=0, column=2, padx=(4, 6), pady=10)

        self._adv_toggle_btn = ctk.CTkButton(
            ctrl, text="⚙ Advanced ▾", width=120, fg_color='transparent',
            border_width=1, command=self._toggle_advanced)
        self._adv_toggle_btn.grid(row=0, column=3, padx=(0, 10), pady=10)

        # --- Row 2: progress bar ---
        self._progress_bar = ctk.CTkProgressBar(parent)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=2, column=0, padx=12, pady=(0, 4), sticky='ew')

        # --- Row 3: advanced panel (hidden by default, parented to `parent`) ---
        self._adv_panel = ctk.CTkFrame(parent, corner_radius=8)
        self._adv_panel_parent = parent   # remember for toggle
        # Not gridded yet — toggled in/out

        self._build_advanced_panel(self._adv_panel)

        # --- Row 4: preview grid ---
        preview_outer = ctk.CTkFrame(parent, corner_radius=8)
        preview_outer.grid(row=4, column=0, padx=12, pady=(4, 12), sticky='nsew')
        preview_outer.grid_rowconfigure(0, weight=1)
        preview_outer.grid_columnconfigure(0, weight=1)

        self._preview_scroll = ctk.CTkScrollableFrame(
            preview_outer, label_text="Processed Images  —  click any image to preview")
        self._preview_scroll.grid(row=0, column=0, padx=4, pady=4, sticky='nsew')

    def _build_advanced_panel(self, parent):
        """Populate the advanced settings panel with three sliders."""
        parent.grid_columnconfigure((1, 4, 7), weight=1)

        def _slider_row(col, label, var, from_, to, fmt='{:.0f}'):
            ctk.CTkLabel(parent, text=label, anchor='e', width=140).grid(
                row=0, column=col, padx=(12, 6), pady=10)
            val_label = ctk.CTkLabel(parent, text=fmt.format(var.get()), width=36, anchor='w')

            def on_change(v, lbl=val_label, f=fmt, v_=var):
                lbl.configure(text=f.format(v_.get()))

            sl = ctk.CTkSlider(parent, from_=from_, to=to, variable=var,
                               command=on_change, width=160)
            sl.grid(row=0, column=col + 1, padx=4, pady=10, sticky='ew')
            val_label.grid(row=0, column=col + 2, padx=(2, 8), pady=10)

        _slider_row(0, "Dark BG tolerance",  self._adv_dark_tol,  5, 40)
        _slider_row(3, "Light BG tolerance", self._adv_light_tol, 5, 40)
        _slider_row(6, "Edge blur radius",   self._adv_blur,      0, 5, fmt='{:.1f}')

    def _toggle_advanced(self):
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv_panel.grid(row=3, column=0, padx=12, pady=(0, 4), sticky='ew')
            self._adv_toggle_btn.configure(text="⚙ Advanced ▴")
            # Shift preview row down
            self._adv_panel_parent.grid_rowconfigure(4, weight=1)
        else:
            self._adv_panel.grid_forget()
            self._adv_toggle_btn.configure(text="⚙ Advanced ▾")

    # ------------------------------------------------------------------
    # Browse handlers
    # ------------------------------------------------------------------

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select folder of PNG images")
        if folder:
            self._folder_var.set(folder)
            self._selected_files = None
            pngs = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
            self._status_label.configure(
                text=f"Folder: {len(pngs)} image(s) found. Click Start to process.")

    def _browse_files(self):
        files = filedialog.askopenfilenames(
            title="Select image(s)",
            filetypes=[("Images", "*.png *.jpg *.jpeg"), ("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"), ("All files", "*.*")])
        if files:
            self._selected_files = list(files)
            display = files[0] if len(files) == 1 else os.path.dirname(files[0])
            self._folder_var.set(display)
            self._status_label.configure(
                text=f"{len(files)} file(s) selected. Click Start to process.")

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def _start_processing(self):
        if getattr(self, '_selected_files', None):
            input_files = self._selected_files
            output_dir = os.path.join(os.path.dirname(input_files[0]), "background removal")
            file_pairs = [
                (f, os.path.join(output_dir, os.path.splitext(os.path.basename(f))[0] + '.png'))
                for f in input_files
            ]
        else:
            folder = self._folder_var.get().strip()
            if not folder or not os.path.isdir(folder):
                messagebox.showerror("No folder", "Please select a valid folder or file(s) first.")
                return
            png_files = sorted(f for f in os.listdir(folder)
                               if f.lower().endswith(('.png', '.jpg', '.jpeg')))
            if not png_files:
                messagebox.showinfo("No images", "No PNG or JPEG files found in the selected folder.")
                return
            output_dir = os.path.join(folder, "background removal")
            file_pairs = [
                (os.path.join(folder, f),
                 os.path.join(output_dir, os.path.splitext(f)[0] + '.png'))
                for f in png_files
            ]

        self._last_output_dir = output_dir

        # Snapshot settings at the moment Start is clicked
        settings = {
            'tolerance_dark':  int(self._adv_dark_tol.get()),
            'tolerance_light': int(self._adv_light_tol.get()),
            'blur_radius':     round(self._adv_blur.get(), 1),
        }

        self._close_viewer()
        self._clear_preview_grid()
        self._progress_bar.set(0)
        self._open_folder_btn.configure(state='disabled')
        self._status_label.configure(text=f"Starting… ({len(file_pairs)} files)")
        self._start_btn.configure(state='disabled')
        self._processing = True

        self._queue = queue.Queue()
        self._worker = threading.Thread(
            target=_worker_func,
            args=(file_pairs, self._queue, settings),
            daemon=True,
        )
        self._worker.start()
        self.after(50, self._poll_queue)

    # ------------------------------------------------------------------
    # Queue polling
    # ------------------------------------------------------------------

    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        if self._processing:
            self.after(50, self._poll_queue)

    def _handle_message(self, msg: dict):
        if msg['type'] == 'progress':
            current = msg['current']
            total   = msg['total']
            result  = msg['result']

            self._progress_bar.set(current / total)
            self._status_label.configure(
                text=f"Processing {current}/{total}: {msg['filename']}")

            if result['status'] == 'ok' and 'thumb' in result:
                self._add_preview_card(
                    result['thumb'], msg['filename'],
                    result.get('bg_type', ''), result.get('output_path', ''))

        elif msg['type'] == 'done':
            total  = msg['total']
            errors = msg['errors']
            self._processing = False
            self._progress_bar.set(1.0)
            self._start_btn.configure(state='normal')
            self._open_folder_btn.configure(state='normal')

            if errors == 0:
                self._status_label.configure(
                    text=f"Done! {total} image(s) saved to 'background removal' subfolder.")
                messagebox.showinfo(
                    "Complete",
                    f"All {total} image(s) processed successfully.\n"
                    f"Output: {self._last_output_dir}")
            else:
                self._status_label.configure(
                    text=f"Done with {errors} error(s). {total - errors}/{total} succeeded.")
                messagebox.showwarning(
                    "Done with errors",
                    f"{total - errors}/{total} images processed.\n"
                    f"{errors} file(s) failed — check they are valid PNGs.")

    # ------------------------------------------------------------------
    # Preview grid
    # ------------------------------------------------------------------

    def _clear_preview_grid(self):
        for widget in self._preview_scroll.winfo_children():
            widget.destroy()
        self._photo_refs.clear()
        self._processed_paths.clear()
        self._card_row = 0
        self._card_col = 0

    def _add_preview_card(self, thumb_pil, filename: str, bg_type: str, output_path: str):
        photo = ImageTk.PhotoImage(thumb_pil)
        self._photo_refs.append(photo)

        # Track path for arrow navigation; index is position in this list
        self._processed_paths.append(output_path)
        card_index = len(self._processed_paths) - 1

        card = ctk.CTkFrame(self._preview_scroll, corner_radius=6)
        card.grid(row=self._card_row, column=self._card_col, padx=6, pady=6)

        img_label = tk.Label(card, image=photo, bg='#2b2b2b', bd=0,
                             highlightthickness=0, cursor='hand2')
        img_label.image = photo
        img_label.pack(padx=4, pady=(4, 2))

        # Click → full-size viewer at this card's index
        img_label.bind('<Button-1>', lambda e, i=card_index: self._show_viewer(i))

        display_name = filename if len(filename) <= 18 else filename[:15] + '…'
        ctk.CTkLabel(card, text=display_name, font=('Arial', 10)).pack(padx=4, pady=(0, 2))

        if bg_type:
            badge_colors = {
                'black':      ('#555', '#ccc'),
                'white':      ('#ddd', '#333'),
                'checkered':  ('#3a6', '#fff'),
                'unknown':    ('#a63', '#fff'),
            }
            fg, tc = badge_colors.get(bg_type, ('#555', '#ccc'))
            ctk.CTkLabel(card, text=bg_type, font=('Arial', 9),
                         fg_color=fg, text_color=tc, corner_radius=4,
                         width=60, height=18).pack(padx=4, pady=(0, 4))

        self._card_col += 1
        if self._card_col >= self._CARDS_PER_ROW:
            self._card_col = 0
            self._card_row += 1

    # ------------------------------------------------------------------
    # Full-size image viewer (overlay)
    # ------------------------------------------------------------------

    def _show_viewer(self, index: int):
        if not self._processed_paths:
            return
        index = max(0, min(index, len(self._processed_paths) - 1))
        self._viewer_index = index
        output_path = self._processed_paths[index]

        if not output_path or not os.path.isfile(output_path):
            return

        # Rebuild overlay in place (reuse frame if already open, else create)
        if not self._viewer_frame:
            overlay = ctk.CTkFrame(self, corner_radius=0, fg_color='#1a1a1a')
            overlay.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            self._viewer_frame = overlay

            # Keyboard nav — bind on the overlay and the root window
            self.bind('<Left>',  lambda e: self._viewer_step(-1))
            self.bind('<Right>', lambda e: self._viewer_step(1))

            # Close button — top-right
            ctk.CTkButton(overlay, text='✕ Close', width=90, height=30,
                          command=self._close_viewer).place(
                              relx=1.0, rely=0.0, anchor='ne', x=-10, y=10)

            # Click dark background to dismiss (not the image itself)
            overlay.bind('<Button-1>', lambda e: self._close_viewer())
        else:
            overlay = self._viewer_frame
            # Remove old image/nav/caption widgets before redrawing
            for w in overlay.winfo_children():
                if getattr(w, '_viewer_content', False):
                    w.destroy()

        # Measure available space
        self.update_idletasks()
        win_w = self.winfo_width()
        win_h = self.winfo_height()
        nav_w = 60   # space reserved for each arrow button
        pad_h = 60
        max_w = win_w - nav_w * 2
        max_h = win_h - pad_h

        # Load, scale, composite over checkerboard
        img = Image.open(output_path)
        img_w, img_h = img.size
        scale = min(max_w / img_w, max_h / img_h, 1.0)
        disp_w = max(1, int(img_w * scale))
        disp_h = max(1, int(img_h * scale))

        checker = processor._tile_pattern(disp_w, disp_h, 12, (180, 180, 180), (230, 230, 230))
        scaled  = img.resize((disp_w, disp_h), Image.LANCZOS)
        if scaled.mode == 'RGBA':
            checker.paste(scaled, (0, 0), mask=scaled.split()[3])
        else:
            checker.paste(scaled.convert('RGB'), (0, 0))

        photo = ImageTk.PhotoImage(checker)
        self._viewer_photo = photo

        # Image — centered
        img_lbl = tk.Label(overlay, image=photo, bg='#1a1a1a', bd=0, highlightthickness=0)
        img_lbl._viewer_content = True
        img_lbl.place(relx=0.5, rely=0.5, anchor='center')

        total = len(self._processed_paths)

        # Left arrow (hidden when at first image)
        if index > 0:
            prev_btn = ctk.CTkButton(overlay, text='◀', width=44, height=60,
                                     command=lambda: self._viewer_step(-1),
                                     fg_color='#333', hover_color='#555')
            prev_btn._viewer_content = True
            prev_btn.place(relx=0.0, rely=0.5, anchor='w', x=8)

        # Right arrow (hidden when at last image)
        if index < total - 1:
            next_btn = ctk.CTkButton(overlay, text='▶', width=44, height=60,
                                     command=lambda: self._viewer_step(1),
                                     fg_color='#333', hover_color='#555')
            next_btn._viewer_content = True
            next_btn.place(relx=1.0, rely=0.5, anchor='e', x=-8)

        # Counter + filename caption
        caption = f"{index + 1} / {total}   —   {os.path.basename(output_path)}"
        cap_lbl = ctk.CTkLabel(overlay, text=caption, font=('Arial', 11), fg_color='#1a1a1a')
        cap_lbl._viewer_content = True
        cap_lbl.place(relx=0.5, rely=1.0, anchor='s', y=-12)

    def _viewer_step(self, delta: int):
        self._show_viewer(self._viewer_index + delta)

    def _close_viewer(self):
        self.unbind('<Left>')
        self.unbind('<Right>')
        if self._viewer_frame:
            self._viewer_frame.place_forget()
            self._viewer_frame.destroy()
            self._viewer_frame = None
        self._viewer_photo = None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _open_output_folder(self):
        if self._last_output_dir and os.path.isdir(self._last_output_dir):
            os.startfile(self._last_output_dir)


# ---------------------------------------------------------------------------
# Grid Slicer Tab
# ---------------------------------------------------------------------------

def _slicer_worker(img_path: str, grid_info: dict,
                   remove_bg: bool, settings: dict,
                   q: queue.Queue):
    img = Image.open(img_path)
    results = processor.crop_grid_cells(
        img,
        grid_info['row_boundaries'],
        grid_info['col_boundaries'],
        remove_bg=remove_bg,
        **settings,
    )
    for r in results:
        q.put({'type': 'progress', 'result': r})
    q.put({'type': 'done', 'total': len(results)})


class GridSlicerTab(ctk.CTkFrame):

    def __init__(self, parent):
        super().__init__(parent, corner_radius=0, fg_color='transparent')
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._image_path: str | None = None
        self._original_img: Image.Image | None = None
        self._grid_info: dict | None = None
        self._output_dir: str | None = None

        self._processing = False
        self._queue: queue.Queue = queue.Queue()
        self._photo_refs: list = []
        self._viewer_index = 0
        self._viewer_frame = None
        self._viewer_photo = None

        # One entry per generated cell:
        # {'pil_img': Image, 'name': str, 'selected': bool,
        #  'frame': widget, 'img_label': widget,
        #  'photo_on': PhotoImage, 'photo_off': PhotoImage}
        self._cell_data: list = []

        # Manual grid vars
        self._manual_rows = tk.IntVar(value=3)
        self._manual_cols = tk.IntVar(value=3)
        self._manual_visible = False
        self._remove_bg_var = tk.BooleanVar(value=True)

        # Card grid tracking
        self._card_row = 0
        self._card_col = 0

        self._build_ui()

        # Bind escape to close viewer
        self.winfo_toplevel().bind('<Escape>', lambda e: self._close_viewer(), add='+')

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # --- Row 0: top controls ---
        top = ctk.CTkFrame(self, corner_radius=8)
        top.grid(row=0, column=0, padx=12, pady=(8, 4), sticky='ew')
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(top, text="Select Image…", width=110,
                      command=self._browse_image).grid(row=0, column=0, padx=(10,4), pady=10)

        self._file_label = ctk.CTkLabel(top, text="No image selected", anchor='w')
        self._file_label.grid(row=0, column=1, padx=4, pady=10, sticky='ew')

        self._detect_btn = ctk.CTkButton(top, text="Auto-Detect Grid", width=130,
                                         command=self._auto_detect, state='disabled')
        self._detect_btn.grid(row=0, column=2, padx=4, pady=10)

        ctk.CTkButton(top, text="Manual ▾", width=90, fg_color='transparent',
                      border_width=1,
                      command=self._toggle_manual).grid(row=0, column=3, padx=(2,10), pady=10)

        # --- Row 1: status + action bar ---
        ctrl = ctk.CTkFrame(self, corner_radius=8)
        ctrl.grid(row=1, column=0, padx=12, pady=4, sticky='ew')
        ctrl.grid_columnconfigure(1, weight=1)

        self._slice_btn = ctk.CTkButton(ctrl, text="Generate Cells", width=130,
                                        command=self._start_slice, state='disabled')
        self._slice_btn.grid(row=0, column=0, padx=(10,4), pady=10)

        self._slicer_status = ctk.CTkLabel(ctrl, text="Select an image to begin.", anchor='w')
        self._slicer_status.grid(row=0, column=1, padx=4, pady=10, sticky='ew')

        ctk.CTkCheckBox(ctrl, text="Remove BG", variable=self._remove_bg_var,
                        width=110).grid(row=0, column=2, padx=4, pady=10)

        self._open_btn = ctk.CTkButton(ctrl, text="Open Output Folder", width=150,
                                       command=self._open_output_folder, state='disabled')
        self._open_btn.grid(row=0, column=3, padx=(4,10), pady=10)

        # Progress bar
        self._progress = ctk.CTkProgressBar(self)
        self._progress.set(0)
        self._progress.grid(row=1, column=0, padx=12, pady=(0,0), sticky='ew')
        # NOTE: gridded below ctrl — use a sub-frame trick to stack them
        # Actually re-assign rows properly:
        self._progress.grid_forget()

        # Redo layout with progress in row 2, preview in row 3
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=1)

        self._progress = ctk.CTkProgressBar(self)
        self._progress.set(0)
        self._progress.grid(row=2, column=0, padx=12, pady=(0, 4), sticky='ew')

        # Manual panel (hidden by default)
        self._manual_panel = ctk.CTkFrame(self, corner_radius=8)
        self._build_manual_panel(self._manual_panel)

        # Preview area
        preview_outer = ctk.CTkFrame(self, corner_radius=8)
        preview_outer.grid(row=3, column=0, padx=12, pady=(4, 8), sticky='nsew')
        preview_outer.grid_rowconfigure(0, weight=0)   # sel_bar (fixed height)
        preview_outer.grid_rowconfigure(1, weight=1)   # content area (expands)
        preview_outer.grid_columnconfigure(0, weight=1)

        self._preview_scroll = ctk.CTkScrollableFrame(
            preview_outer,
            label_text="Preview — detect grid, then Generate Cells")
        self._preview_scroll.grid(row=0, column=0, rowspan=2,
                                  sticky='nsew', padx=4, pady=4)

        self._preview_label = tk.Label(self._preview_scroll, bg='#2b2b2b', bd=0,
                                       highlightthickness=0, cursor='crosshair')
        self._preview_label.pack(padx=4, pady=4)

        # Selection toolbar (shown above thumb grid, hidden initially)
        self._sel_bar = ctk.CTkFrame(preview_outer, corner_radius=6, height=36)
        self._sel_bar.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(self._sel_bar, text="✓ All", width=70, height=28,
                      command=self._select_all).grid(row=0, column=0, padx=(8,2), pady=4)
        ctk.CTkButton(self._sel_bar, text="✗ None", width=70, height=28,
                      command=self._select_none).grid(row=0, column=1, padx=(2,4), pady=4)
        self._sel_label = ctk.CTkLabel(self._sel_bar, text="", anchor='w')
        self._sel_label.grid(row=0, column=2, padx=4, pady=4, sticky='ew')
        self._save_sel_btn = ctk.CTkButton(
            self._sel_bar, text="💾 Save Selected (0/0)", width=190, height=28,
            command=self._save_selected, state='disabled')
        self._save_sel_btn.grid(row=0, column=3, padx=(4,8), pady=4)
        # Not shown until cells are generated

        # Thumbnail grid frame (shown after generating cells)
        self._thumb_frame = ctk.CTkScrollableFrame(
            preview_outer, label_text="Generated cells — click to toggle selection")
        # Not gridded until cells are generated

    def _build_manual_panel(self, parent):
        parent.grid_columnconfigure((1, 3), weight=0)
        ctk.CTkLabel(parent, text="Rows:").grid(row=0, column=0, padx=(12,4), pady=8)
        ctk.CTkEntry(parent, textvariable=self._manual_rows, width=50).grid(
            row=0, column=1, padx=4, pady=8)
        ctk.CTkLabel(parent, text="Cols:").grid(row=0, column=2, padx=(12,4), pady=8)
        ctk.CTkEntry(parent, textvariable=self._manual_cols, width=50).grid(
            row=0, column=3, padx=4, pady=8)
        ctk.CTkButton(parent, text="Apply", width=80,
                      command=self._apply_manual).grid(row=0, column=4, padx=(8,12), pady=8)

    def _toggle_manual(self):
        self._manual_visible = not self._manual_visible
        if self._manual_visible:
            self._manual_panel.grid(row=1, column=0, padx=12, pady=(0,4), sticky='ew',
                                    in_=self)
            # shift rows down — insert before ctrl row by placing in correct spot
            # simpler: just grid_configure row weights
        else:
            self._manual_panel.grid_forget()

    def _apply_manual(self):
        if not self._original_img:
            messagebox.showerror("No image", "Load an image first.")
            return
        try:
            nr = max(1, self._manual_rows.get())
            nc = max(1, self._manual_cols.get())
        except Exception:
            messagebox.showerror("Invalid", "Rows and cols must be integers.")
            return
        self._grid_info = processor.find_grid_boundaries_manual(self._original_img, nr, nc)
        self._slicer_status.configure(text=f"Manual grid: {nr}×{nc}. Ready to slice.")
        self._slice_btn.configure(state='normal')
        # Hide the manual panel and show the grid preview
        if self._manual_visible:
            self._toggle_manual()
        self._update_grid_preview()

    # ------------------------------------------------------------------
    # Image loading & detection
    # ------------------------------------------------------------------

    def _browse_image(self):
        path = filedialog.askopenfilename(
            title="Select grid image",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg"), ("PNG", "*.png"),
                       ("JPEG", "*.jpg;*.jpeg"), ("All files", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()  # force decode now so errors surface immediately
        except Exception as exc:
            messagebox.showerror("Cannot open image", str(exc))
            return
        self._image_path = path
        self._original_img = img
        self._grid_info = None
        self._slice_btn.configure(state='disabled')
        self._open_btn.configure(state='disabled')
        self._progress.set(0)
        self._file_label.configure(text=os.path.basename(path))
        self._detect_btn.configure(state='normal')
        self._slicer_status.configure(text="Image loaded. Click Auto-Detect Grid or use Manual.")
        # Always restore the preview pane and clear old thumbnails
        self._clear_thumb_grid()
        self._sel_bar.grid_forget()
        self._thumb_frame.grid_forget()
        self._preview_scroll.grid(row=0, column=0, rowspan=2,
                                  sticky='nsew', padx=4, pady=4)
        self._show_plain_preview()

    def _show_plain_preview(self):
        """Show the image without grid lines."""
        if not self._original_img:
            return
        display = self._original_img.convert('RGB').copy()
        display.thumbnail((700, 700), Image.LANCZOS)
        photo = ImageTk.PhotoImage(display)
        self._preview_photo = photo
        self._preview_label.configure(image=photo)
        self._preview_label.image = photo

    def _auto_detect(self):
        if not self._original_img:
            return
        self._slicer_status.configure(text="Detecting grid…")
        self.update_idletasks()

        result = processor.find_grid_boundaries(self._original_img)

        if result['status'] != 'ok':
            self._slicer_status.configure(
                text=f"Auto-detect failed: {result['reason']}  —  try Manual.")
            return

        self._grid_info = result
        nr, nc = result['n_rows'], result['n_cols']
        self._slicer_status.configure(
            text=f"Detected {nr}×{nc} grid. Verify lines below, then Slice & Export.")
        self._slice_btn.configure(state='normal')
        self._update_grid_preview()

    def _update_grid_preview(self):
        """Redraw the preview image with green grid lines overlaid."""
        if not self._original_img or not self._grid_info:
            return
        # Make sure the preview scroll is visible (might be hidden after a previous slice)
        self._sel_bar.grid_forget()
        self._thumb_frame.grid_forget()
        self._preview_scroll.grid(row=0, column=0, rowspan=2,
                                  sticky='nsew', padx=4, pady=4)
        overlay = processor.draw_grid_overlay(
            self._original_img,
            self._grid_info['row_boundaries'],
            self._grid_info['col_boundaries'])
        overlay.thumbnail((700, 700), Image.LANCZOS)
        photo = ImageTk.PhotoImage(overlay)
        self._preview_photo = photo
        self._preview_label.configure(image=photo)
        self._preview_label.image = photo

    # ------------------------------------------------------------------
    # Slicing
    # ------------------------------------------------------------------

    def _start_slice(self):
        if not self._grid_info or not self._image_path:
            return

        settings = {'tolerance_dark': 10, 'tolerance_light': 20, 'blur_radius': 2.0}
        remove_bg = self._remove_bg_var.get()

        # Switch preview area: sel_bar at row 0, thumb_frame at row 1
        self._preview_scroll.grid_forget()
        self._sel_bar.grid(row=0, column=0, sticky='ew', padx=4, pady=(4, 2))
        self._thumb_frame.grid(row=1, column=0, sticky='nsew', padx=4, pady=(2, 4))

        self._clear_thumb_grid()
        self._save_sel_btn.configure(text="💾 Save Selected (0/0)", state='disabled')
        self._progress.set(0)
        self._slice_btn.configure(state='disabled')
        self._open_btn.configure(state='disabled')
        self._processing = True

        self._queue = queue.Queue()
        threading.Thread(
            target=_slicer_worker,
            args=(self._image_path, self._grid_info,
                  remove_bg, settings, self._queue),
            daemon=True,
        ).start()
        self.after(50, self._poll_queue)

    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        if self._processing:
            self.after(50, self._poll_queue)

    def _handle_msg(self, msg):
        if msg['type'] == 'progress':
            r = msg['result']
            current = r['current']
            total = r['total']
            self._progress.set(current / total)
            self._slicer_status.configure(
                text=f"Generating {current}/{total}  —  row {r['row']}, col {r['col']}")
            if r['status'] == 'ok':
                label = f"r{r['row']} c{r['col']}"
                self._add_thumb_card(r['thumb'], label, r['pil_img'])
        elif msg['type'] == 'done':
            self._processing = False
            total = msg['total']
            self._slice_btn.configure(state='normal')
            self._update_sel_label()
            self._slicer_status.configure(
                text=f"{total} cells ready — deselect any you don't want, then Save Selected.")

    # ------------------------------------------------------------------
    # Thumbnail grid
    # ------------------------------------------------------------------

    def _clear_thumb_grid(self):
        for w in self._thumb_frame.winfo_children():
            w.destroy()
        self._photo_refs.clear()
        self._cell_data.clear()
        self._card_row = 0
        self._card_col = 0

    @staticmethod
    def _dim_thumb(thumb: Image.Image) -> Image.Image:
        """Blend thumbnail with dark overlay for deselected state."""
        dark = Image.new('RGB', thumb.size, (25, 25, 25))
        return Image.blend(thumb.convert('RGB'), dark, 0.60)

    def _add_thumb_card(self, thumb_pil: Image.Image, label: str,
                        pil_img: Image.Image):
        """Add a selectable thumbnail card. All cells start selected."""
        photo_on  = ImageTk.PhotoImage(thumb_pil)
        photo_off = ImageTk.PhotoImage(self._dim_thumb(thumb_pil))
        self._photo_refs.extend([photo_on, photo_off])

        idx = len(self._cell_data)

        card = ctk.CTkFrame(self._thumb_frame, corner_radius=6,
                            border_width=2, border_color='#4CAF50')
        card.grid(row=self._card_row, column=self._card_col, padx=5, pady=5)

        img_lbl = tk.Label(card, image=photo_on, bg='#2b2b2b', bd=0,
                           highlightthickness=0, cursor='hand2')
        img_lbl.image = photo_on
        img_lbl.pack(padx=4, pady=(4, 2))
        img_lbl.bind('<Button-1>', lambda e, i=idx: self._toggle_selection(i))

        ctk.CTkLabel(card, text=label, font=('Arial', 10)).pack(padx=4, pady=(0, 4))

        self._cell_data.append({
            'pil_img':   pil_img,
            'name':      label,
            'selected':  True,
            'frame':     card,
            'img_label': img_lbl,
            'photo_on':  photo_on,
            'photo_off': photo_off,
        })

        self._card_col += 1
        if self._card_col >= 4:
            self._card_col = 0
            self._card_row += 1

    def _toggle_selection(self, idx: int):
        """Toggle a cell's selected state and update its visual."""
        if idx >= len(self._cell_data):
            return
        cell = self._cell_data[idx]
        cell['selected'] = not cell['selected']
        if cell['selected']:
            cell['frame'].configure(border_color='#4CAF50')
            cell['img_label'].configure(image=cell['photo_on'])
            cell['img_label'].image = cell['photo_on']
        else:
            cell['frame'].configure(border_color='#555555')
            cell['img_label'].configure(image=cell['photo_off'])
            cell['img_label'].image = cell['photo_off']
        self._update_sel_label()

    def _select_all(self):
        for i in range(len(self._cell_data)):
            if not self._cell_data[i]['selected']:
                self._cell_data[i]['selected'] = True
                self._cell_data[i]['frame'].configure(border_color='#4CAF50')
                self._cell_data[i]['img_label'].configure(
                    image=self._cell_data[i]['photo_on'])
                self._cell_data[i]['img_label'].image = self._cell_data[i]['photo_on']
        self._update_sel_label()

    def _select_none(self):
        for i in range(len(self._cell_data)):
            if self._cell_data[i]['selected']:
                self._cell_data[i]['selected'] = False
                self._cell_data[i]['frame'].configure(border_color='#555555')
                self._cell_data[i]['img_label'].configure(
                    image=self._cell_data[i]['photo_off'])
                self._cell_data[i]['img_label'].image = self._cell_data[i]['photo_off']
        self._update_sel_label()

    def _update_sel_label(self):
        total = len(self._cell_data)
        n_sel = sum(1 for c in self._cell_data if c['selected'])
        self._save_sel_btn.configure(
            text=f"💾 Save Selected ({n_sel}/{total})",
            state='normal' if n_sel > 0 else 'disabled')

    def _save_selected(self):
        if not self._image_path:
            return
        base_name = os.path.splitext(os.path.basename(self._image_path))[0]
        self._output_dir = os.path.join(
            os.path.dirname(self._image_path), f"{base_name}_slices")
        os.makedirs(self._output_dir, exist_ok=True)

        saved = 0
        for cell in self._cell_data:
            if not cell['selected']:
                continue
            fname = f"{base_name}_{cell['name'].replace(' ', '_')}.png"
            out_path = os.path.join(self._output_dir, fname)
            cell['pil_img'].save(out_path, 'PNG')
            saved += 1

        self._open_btn.configure(state='normal')
        self._slicer_status.configure(
            text=f"Saved {saved} cell(s) to '{os.path.basename(self._output_dir)}'  "
                 f"(next to your source image)")

    # ------------------------------------------------------------------
    # Full-size viewer (same pattern as BgRemoverApp)
    # ------------------------------------------------------------------

    def _show_viewer(self, index: int):
        if not self._cell_data:
            return
        index = max(0, min(index, len(self._cell_data) - 1))
        self._viewer_index = index

        root = self.winfo_toplevel()

        if not self._viewer_frame:
            overlay = ctk.CTkFrame(root, corner_radius=0, fg_color='#1a1a1a')
            overlay.place(x=0, y=0, relwidth=1.0, relheight=1.0)
            self._viewer_frame = overlay
            root.bind('<Left>',  lambda e: self._viewer_step(-1), add='+')
            root.bind('<Right>', lambda e: self._viewer_step(1),  add='+')
            ctk.CTkButton(overlay, text='✕ Close', width=90, height=30,
                          command=self._close_viewer).place(
                              relx=1.0, rely=0.0, anchor='ne', x=-10, y=10)
            overlay.bind('<Button-1>', lambda e: self._close_viewer())
        else:
            overlay = self._viewer_frame
            for w in overlay.winfo_children():
                if getattr(w, '_viewer_content', False):
                    w.destroy()

        root.update_idletasks()
        win_w = root.winfo_width()
        win_h = root.winfo_height()
        max_w = win_w - 120
        max_h = win_h - 60

        img = self._cell_data[index]['pil_img']
        iw, ih = img.size
        scale = min(max_w / iw, max_h / ih, 1.0)
        dw, dh = max(1, int(iw * scale)), max(1, int(ih * scale))

        checker = processor._tile_pattern(dw, dh, 12, (180, 180, 180), (230, 230, 230))
        scaled = img.resize((dw, dh), Image.LANCZOS)
        if scaled.mode == 'RGBA':
            checker.paste(scaled, (0, 0), mask=scaled.split()[3])
        else:
            checker.paste(scaled.convert('RGB'), (0, 0))

        photo = ImageTk.PhotoImage(checker)
        self._viewer_photo = photo

        img_lbl = tk.Label(overlay, image=photo, bg='#1a1a1a', bd=0, highlightthickness=0)
        img_lbl._viewer_content = True
        img_lbl.place(relx=0.5, rely=0.5, anchor='center')

        total = len(self._cell_data)
        cell_name = self._cell_data[index]['name']
        if index > 0:
            b = ctk.CTkButton(overlay, text='◀', width=44, height=60,
                              command=lambda: self._viewer_step(-1),
                              fg_color='#333', hover_color='#555')
            b._viewer_content = True
            b.place(relx=0.0, rely=0.5, anchor='w', x=8)
        if index < total - 1:
            b = ctk.CTkButton(overlay, text='▶', width=44, height=60,
                              command=lambda: self._viewer_step(1),
                              fg_color='#333', hover_color='#555')
            b._viewer_content = True
            b.place(relx=1.0, rely=0.5, anchor='e', x=-8)

        cap = ctk.CTkLabel(overlay,
                           text=f"{index+1} / {total}   —   {cell_name}",
                           font=('Arial', 11), fg_color='#1a1a1a')
        cap._viewer_content = True
        cap.place(relx=0.5, rely=1.0, anchor='s', y=-12)

    def _viewer_step(self, delta):
        self._show_viewer(self._viewer_index + delta)

    def _close_viewer(self):
        root = self.winfo_toplevel()
        root.unbind('<Left>')
        root.unbind('<Right>')
        if self._viewer_frame:
            self._viewer_frame.place_forget()
            self._viewer_frame.destroy()
            self._viewer_frame = None
        self._viewer_photo = None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _open_output_folder(self):
        if self._output_dir and os.path.isdir(self._output_dir):
            os.startfile(self._output_dir)
