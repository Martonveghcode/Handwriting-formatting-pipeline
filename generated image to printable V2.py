import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox, ttk
from PIL import Image, ImageDraw
import numpy as np
from sklearn.cluster import DBSCAN
import os
import uuid


# === UTILITY FUNCTIONS ===
def cm_to_px(cm, dpi=300):
    return int((cm / 2.54) * dpi)


def flatten_transparency(img, background_color=(255, 255, 255)):
    if img.mode == 'RGBA':
        flattened = Image.new("RGB", img.size, background_color)
        flattened.paste(img, mask=img.split()[3])
        return flattened
    return img.convert("RGB")


# === CONNECTION FUNCTIONS ===
def detect_and_connect_image(image, line_thickness, y_tolerance, line_color):
    pixels = np.array(image)
    yellow_mask = (
        ((pixels[:, :, 0] >= 200) & (pixels[:, :, 0] <= 255))
        & ((pixels[:, :, 1] >= 180) & (pixels[:, :, 1] <= 240))
        & (pixels[:, :, 2] < 50)
    )

    ys, xs = np.where(yellow_mask)
    points = list(zip(xs, ys))

    centers = []
    if points:
        clustering = DBSCAN(eps=6, min_samples=3).fit(points)
        labels = clustering.labels_
        unique_labels = set(labels)
        for label in unique_labels:
            if label == -1:
                continue
            cluster_points = np.array([p for p, l in zip(points, labels) if l == label])
            mean_x = int(np.mean(cluster_points[:, 0]))
            mean_y = int(np.mean(cluster_points[:, 1]))
            centers.append((mean_x, mean_y))

    if not centers:
        return image

    centers_array = np.array(centers)
    y_values = centers_array[:, 1].reshape(-1, 1)
    eps = max(1, int(abs(y_tolerance)))
    row_clustering = DBSCAN(eps=eps, min_samples=1).fit(y_values)
    row_labels = row_clustering.labels_

    draw_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)

    # Group detected segments by row to draw a single horizontal guide per line
    unique_rows = np.unique(row_labels)
    for label in unique_rows:
        if label == -1:
            continue
        row_points = centers_array[row_labels == label]
        if len(row_points) < 2:
            continue
        line_y = int(np.median(row_points[:, 1]))
        x_start = max(0, int(np.min(row_points[:, 0])) - line_thickness)
        x_end = image.width - 1
        draw.line([(x_start, line_y), (x_end, line_y)], fill=line_color + (255,), width=line_thickness)

    return Image.alpha_composite(image, draw_layer)

# === RESIZE & STITCH ===
def resize_to_match_width(images, target_width):
    resized_images = []
    for img in images:
        if img.width == target_width:
            resized_images.append(img)
            continue
        padded = Image.new("RGBA", (target_width, img.height), (255, 255, 255, 0))
        padded.paste(img, (0, 0))
        resized_images.append(padded)
    return resized_images


def stitch_images_from_paths(file_paths, connect=False, overlap_px=0):
    if not file_paths:
        return None, "Warning: No images to stitch.", None
    try:
        images = [Image.open(f).convert("RGBA") for f in file_paths]
        base_width = max(img.width for img in images)
        resized_images = resize_to_match_width(images, base_width)

        overlap_value = int(overlap_px)
        y_offset = 0
        segments = []
        for idx, img in enumerate(resized_images):
            segment_start = y_offset
            segment_end = segment_start + img.height
            segments.append((segment_start, segment_end, img))
            y_offset = segment_end
            if idx < len(resized_images) - 1:
                effective_overlap = overlap_value
                if effective_overlap > 0:
                    effective_overlap = min(effective_overlap, img.height - 1)
                y_offset -= effective_overlap
                y_offset = max(0, y_offset)

        total_height = max(end for _, end, _ in segments)
        total_height = max(1, total_height)
        stitched_img = Image.new("RGBA", (base_width, total_height))

        segment_bounds = []
        for segment_start, segment_end, img in segments:
            stitched_img.paste(img, (0, segment_start))
            segment_bounds.append((segment_start, segment_end))

        if connect:
            try:
                thickness = int(thickness_entry.get())
                tolerance = int(tolerance_entry.get())
                r, g, b = map(int, color_entry.get().split(","))
            except Exception:
                return None, "Warning: Invalid line settings", None
            stitched_img = detect_and_connect_image(stitched_img, thickness, tolerance, (r, g, b))

        return stitched_img, None, segment_bounds

    except Exception as e:
        return None, f"Error: {e}", None


