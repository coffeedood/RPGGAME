import os
import sys
import json
import urllib.parse
import subprocess
import string
import random
import hashlib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from fuzzywuzzy import process
import socket
import time
import threading

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

THUMBNAIL_FOLDER = os.path.join("playlists", "thumbnails")
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)
THUMBNAIL_SIZE = (200, 150)

MAX_FILENAME_LENGTH = 215
VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

if sys.platform == "win32":
    VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
else:
    VLC_PATH = "vlc"

def open_file_with_default_app(filepath):
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["open", filepath])
    else:
        subprocess.run(["xdg-open", filepath])

PLAYLIST_FOLDER = "playlists"
os.makedirs(PLAYLIST_FOLDER, exist_ok=True)

CONFIG_FILE = os.path.join(PLAYLIST_FOLDER, "config.json")

def load_config():
    default_config = {
        "auto_scan_enabled": False,
        "scan_folders": {
            "mkv": [],
            "mp4": [],
            "pdf": [],
            "music": []
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return default_config

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

def get_thumbnail_path(media_path):
    path_hash = hashlib.md5(media_path.encode()).hexdigest()
    return os.path.join(THUMBNAIL_FOLDER, f"{path_hash}.png")

def extract_video_thumbnail(video_path):
    if not os.path.exists(video_path):
        return None

    thumb_path = get_thumbnail_path(video_path)
    if os.path.exists(thumb_path):
        return thumb_path

    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-ss", "00:00:30",
            "-vframes", "1",
            "-vf", f"scale={THUMBNAIL_SIZE[0]}:{THUMBNAIL_SIZE[1]}:force_original_aspect_ratio=decrease",
            "-y", thumb_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if os.path.exists(thumb_path):
            return thumb_path
    except:
        pass

    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-ss", "00:00:05",
            "-vframes", "1",
            "-vf", f"scale={THUMBNAIL_SIZE[0]}:{THUMBNAIL_SIZE[1]}:force_original_aspect_ratio=decrease",
            "-y", thumb_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        if os.path.exists(thumb_path):
            return thumb_path
    except:
        pass

    return None

def extract_audio_thumbnail(audio_path):
    if not MUTAGEN_AVAILABLE or not PIL_AVAILABLE:
        return None

    if not os.path.exists(audio_path):
        return None

    thumb_path = get_thumbnail_path(audio_path)
    if os.path.exists(thumb_path):
        return thumb_path

    try:
        audio = MutagenFile(audio_path)
        if audio is None:
            return None

        artwork = None
        if hasattr(audio, 'pictures') and audio.pictures:
            artwork = audio.pictures[0].data
        elif 'APIC:' in audio:
            artwork = audio['APIC:'].data
        elif hasattr(audio, 'tags'):
            for key in audio.tags.keys():
                if key.startswith('APIC'):
                    artwork = audio.tags[key].data
                    break
            if not artwork and 'covr' in audio.tags:
                artwork = bytes(audio.tags['covr'][0])

        if artwork:
            import io
            img = Image.open(io.BytesIO(artwork))
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            img.save(thumb_path, "PNG")
            return thumb_path
    except:
        pass

    return None

def extract_pdf_thumbnail(pdf_path):
    if not PIL_AVAILABLE:
        return None

    thumb_path = get_thumbnail_path(pdf_path)
    if os.path.exists(thumb_path):
        return thumb_path

    try:
        cmd = [
            "pdftoppm", "-png", "-f", "1", "-l", "1",
            "-scale-to", str(THUMBNAIL_SIZE[0]),
            pdf_path, thumb_path.replace(".png", "")
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        generated = thumb_path.replace(".png", "-1.png")
        if os.path.exists(generated):
            os.rename(generated, thumb_path)
            return thumb_path
    except:
        pass

    return None

def get_thumbnail(media_path, media_type):
    if not PIL_AVAILABLE:
        return None

    ext = os.path.splitext(media_path)[1].lower()

    if media_type in ("MKV", "MP4", "AVI", "Video") or ext in (".mkv", ".mp4", ".avi", ".webm", ".mov"):
        return extract_video_thumbnail(media_path)
    elif media_type == "PDF" or ext == ".pdf":
        return extract_pdf_thumbnail(media_path)
    elif ext in (".mp3", ".flac", ".ogg", ".m4a", ".wma", ".aac", ".aif", ".aiff"):
        return extract_audio_thumbnail(media_path)

    return None

HISTORY_AUDIO = os.path.join(PLAYLIST_FOLDER, "history.m3u")
HISTORY_VIDEO = os.path.join(PLAYLIST_FOLDER, "history2.m3u")
PDF_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_history.txt")
PDF_OPENED_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_opened_history.txt")
SEARCH_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "search_history.txt")

class MKVPlaylistGenerator:
    def __init__(self):
        self.video_source_dir = ""
        self.playlist_dest_dir = PLAYLIST_FOLDER

    def set_directories(self, video_source_dir):
        self.video_source_dir = video_source_dir

    def sanitize_playlist_name(self, name):
        return ''.join(c for c in name if c in VALID_CHARS).strip()

    def create_mkv_playlists(self):
        try:
            if not os.path.exists(self.video_source_dir) or not os.path.isdir(self.video_source_dir):
                print(f"Error: Source directory '{self.video_source_dir}' not found.")
                return False

            if not os.path.exists(self.playlist_dest_dir):
                os.makedirs(self.playlist_dest_dir)

            mkv_files = []
            for root, _, files in os.walk(self.video_source_dir):
                for file in files:
                    if file.lower().endswith(".mkv"):
                        full_path = os.path.abspath(os.path.join(root, file))
                        mkv_files.append(full_path)

            if not mkv_files:
                print("No MKV files found.")
                return False

            for file_path in mkv_files:
                filename = os.path.splitext(os.path.basename(file_path))[0]
                safe_name = self.sanitize_playlist_name(filename)
                safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                playlist_path = os.path.join(self.playlist_dest_dir, f"{safe_name}.m3u")

                encoded_path = urllib.parse.quote(file_path)
                playlist_entry = f"file:///{encoded_path}"

                with open(playlist_path, "w", encoding="utf-8") as f:
                    f.write(f"# Movie: {filename}\n")
                    f.write(f"{playlist_entry}\n")

                print(f"Written MKV playlist: {playlist_path}")

            return True

        except Exception as e:
            print(f"Error occurred while creating MKV playlists: {e}")
            return False

def create_mp4_playlists(folder, playlist_dest):
    """Scan folder for mp4 files and create one .m3u playlist per mp4 file."""
    if not folder or not os.path.isdir(folder):
        return False

    mp4_files = []
    for root, _, files in os.walk(folder):
        for file in files:
            if file.lower().endswith(".mp4"):
                full_path = os.path.abspath(os.path.join(root, file))
                mp4_files.append(full_path)

    if not mp4_files:
        return False

    if not os.path.exists(playlist_dest):
        os.makedirs(playlist_dest)

    for file_path in mp4_files:
        filename = os.path.splitext(os.path.basename(file_path))[0]
        safe_name = ''.join(c for c in filename if c in VALID_CHARS).strip()
        safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
        playlist_path = os.path.join(playlist_dest, f"{safe_name}.m3u")

        encoded_path = urllib.parse.quote(file_path)
        playlist_entry = f"file:///{encoded_path}"

        try:
            with open(playlist_path, "w", encoding="utf-8") as f:
                f.write(f"# Movie: {filename}\n")
                f.write(f"{playlist_entry}\n")
            print(f"Written MP4 playlist: {playlist_path}")
        except Exception as e:
            print(f"Failed to write playlist for {file_path}: {e}")

    return True

def create_music_playlists(folder, playlist_dest):
    """
    Scan a folder expecting an Artist/Album structure and create playlists.
    """
    if not folder or not os.path.isdir(folder):
        return False

    if not os.path.exists(playlist_dest):
        os.makedirs(playlist_dest)

    def write_playlist(name, songs, prefix="# Playlist"):
        """Helper function to write a .m3u playlist file."""
        safe_name = ''.join(c for c in name if c in VALID_CHARS).strip()
        if not safe_name:
            safe_name = "playlist_" + hashlib.md5(name.encode()).hexdigest()[:8]

        safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
        playlist_path = os.path.join(playlist_dest, f"{safe_name}.m3u")

        try:
            with open(playlist_path, "w", encoding="utf-8") as f:
                f.write(f"# {prefix}: {name}\n")
                for song_path in songs:
                    abs_song_path = os.path.abspath(song_path)
                    encoded_path = urllib.parse.quote(abs_song_path)
                    f.write(f"file:///{encoded_path}\n")
            print(f"Written playlist: {playlist_path}")
        except Exception as e:
            print(f"Failed to write playlist for {name}: {e}")

    found_music = False
    for artist_name in os.listdir(folder):
        artist_path = os.path.join(folder, artist_name)
        if not os.path.isdir(artist_path):
            continue

        all_artist_songs = []
        for album_name in os.listdir(artist_path):
            album_path = os.path.join(artist_path, album_name)
            if not os.path.isdir(album_path):
                continue

            album_songs = []
            for song_file in os.listdir(album_path):
                if song_file.lower().endswith(('.aif', '.aiff')):
                    found_music = True
                    song_path = os.path.join(album_path, song_file)
                    album_songs.append(song_path)

            if album_songs:
                album_songs.sort()
                for i, song_path in enumerate(album_songs):
                    song_title = os.path.splitext(os.path.basename(song_path))[0]
                    rotated_songs = album_songs[i:] + album_songs[:i]
                    write_playlist(song_title, rotated_songs, prefix="# Song")

                album_playlist_name = f"{artist_name} - {album_name}"
                write_playlist(album_playlist_name, album_songs, prefix="# Album")
                all_artist_songs.extend(album_songs)

        if all_artist_songs:
            all_artist_songs.sort()
            write_playlist(artist_name, all_artist_songs, prefix="# Artist")

    return True

def log_pdf_opened(pdf_path):
    """Log PDF path to opened PDFs history file (avoid duplicates)."""
    try:
        if not os.path.exists(PDF_OPENED_HISTORY_FILE):
            with open(PDF_OPENED_HISTORY_FILE, "w", encoding="utf-8") as f:
                f.write(pdf_path + "\n")
            return

        with open(PDF_OPENED_HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = set(line.strip() for line in f if line.strip())

        if pdf_path not in lines:
            with open(PDF_OPENED_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(pdf_path + "\n")
    except Exception as e:
        print(f"Failed to log opened PDF: {e}")

def log_search_history(name):
    try:
        with open(SEARCH_HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"{name}\n")
    except:
        pass

def search_and_open(query):
    playlist_files = [f for f in os.listdir(PLAYLIST_FOLDER) if f.endswith(".m3u")]
    playlist_titles = [os.path.splitext(f)[0] for f in playlist_files]

    pdf_files = []
    if os.path.exists(PDF_HISTORY_FILE):
        with open(PDF_HISTORY_FILE, "r", encoding="utf-8") as f:
            pdf_paths = [line.strip() for line in f if line.strip()]
        pdf_files = [os.path.basename(p) for p in pdf_paths]

    all_titles = playlist_titles + pdf_files

    if not all_titles:
        messagebox.showwarning("No Data", "No playlists or PDFs found to search.")
        return

    best_match, score = process.extractOne(query, all_titles)
    if score < 60:
        messagebox.showinfo("No Match", f"No close matches found for '{query}'.")
        return

    if best_match in playlist_titles:
        playlist_path = os.path.join(PLAYLIST_FOLDER, best_match + ".m3u")
        try:
            launch_vlc(playlist_path)
            log_search_history(best_match)
            messagebox.showinfo("Opening Playlist", f"Opening playlist: {best_match}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open playlist in VLC:\n{e}")
    else:
        try:
            with open(PDF_HISTORY_FILE, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            matched_paths = [p for p in lines if os.path.basename(p) == best_match]
            if not matched_paths:
                messagebox.showerror("Error", "PDF file not found in history.")
                return
            pdf_path = matched_paths[0]
            if not os.path.exists(pdf_path):
                messagebox.showwarning("Missing File", f"PDF file no longer exists:\n{pdf_path}")
                return

            open_file_with_default_app(pdf_path)
            log_pdf_opened(pdf_path)  # Log that this PDF has been opened
            log_search_history(best_match)
            messagebox.showinfo("Opening PDF", f"Opening PDF: {best_match}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open PDF:\n{e}")

def play_random_from_history2(history_file, description):
    import os
    import sys
    import urllib.parse
    import subprocess
    import random
    from natsort import natsorted
    import string
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from fuzzywuzzy import process

    # Maximum length for Windows filenames
    MAX_FILENAME_LENGTH = 215
    VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

    # Path to VLC executable
    if sys.platform == "win32":
        VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    else:
        VLC_PATH = "vlc"

    # Folder to store generated .m3u playlists
    PLAYLIST_FOLDER = "playlists"
    os.makedirs(PLAYLIST_FOLDER, exist_ok=True)

    class MKVPlaylistGenerator:
        def __init__(self):
            self.video_source_dir = ""
            self.playlist_dest_dir = PLAYLIST_FOLDER

        def set_directories(self, video_source_dir):
            self.video_source_dir = video_source_dir

        def sanitize_playlist_name(self, name):
            """Sanitize filenames to only include valid characters."""
            return ''.join(c for c in name if c in VALID_CHARS).strip()

        def create_mkv_playlists(self):
            """Recursively scan the source directory for .mkv files and create .m3u playlists."""
            try:
                if not os.path.exists(self.video_source_dir) or not os.path.isdir(self.video_source_dir):
                    print(f"Error: Source directory '{self.video_source_dir}' not found.")
                    return False

                if not os.path.exists(self.playlist_dest_dir):
                    os.makedirs(self.playlist_dest_dir)

                mkv_files = []

                for root, _, files in os.walk(self.video_source_dir):
                    for file in files:
                        if file.lower().endswith(".mkv"):
                            full_path = os.path.abspath(os.path.join(root, file))
                            mkv_files.append(full_path)

                if not mkv_files:
                    print("No MKV files found.")
                    return False

                for file_path in mkv_files:
                    filename = os.path.splitext(os.path.basename(file_path))[0]
                    safe_name = self.sanitize_playlist_name(filename)
                    safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                    playlist_path = os.path.join(self.playlist_dest_dir, f"{safe_name}.m3u")

                    encoded_path = urllib.parse.quote(file_path)
                    playlist_entry = f"file:///{encoded_path}"

                    with open(playlist_path, "w", encoding="utf-8") as f:
                        f.write(f"# Movie: {filename}\n")
                        f.write(f"{playlist_entry}\n")

                    print(f"Written MKV playlist: {playlist_path}")

                return True

            except Exception as e:
                print(f"Error occurred while creating MKV playlists: {e}")
                return False

    def log_history(playlist_path):
        """Append played playlist to history.m3u in the playlists folder (no duplicates)."""
        history_path = os.path.join(PLAYLIST_FOLDER, "history.m3u")

        # Normalize the entry
        entry_path = os.path.abspath(playlist_path)
        encoded_entry = f"file:///{urllib.parse.quote(entry_path.replace(os.sep, '/'))}"

        # Load and normalize existing entries
        existing_entries = set()
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        norm_line = urllib.parse.unquote(line).replace("\\", "/").lower()
                        existing_entries.add(norm_line)

        norm_encoded = urllib.parse.unquote(encoded_entry).replace("\\", "/").lower()

        if norm_encoded not in existing_entries:
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(f"{encoded_entry}\n")
            print(f"Added to history: {encoded_entry}")
        else:
            print(f"Already in history: {encoded_entry}")

    def play_random_from_history():
        """Play a random playlist entry from history.m3u."""
        history_path = os.path.join(PLAYLIST_FOLDER, "history.m3u")
        
        if not os.path.exists(history_path):
            messagebox.showinfo("History Empty", "No history found.")
            return

        with open(history_path, "r", encoding="utf-8") as f:
            entries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        if not entries:
            messagebox.showinfo("History Empty", "No valid entries in history.")
            return

        random_entry = random.choice(entries)

        try:
            subprocess.run([VLC_PATH, random_entry])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to play random playlist:\n{e}")

    def search_and_open(query):
        """Search for .m3u files matching the query and open them in VLC."""
        playlist_files = [f for f in os.listdir(PLAYLIST_FOLDER) if f.endswith(".m3u") and f != "history.m3u"]
        titles = [os.path.splitext(f)[0] for f in playlist_files]

        if not titles:
            messagebox.showwarning("No Playlists", "No playlists found. Please scan a folder first.")
            return

        best_match, score = process.extractOne(query, titles)
        if score < 60:
            messagebox.showinfo("No Match", f"No close matches found for '{query}'.")
            return

        playlist_path = os.path.join(PLAYLIST_FOLDER, best_match + ".m3u")
        try:
            subprocess.run([VLC_PATH, playlist_path])
            log_history(playlist_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open playlist in VLC:\n{e}")

    class MKVPlayerApp:
        def __init__(self, root):
            self.root = root
            self.generator = MKVPlaylistGenerator()

            tk.Button(root, text="Scan Folder for MKVs", command=self.scan_folder).pack(pady=10)
            tk.Button(root, text="Scan for Others", command=self.set_playlist_folder).pack(pady=5)

            self.entry = tk.Entry(root, width=40)
            self.entry.pack(pady=5)
            self.entry.insert(0, "")
            self.entry.bind("<Return>", self.search_movie_event)
            self.entry.bind("<KeyRelease>", self.check_for_done)  # <--- Added binding

            tk.Button(root, text="Search and Play", command=self.search_movie).pack(pady=10)
            tk.Button(root, text="Play Random from History", command=play_random_from_history).pack(pady=10)

        def scan_folder(self):
            folder = filedialog.askdirectory()
            if not folder:
                return

            self.generator.set_directories(folder)
            if self.generator.create_mkv_playlists():
                messagebox.showinfo("Scan Complete", "Playlists created successfully.")

        def set_playlist_folder(self):
            folder = filedialog.askdirectory()
            if folder:
                global PLAYLIST_FOLDER
                PLAYLIST_FOLDER = folder
                os.makedirs(PLAYLIST_FOLDER, exist_ok=True)
                self.generator.playlist_dest_dir = PLAYLIST_FOLDER
                messagebox.showinfo("Playlist Folder Set", f"Now using: {PLAYLIST_FOLDER}")

        def search_movie(self):
            query = self.entry.get()
            if query:
                search_and_open(query)
                self.entry.delete(0, tk.END)

        def search_movie_event(self, event):
            self.search_movie()

        def check_for_done(self, event):
            query = self.entry.get().strip()
            if "register" in query.lower().split():
                cleaned_query = ' '.join(word for word in query.split() if word.lower() != "register")
                self.entry.delete(0, tk.END)
                self.entry.insert(0, cleaned_query)
                self.search_movie()

    # Launch the app
    if __name__ == "__main__":
        root = tk.Tk()
        root.title("MKV Playlist Player")
        app = MKVPlayerApp(root)
        root.mainloop()

def play_random_from_history5(history_file, description):

    import os
    import pathlib
    import tkinter as tk
    from tkinter import filedialog, messagebox
    import urllib.parse

    # ---------------------------------------------------------------------------
    # Core parsing / conversion functions
    # ---------------------------------------------------------------------------

    def parse_m3u_paths(text: str, *, decode_percent: bool = True) -> list[str]:
        """Extract *display names* from an M3U file that lists file:///... URIs.

        Rules:
        - Ignore blank lines.
        - Ignore lines beginning with '#'. (standard M3U comments / directives)
        - Strip a leading 'file:///' (case-insensitive) if present.
        - Optionally percent-decode the remaining path.
        - Take the basename (last path component).
        - Drop a trailing '.m3u' (case-insensitive).
        - Keep resulting text as the playlist display name.
        """
        names: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('#'):  # comment / directive
                continue

            # Strip file:/// prefix
            if line.lower().startswith('file:///'):
                path_part = line[8:]  # keep the remaining characters exactly as-is
            else:
                path_part = line

            # Percent decode, if desired
            if decode_percent:
                path_part = urllib.parse.unquote(path_part)

            # Get just the filename
            filename = os.path.basename(path_part)

            # Remove extension (.m3u)
            if filename.lower().endswith('.m3u'):
                filename = filename[:-4]

            filename = filename.strip()
            if filename:
                names.append(filename)
        return names


    def build_playlist_lines(names: list[str], *, prefix: str = '# Playlist: ') -> str:
        """Build the # Playlist: lines text block from a list of names."""
        return "\n".join(f"{prefix}{n}" for n in names) + ("\n" if names else "")


    def convert_file_uris_m3u_to_playlist_lines(input_path: os.PathLike[str] | str,
                                                *,
                                                decode_percent: bool = True) -> tuple[list[str], str]:
        """Read an input M3U file and return (names_list, playlist_block_text)."""
        text = pathlib.Path(input_path).read_text(encoding='utf-8', errors='replace')
        names = parse_m3u_paths(text, decode_percent=decode_percent)
        block = build_playlist_lines(names)
        return names, block


    # ---------------------------------------------------------------------------
    # Tkinter workflow
    # ---------------------------------------------------------------------------

    def run_gui():
        """Interactive GUI flow: pick source .m3u, pick output folder, write converted file."""
        root = tk.Tk()
        root.withdraw()  # Hide the root window; we only want dialogs.

        # 1. Select source M3U file
        in_path = filedialog.askopenfilename(
            title="Select source M3U file (with file:/// paths)",
            filetypes=[("M3U playlists", "*.m3u"), ("All files", "*.*")],
        )
        if not in_path:
            messagebox.showinfo("Cancelled", "No source file selected.")
            return

        # 2. Parse the file
        names, block = convert_file_uris_m3u_to_playlist_lines(in_path)
        if not names:
            messagebox.showwarning("No entries", "No valid file URIs found in the selected file.")
            return

        # 3. Choose output directory
        out_dir = filedialog.askdirectory(title="Select folder to save converted M3U")
        if not out_dir:
            messagebox.showinfo("Cancelled", "No output folder selected.")
            return

        # 4. Determine output filename
        in_stem = pathlib.Path(in_path).stem
        out_name = f"{in_stem}_converted.m3u"
        out_path = pathlib.Path(out_dir) / out_name

        # 5. Write the converted playlist
        try:
            out_path.write_text(block, encoding='utf-8')
        except OSError as e:
            messagebox.showerror("Write Error", f"Couldn't write file:\n{out_path}\n\n{e}")
            return

        # 6. Done
        messagebox.showinfo("Conversion complete", f"Converted playlist written to:\n{out_path}")


    # ---------------------------------------------------------------------------
    # CLI support
    # ---------------------------------------------------------------------------

    def _cli(argv: list[str] | None = None) -> int:
        """Command-line interface.

        Usage:
            python m3u_uri_to_playlist_converter.py            # GUI flow
            python m3u_uri_to_playlist_converter.py in.m3u     # Convert to stdout
            python m3u_uri_to_playlist_converter.py in.m3u out.m3u  # Convert, write to out.m3u
        """
        import sys
        if argv is None:
            argv = sys.argv[1:]

        if not argv:
            # No args: launch GUI
            run_gui()
            return 0

        in_path = argv[0]
        if not pathlib.Path(in_path).is_file():
            print(f"Error: input file not found: {in_path}", file=sys.stderr)
            return 1

        names, block = convert_file_uris_m3u_to_playlist_lines(in_path)
        if len(argv) == 1:
            # Write to stdout
            sys.stdout.write(block)
            return 0

        out_path = argv[1]
        try:
            pathlib.Path(out_path).write_text(block, encoding='utf-8')
        except OSError as e:
            print(f"Error writing {out_path}: {e}", file=sys.stderr)
            return 1
        return 0


    if __name__ == "__main__":
        import sys
        raise SystemExit(_cli())

def play_random_from_history6(history_file, description):
                    import os
                    import shutil
                    import tkinter as tk
                    from tkinter import filedialog

                    def read_m3u_file(m3u_file_path):
                        """Reads the m3u file and returns a list of playlist names."""
                        playlists = []
                        with open(m3u_file_path, 'r') as file:
                            for line in file:
                                if line.startswith("# Playlist:"):
                                    playlist_name = line.strip().replace("# Playlist:", "").strip()
                                    playlists.append(playlist_name)
                        return playlists

                    def find_playlist_files(playlist_name, base_directory):
                        """Search for .m3u playlist files in the given base directory."""
                        matching_files = []
                        for root, dirs, files in os.walk(base_directory):
                            for file in files:
                                if file.lower() == f"{playlist_name.lower()}.m3u":  # Match exactly (case-insensitive)
                                    matching_files.append(os.path.join(root, file))
                        return matching_files

                    def copy_files_to_target(files, target_folder):
                        """Copies all the playlist files in the list to the target folder."""
                        for file in files:
                            if os.path.exists(file):
                                try:
                                    file_name = os.path.basename(file)
                                    target_file = os.path.join(target_folder, file_name)
                                    shutil.copy(file, target_file)
                                    print(f"Copied: {file} to {target_file}")
                                except Exception as e:
                                    print(f"Error copying {file}: {e}")
                            else:
                                print(f"File not found: {file}")

                    def select_folder(title):
                        """Prompts the user to select a folder."""
                        root = tk.Tk()
                        root.withdraw()
                        folder = filedialog.askdirectory(title=title)
                        if not folder:
                            print("No folder selected.")
                            return None
                        return folder

                    def select_m3u_file():
                        """Prompts the user to select the history.m3u file."""
                        root = tk.Tk()
                        root.withdraw()
                        file = filedialog.askopenfilename(title="Select the history.m3u file", filetypes=[("M3U Playlist Files", "*.m3u")])
                        if not file:
                            print("No history.m3u file selected.")
                            return None
                        return file

                    def main():
                        # Ask the user to select the history.m3u file
                        history_file = select_m3u_file()
                        if not history_file:
                            return

                        # Read the history.m3u file to get the list of playlists
                        playlists = read_m3u_file(history_file)
                        if not playlists:
                            print(f"No playlists found in the selected m3u file {history_file}.")
                            return

                        # Ask the user to select the folder where .m3u playlists are located
                        m3u_folder = select_folder("Select the folder containing .m3u playlists")
                        if not m3u_folder:
                            return

                        # Ask the user to select a target folder to copy the files to
                        target_folder = select_folder("Select Target Folder")
                        if not target_folder:
                            return

                        # Process each playlist and find matching .m3u files
                        for playlist in playlists:
                            print(f"Processing playlist: {playlist}")
                            playlist_files = find_playlist_files(playlist, m3u_folder)
                            if playlist_files:
                                print(f"Found {len(playlist_files)} .m3u file(s) for playlist: {playlist}")
                                copy_files_to_target(playlist_files, target_folder)
                            else:
                                print(f"No .m3u files found for playlist: {playlist}")

                        print("All files have been copied.")

                    if __name__ == "__main__":
                        main()


def play_random_from_history4(history_file, description):
                    import os
                    import shutil
                    import urllib.request
                    import tkinter as tk
                    from tkinter import filedialog
                    from urllib.parse import unquote


                    def download_song(url_or_path, download_folder):
                        """
                        Download a song from a URL or copy it from a local path into the specified folder.
                        """
                        try:
                            # Clean the path and decode any URL-encoded characters (e.g., %20 for space)
                            path = unquote(url_or_path.strip())

                            # If the path starts with file:\, replace it with file://
                            if path.lower().startswith("file:\\"):
                                path = path.replace("file:\\", "file:///")

                            # Handle 'file://' URLs
                            if path.lower().startswith("file:///"):
                                path = path[8:]  # Remove 'file:///' prefix
                                path = unquote(path)
                                path = path.replace("/", "\\")
                                path = os.path.normpath(path)

                            filename = os.path.basename(path)
                            target_path = os.path.join(download_folder, filename)

                            # Handle URL download or local file copy
                            if path.startswith("http://") or path.startswith("https://"):
                                print(f"Downloading from URL: {path}")
                                urllib.request.urlretrieve(path, target_path)
                                print(f"Downloaded: {filename}")
                            elif os.path.exists(path):
                                print(f"Copying local file: {path}")
                                shutil.copy(path, target_path)
                                print(f"Copied: {filename}")
                            else:
                                print(f"File not found or invalid path: {path}")

                        except Exception as e:
                            print(f"Error handling {url_or_path}: {e}")


                    def parse_m3u_file(file_path):
                        """
                        Parse an M3U file and return a list of resolved file paths or URLs.
                        """
                        lines = []
                        for encoding in ['utf-8', 'latin-1']:
                            try:
                                with open(file_path, 'r', encoding=encoding) as file:
                                    lines = file.readlines()
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            print(f"Error: Could not decode file {file_path}")
                            return []

                        base_dir = os.path.dirname(file_path)
                        song_paths = []

                        for line in lines:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                decoded_line = unquote(line)

                                # If path is not absolute or URL, make it absolute
                                if not decoded_line.startswith(("http://", "https://", "file://")) and not os.path.isabs(decoded_line):
                                    decoded_line = os.path.abspath(os.path.join(base_dir, decoded_line))

                                decoded_line = os.path.normpath(decoded_line)
                                song_paths.append(decoded_line)

                        return song_paths


                    def download_playlist_from_folder(source_folder, download_folder):
                        """
                        Process all M3U files in the source folder and download/copy songs to the download folder.
                        Each playlist gets its own subfolder named after the playlist file.
                        """
                        if not os.path.exists(download_folder):
                            os.makedirs(download_folder)

                        for m3u_file in os.listdir(source_folder):
                            m3u_path = os.path.join(source_folder, m3u_file)

                            if m3u_file.endswith(".m3u"):
                                playlist_name = os.path.splitext(m3u_file)[0]
                                playlist_folder = os.path.join(download_folder, playlist_name)

                                if not os.path.exists(playlist_folder):
                                    os.makedirs(playlist_folder)

                                print(f"\nProcessing playlist: {m3u_file}")
                                print(f"Saving files to: {playlist_folder}")

                                song_paths = parse_m3u_file(m3u_path)

                                for path in song_paths:
                                    print(f"Resolved path: {path}")
                                    download_song(path, playlist_folder)
                            else:
                                print(f"Skipping non-M3U file: {m3u_file}")


                    def select_folder(title="Select a Folder"):
                        """
                        Open a folder dialog for the user to select a folder.
                        """
                        root = tk.Tk()
                        root.withdraw()
                        folder = filedialog.askdirectory(title=title)
                        return folder


                    if __name__ == "__main__":
                        # Ask user to select the folder containing M3U playlists
                        source_folder = select_folder("Select Folder Containing M3U Playlists")

                        # Ask user to select the folder to save downloaded/copied songs
                        download_folder = select_folder("Select Folder to Save Songs")

                        if source_folder and download_folder:
                            print(f"\nSource folder: {source_folder}")
                            print(f"Download folder: {download_folder}")
                            download_playlist_from_folder(source_folder, download_folder)
                        else:
                            print("Folder selection was canceled.")


def play_random_from_history3(history_file, description):
    import os
    import sys
    import urllib.parse
    import subprocess
    import random
    import string
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from fuzzywuzzy import process

    # Constants
    MAX_FILENAME_LENGTH = 215
    VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"
    if sys.platform == "win32":
        VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    else:
        VLC_PATH = "vlc"
    PLAYLIST_FOLDER = "playlists"
    os.makedirs(PLAYLIST_FOLDER, exist_ok=True)

    class MKVPlaylistGenerator:
        def __init__(self):
            self.video_source_dir = ""
            self.playlist_dest_dir = PLAYLIST_FOLDER

        def set_directories(self, video_source_dir):
            self.video_source_dir = video_source_dir

        def sanitize_playlist_name(self, name):
            return ''.join(c for c in name if c in VALID_CHARS).strip()

        def create_mkv_playlists(self):
            try:
                if not os.path.exists(self.video_source_dir) or not os.path.isdir(self.video_source_dir):
                    print(f"Error: Source directory '{self.video_source_dir}' not found.")
                    return False

                if not os.path.exists(self.playlist_dest_dir):
                    os.makedirs(self.playlist_dest_dir)

                mkv_files = []

                for root, _, files in os.walk(self.video_source_dir):
                    for file in files:
                        if file.lower().endswith(".mkv"):
                            full_path = os.path.abspath(os.path.join(root, file))
                            mkv_files.append(full_path)

                if not mkv_files:
                    print("No MKV files found.")
                    return False

                for file_path in mkv_files:
                    filename = os.path.splitext(os.path.basename(file_path))[0]
                    safe_name = self.sanitize_playlist_name(filename)
                    safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                    playlist_path = os.path.join(self.playlist_dest_dir, f"{safe_name}.m3u")

                    encoded_path = urllib.parse.quote(file_path)
                    playlist_entry = f"file:///{encoded_path}"

                    with open(playlist_path, "w", encoding="utf-8") as f:
                        f.write(f"# Movie: {filename}\n")
                        f.write(f"{playlist_entry}\n")

                    print(f"Written MKV playlist: {playlist_path}")

                return True

            except Exception as e:
                print(f"Error occurred while creating MKV playlists: {e}")
                return False

    def log_history(playlist_path):
        history_path = os.path.join(PLAYLIST_FOLDER, "history.m3u")
        entry_path = os.path.abspath(playlist_path)
        encoded_entry = f"file:///{urllib.parse.quote(entry_path.replace(os.sep, '/'))}"

        existing_entries = set()
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        norm_line = urllib.parse.unquote(line).replace("\\", "/").lower()
                        existing_entries.add(norm_line)

        norm_encoded = urllib.parse.unquote(encoded_entry).replace("\\", "/").lower()

        if norm_encoded not in existing_entries:
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(f"{encoded_entry}\n")
            print(f"Added to history: {encoded_entry}")
        else:
            print(f"Already in history: {encoded_entry}")

    def play_random_from_history():
        history_path = os.path.join(PLAYLIST_FOLDER, "history.m3u")

        if not os.path.exists(history_path):
            messagebox.showinfo("History Empty", "No history found.")
            return

        with open(history_path, "r", encoding="utf-8") as f:
            entries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        if not entries:
            messagebox.showinfo("History Empty", "No valid entries in history.")
            return

        random_entry = random.choice(entries)

        try:
            subprocess.run([VLC_PATH, random_entry])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to play random playlist:\n{e}")

    def search_and_open(query):
        playlist_files = [f for f in os.listdir(PLAYLIST_FOLDER) if f.endswith(".m3u") and f != "history.m3u"]
        titles = [os.path.splitext(f)[0] for f in playlist_files]

        if not titles:
            messagebox.showwarning("No Playlists", "No playlists found. Please scan a folder first.")
            return

        best_match, score = process.extractOne(query, titles)
        if score < 60:
            messagebox.showinfo("No Match", f"No close matches found for '{query}'.")
            return

        playlist_path = os.path.join(PLAYLIST_FOLDER, best_match + ".m3u")
        try:
            subprocess.run([VLC_PATH, playlist_path])
            log_history(playlist_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open playlist in VLC:\n{e}")

    class MKVPlayerApp:
        def __init__(self, root):
            self.root = root
            self.generator = MKVPlaylistGenerator()

            self.entry = tk.Entry(root, width=40)
            self.entry.pack(pady=5)
            self.entry.insert(0, "")
            self.entry.bind("<Return>", self.search_movie_event)
            self.entry.bind("<KeyRelease>", self.check_for_done)

            tk.Button(root, text="Play Random from History", command=play_random_from_history).pack(pady=10)

        def scan_folder(self):
            folder = filedialog.askdirectory()
            if not folder:
                return

            self.generator.set_directories(folder)
            if self.generator.create_mkv_playlists():
                messagebox.showinfo("Scan Complete", "Playlists created successfully.")

        def set_playlist_folder(self):
            folder = filedialog.askdirectory()
            if folder:
                global PLAYLIST_FOLDER
                PLAYLIST_FOLDER = folder
                os.makedirs(PLAYLIST_FOLDER, exist_ok=True)
                self.generator.playlist_dest_dir = PLAYLIST_FOLDER
                messagebox.showinfo("Playlist Folder Set", f"Now using: {PLAYLIST_FOLDER}")

        def search_movie(self):
            query = self.entry.get().strip().lower()
            if query == "random":
                play_random_from_history()
                self.entry.delete(0, tk.END)
                return

            if query:
                search_and_open(query)
                self.entry.delete(0, tk.END)

        def search_movie_event(self, event):
            self.search_movie()

        def check_for_done(self, event):
            query = self.entry.get().strip()
            if "register" in query.lower().split():
                cleaned_query = ' '.join(word for word in query.split() if word.lower() != "register")
                self.entry.delete(0, tk.END)
                self.entry.insert(0, cleaned_query)
                self.search_movie()

    # Launch the app
    if __name__ == "__main__":
        root = tk.Tk()
        root.title("MKV Playlist Player")
        app = MKVPlayerApp(root)
        root.mainloop()



def play_random_from_history(history_file, description):
    import os
    import sys
    import urllib.parse
    import subprocess
    import string
    import random
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from fuzzywuzzy import process

    MAX_FILENAME_LENGTH = 215
    VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

    if sys.platform == "win32":
        VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    else:
        VLC_PATH = "vlc"

    PLAYLIST_FOLDER = "playlists"
    os.makedirs(PLAYLIST_FOLDER, exist_ok=True)

    HISTORY_VIDEO_MKV = os.path.join(PLAYLIST_FOLDER, "history_mkv.m3u")
    HISTORY_VIDEO_MP4 = os.path.join(PLAYLIST_FOLDER, "history_mp4.m3u")
    PDF_PLAYLIST_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_playlist.txt")  # For scanned PDFs
    PDF_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_history.txt")

    class PlaylistGenerator:
        def __init__(self, extension):
            self.extension = extension.lower()
            self.source_dir = ""
            self.playlist_dest_dir = PLAYLIST_FOLDER

        def set_directories(self, source_dir):
            self.source_dir = source_dir

        def sanitize_playlist_name(self, name):
            return ''.join(c for c in name if c in VALID_CHARS).strip()

        def create_playlists(self):
            try:
                if not os.path.exists(self.source_dir) or not os.path.isdir(self.source_dir):
                    print(f"Error: Source directory '{self.source_dir}' not found.")
                    return False

                if not os.path.exists(self.playlist_dest_dir):
                    os.makedirs(self.playlist_dest_dir)

                media_files = []
                for root, _, files in os.walk(self.source_dir):
                    for file in files:
                        if file.lower().endswith(self.extension):
                            full_path = os.path.abspath(os.path.join(root, file))
                            media_files.append(full_path)

                if not media_files:
                    print(f"No {self.extension.upper()} files found.")
                    return False

                for file_path in media_files:
                    filename = os.path.splitext(os.path.basename(file_path))[0]
                    safe_name = self.sanitize_playlist_name(filename)
                    safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                    playlist_path = os.path.join(self.playlist_dest_dir, f"{safe_name}.m3u")

                    encoded_path = urllib.parse.quote(file_path)
                    playlist_entry = f"file:///{encoded_path}"

                    with open(playlist_path, "w", encoding="utf-8") as f:
                        f.write(f"# {self.extension.upper()} File: {filename}\n")
                        f.write(f"{playlist_entry}\n")

                    print(f"Written {self.extension.upper()} playlist: {playlist_path}")

                return True

            except Exception as e:
                print(f"Error occurred while creating {self.extension.upper()} playlists: {e}")
                return False

    class PDFPlaylistGenerator:
        """
        Scans for PDFs and creates a single text playlist file listing all found PDFs.
        """
        def __init__(self):
            self.source_dir = ""
            self.playlist_dest_dir = PLAYLIST_FOLDER

        def set_directories(self, source_dir):
            self.source_dir = source_dir

        def create_pdf_playlist(self):
            try:
                if not os.path.exists(self.source_dir) or not os.path.isdir(self.source_dir):
                    print(f"Error: Source directory '{self.source_dir}' not found.")
                    return False

                pdf_files = []
                for root, _, files in os.walk(self.source_dir):
                    for file in files:
                        if file.lower().endswith(".pdf"):
                            full_path = os.path.abspath(os.path.join(root, file))
                            pdf_files.append(full_path)

                if not pdf_files:
                    print("No PDF files found.")
                    return False

                with open(PDF_PLAYLIST_FILE, "w", encoding="utf-8") as f:
                    for file_path in pdf_files:
                        encoded_path = urllib.parse.quote(file_path)
                        playlist_entry = f"file:///{encoded_path}"
                        f.write(f"{playlist_entry}\n")

                print(f"Written PDF playlist file: {PDF_PLAYLIST_FILE}")
                return True

            except Exception as e:
                print(f"Error occurred while creating PDF playlist: {e}")
                return False

    def log_media_opened(media_path, history_file):
        try:
            with open(history_file, "a", encoding="utf-8") as f:
                encoded_path = urllib.parse.quote(media_path)
                f.write(f"file:///{encoded_path}\n")
        except Exception as e:
            print(f"Failed to log opened media: {e}")

    def play_random_from_history(history_file, description):
        if not os.path.exists(history_file):
            messagebox.showinfo("No History", f"No {description} history found.")
            return

        with open(history_file, "r", encoding="utf-8") as f:
            entries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        if not entries:
            messagebox.showinfo("Empty History", f"No entries found in {description} history.")
            return

        selected = random.choice(entries)
        selected_path = urllib.parse.unquote(selected[8:]) if selected.startswith("file:///") else selected

        if not os.path.exists(selected_path):
            messagebox.showwarning("Missing File", f"File does not exist:\n{selected_path}")
            return

        try:
            if description.startswith("pdf"):
                if sys.platform == "win32":
                    os.startfile(selected_path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", selected_path])
                else:
                    subprocess.run(["xdg-open", selected_path])
            else:
                subprocess.run([VLC_PATH, selected_path])
            messagebox.showinfo("Playing Random", f"Playing random {description}:\n{os.path.basename(selected_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open {description}:\n{e}")

    def open_playlist_and_log(file_path, history_file):
        if not os.path.exists(file_path):
            messagebox.showwarning("Missing File", f"Playlist file does not exist:\n{file_path}")
            return

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("file:///"):
                    path = urllib.parse.unquote(line.strip()[8:])
                    if os.path.exists(path):
                        try:
                            if history_file == PDF_HISTORY_FILE:
                                if sys.platform == "win32":
                                    os.startfile(path)
                                elif sys.platform == "darwin":
                                    subprocess.run(["open", path])
                                else:
                                    subprocess.run(["xdg-open", path])
                            else:
                                subprocess.run([VLC_PATH, path])
                            log_media_opened(path, history_file)
                        except Exception as e:
                            messagebox.showerror("Error", f"Failed to open media:\n{e}")
                        return

        messagebox.showerror("Error", "No valid media path found in playlist.")

    def search_and_open(query):
        # Gather all playlist files for MKV and MP4
        playlist_files = [f for f in os.listdir(PLAYLIST_FOLDER) if f.endswith(".m3u")]
        # Include PDF playlist file if exists
        if os.path.exists(PDF_PLAYLIST_FILE):
            playlist_files.append(os.path.basename(PDF_PLAYLIST_FILE))

        # Map playlist to type for history_file choice
        playlist_map = {}
        titles = []
        for f in playlist_files:
            title = os.path.splitext(f)[0]
            titles.append(title)
            # Determine type
            if f == os.path.basename(PDF_PLAYLIST_FILE):
                playlist_map[title] = ("pdf", os.path.join(PLAYLIST_FOLDER, f))
            elif f.lower().endswith(".m3u"):
                # Heuristic for mp4 or mkv
                if ".mp4" in title.lower():
                    playlist_map[title] = ("mp4", os.path.join(PLAYLIST_FOLDER, f))
                else:
                    playlist_map[title] = ("mkv", os.path.join(PLAYLIST_FOLDER, f))

        if not titles:
            messagebox.showwarning("No Data", "No playlists found to search.")
            return

        best_match, score = process.extractOne(query, titles)
        if score < 60:
            messagebox.showinfo("No Match", f"No close matches found for '{query}'.")
            return

        media_type, playlist_path = playlist_map.get(best_match, ("mkv", None))
        if not playlist_path:
            messagebox.showerror("Error", "Playlist not found.")
            return

        history_file = {
            "mp4": HISTORY_VIDEO_MP4,
            "mkv": HISTORY_VIDEO_MKV,
            "pdf": PDF_HISTORY_FILE
        }.get(media_type, HISTORY_VIDEO_MKV)

        open_playlist_and_log(playlist_path, history_file)

    class MediaPlayerApp:
        def __init__(self, root):
            self.root = root

            self.mkv_generator = PlaylistGenerator(".mkv")
            self.mp4_generator = PlaylistGenerator(".mp4")
            self.pdf_generator = PDFPlaylistGenerator()

            # Scan buttons
            tk.Button(root, text="Scan Folder for MKVs", command=self.scan_mkv_folder).pack(pady=5)
            tk.Button(root, text="Scan Folder for MP4s", command=self.scan_mp4_folder).pack(pady=5)
            tk.Button(root, text="Scan Folder for PDFs", command=self.scan_pdf_folder).pack(pady=5)

            # Single Search Bar
            self.search_entry = tk.Entry(root, width=50)
            self.search_entry.pack(pady=10)
            self.search_entry.bind("<Return>", self.search_event)

            tk.Button(root, text="Search and Play Media", command=self.search).pack(pady=5)

            # Random play buttons
            tk.Button(root, text="🎬 Play Random MKV from History", command=lambda: play_random_from_history(HISTORY_VIDEO_MKV, "MKV video history")).pack(pady=5)
            tk.Button(root, text="🎬 Play Random MP4 from History", command=lambda: play_random_from_history(HISTORY_VIDEO_MP4, "MP4 video history")).pack(pady=5)
            tk.Button(root, text="📄 Open Random PDF from History", command=lambda: play_random_from_history(PDF_HISTORY_FILE, "pdf history")).pack(pady=5)

        def scan_mkv_folder(self):
            folder = filedialog.askdirectory(title="Select folder to scan for MKVs")
            if not folder:
                return
            self.mkv_generator.set_directories(folder)
            if self.mkv_generator.create_playlists():
                messagebox.showinfo("Scan Complete", "MKV playlists created.")
            else:
                messagebox.showinfo("Scan Failed", "No MKV files found.")

        def scan_mp4_folder(self):
            folder = filedialog.askdirectory(title="Select folder to scan for MP4s")
            if not folder:
                return
            self.mp4_generator.set_directories(folder)
            if self.mp4_generator.create_playlists():
                messagebox.showinfo("Scan Complete", "MP4 playlists created.")
            else:
                messagebox.showinfo("Scan Failed", "No MP4 files found.")

        def scan_pdf_folder(self):
            import os
            import sys
            import urllib.parse
            import subprocess
            import string
            import random
            import tkinter as tk
            from tkinter import filedialog, messagebox
            from fuzzywuzzy import process

            MAX_FILENAME_LENGTH = 215
            VALID_CHARS = f"-_.() {string.ascii_letters}{string.digits}"

            if sys.platform == "win32":
                VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
            else:
                VLC_PATH = "vlc"

            PLAYLIST_FOLDER = "playlists"
            os.makedirs(PLAYLIST_FOLDER, exist_ok=True)

            HISTORY_AUDIO = os.path.join(PLAYLIST_FOLDER, "history.m3u")
            HISTORY_VIDEO = os.path.join(PLAYLIST_FOLDER, "history2.m3u")
            PDF_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_history.txt")
            PDF_OPENED_HISTORY_FILE = os.path.join(PLAYLIST_FOLDER, "pdf_opened_history.txt")

            class MKVPlaylistGenerator:
                def __init__(self):
                    self.video_source_dir = ""
                    self.playlist_dest_dir = PLAYLIST_FOLDER

                def set_directories(self, video_source_dir):
                    self.video_source_dir = video_source_dir

                def sanitize_playlist_name(self, name):
                    return ''.join(c for c in name if c in VALID_CHARS).strip()

                def create_mkv_playlists(self):
                    try:
                        if not os.path.exists(self.video_source_dir) or not os.path.isdir(self.video_source_dir):
                            print(f"Error: Source directory '{self.video_source_dir}' not found.")
                            return False

                        if not os.path.exists(self.playlist_dest_dir):
                            os.makedirs(self.playlist_dest_dir)

                        mkv_files = []
                        for root, _, files in os.walk(self.video_source_dir):
                            for file in files:
                                if file.lower().endswith(".mkv"):
                                    full_path = os.path.abspath(os.path.join(root, file))
                                    mkv_files.append(full_path)

                        if not mkv_files:
                            print("No MKV files found.")
                            return False

                        for file_path in mkv_files:
                            filename = os.path.splitext(os.path.basename(file_path))[0]
                            safe_name = self.sanitize_playlist_name(filename)
                            safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                            playlist_path = os.path.join(self.playlist_dest_dir, f"{safe_name}.m3u")

                            encoded_path = urllib.parse.quote(file_path)
                            playlist_entry = f"file:///{encoded_path}"

                            with open(playlist_path, "w", encoding="utf-8") as f:
                                f.write(f"# Movie: {filename}\n")
                                f.write(f"{playlist_entry}\n")

                            print(f"Written MKV playlist: {playlist_path}")

                        return True

                    except Exception as e:
                        print(f"Error occurred while creating MKV playlists: {e}")
                        return False

            def create_mp4_playlists(folder, playlist_dest):
                """Scan folder for mp4 files and create one .m3u playlist per mp4 file."""
                if not folder or not os.path.isdir(folder):
                    return False

                mp4_files = []
                for root, _, files in os.walk(folder):
                    for file in files:
                        if file.lower().endswith(".mp4"):
                            full_path = os.path.abspath(os.path.join(root, file))
                            mp4_files.append(full_path)

                if not mp4_files:
                    return False

                if not os.path.exists(playlist_dest):
                    os.makedirs(playlist_dest)

                for file_path in mp4_files:
                    filename = os.path.splitext(os.path.basename(file_path))[0]
                    safe_name = ''.join(c for c in filename if c in VALID_CHARS).strip()
                    safe_name = safe_name[:MAX_FILENAME_LENGTH - len(".m3u")]
                    playlist_path = os.path.join(playlist_dest, f"{safe_name}.m3u")

                    encoded_path = urllib.parse.quote(file_path)
                    playlist_entry = f"file:///{encoded_path}"

                    try:
                        with open(playlist_path, "w", encoding="utf-8") as f:
                            f.write(f"# Movie: {filename}\n")
                            f.write(f"{playlist_entry}\n")
                        print(f"Written MP4 playlist: {playlist_path}")
                    except Exception as e:
                        print(f"Failed to write playlist for {file_path}: {e}")

                return True

            def log_pdf_opened(pdf_path):
                """Log PDF path to opened PDFs history file (avoid duplicates)."""
                try:
                    if not os.path.exists(PDF_OPENED_HISTORY_FILE):
                        with open(PDF_OPENED_HISTORY_FILE, "w", encoding="utf-8") as f:
                            f.write(pdf_path + "\n")
                        return

                    with open(PDF_OPENED_HISTORY_FILE, "r", encoding="utf-8") as f:
                        lines = set(line.strip() for line in f if line.strip())

                    if pdf_path not in lines:
                        with open(PDF_OPENED_HISTORY_FILE, "a", encoding="utf-8") as f:
                            f.write(pdf_path + "\n")
                except Exception as e:
                    print(f"Failed to log opened PDF: {e}")

            def search_and_open(query):
                playlist_files = [f for f in os.listdir(PLAYLIST_FOLDER) if f.endswith(".m3u")]
                playlist_titles = [os.path.splitext(f)[0] for f in playlist_files]

                pdf_files = []
                if os.path.exists(PDF_HISTORY_FILE):
                    with open(PDF_HISTORY_FILE, "r", encoding="utf-8") as f:
                        pdf_paths = [line.strip() for line in f if line.strip()]
                    pdf_files = [os.path.basename(p) for p in pdf_paths]

                all_titles = playlist_titles + pdf_files

                if not all_titles:
                    messagebox.showwarning("No Data", "No playlists or PDFs found to search.")
                    return

                best_match, score = process.extractOne(query, all_titles)
                if score < 60:
                    messagebox.showinfo("No Match", f"No close matches found for '{query}'.")
                    return

                if best_match in playlist_titles:
                    playlist_path = os.path.join(PLAYLIST_FOLDER, best_match + ".m3u")
                    try:
                        subprocess.run([VLC_PATH, playlist_path])
                        messagebox.showinfo("Opening Playlist", f"Opening playlist: {best_match}")
                    except Exception as e:
                        messagebox.showerror("Error", f"Failed to open playlist in VLC:\n{e}")
                else:
                    try:
                        with open(PDF_HISTORY_FILE, "r", encoding="utf-8") as f:
                            lines = [line.strip() for line in f if line.strip()]
                        matched_paths = [p for p in lines if os.path.basename(p) == best_match]
                        if not matched_paths:
                            messagebox.showerror("Error", "PDF file not found in history.")
                            return
                        pdf_path = matched_paths[0]
                        if not os.path.exists(pdf_path):
                            messagebox.showwarning("Missing File", f"PDF file no longer exists:\n{pdf_path}")
                            return

                        if sys.platform == "win32":
                            os.startfile(pdf_path)
                        elif sys.platform == "darwin":
                            subprocess.run(["open", pdf_path])
                        else:
                            subprocess.run(["xdg-open", pdf_path])
                        log_pdf_opened(pdf_path)  # Log that this PDF has been opened
                        messagebox.showinfo("Opening PDF", f"Opening PDF: {best_match}")
                    except Exception as e:
                        messagebox.showerror("Error", f"Failed to open PDF:\n{e}")

            def play_random_from_history(history_file, description):
                if not os.path.exists(history_file):
                    messagebox.showinfo("No History", f"No {description} history found.")
                    return

                with open(history_file, "r", encoding="utf-8") as f:
                    entries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

                if not entries:
                    messagebox.showinfo("Empty History", f"No entries found in {description} history.")
                    return

                selected = random.choice(entries)
                if selected.startswith("file:///"):
                    selected_path = urllib.parse.unquote(selected[8:])
                else:
                    selected_path = selected

                if not os.path.exists(selected_path):
                    messagebox.showwarning("Missing File", f"File does not exist:\n{selected_path}")
                    return

                try:
                    if description.startswith("pdf"):
                        if sys.platform == "win32":
                            os.startfile(selected_path)
                        elif sys.platform == "darwin":
                            subprocess.run(["open", selected_path])
                        else:
                            subprocess.run(["xdg-open", selected_path])
                    else:
                        subprocess.run([VLC_PATH, selected_path])
                    messagebox.showinfo("Playing Random", f"Playing random {description}:\n{os.path.basename(selected_path)}")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to open {description}:\n{e}")

            def scan_and_log_files(folder, extensions, history_file):
                if not folder or not os.path.isdir(folder):
                    return False

                files = []
                for root, _, filenames in os.walk(folder):
                    for filename in filenames:
                        if filename.lower().endswith(extensions):
                            full_path = os.path.abspath(os.path.join(root, filename))
                            files.append(full_path)

                if not files:
                    return False

                existing_files = set()
                if os.path.exists(history_file):
                    with open(history_file, "r", encoding="utf-8") as f:
                        existing_files = set(line.strip() for line in f if line.strip())

                new_files = [f for f in files if f not in existing_files]

                if not new_files:
                    return False

                with open(history_file, "a", encoding="utf-8") as f:
                    for file_path in new_files:
                        if history_file.endswith(".m3u"):
                            encoded_path = urllib.parse.quote(file_path)
                            f.write(f"file:///{encoded_path}\n")
                        else:
                            f.write(f"{file_path}\n")

                return True

            def play_random_pdf_opened():
                if not os.path.exists(PDF_OPENED_HISTORY_FILE):
                    messagebox.showinfo("No History", "No PDFs have been opened yet.")
                    return

                with open(PDF_OPENED_HISTORY_FILE, "r", encoding="utf-8") as f:
                    pdf_paths = [line.strip() for line in f if line.strip()]

                if not pdf_paths:
                    messagebox.showinfo("Empty History", "No PDFs have been opened yet.")
                    return

                selected_pdf = random.choice(pdf_paths)

                if not os.path.exists(selected_pdf):
                    messagebox.showwarning("Missing File", f"PDF file does not exist:\n{selected_pdf}")
                    return

                try:
                    if sys.platform == "win32":
                        os.startfile(selected_pdf)
                    elif sys.platform == "darwin":
                        subprocess.run(["open", selected_pdf])
                    else:
                        subprocess.run(["xdg-open", selected_pdf])
                    messagebox.showinfo("Opening Random PDF", f"Opening PDF: {os.path.basename(selected_pdf)}")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to open PDF:\n{e}")

            class MKVPlayerApp:
                def __init__(self, root):
                    self.root = root
                    self.generator = MKVPlaylistGenerator()

                    # Video scan and playlist
                    tk.Button(root, text="Scan Folder for MKVs", command=self.scan_folder).pack(pady=5)

                    # New button: scan for MP4s and create playlists
                    tk.Button(root, text="Scan Folder for MP4s", command=self.scan_mp4_folder).pack(pady=5)

                    # Search and play by name (audio/video/pdf)
                    self.entry = tk.Entry(root, width=40)
                    self.entry.pack(pady=5)
                    self.entry.bind("<Return>", self.search_event)
                    tk.Button(root, text="Search and Play (Audio/Video/PDF)", command=self.search).pack(pady=5)

                    # Play random from audio history (history.m3u)
                    tk.Button(root, text="🎵 Play Random Audio from History", command=lambda: play_random_from_history(HISTORY_AUDIO, "audio history")).pack(pady=5)

                    # Play random from video history (history2.m3u)
                    tk.Button(root, text="🎬 Play Random Video from History", command=lambda: play_random_from_history(HISTORY_VIDEO, "video history")).pack(pady=5)

                    # PDF scan button (just scans & logs PDFs)
                    tk.Button(root, text="Scan Folder for PDFs", command=self.scan_pdfs).pack(pady=5)

                    # New button: open a random PDF from opened PDF history
                    tk.Button(root, text="📄 Open Random PDF from Opened History", command=play_random_pdf_opened).pack(pady=5)

                def scan_folder(self):
                    folder = filedialog.askdirectory(title="Select folder to scan for MKVs")
                    if not folder:
                        return

                    self.generator.set_directories(folder)
                    if self.generator.create_mkv_playlists():
                        scan_and_log_files(folder, (".mkv", ".mp4", ".avi"), HISTORY_VIDEO)
                        messagebox.showinfo("Scan Complete", "MKV playlists and video history updated.")

                def scan_mp4_folder(self):
                    folder = filedialog.askdirectory(title="Select folder to scan for MP4s")
                    if not folder:
                        return

                    if create_mp4_playlists(folder, PLAYLIST_FOLDER):
                        messagebox.showinfo("Scan Complete", "MP4 playlists created in playlists folder.")
                    else:
                        messagebox.showinfo("No MP4s Found", "No MP4 files found in the selected folder.")

                def scan_pdfs(self):
                    folder = filedialog.askdirectory(title="Select folder to scan for PDFs")
                    if not folder:
                        return

                    found = scan_and_log_files(folder, (".pdf",), PDF_HISTORY_FILE)
                    if found:
                        messagebox.showinfo("Scan Complete", "PDF history updated with scanned files.")
                    else:
                        messagebox.showinfo("No PDFs Found", "No new PDFs found in the selected folder.")

                def search_event(self, event):
                    self.search()

                def search(self):
                    query = self.entry.get().strip()
                    if not query:
                        messagebox.showwarning("Empty Search", "Please enter a search term.")
                        return
                    search_and_open(query)
                    self.entry.delete(0, tk.END)  # Clear the search bar after search

            if __name__ == "__main__":
                root = tk.Tk()
                root.title("Media & PDF Playlist Manager")
                root.geometry("450x560")
                app = MKVPlayerApp(root)
                root.mainloop()



        def search_event(self, event):
            self.search()

        def search(self):
            query = self.search_entry.get().strip()
            if not query:
                messagebox.showwarning("Empty Search", "Please enter a search term.")
                return
            search_and_open(query)
            self.search_entry.delete(0, tk.END)

    if __name__ == "__main__":
        root = tk.Tk()
        root.title("Media Playlist Manager with PDFs")
        root.geometry("520x550")
        app = MediaPlayerApp(root)
        root.mainloop()


def scan_and_log_files(folder, extensions, history_file):
    if not folder or not os.path.isdir(folder):
        return False

    files = []
    for root, _, filenames in os.walk(folder):
        for filename in filenames:
            if filename.lower().endswith(extensions):
                full_path = os.path.abspath(os.path.join(root, filename))
                files.append(full_path)

    if not files:
        return False

    existing_files = set()
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            existing_files = set(line.strip() for line in f if line.strip())

    new_files = [f for f in files if f not in existing_files]

    if not new_files:
        return False

    with open(history_file, "a", encoding="utf-8") as f:
        for file_path in new_files:
            if history_file.endswith(".m3u"):
                encoded_path = urllib.parse.quote(file_path)
                f.write(f"file:///{encoded_path}\n")
            else:
                f.write(f"{file_path}\n")

    return True

def play_random_pdf_opened():
    if not os.path.exists(PDF_OPENED_HISTORY_FILE):
        messagebox.showinfo("No History", "No PDFs have been opened yet.")
        return

    with open(PDF_OPENED_HISTORY_FILE, "r", encoding="utf-8") as f:
        pdf_paths = [line.strip() for line in f if line.strip()]

    if not pdf_paths:
        messagebox.showinfo("Empty History", "No PDFs have been opened yet.")
        return

    selected_pdf = random.choice(pdf_paths)

    if not os.path.exists(selected_pdf):
        messagebox.showwarning("Missing File", f"PDF file does not exist:\n{selected_pdf}")
        return

    try:
        open_file_with_default_app(selected_pdf)
        messagebox.showinfo("Opening Random PDF", f"Opening PDF: {os.path.basename(selected_pdf)}")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to open PDF:\n{e}")

def play_random_from_search_history():
    if not os.path.exists(SEARCH_HISTORY_FILE):
        messagebox.showinfo("No History", "No search history found.")
        return

    try:
        with open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        
        if not lines:
            messagebox.showinfo("Empty History", "Search history is empty.")
            return

        random_query = random.choice(lines)
        search_and_open(random_query)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to play from search history: {e}")

VLC_SOCKET = None
VLC_PROCESS = None
VLC_RC_HOST = "localhost"
VLC_RC_PORT = 42123

def connect_vlc_socket():
    global VLC_SOCKET
    start = time.time()
    while time.time() - start < 5:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((VLC_RC_HOST, VLC_RC_PORT))
            VLC_SOCKET = s
            break
        except ConnectionRefusedError:
            time.sleep(0.1)

def send_vlc_command(cmd):
    global VLC_SOCKET
    if VLC_SOCKET:
        try:
            VLC_SOCKET.sendall(f"{cmd}\n".encode())
            return True
        except Exception as e:
            print(f"VLC Socket error: {e}")
            VLC_SOCKET = None
    return False

def launch_vlc(media_path):
    global VLC_PROCESS, VLC_SOCKET
    
    if VLC_SOCKET:
        try:
            VLC_SOCKET.close()
        except:
            pass
        VLC_SOCKET = None
        
    if VLC_PROCESS:
        try:
            VLC_PROCESS.kill()
        except:
            pass
            
    cmd = [
        VLC_PATH,
        "--extraintf", "rc",
        "--rc-host", f"{VLC_RC_HOST}:{VLC_RC_PORT}",
        media_path
    ]
    
    try:
        VLC_PROCESS = subprocess.Popen(cmd)
        
        threading.Thread(target=connect_vlc_socket, daemon=True).start()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to launch VLC: {e}")

class MKVPlayerApp:
    def __init__(self, root):
        self.root = root
        self.generator = MKVPlaylistGenerator()
        self.config = load_config()
        self.current_thumbnail = None

        self.create_menu()

        # Search frame
        search_frame = tk.Frame(root)
        search_frame.pack(fill=tk.X, padx=10, pady=5)

        self.entry = tk.Entry(search_frame, width=30)
        self.entry.pack(side=tk.LEFT, padx=(0, 5))
        self.entry.bind("<Return>", self.search_event)
        self.entry.bind("<KeyRelease>", self.filter_library)
        tk.Button(search_frame, text="Search", command=self.search).pack(side=tk.LEFT, padx=5)
        tk.Button(search_frame, text="Play", command=self.play_selected).pack(side=tk.LEFT, padx=5)

        # Controls frame
        controls_frame = tk.Frame(root)
        controls_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Button(controls_frame, text="<< Prev", command=lambda: send_vlc_command("prev")).pack(side=tk.LEFT, padx=5)
        tk.Button(controls_frame, text="Play/Pause", command=lambda: send_vlc_command("pause")).pack(side=tk.LEFT, padx=5)
        tk.Button(controls_frame, text="Next >>", command=lambda: send_vlc_command("next")).pack(side=tk.LEFT, padx=5)

        # Main content area with grid and preview panel
        content_frame = tk.Frame(root)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Media library grid (left side)
        grid_frame = tk.Frame(content_frame)
        grid_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("name", "type", "path")
        self.tree = ttk.Treeview(grid_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Name", command=lambda: self.sort_column("name"))
        self.tree.heading("type", text="Type", command=lambda: self.sort_column("type"))
        self.tree.heading("path", text="Path", command=lambda: self.sort_column("path"))
        self.tree.column("name", width=250)
        self.tree.column("type", width=60)
        self.tree.column("path", width=250)

        scrollbar_y = ttk.Scrollbar(grid_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(grid_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar_y.grid(row=0, column=1, sticky="ns")
        scrollbar_x.grid(row=1, column=0, sticky="ew")
        grid_frame.grid_rowconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_selection_change)

        # Preview panel (right side)
        preview_frame = tk.Frame(content_frame, width=220, relief=tk.GROOVE, borderwidth=1)
        preview_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        preview_frame.pack_propagate(False)

        tk.Label(preview_frame, text="Preview", font=("Arial", 10, "bold")).pack(pady=5)

        self.thumbnail_label = tk.Label(preview_frame, text="No preview", width=25, height=10, relief=tk.SUNKEN, bg="#2a2a2a", fg="#888888")
        self.thumbnail_label.pack(padx=10, pady=5)

        self.preview_name = tk.Label(preview_frame, text="", wraplength=200, justify=tk.CENTER)
        self.preview_name.pack(pady=5)

        self.preview_type = tk.Label(preview_frame, text="", fg="#666666")
        self.preview_type.pack()

        self.preview_status = tk.Label(preview_frame, text="", fg="#888888", font=("Arial", 8))
        self.preview_status.pack(pady=5)

        tk.Button(preview_frame, text="Play Selected", command=self.play_selected).pack(pady=10)

        # Status bar
        self.status_var = tk.StringVar(value="0 items")
        status_bar = tk.Label(root, textvariable=self.status_var, anchor="w", relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Button frame
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(btn_frame, text="VIDEOS", command=lambda: play_random_from_history(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="VIDEOS", command=lambda: self.play_random_media(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="MUSIC SCAN", command=lambda: play_random_from_history2(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame, text="MUSIC PLAYER", command=lambda: play_random_from_history3(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)

        btn_frame2 = tk.Frame(root)
        btn_frame2.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(btn_frame2, text="History Converter", command=lambda: play_random_from_history5(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame2, text="Playlist Converter", command=lambda: play_random_from_history6(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame2, text="Downloader", command=lambda: play_random_from_history4(HISTORY_AUDIO, "audio history")).pack(side=tk.LEFT, padx=2)

        btn_frame3 = tk.Frame(root)
        btn_frame3.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(btn_frame3, text="Random Video", command=lambda: play_random_from_history(HISTORY_VIDEO, "video history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame3, text="Random Video", command=lambda: self.play_random_media(HISTORY_VIDEO, "video history")).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame3, text="Random PDF", command=play_random_pdf_opened).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame3, text="Random Search", command=play_random_from_search_history).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_frame3, text="Refresh Library", command=self.refresh_library).pack(side=tk.LEFT, padx=2)

        self.all_media_items = []
        self.sort_reverse = False
        self.sort_col = "name"

        self.run_auto_scan()
        self.refresh_library()

    def play_random_media(self, history_file, description):
        if not os.path.exists(history_file):
            messagebox.showinfo("No History", f"No {description} history found.")
            return

        try:
            with open(history_file, "r", encoding="utf-8") as f:
                entries = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        except:
            return

        if not entries:
            messagebox.showinfo("Empty History", f"No entries found in {description} history.")
            return

        selected = random.choice(entries)
        if selected.startswith("file:///"):
            selected = urllib.parse.unquote(selected[8:])
        
        launch_vlc(selected)

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        scan_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Scan", menu=scan_menu)

        scan_menu.add_command(label="Scan Folder for MKVs...", command=self.scan_folder)
        scan_menu.add_command(label="Scan Folder for MP4s...", command=self.scan_mp4_folder)
        scan_menu.add_command(label="Scan Folder for PDFs...", command=self.scan_pdfs)
        scan_menu.add_command(label="Scan Folder for Music (aif/aiff)...", command=self.scan_music_folder)
        scan_menu.add_separator()
        scan_menu.add_command(label="Configure Auto-Scan Folders...", command=self.configure_auto_scan)
        self.auto_scan_var = tk.BooleanVar(value=self.config.get("auto_scan_enabled", False))
        scan_menu.add_checkbutton(label="Auto-Scan on Startup", variable=self.auto_scan_var, command=self.toggle_auto_scan)
        scan_menu.add_separator()
        scan_menu.add_command(label="Run Auto-Scan Now", command=lambda: self.run_auto_scan(show_message=True))

    def toggle_auto_scan(self):
        self.config["auto_scan_enabled"] = self.auto_scan_var.get()
        save_config(self.config)

    def configure_auto_scan(self):
        config_window = tk.Toplevel(self.root)
        config_window.title("Configure Auto-Scan Folders")
        config_window.geometry("500x550")

        tk.Label(config_window, text="MKV Folders:").pack(anchor="w", padx=10, pady=(10, 0))
        mkv_listbox = tk.Listbox(config_window, height=4, width=60)
        mkv_listbox.pack(padx=10, pady=5)
        for folder in self.config["scan_folders"].get("mkv", []):
            mkv_listbox.insert(tk.END, folder)

        mkv_btn_frame = tk.Frame(config_window)
        mkv_btn_frame.pack()
        tk.Button(mkv_btn_frame, text="Add", command=lambda: self.add_folder(mkv_listbox, "mkv")).pack(side=tk.LEFT, padx=5)
        tk.Button(mkv_btn_frame, text="Remove", command=lambda: self.remove_folder(mkv_listbox, "mkv")).pack(side=tk.LEFT, padx=5)

        tk.Label(config_window, text="MP4 Folders:").pack(anchor="w", padx=10, pady=(10, 0))
        mp4_listbox = tk.Listbox(config_window, height=4, width=60)
        mp4_listbox.pack(padx=10, pady=5)
        for folder in self.config["scan_folders"].get("mp4", []):
            mp4_listbox.insert(tk.END, folder)

        mp4_btn_frame = tk.Frame(config_window)
        mp4_btn_frame.pack()
        tk.Button(mp4_btn_frame, text="Add", command=lambda: self.add_folder(mp4_listbox, "mp4")).pack(side=tk.LEFT, padx=5)
        tk.Button(mp4_btn_frame, text="Remove", command=lambda: self.remove_folder(mp4_listbox, "mp4")).pack(side=tk.LEFT, padx=5)

        tk.Label(config_window, text="PDF Folders:").pack(anchor="w", padx=10, pady=(10, 0))
        pdf_listbox = tk.Listbox(config_window, height=4, width=60)
        pdf_listbox.pack(padx=10, pady=5)
        for folder in self.config["scan_folders"].get("pdf", []):
            pdf_listbox.insert(tk.END, folder)

        pdf_btn_frame = tk.Frame(config_window)
        pdf_btn_frame.pack()
        tk.Button(pdf_btn_frame, text="Add", command=lambda: self.add_folder(pdf_listbox, "pdf")).pack(side=tk.LEFT, padx=5)
        tk.Button(pdf_btn_frame, text="Remove", command=lambda: self.remove_folder(pdf_listbox, "pdf")).pack(side=tk.LEFT, padx=5)

        tk.Label(config_window, text="Music Folders (aif/aiff):").pack(anchor="w", padx=10, pady=(10, 0))
        music_listbox = tk.Listbox(config_window, height=4, width=60)
        music_listbox.pack(padx=10, pady=5)
        for folder in self.config["scan_folders"].get("music", []):
            music_listbox.insert(tk.END, folder)

        music_btn_frame = tk.Frame(config_window)
        music_btn_frame.pack()
        tk.Button(music_btn_frame, text="Add", command=lambda: self.add_folder(music_listbox, "music")).pack(side=tk.LEFT, padx=5)
        tk.Button(music_btn_frame, text="Remove", command=lambda: self.remove_folder(music_listbox, "music")).pack(side=tk.LEFT, padx=5)


        tk.Button(config_window, text="Close", command=config_window.destroy).pack(pady=20)

    def add_folder(self, listbox, folder_type):
        folder = filedialog.askdirectory(title=f"Select folder for {folder_type.upper()} scanning")
        if folder:
            if folder not in self.config["scan_folders"][folder_type]:
                self.config["scan_folders"][folder_type].append(folder)
                listbox.insert(tk.END, folder)
                save_config(self.config)

    def remove_folder(self, listbox, folder_type):
        selection = listbox.curselection()
        if selection:
            index = selection[0]
            folder = listbox.get(index)
            listbox.delete(index)
            if folder in self.config["scan_folders"][folder_type]:
                self.config["scan_folders"][folder_type].remove(folder)
                save_config(self.config)

    def run_auto_scan(self, show_message=False):
        if not self.config.get("auto_scan_enabled", False) and not show_message:
            return

        scan_folders = self.config.get("scan_folders", {})
        scanned_any = False

        for folder in scan_folders.get("mkv", []):
            if os.path.isdir(folder):
                self.generator.set_directories(folder)
                if self.generator.create_mkv_playlists():
                    scan_and_log_files(folder, (".mkv", ".mp4", ".avi"), HISTORY_VIDEO)
                    scanned_any = True

        for folder in scan_folders.get("mp4", []):
            if os.path.isdir(folder):
                if create_mp4_playlists(folder, PLAYLIST_FOLDER):
                    scanned_any = True

        for folder in scan_folders.get("pdf", []):
            if os.path.isdir(folder):
                if scan_and_log_files(folder, (".pdf",), PDF_HISTORY_FILE):
                    scanned_any = True

        for folder in scan_folders.get("music", []):
            if os.path.isdir(folder):
                if create_music_playlists(folder, PLAYLIST_FOLDER):
                    scan_and_log_files(folder, (".aif", ".aiff"), HISTORY_AUDIO)
                    scanned_any = True

        if show_message:
            if scanned_any:
                messagebox.showinfo("Auto-Scan Complete", "All configured folders have been scanned.")
            else:
                messagebox.showinfo("Auto-Scan", "No folders configured or no new files found.")

    def scan_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan for MKVs")
        if not folder:
            return

        self.generator.set_directories(folder)
        if self.generator.create_mkv_playlists():
            scan_and_log_files(folder, (".mkv", ".mp4", ".avi"), HISTORY_VIDEO)
            messagebox.showinfo("Scan Complete", "MKV playlists and video history updated.")

    def scan_mp4_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan for MP4s")
        if not folder:
            return

        if create_mp4_playlists(folder, PLAYLIST_FOLDER):
            messagebox.showinfo("Scan Complete", "MP4 playlists created in playlists folder.")
        else:
            messagebox.showinfo("No MP4s Found", "No MP4 files found in the selected folder.")

    def scan_music_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan for Music (aif/aiff)")
        if not folder:
            return

        if create_music_playlists(folder, PLAYLIST_FOLDER):
            scan_and_log_files(folder, (".aif", ".aiff"), HISTORY_AUDIO)
            messagebox.showinfo("Scan Complete", "Music playlists created and audio history updated.")
        else:
            messagebox.showinfo("Scan Failed", "No AIF/AIFF files found.")

    def scan_pdfs(self):
        folder = filedialog.askdirectory(title="Select folder to scan for PDFs")
        if not folder:
            return

        found = scan_and_log_files(folder, (".pdf",), PDF_HISTORY_FILE)
        if found:
            messagebox.showinfo("Scan Complete", "PDF history updated with scanned files.")
        else:
            messagebox.showinfo("No PDFs Found", "No new PDFs found in the selected folder.")

    def search_event(self, event):
        self.search()

    def search(self):
        query = self.entry.get().strip()
        if not query:
            messagebox.showwarning("Empty Search", "Please enter a search term.")
            return
        search_and_open(query)
        self.entry.delete(0, tk.END)

    def refresh_library(self):
        self.all_media_items = []

        for f in os.listdir(PLAYLIST_FOLDER):
            if f.endswith(".m3u") and f not in ("history.m3u", "history2.m3u"):
                playlist_path = os.path.join(PLAYLIST_FOLDER, f)
                name = os.path.splitext(f)[0]
                media_path = ""
                media_type = "Video"

                try:
                    with open(playlist_path, "r", encoding="utf-8") as pf:
                        for line in pf:
                            line = line.strip()
                            if line.startswith("file:///"):
                                media_path = urllib.parse.unquote(line[8:])
                                ext = os.path.splitext(media_path)[1].lower()
                                if ext == ".mkv":
                                    media_type = "MKV"
                                elif ext == ".mp4":
                                    media_type = "MP4"
                                elif ext == ".avi":
                                    media_type = "AVI"
                                elif ext in (".aif", ".aiff"):
                                    media_type = "Music"
                                break
                            elif line and not line.startswith("#"):
                                media_path = line
                                break
                except:
                    pass

                artist = ""
                album = ""
                if media_type == "Music" and media_path:
                    try:
                        path_parts = os.path.normpath(media_path).split(os.sep)
                        if len(path_parts) >= 3:
                            album = path_parts[-2]
                            artist = path_parts[-3]
                    except IndexError:
                        pass

                self.all_media_items.append({
                    "name": name,
                    "type": media_type,
                    "path": media_path,
                    "playlist": playlist_path,
                    "artist": artist,
                    "album": album
                })

        if os.path.exists(PDF_HISTORY_FILE):
            try:
                with open(PDF_HISTORY_FILE, "r", encoding="utf-8") as f:
                    for line in f:
                        pdf_path = line.strip()
                        if pdf_path and os.path.exists(pdf_path):
                            self.all_media_items.append({
                                "name": os.path.splitext(os.path.basename(pdf_path))[0],
                                "type": "PDF",
                                "path": pdf_path,
                                "playlist": None,
                                "artist": "",
                                "album": ""
                            })
            except:
                pass

        self.populate_tree(self.all_media_items)

    def populate_tree(self, items):
        self.tree.delete(*self.tree.get_children())
        for item in items:
            self.tree.insert("", tk.END, values=(item["name"], item["type"], item["path"]))
        self.status_var.set(f"{len(items)} items")

    def filter_library(self, event=None):
        query = self.entry.get().strip().lower()
        if not query:
            self.populate_tree(self.all_media_items)
        else:
            parts = [p.strip() for p in query.split(',')]
            filtered_items = []
            SCORE_THRESHOLD = 70  # Adjust for desired fuzziness (0-100)

            if len(parts) == 1:
                # Search for "song title", "album title", or "artist title"
                term = parts[0]
                for item in self.all_media_items:
                    scores = [
                        process.fuzz.ratio(term, item.get("name", "").lower()),
                        process.fuzz.ratio(term, item.get("album", "").lower()),
                        process.fuzz.ratio(term, item.get("artist", "").lower())
                    ]
                    if any(score >= SCORE_THRESHOLD for score in scores):
                        filtered_items.append(item)

            elif len(parts) == 2:
                # Search for "album title, artist title"
                album_q, artist_q = parts[0], parts[1]
                for item in self.all_media_items:
                    if item.get('album') and item.get('artist'):
                        album_score = process.fuzz.ratio(album_q, item['album'].lower())
                        artist_score = process.fuzz.ratio(artist_q, item['artist'].lower())
                        if album_score >= SCORE_THRESHOLD and artist_score >= SCORE_THRESHOLD:
                            filtered_items.append(item)

            elif len(parts) == 3:
                # Search for "song title, album title, artist title"
                song_q, album_q, artist_q = parts[0], parts[1], parts[2]
                for item in self.all_media_items:
                    if item.get('name') and item.get('album') and item.get('artist'):
                        song_score = process.fuzz.ratio(song_q, item['name'].lower())
                        album_score = process.fuzz.ratio(album_q, item['album'].lower())
                        artist_score = process.fuzz.ratio(artist_q, item['artist'].lower())
                        if song_score >= SCORE_THRESHOLD and album_score >= SCORE_THRESHOLD and artist_score >= SCORE_THRESHOLD:
                            filtered_items.append(item)

            self.populate_tree(filtered_items)

    def sort_column(self, col):
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = False

        self.all_media_items.sort(key=lambda x: x[col].lower(), reverse=self.sort_reverse)
        self.filter_library()

    def play_selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select an item to play.")
            return

        item = self.tree.item(selection[0])
        values = item["values"]
        name, media_type, path = values[0], values[1], values[2]

        if media_type == "PDF":
            if os.path.exists(path):
                open_file_with_default_app(path)
            else:
                messagebox.showwarning("File Not Found", f"PDF file not found:\n{path}")
        else:
            for media_item in self.all_media_items:
                if media_item["name"] == name and media_item["playlist"]:
                    try:
                        launch_vlc(media_item["playlist"])
                    except Exception as e:
                        messagebox.showerror("Error", f"Failed to play:\n{e}")
                    return
            if os.path.exists(path):
                try:
                    launch_vlc(path)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to play:\n{e}")

    def on_double_click(self, event):
        self.play_selected()

    def on_selection_change(self, event=None):
        selection = self.tree.selection()
        if not selection:
            self.clear_preview()
            return

        item = self.tree.item(selection[0])
        values = item["values"]
        name, media_type, path = values[0], values[1], values[2]

        self.preview_name.config(text=name)
        self.preview_type.config(text=media_type)

        if not PIL_AVAILABLE:
            self.preview_status.config(text="Install Pillow for thumbnails")
            self.thumbnail_label.config(image="", text="No preview\n(Pillow not installed)")
            return

        self.preview_status.config(text="Loading thumbnail...")
        self.root.update_idletasks()

        self.root.after(10, lambda: self.load_thumbnail(path, media_type))

    def load_thumbnail(self, path, media_type):
        thumb_path = get_thumbnail(path, media_type)

        if thumb_path and os.path.exists(thumb_path):
            try:
                img = Image.open(thumb_path)
                img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                self.current_thumbnail = ImageTk.PhotoImage(img)
                self.thumbnail_label.config(image=self.current_thumbnail, text="")
                self.preview_status.config(text="")
            except Exception as e:
                self.thumbnail_label.config(image="", text="Error loading\nthumbnail")
                self.preview_status.config(text=str(e)[:30])
        else:
            self.thumbnail_label.config(image="", text="No thumbnail\navailable")
            if media_type in ("MKV", "MP4", "AVI", "Video"):
                self.preview_status.config(text="ffmpeg required")
            elif media_type == "PDF":
                self.preview_status.config(text="pdftoppm required")
            else:
                self.preview_status.config(text="")

    def clear_preview(self):
        self.thumbnail_label.config(image="", text="No preview")
        self.preview_name.config(text="")
        self.preview_type.config(text="")
        self.preview_status.config(text="")
        self.current_thumbnail = None

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Media & PDF Playlist Manager")
    root.geometry("1000x650")
    app = MKVPlayerApp(root)
    root.mainloop()