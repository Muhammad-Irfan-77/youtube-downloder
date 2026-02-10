import os
import subprocess
import re
from static_ffmpeg import run

def get_duration(input_path):
    """Gets the duration of a video file in seconds using ffprobe."""
    try:
        ffmpeg_path, ffprobe_path = run.get_or_fetch_platform_executables_else_raise()
        cmd = [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration: {e}")
        return 0

def apply_transformations(input_path, output_path, transformations, progress_callback=None):
    """
    Applies FFmpeg transformations to a video and tracks progress.
    """
    try:
        ffmpeg_path, _ = run.get_or_fetch_platform_executables_else_raise()
        
        duration = get_duration(input_path)
        
        filter_complex = []
        # Support common transformations provided in user example
        if transformations.get('flip'):
            filter_complex.append("hflip")
        if transformations.get('scale'):
            # scale=iw*1.1:ih*1.1,crop=iw/1.1:ih/1.1
            filter_complex.append("scale=iw*1.1:ih*1.1,crop=iw/1.1:ih/1.1")
        if transformations.get('noise'):
            filter_complex.append("noise=alls=10:allf=t+u")
        if transformations.get('jitter'):
            filter_complex.append("eq=brightness=0.02:contrast=1.05")

        cmd = [ffmpeg_path, "-y", "-i", input_path]
        if filter_complex:
            cmd.extend(["-vf", ",".join(filter_complex)])
            
        if transformations.get('pitch'):
            cmd.extend(["-af", "asetrate=44100*1.05,aresample=44100"])
        
        # Progress flag for FFmpeg
        cmd.extend(["-progress", "pipe:1"])
        cmd.append(output_path)
        
        print(f"Running FFmpeg: {' '.join(cmd)}")
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, universal_newlines=True)
        
        out_time_regex = re.compile(r"out_time_ms=(\d+)")
        
        for line in process.stdout:
            match = out_time_regex.search(line)
            if match and duration > 0:
                time_ms = int(match.group(1))
                # FFmpeg time is in microseconds (10^-6), so divide by 1,000,000 to get seconds
                percent = min(round((time_ms / 1000000) / duration * 100), 100)
                if progress_callback:
                    progress_callback(percent)
        
        process.wait()
        
        if process.returncode != 0:
            return False, f"FFmpeg failed with return code {process.returncode}"
        
        return True, "Success"
    except Exception as e:
        return False, str(e)