# === FINAL FORMATTING ===
def prepare_printable_a4(image, original_path, dpi=300):
    a4_width_px = cm_to_px(21, dpi)
    a4_height_px = cm_to_px(29.7, dpi)
    margin_left = cm_to_px(0.4, dpi)
    margin_right = cm_to_px(0.5, dpi)
    margin_top = cm_to_px(2.0, dpi)

    printable_width = a4_width_px - margin_left - margin_right

    img = flatten_transparency(image)
    img_width, img_height = img.size

    if img_width > printable_width:
        scale_factor = printable_width / img_width
        new_width = printable_width
        new_height = int(img_height * scale_factor)
        img = img.resize((new_width, new_height), Image.LANCZOS)
        img_width, img_height = img.size

    available_height = a4_height_px - margin_top
    if img_height > available_height:
        messagebox.showerror("Too Tall", "Image too tall for A4 page with margins.")
        return

    canvas = Image.new("RGB", (a4_width_px, a4_height_px), "white")
    canvas.paste(img, (margin_left, margin_top))

    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_dir = os.path.join(desktop_path, "for printing")
    os.makedirs(output_dir, exist_ok=True)

    unique_id = uuid.uuid4().hex[:8]
    output_path = os.path.join(output_dir, f"output_{unique_id}_formatted.png")

    canvas.save(output_path, dpi=(dpi, dpi))
    messagebox.showinfo("Success", f"Saved printable A4 image:\n{output_path}")


# === PDF EXPORT ===
def generate_pdf_pages(image, segments, dpi=300):
    a4_width_px = cm_to_px(21, dpi)
    a4_height_px = cm_to_px(29.7, dpi)
    margin_left = cm_to_px(0.4, dpi)
    margin_right = cm_to_px(0.5, dpi)
    margin_top = cm_to_px(2.0, dpi)

    printable_width = a4_width_px - margin_left - margin_right
    printable_height = a4_height_px - margin_top

    img = flatten_transparency(image)
    img_width, img_height = img.size

    if not segments:
        raise ValueError("No segment data available for pagination.")

    scale_factor = 1.0
    if img_width > printable_width:
        scale_factor = printable_width / img_width
        new_width = printable_width
        new_height = int(round(img_height * scale_factor))
        img = img.resize((new_width, new_height), Image.LANCZOS)
        img_width, img_height = img.size
        segments = [
            (
                int(round(start * scale_factor)),
                int(round(end * scale_factor)),
            )
            for start, end in segments
        ]
    else:
        segments = [(int(round(start)), int(round(end))) for start, end in segments]

    img_height = img.size[1]
    normalized_segments = []
    for start, end in segments:
        start = max(0, min(start, img_height))
        end = max(0, min(end, img_height))
        if end > start:
            normalized_segments.append((start, end))
    if not normalized_segments:
        return []

    pages_meta = []
    page_start = None
    page_end = None
    current_segments = []

    for start, end in normalized_segments:
        segment_height = end - start
        if segment_height > printable_height:
            raise ValueError("A source image exceeds the printable height of the page.")
        if page_start is None:
            page_start = start
            page_end = end
            current_segments = [(start, end)]
            continue

        new_page_end = max(page_end, end)
        if new_page_end - page_start <= printable_height:
            current_segments.append((start, end))
            page_end = new_page_end
        else:
            pages_meta.append((page_start, page_end, current_segments))
            page_start = start
            page_end = end
            current_segments = [(start, end)]

    if current_segments:
        pages_meta.append((page_start, page_end, current_segments))

    pages = []
    for page_start, _, segs in pages_meta:
        canvas = Image.new("RGB", (a4_width_px, a4_height_px), "white")
        for start, end in segs:
            segment = img.crop((0, start, img_width, end))
            offset_y = margin_top + (start - page_start)
            canvas.paste(segment, (margin_left, int(offset_y)))
        pages.append(canvas)

    return pages


# === GUI ACTIONS ===
def browse_file():
    file_path = filedialog.askopenfilename(filetypes=[("PNG Images", "*.png")])
    if file_path:
        file_entry.delete(0, tk.END)
        file_entry.insert(0, file_path)
        status_var.set("Loaded image for connection")


