import yt_dlp
import threading
from pathlib import Path
import shutil
import platform
import subprocess
import os

def get_default_downloads_path():
    """
    Returns the default downloads path for the current OS/user.
    Robustly handles Windows where the folder might be moved.
    """
    if platform.system() == "Windows":
        try:
            # Use PowerShell to get the true Downloads path from the Windows API
            command = "[Environment]::GetFolderPath('UserProfile') + '\\Downloads'"
            result = subprocess.run(["powershell", "-Command", command], capture_output=True, text=True, check=True)
            path = result.stdout.strip()
            if os.path.exists(path):
                return path
        except Exception:
            pass
    
    # Fallback for all OSs
    return str(Path.home() / "Downloads")

class YouTubeDownloader:
    def __init__(self, download_path=None):
        if download_path:
            self.download_path = download_path
        else:
            self.download_path = get_default_downloads_path()
        
        self.ffmpeg_available = shutil.which('ffmpeg') is not None

    def get_video_info(self, url):
        """
        Fetches video metadata without downloading.
        """
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'title': info.get('title', 'Unknown Title'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration_string', 'Unknown'),
                    'uploader': info.get('uploader', 'Unknown Channel'),
                    'id': info.get('id', '') # Useful for uniqueness
                }
        except Exception as e:
            print(f"Error fetching info: {e}")
            return None

    def download_video(self, url, format_type='video', quality='best', progress_callback=None, complete_callback=None):
        """
        Downloads a video or audio from YouTube.
        """
        
        def run_download():
            ydl_opts = {
                'outtmpl': os.path.join(self.download_path, '%(title)s.%(ext)s'),
                'progress_hooks': [lambda d: self._progress_hook(d, progress_callback)],
                'noplaylist': True,
            }

            if format_type == 'audio':
                if self.ffmpeg_available:
                    ydl_opts.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                    })
                else:
                    # Fallback if no ffmpeg: just download best audio (often m4a/opus)
                    # We can't convert to mp3 without ffmpeg effectively
                    ydl_opts['format'] = 'bestaudio/best'
            else:
                # Video format selection
                if self.ffmpeg_available:
                    if quality == 'best':
                        ydl_opts['format'] = 'bestvideo+bestaudio/best'
                    elif quality == '1080p':
                        ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
                    elif quality == '720p':
                        ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
                    else:
                        ydl_opts['format'] = 'best'
                else:
                    # Without ffmpeg, we must use pre-merged formats
                    # 'best' usually gives the best pre-merged file (often 720p or 360p)
                    # We cannot force logic like bestvideo+bestaudio because that requires merge
                    if quality == '1080p':
                       # Try to find best pre-merged with height 1080 (rare) or fallback
                       ydl_opts['format'] = 'best[height<=1080]/best'
                    elif quality == '720p':
                       ydl_opts['format'] = 'best[height<=720]/best'
                    else:
                       ydl_opts['format'] = 'best'
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                if complete_callback:
                    complete_callback(True, "Download completed successfully!")
            except Exception as e:
                if complete_callback:
                    complete_callback(False, str(e))

        # Run in a separate thread to avoid freezing the UI
        thread = threading.Thread(target=run_download)
        thread.start()

    def _progress_hook(self, d, progress_callback):
        if d['status'] == 'downloading':
            if progress_callback:
                try:
                    p = d.get('_percent_str', '0%').replace('%', '')
                    progress = float(p) / 100
                    progress_callback(progress, d.get('_percent_str', '0%'))
                except:
                    pass
        elif d['status'] == 'finished':
            if progress_callback:
                progress_callback(1.0, "Processing...")
