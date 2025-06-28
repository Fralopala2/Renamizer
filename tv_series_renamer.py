import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import sqlite3
import os
import subprocess
import json
from datetime import datetime, timedelta
import platform
from tkinter.font import Font
from dotenv import load_dotenv
from tkinter import PhotoImage

# Ensure Windows-only execution
if platform.system() != "Windows":
    raise SystemExit("Esta aplicación está diseñada solo para Windows.")

# Load environment variables from .env file
load_dotenv()

# API setup
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_TOKEN = os.getenv("TMDB_TOKEN")
TVMAZE_API_URL = "https://api.tvmaze.com"
TMDB_API_URL = "https://api.themoviedb.org/3"

# SQLite database setup
DB_FILE = os.path.join(os.getenv("LOCALAPPDATA"), "TVSeriesRenamer", "cache.db")
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS shows (
        id INTEGER PRIMARY KEY,
        source TEXT,
        name TEXT,
        year INTEGER,
        data TEXT,
        last_updated TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS episodes (
        show_id INTEGER,
        source TEXT,
        season INTEGER,
        episode INTEGER,
        title TEXT,
        air_date TEXT,
        data TEXT
    )
""")
conn.commit()

# FFprobe path
FFPROBE_PATH = os.path.join(os.path.dirname(__file__), "ffprobe.exe")

# Special characters replacement for Windows (Plex-compatible)
SPECIAL_CHARS = {
    '<': '', '>': '', ':': '', '"': '', '/': '', '\\': '', '|': '', '?': '', '*': ''
}

def is_cache_expired(last_updated):
    if not last_updated:
        return True
    last_updated_dt = datetime.fromisoformat(last_updated)
    return (datetime.now() - last_updated_dt) > timedelta(days=7)

def search_shows(query):
    # TVMaze search
    cursor.execute("SELECT id, source, name, year, data, last_updated FROM shows WHERE source = ? AND name LIKE ?", ("tvmaze", f"%{query}%",))
    cached_tvmaze = cursor.fetchall()
    tvmaze_result = []
    if cached_tvmaze and not is_cache_expired(cached_tvmaze[0][5]):
        for row in cached_tvmaze:
            show_data = json.loads(row[4])
            display_name = show_data.get("name", "")
            if "akas" in show_data:
                for aka in show_data["akas"]:
                    if aka.get("country", {}).get("code") == "ES":
                        display_name = aka.get("name", display_name)
                        break
            tvmaze_result.append((row[0], display_name, row[3], show_data, "tvmaze"))
    else:
        try:
            response = requests.get(f"{TVMAZE_API_URL}/search/shows?q={query}")
            response.raise_for_status()
            shows = response.json()
            for show in shows:
                show_data = show["show"]
                if "akas" not in show_data:
                    akas_resp = requests.get(f"{TVMAZE_API_URL}/shows/{show_data['id']}")
                    if akas_resp.ok:
                        show_data_full = akas_resp.json()
                        if "akas" in show_data_full:
                            show_data["akas"] = show_data_full["akas"]
                year = show_data.get("premiered", "")[:4] if show_data.get("premiered") else None
                display_name = show_data.get("name", "")
                if "akas" in show_data:
                    for aka in show_data["akas"]:
                        if aka.get("country", {}).get("code") == "ES":
                            display_name = aka.get("name", display_name)
                            break
                cursor.execute(
                    "INSERT OR REPLACE INTO shows (id, source, name, year, data, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
                    (show_data["id"], "tvmaze", display_name, year, json.dumps(show_data), datetime.now().isoformat())
                )
                tvmaze_result.append((show_data["id"], display_name, year, show_data, "tvmaze"))
            conn.commit()
        except requests.RequestException as e:
            messagebox.showerror("Error", f"La búsqueda en TVmaze falló: {e}")

    # TMDB search
    headers = {"Authorization": f"Bearer {TMDB_TOKEN}"}
    tmdb_result = []
    cursor.execute("SELECT id, source, name, year, data, last_updated FROM shows WHERE source = ? AND name LIKE ?", ("tmdb", f"%{query}%",))
    cached_tmdb = cursor.fetchall()
    if cached_tmdb and not is_cache_expired(cached_tmdb[0][5]):
        for row in cached_tmdb:
            show_data = json.loads(row[4])
            display_name = show_data.get("name", "")
            tmdb_result.append((row[0], display_name, show_data.get("first_air_date", "")[:4], show_data, "tmdb"))
    else:
        try:
            response = requests.get(f"{TMDB_API_URL}/search/tv?api_key={TMDB_API_KEY}&query={query}", headers=headers)
            response.raise_for_status()
            shows = response.json().get("results", [])
            for show in shows:
                display_name = show.get("name", "")
                year = show.get("first_air_date", "")[:4] if show.get("first_air_date") else None
                cursor.execute(
                    "INSERT OR REPLACE INTO shows (id, source, name, year, data, last_updated) VALUES (?, ?, ?, ?, ?, ?)",
                    (show["id"], "tmdb", display_name, year, json.dumps(show), datetime.now().isoformat())
                )
                tmdb_result.append((show["id"], display_name, year, show, "tmdb"))
            conn.commit()
        except requests.RequestException as e:
            messagebox.showerror("Error", f"La búsqueda en TMDB falló: {e}")

    return tvmaze_result + tmdb_result

def get_episodes(show_id, source):
    cursor.execute("SELECT season, episode, title, air_date, data FROM episodes WHERE show_id = ? AND source = ?", (show_id, source))
    cached = cursor.fetchall()
    if cached and not is_cache_expired(cached[0][3]):
        return [(row[0], row[1], row[2], row[3], json.loads(row[4])) for row in cached]
    try:
        if source == "tvmaze":
            response = requests.get(f"{TVMAZE_API_URL}/shows/{show_id}/episodes")
            response.raise_for_status()
            episodes = response.json()
        else:  # tmdb
            headers = {"Authorization": f"Bearer {TMDB_TOKEN}"}
            response = requests.get(
                f"{TMDB_API_URL}/tv/{show_id}?api_key={TMDB_API_KEY}&language=es-ES",
                headers=headers
            )
            response.raise_for_status()
            seasons = response.json().get("seasons", [])
            episodes = []
            for season in seasons:
                season_number = season.get("season_number")
                if season_number is not None:
                    season_response = requests.get(
                        f"{TMDB_API_URL}/tv/{show_id}/season/{season_number}?api_key={TMDB_API_KEY}&language=es-ES",
                        headers=headers
                    )
                    season_response.raise_for_status()
                    episodes.extend(season_response.json().get("episodes", []))
        
        for ep in episodes:
            cursor.execute(
                "INSERT OR REPLACE INTO episodes (show_id, source, season, episode, title, air_date, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (show_id, source, ep["season"] if source == "tvmaze" else ep["season_number"], 
                 ep["number"] if source == "tvmaze" else ep["episode_number"], 
                 ep["name"], 
                 ep.get("airdate", "") if source == "tvmaze" else ep.get("air_date", ""), 
                 json.dumps(ep))
            )
        conn.commit()
        return [(ep["season"] if source == "tvmaze" else ep["season_number"], 
                ep["number"] if source == "tvmaze" else ep["episode_number"], 
                ep["name"], 
                ep.get("airdate", "") if source == "tvmaze" else ep.get("air_date", ""), 
                ep) for ep in episodes]
    except requests.RequestException as e:
        messagebox.showerror("Error", f"No se pudieron obtener los episodios de {source}: {e}")
        return []

def get_media_info(file_path):
    try:
        result = subprocess.run(
            [FFPROBE_PATH, "-v", "error", "-show_streams", "-print_format", "json", file_path],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
        audio_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
        return {
            "resolution": f"{video_stream['width']}x{video_stream['height']}" if video_stream else "",
            "video_codec": video_stream["codec_name"].upper() if video_stream else "",
            "audio_codec": audio_stream["codec_name"].upper() if audio_stream else "",
            "audio_channels": audio_stream.get("channels", "") if audio_stream else ""
        }
    except (subprocess.SubprocessError, KeyError):
        return {}

def rename_file(filename, show_name, season, episode, title, year, media_info, output_dir, include_title):
    season_str = f"s{season:02d}"
    episode_str = f"e{episode:02d}"
    filename_base = f"{show_name} - {season_str}{episode_str}"
    if title and include_title:
        filename_base += f" - {title}"
    for char, replacement in SPECIAL_CHARS.items():
        filename_base = filename_base.replace(char, replacement)
    ext = os.path.splitext(filename)[1] or ".mp4"
    season_folder = f"Temporada {season:02d}"
    show_folder = f"{show_name} ({year})" if year else show_name
    for char, replacement in SPECIAL_CHARS.items():
        show_folder = show_folder.replace(char, replacement)
    output_path = os.path.join(output_dir, "Series de TV", show_folder, season_folder)
    os.makedirs(output_path, exist_ok=True)
    new_filename = f"{filename_base}{ext}"
    return os.path.join(output_path, new_filename)

class Renamizer(tk.Tk):
    def __init__(self):
        super().__init__()
        # Logo como icono de la ventana
        try:
            self.logo_img = PhotoImage(file=os.path.join(os.path.dirname(__file__), "logo.png"))
            self.iconphoto(False, self.logo_img)
        except Exception as e:
            print(f"No se pudo cargar el logo: {e}")
        # Logo reducido para la interfaz
        try:
            self.logo_app_img = PhotoImage(file=os.path.join(os.path.dirname(__file__), "logo_app.png"))
        except Exception as e:
            print(f"No se pudo cargar el logo reducido: {e}")
            self.logo_app_img = None
        self.title("Renombrador de Series de TV para Plex")
        self.geometry("1280x800")
        self.minsize(1024, 768)
        self.configure(bg="#f0f0f0")
        
        # Custom fonts
        self.title_font = Font(family="Segoe UI", size=12, weight="bold")
        self.normal_font = Font(family="Segoe UI", size=10)
        self.small_font = Font(family="Segoe UI", size=9)
        
        # Variables
        self.selected_episodes = []
        self.selected_files = []
        self.shows = []
        self.output_dir = None
        self.include_episode_title = tk.BooleanVar(value=True)
        
        # Configure style
        self.style = ttk.Style()
        self.configure_style()
        
        # Create widgets
        self.create_widgets()
        
        # Center window
        self.eval('tk::PlaceWindow . center')

    def configure_style(self):
        self.style.theme_use('clam')
        
        # Main colors
        bg_color = "#f0f0f0"
        frame_bg = "#ffffff"
        accent_color = "#4a6fa5"
        hover_color = "#3a5a80"
        text_color = "#333333"
        highlight_color = "#e6f2ff"
        
        # Configure styles
        self.style.configure('.', background=bg_color, foreground=text_color, font=self.normal_font)
        self.style.configure('TFrame', background=bg_color)
        self.style.configure('TLabelframe', background=frame_bg, foreground=text_color, 
                            bordercolor="#cccccc", relief="solid", padding=10)
        self.style.configure('TLabelframe.Label', background=frame_bg, foreground=text_color)
        self.style.configure('TLabel', background=frame_bg, foreground=text_color)
        self.style.configure('TButton', background="#e0e0e0", foreground=text_color, 
                            borderwidth=1, relief="solid", padding=6)
        self.style.map('TButton', 
                      background=[('active', '#d0d0d0')],
                      relief=[('pressed', 'sunken')])
        self.style.configure('Accent.TButton', background=accent_color, foreground="white")
        self.style.map('Accent.TButton',
                      background=[('active', hover_color)],
                      foreground=[('active', 'white')])
        self.style.configure('TEntry', fieldbackground="white", foreground=text_color, 
                           bordercolor="#cccccc", lightcolor="#cccccc", 
                           padding=5, relief="solid")
        self.style.configure('Treeview', background="white", foreground=text_color, 
                           fieldbackground="white", rowheight=25, bordercolor="#dddddd")
        self.style.configure('Treeview.Heading', background="#e0e0e0", foreground=text_color, 
                            font=Font(family="Segoe UI", size=10, weight="bold"), 
                            relief="flat", padding=5)
        self.style.map('Treeview', background=[('selected', highlight_color)])
        self.style.configure('Vertical.TScrollbar', background="#e0e0e0", bordercolor="#cccccc", 
                            arrowcolor=text_color, relief="solid")
        self.style.configure('Horizontal.TScrollbar', background="#e0e0e0", bordercolor="#cccccc", 
                            arrowcolor=text_color, relief="solid")
        self.style.configure('TCheckbutton', background=frame_bg, foreground=text_color)
        self.style.configure('TRadiobutton', background=frame_bg, foreground=text_color)

    def create_widgets(self):
        # Main container
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Top section - Search and Shows
        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Left panel - TV Shows
        shows_frame = ttk.LabelFrame(top_frame, text=" Buscar Series de TV ", style='TLabelframe')
        shows_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Search bar
        search_frame = ttk.Frame(shows_frame)
        search_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.search_entry = ttk.Entry(search_frame, font=self.normal_font)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        search_btn = ttk.Button(search_frame, text="Buscar", command=self.search_shows)
        search_btn.pack(side=tk.LEFT)
        
        # Shows list with scrollbar
        list_container = ttk.Frame(shows_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        scroll_y = ttk.Scrollbar(list_container)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.shows_listbox = tk.Listbox(
            list_container, 
            bg="white", 
            fg="#333333", 
            font=self.normal_font, 
            selectbackground="#4a6fa5", 
            selectforeground="white",
            activestyle="none",
            borderwidth=1,
            relief="solid",
            highlightthickness=0,
            yscrollcommand=scroll_y.set
        )
        self.shows_listbox.pack(fill=tk.BOTH, expand=True)
        scroll_y.config(command=self.shows_listbox.yview)
        self.shows_listbox.bind("<<ListboxSelect>>", self.on_show_select)
        
        # Buttons below shows list
        btn_frame = ttk.Frame(shows_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="Títulos en Español", command=self.force_spanish).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Obtener Episodios", command=self.get_episodes_btn).pack(side=tk.LEFT, padx=2)
        
        # Middle panel - Episodes
        episodes_frame = ttk.LabelFrame(top_frame, text=" Episodios ", style='TLabelframe')
        episodes_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Episodes treeview with scrollbars
        tree_container = ttk.Frame(episodes_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        
        scroll_y = ttk.Scrollbar(tree_container)
        scroll_x = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.episodes_tree = ttk.Treeview(
            tree_container, 
            columns=("Temporada", "Episodio", "Título"), 
            show="headings",
            yscrollcommand=scroll_y.set,
            xscrollcommand=scroll_x.set
        )
        self.episodes_tree.pack(fill=tk.BOTH, expand=True)
        
        scroll_y.config(command=self.episodes_tree.yview)
        scroll_x.config(command=self.episodes_tree.xview)
        
        self.episodes_tree.heading("Temporada", text="Temporada")
        self.episodes_tree.heading("Episodio", text="Episodio")
        self.episodes_tree.heading("Título", text="Título")
        self.episodes_tree.column("Temporada", width=80, anchor="center")
        self.episodes_tree.column("Episodio", width=80, anchor="center")
        self.episodes_tree.column("Título", width=300)
        self.episodes_tree.bind("<Double-1>", self.add_episode)
        
        # Order options
        order_frame = ttk.Frame(episodes_frame)
        order_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(order_frame, text="Ordenar:").pack(side=tk.LEFT)
        ttk.Radiobutton(order_frame, text="Por emisión").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(order_frame, text="DVD").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(order_frame, text="Continuo").pack(side=tk.LEFT, padx=5)
        
        # Right panels container
        right_panels = ttk.Frame(top_frame)
        right_panels.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Selected Episodes panel
        selected_episodes_frame = ttk.LabelFrame(right_panels, text=" Episodios Seleccionados ", style='TLabelframe')
        selected_episodes_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        btn_frame = ttk.Frame(selected_episodes_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(btn_frame, text="Añadir Episodios", command=self.add_selected_episodes).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Ordenar Episodios", command=self.sort_episodes).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        tree_container = ttk.Frame(selected_episodes_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        
        scroll_y = ttk.Scrollbar(tree_container)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.selected_episodes_tree = ttk.Treeview(
            tree_container, 
            columns=("Episodio",), 
            show="headings",
            yscrollcommand=scroll_y.set
        )
        self.selected_episodes_tree.pack(fill=tk.BOTH, expand=True)
        scroll_y.config(command=self.selected_episodes_tree.yview)
        
        self.selected_episodes_tree.heading("Episodio", text="Episodios Seleccionados")
        self.selected_episodes_tree.column("Episodio", width=400)
        
        # Selected Files panel
        selected_files_frame = ttk.LabelFrame(right_panels, text=" Archivos Seleccionados ", style='TLabelframe')
        selected_files_frame.pack(fill=tk.BOTH, expand=True)
        
        btn_frame = ttk.Frame(selected_files_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Button(btn_frame, text="Añadir Archivos", command=self.add_files).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Añadir Carpetas", command=self.add_dirs).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(btn_frame, text="Ordenar Archivos", command=self.sort_files).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        tree_container = ttk.Frame(selected_files_frame)
        tree_container.pack(fill=tk.BOTH, expand=True)
        
        scroll_y = ttk.Scrollbar(tree_container)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.selected_files_tree = ttk.Treeview(
            tree_container, 
            columns=("Archivo",), 
            show="headings",
            yscrollcommand=scroll_y.set
        )
        self.selected_files_tree.pack(fill=tk.BOTH, expand=True)
        scroll_y.config(command=self.selected_files_tree.yview)
        
        self.selected_files_tree.heading("Archivo", text="Archivos Seleccionados")
        self.selected_files_tree.column("Archivo", width=400)
        
        # Bottom section - Output and actions
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))

        # Output directory
        output_frame = ttk.LabelFrame(bottom_frame, text=" Directorio de Salida ", style='TLabelframe')
        output_frame.pack(fill=tk.X, pady=(0, 10))

        self.output_dir_label = ttk.Label(output_frame, text="No seleccionado", font=self.small_font)
        self.output_dir_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        ttk.Button(output_frame, text="Seleccionar Directorio", command=self.select_output_dir).pack(side=tk.RIGHT, padx=2)
        ttk.Button(output_frame, text="Renombrar Archivos", command=self.rename_files, style='Accent.TButton').pack(side=tk.RIGHT, padx=2)

        # --- AQUI VA EL LOGO ---
        logo_frame = ttk.Frame(bottom_frame)
        logo_frame.pack(side=tk.LEFT, anchor="s", padx=(0, 10))
        try:
            if self.logo_app_img:
                logo_label = ttk.Label(logo_frame, image=self.logo_app_img)
                logo_label.pack()
        except Exception:
            pass

        # Action buttons
        action_frame = ttk.Frame(bottom_frame)
        action_frame.pack(fill=tk.X)

        # Left side buttons
        left_btn_frame = ttk.Frame(action_frame)
        left_btn_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(left_btn_frame, text="Salir", command=self.quit).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="Acerca de", command=self.about).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="Sitio Web", command=self.website).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="Exportar", command=self.export).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="Configuración", command=self.open_preferences).pack(side=tk.LEFT, padx=2)
        ttk.Button(left_btn_frame, text="Forzar Actualización", command=self.force_refresh).pack(side=tk.LEFT, padx=2)
        
        # Right side buttons
        right_btn_frame = ttk.Frame(action_frame)
        right_btn_frame.pack(side=tk.RIGHT, fill=tk.X)
        
        ttk.Button(right_btn_frame, text="Limpiar Listas", command=self.clear_lists).pack(side=tk.RIGHT, padx=2)
        ttk.Button(right_btn_frame, text="Eliminar Todo", command=self.clear_all).pack(side=tk.RIGHT, padx=2)

    def search_shows(self):
        query = self.search_entry.get()
        self.shows_listbox.delete(0, tk.END)
        self.shows = search_shows(query)
        for show in self.shows:
            self.shows_listbox.insert(tk.END, f"{show[1]} ({show[2]}) [{show[4].upper()}]")

    def on_show_select(self, event):
        selection = self.shows_listbox.curselection()
        if not selection:
            return
        show_index = selection[0]
        show = self.shows[show_index]
        self.episodes_tree.delete(*self.episodes_tree.get_children())
        episodes = get_episodes(show[0], show[4])
        for ep in episodes:
            self.episodes_tree.insert("", tk.END, values=(ep[0], ep[1], ep[2]))

    def add_episode(self, event):
        selection = self.episodes_tree.selection()
        for item in selection:
            values = self.episodes_tree.item(item, "values")
            if values not in self.selected_episodes:
                self.selected_episodes.append(values)
                self.selected_episodes_tree.insert("", tk.END, values=(f"T{values[0]}E{values[1]} - {values[2]}"))

    def add_selected_episodes(self):
        self.add_episode(None)

    def sort_episodes(self):
        self.selected_episodes.sort(key=lambda x: (int(x[0]), int(x[1])))
        self.selected_episodes_tree.delete(*self.selected_episodes_tree.get_children())
        for values in self.selected_episodes:
            self.selected_episodes_tree.insert("", tk.END, values=(f"T{values[0]}E{values[1]} - {values[2]}"))

    def add_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Archivos de Video", "*.mp4 *.mkv *.avi")])
        for file in files:
            if file not in self.selected_files:
                self.selected_files.append(file)
                self.selected_files_tree.insert("", tk.END, values=(os.path.basename(file)))

    def add_dirs(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            for root, _, files in os.walk(dir_path):
                for file in files:
                    if file.lower().endswith(('.mp4', '.mkv', '.avi')):
                        full_path = os.path.join(root, file)
                        if full_path not in self.selected_files:
                            self.selected_files.append(full_path)
                            self.selected_files_tree.insert("", tk.END, values=(os.path.basename(full_path)))

    def sort_files(self):
        self.selected_files.sort()
        self.selected_files_tree.delete(*self.selected_files_tree.get_children())
        for file in self.selected_files:
            self.selected_files_tree.insert("", tk.END, values=(os.path.basename(file)))

    def select_output_dir(self):
        output_dir = filedialog.askdirectory()
        if output_dir:
            self.output_dir = output_dir
            self.output_dir_label.config(text=f"Directorio de Salida: {output_dir}")
        else:
            self.output_dir = None
            self.output_dir_label.config(text="Directorio de Salida: No seleccionado")

    def rename_files(self):
        if not self.output_dir:
            messagebox.showerror("Error", "Por favor, selecciona un directorio de salida.")
            return
        if len(self.selected_episodes) != len(self.selected_files):
            messagebox.showerror("Error", "El número de episodios y archivos debe coincidir.")
            return
        for i, (episode, file) in enumerate(zip(self.selected_episodes, self.selected_files)):
            show_index = self.shows_listbox.curselection()
            if not show_index:
                messagebox.showerror("Error", "No se ha seleccionado ninguna serie.")
                return
            show = self.shows[show_index[0]]
            show_name = show[1]
            year = show[2]
            media_info = get_media_info(file)
            new_path = rename_file(
                os.path.basename(file),
                show_name,
                int(episode[0]),
                int(episode[1]),
                episode[2],
                year,
                media_info,
                self.output_dir,
                self.include_episode_title.get()
            )
            try:
                os.rename(file, new_path)
            except OSError as e:
                messagebox.showerror("Error", f"No se pudo renombrar {file}: {e}")
        messagebox.showinfo("Éxito", "¡Archivos renombrados con éxito! Vuelve a escanear tu biblioteca de Plex para actualizar.")
        self.clear_lists()

    def open_preferences(self):
        pref_window = tk.Toplevel(self)
        pref_window.title("Configuración")
        pref_window.geometry("400x200")
        pref_window.configure(bg="#f0f0f0")
        
        content_frame = ttk.Frame(pref_window)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        ttk.Label(content_frame, text="Incluir Título del Episodio en el Nombre:", font=self.normal_font).pack(pady=10)
        ttk.Checkbutton(content_frame, text="Habilitar", variable=self.include_episode_title).pack(pady=5)
        
        btn_frame = ttk.Frame(content_frame)
        btn_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(btn_frame, text="Guardar", command=pref_window.destroy, style='Accent.TButton').pack(pady=5)

    def force_spanish(self):
        selection = self.shows_listbox.curselection()
        if not selection:
            return
        show_index = selection[0]
        show = self.shows[show_index]
        show_data = show[3]
        display_name = show_data.get("name", "")
        if "akas" in show_data and show[4] == "tvmaze":
            for aka in show_data["akas"]:
                if aka.get("country", {}).get("code") == "ES":
                    display_name = aka.get("name", display_name)
                    break
        self.shows_listbox.delete(show_index)
        self.shows_listbox.insert(show_index, f"{display_name} ({show[2]}) [{show[4].upper()}]")
        self.shows[show_index] = (show[0], display_name, show[2], show_data, show[4])

    def get_episodes_btn(self):
        self.on_show_select(None)

    def about(self):
        messagebox.showinfo("Acerca de", "Renombrador de Series de TV para Plex\nPor Paco López\nVersión 1.0\n\nEsta aplicación te ayuda a renombrar episodios de series de TV para compatibilidad con Plex.\nUtiliza las APIs de TVMaze y TMDB para obtener datos de series y episodios.")

    def website(self):
        import webbrowser
        webbrowser.open("https://github.com/Fralopala2/Renamizer")

    def export(self):
        messagebox.showinfo("Exportar", "La funcionalidad de exportación no está implementada aún.")

    def force_refresh(self):
        selection = self.shows_listbox.curselection()
        if not selection:
            return
        show_index = selection[0]
        show = self.shows[show_index]
        cursor.execute("DELETE FROM shows WHERE id = ? AND source = ?", (show[0], show[4]))
        cursor.execute("DELETE FROM episodes WHERE show_id = ? AND source = ?", (show[0], show[4]))
        conn.commit()
        messagebox.showinfo("Caché", "Caché limpiado para la serie seleccionada. Por favor, busca de nuevo.")

    def clear_lists(self):
        self.selected_episodes_tree.delete(*self.selected_episodes_tree.get_children())
        self.selected_files_tree.delete(*self.selected_files_tree.get_children())
        self.selected_episodes = []
        self.selected_files = []

    def clear_all(self):
        self.clear_lists()
        self.shows_listbox.delete(0, tk.END)
        self.episodes_tree.delete(*self.episodes_tree.get_children())
        self.output_dir = None
        self.output_dir_label.config(text="Directorio de Salida: No seleccionado")

if __name__ == "__main__":
    app = Renamizer()
    app.mainloop()