def choose_color():
    color_code = colorchooser.askcolor(title="Choose Line Color")
    if color_code[0]:
        r, g, b = map(int, color_code[0])
        color_entry.delete(0, tk.END)
        color_entry.insert(0, f"{r},{g},{b}")
        status_var.set("Updated connection color")


def run_script():
    path = file_entry.get()
    if not path:
        status_var.set("Warning: Please select a PNG file")
        return
    try:
        thickness = int(thickness_entry.get())
        tolerance = int(tolerance_entry.get())
        r, g, b = map(int, color_entry.get().split(","))
    except Exception:
        status_var.set("Warning: Invalid input")
        return
    image = Image.open(path).convert("RGBA")
    result = detect_and_connect_image(image, thickness, tolerance, (r, g, b))
    base, _ = os.path.splitext(path)
    output_path = base + "_connected.png"
    result.save(output_path)
    try:
        os.remove(path)
        status_var.set(f"Success: Saved {output_path} and deleted original")
    except Exception:
        status_var.set(f"Success: Saved {output_path} but original could not be deleted")


def add_images_to_stitch():
    files = filedialog.askopenfilenames(filetypes=[("PNG Images", "*.png")])
    if not files:
        return
    existing = set(stitch_listbox.get(0, tk.END))
    added = 0
    for path in files:
        if path not in existing:
            stitch_listbox.insert(tk.END, path)
            added += 1
    if added:
        status_var.set(f"Added {added} file(s) to stitch queue")
    else:
        status_var.set("Files already in the stitch queue")


def remove_selected_files():
    selections = stitch_listbox.curselection()
    if not selections:
        return
    for index in reversed(selections):
        stitch_listbox.delete(index)
    status_var.set("Removed selected file(s) from queue")


def run_stitch(connect=False, format_to_a4=False, to_pdf=False):
    stitch_and_save(connect, format_to_a4, to_pdf)


def stitch_and_save(connect=False, format_to_a4=False, to_pdf=False):
    files = stitch_listbox.get(0, tk.END)
    if not files:
        messagebox.showwarning("No Files", "Please add images to stitch.")
        return
    try:
        overlap_value = int(overlap_entry.get())
    except (ValueError, TypeError):
        status_var.set("Warning: Invalid overlap value; using 0")
        overlap_value = 0
    if overlap_value < 0:
        status_var.set("Warning: Overlap cannot be negative; using 0")
        overlap_value = 0

    result_img, error, segment_bounds = stitch_images_from_paths(
        files, connect=connect, overlap_px=overlap_value
    )
    if error:
        status_var.set(error)
        return
    if segment_bounds is None:
        status_var.set("Error: Failed to compute segment layout.")
        return

    for file_path in files:
        try:
            os.remove(file_path)
        except Exception:
            pass

    if to_pdf:
        pdf_name = pdf_name_entry.get().strip()
        if not pdf_name:
            status_var.set("Warning: Enter a PDF file name")
            return
        output_dir = os.path.join(
            os.path.expanduser("~"), "Desktop", "for printing"
        )
        os.makedirs(output_dir, exist_ok=True)
        if not pdf_name.lower().endswith(".pdf"):
            pdf_name += ".pdf"
        save_path = os.path.join(output_dir, pdf_name)
        try:
            pages = generate_pdf_pages(result_img, segment_bounds, dpi=300)
        except ValueError as err:
            messagebox.showerror("Pagination Error", str(err))
            status_var.set(f"Error: {err}")
            return
        if not pages:
            status_var.set("Error: No printable pages generated")
            return
        first_page, *remaining_pages = pages
        first_page.save(
            save_path,
            "PDF",
            resolution=300.0,
            save_all=True,
            append_images=remaining_pages,
        )
        messagebox.showinfo("Success", f"Saved PDF:\n{save_path}")
        status_var.set(f"Success: PDF saved as {os.path.basename(save_path)}")
    elif format_to_a4:
        prepare_printable_a4(result_img, files[0])
        status_var.set("Success: Connected, stitched, and formatted for A4 printing")
    else:
        save_path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")])
        if save_path:
            result_img.save(save_path)
            status = "Connected and stitched" if connect else "Stitched"
            status_var.set(f"Success: {status} image saved as {os.path.basename(save_path)}")


def move_file(direction):
    selected = stitch_listbox.curselection()
    if not selected:
        return
    for index in selected:
        new_index = index + direction
        if 0 <= new_index < stitch_listbox.size():
            text = stitch_listbox.get(index)
            stitch_listbox.delete(index)
            stitch_listbox.insert(new_index, text)
            stitch_listbox.selection_set(new_index)


# === GUI SETUP ===
root = tk.Tk()
root.title("Image Tools: Connect, Stitch, Format")
root.minsize(760, 640)
root.configure(bg="#f3f4f6")

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

style.configure("TFrame", background="#f3f4f6")
style.configure("Card.TLabelframe", background="#ffffff", borderwidth=0)
style.configure("Card.TLabelframe.Label", font=("Segoe UI", 12, "bold"))
style.configure("Card.TFrame", background="#ffffff")
style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), foreground="#ffffff")
style.map(
    "Accent.TButton",
    background=[("!disabled", "#2563eb"), ("pressed", "#1d4ed8"), ("active", "#3b82f6")],
    foreground=[("disabled", "#d1d5db"), ("!disabled", "#ffffff")]
)
style.configure("Secondary.TButton", font=("Segoe UI", 10), padding=6)
style.map(
    "Secondary.TButton",
    background=[("!disabled", "#e5e7eb"), ("pressed", "#d1d5db"), ("active", "#dbeafe")]
)
style.configure("Status.TLabel", font=("Segoe UI", 10), background="#f3f4f6")

root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

main_frame = ttk.Frame(root, padding=(20, 20, 20, 20))
main_frame.grid(row=0, column=0, sticky="nsew")
main_frame.columnconfigure(0, weight=1)

header = ttk.Frame(main_frame, style="Card.TFrame", padding=(20, 15))
header.grid(row=0, column=0, sticky="ew", pady=(0, 15))
header.columnconfigure(0, weight=1)

header_label = ttk.Label(
    header,
    text="MyText Handwriting Image Toolkit",
    font=("Segoe UI", 18, "bold"),
    background="#ffffff"
)
header_label.grid(row=0, column=0, sticky="w")

subheader_label = ttk.Label(
    header,
    text="Connect yellow strokes, stitch exported lines, and format for A4 printing.",
    font=("Segoe UI", 10),
    foreground="#4b5563",
    background="#ffffff"
)
subheader_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

connect_section = ttk.LabelFrame(main_frame, text="Connect Yellow Lines", padding=15, style="Card.TLabelframe")
connect_section.grid(row=1, column=0, sticky="nsew")
connect_section.columnconfigure(1, weight=1)

file_label = ttk.Label(connect_section, text="PNG File:")
file_label.grid(row=0, column=0, sticky="e", pady=4, padx=(0, 10))

file_entry = ttk.Entry(connect_section)
file_entry.grid(row=0, column=1, sticky="ew", pady=4)

browse_button = ttk.Button(connect_section, text="Browse", command=browse_file, style="Secondary.TButton")
browse_button.grid(row=0, column=2, padx=(10, 0), pady=4)

thickness_label = ttk.Label(connect_section, text="Line Thickness (px):")
thickness_label.grid(row=1, column=0, sticky="e", pady=4, padx=(0, 10))

thickness_entry = ttk.Entry(connect_section, width=8)
thickness_entry.insert(0, "7")
thickness_entry.grid(row=1, column=1, sticky="w", pady=4)

tolerance_label = ttk.Label(connect_section, text="Vertical Tolerance (px):")
tolerance_label.grid(row=2, column=0, sticky="e", pady=4, padx=(0, 10))

tolerance_entry = ttk.Entry(connect_section, width=8)
tolerance_entry.insert(0, "2")
tolerance_entry.grid(row=2, column=1, sticky="w", pady=4)

color_label = ttk.Label(connect_section, text="Line Color (R,G,B):")
color_label.grid(row=3, column=0, sticky="e", pady=4, padx=(0, 10))

color_entry = ttk.Entry(connect_section)
color_entry.insert(0, "0,0,0")
color_entry.grid(row=3, column=1, sticky="ew", pady=4)

color_button = ttk.Button(connect_section, text="Pick Color", command=choose_color, style="Secondary.TButton")
color_button.grid(row=3, column=2, padx=(10, 0), pady=4)

run_button = ttk.Button(connect_section, text="Connect Strokes", command=run_script, style="Accent.TButton")
run_button.grid(row=4, column=0, columnspan=3, pady=(12, 0))

stitch_section = ttk.LabelFrame(main_frame, text="Stitch & Export", padding=15, style="Card.TLabelframe")
stitch_section.grid(row=2, column=0, sticky="nsew", pady=(15, 0))
stitch_section.columnconfigure(0, weight=1)

instructions = ttk.Label(
    stitch_section,
    text="Queue PNG exports in order. Arrange them, set overlap, and export formatted pages or PDFs.",
    foreground="#4b5563"
)
instructions.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

listbox_frame = ttk.Frame(stitch_section, style="Card.TFrame")
listbox_frame.grid(row=1, column=0, columnspan=3, sticky="nsew")
listbox_frame.columnconfigure(0, weight=1)

stitch_listbox = tk.Listbox(listbox_frame, height=8, activestyle="none", selectmode=tk.EXTENDED, borderwidth=0, highlightthickness=1)
stitch_listbox.grid(row=0, column=0, sticky="nsew")

scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical", command=stitch_listbox.yview)
scrollbar.grid(row=0, column=1, sticky="ns")
stitch_listbox.configure(yscrollcommand=scrollbar.set)

settings_frame = ttk.Frame(stitch_section)
settings_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
settings_frame.columnconfigure(3, weight=1)

overlap_label = ttk.Label(settings_frame, text="Overlap (px):")
overlap_label.grid(row=0, column=0, sticky="e", padx=(0, 10))

overlap_entry = ttk.Entry(settings_frame, width=8)
overlap_entry.insert(0, "0")
overlap_entry.grid(row=0, column=1, sticky="w")

pdf_name_label = ttk.Label(settings_frame, text="PDF File Name:")
pdf_name_label.grid(row=0, column=2, sticky="e", padx=(20, 10))

pdf_name_entry = ttk.Entry(settings_frame)
pdf_name_entry.insert(0, "handwriting.pdf")
pdf_name_entry.grid(row=0, column=3, sticky="ew")

controls_frame = ttk.Frame(stitch_section)
controls_frame.grid(row=3, column=0, sticky="ew", pady=12)
controls_frame.columnconfigure(4, weight=1)

add_button = ttk.Button(controls_frame, text="Add Images", command=add_images_to_stitch, style="Secondary.TButton")
add_button.grid(row=0, column=0, padx=(0, 6))

remove_button = ttk.Button(controls_frame, text="Remove Selected", command=remove_selected_files, style="Secondary.TButton")
remove_button.grid(row=0, column=1, padx=6)

up_button = ttk.Button(controls_frame, text="Move Up", command=lambda: move_file(-1), style="Secondary.TButton")
up_button.grid(row=0, column=2, padx=6)

down_button = ttk.Button(controls_frame, text="Move Down", command=lambda: move_file(1), style="Secondary.TButton")
down_button.grid(row=0, column=3, padx=6)

buttons_frame = ttk.Frame(stitch_section)
buttons_frame.grid(row=4, column=0, sticky="ew")
buttons_frame.columnconfigure((0, 1, 2, 3), weight=1)

stitch_button = ttk.Button(buttons_frame, text="Stitch & Save", command=lambda: run_stitch(False, False, False), style="Accent.TButton")
stitch_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))

connect_stitch_button = ttk.Button(buttons_frame, text="Stitch & Connect", command=lambda: run_stitch(True, False, False), style="Accent.TButton")
connect_stitch_button.grid(row=0, column=1, sticky="ew", padx=6)

full_process_button = ttk.Button(buttons_frame, text="Connect + Stitch + Format", command=lambda: run_stitch(True, True, False), style="Accent.TButton")
full_process_button.grid(row=0, column=2, sticky="ew", padx=6)

pdf_button = ttk.Button(buttons_frame, text="Connect + Stitch + PDF", command=lambda: run_stitch(True, False, True), style="Accent.TButton")
pdf_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))

status_var = tk.StringVar(value="Ready")
status_label = ttk.Label(main_frame, textvariable=status_var, style="Status.TLabel")
status_label.grid(row=3, column=0, sticky="w", pady=(15, 0))

footer = ttk.Label(main_frame, text="Optimised for MyText handwriting exports by Thaines", font=("Segoe UI", 9), foreground="#6b7280")
footer.grid(row=4, column=0, sticky="w", pady=(6, 0))

root.mainloop()


