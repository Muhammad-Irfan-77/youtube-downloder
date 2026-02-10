from flask import Flask, render_template, request, jsonify, send_file, Response, after_this_request
from downloader import YouTubeDownloader
import os
import threading
import uuid
import yt_dlp
import json
import time
import queue
import re
from video_processor import apply_transformations
from flask_cors import CORS

app = Flask(__name__)

# Configure CORS with explicit settings
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

downloader = YouTubeDownloader()

# Global job tracking
jobs = {}
job_queues = {} # For SSE updates

def strip_ansi(text):
    """Removes ANSI escape sequences from strings."""
    if not isinstance(text, str):
        return text
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'message': 'YouTube Downloader API is running',
        'endpoints': {
            '/check': 'POST - Check video info',
            '/download': 'POST - Start download',
            '/events/<job_id>': 'GET - Download progress stream',
            '/files/<job_id>': 'GET - Download completed file'
        }
    })

@app.route('/check', methods=['POST'])
def check_video():
    data = request.get_json()
    url = data.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    info = downloader.get_video_info(url)
    if info:
        return jsonify(info)
    else:
        return jsonify({'error': 'Could not fetch video info'}), 500

@app.route('/download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = data.get('url')
    format_type = data.get('format', 'video')
    quality = data.get('quality', 'best')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {'status': 'starting', 'progress': 0, 'eta': '', 'filename': None, 'error': None}
    job_queues[job_id] = queue.Queue()

    def background_download(jid, u, f, q):
        def progress_hook(d):
            if d['status'] == 'downloading':
                jobs[jid]['status'] = 'downloading'
                # Robust percentage parsing
                p_str = d.get('_percent_str')
                if p_str:
                    try:
                        jobs[jid]['progress'] = float(p_str.replace('%', '').strip())
                    except:
                        pass
                
                # Speed and Size metadata
                jobs[jid]['speed'] = strip_ansi(d.get('_speed_str', 'Pending...')).strip()
                jobs[jid]['eta'] = strip_ansi(d.get('_eta_str', 'Calculating...')).strip()
                
                # Downloaded vs Total
                total = strip_ansi(d.get('_total_bytes_str') or d.get('_total_bytes_estimate_str', 'Unknown size'))
                downloaded = strip_ansi(d.get('_downloaded_bytes_str', '0B'))
                jobs[jid]['size_info'] = f"{downloaded} / {total}"
                
                # Push update to SSE queue
                if jid in job_queues:
                    job_queues[jid].put(jobs[jid].copy())
                
            elif d['status'] == 'finished':
                # Placeholder for transitional status
                jobs[jid]['status'] = 'processing'
                jobs[jid]['progress'] = 0 
                jobs[jid]['size_info'] = "Download complete, preparing transformations..."
                if jid in job_queues:
                    job_queues[jid].put(jobs[jid].copy())

        def postprocessor_hook(d):
            # We will handle granular progress via apply_transformations instead of hooks
            # but we can still use this to update the UI status text
            if d['status'] == 'started':
                jobs[jid]['status'] = 'processing'
                jobs[jid]['processing_step'] = d.get('postprocessor', 'ffmpeg')
                if jid in job_queues:
                    job_queues[jid].put(jobs[jid].copy())
            elif d['status'] == 'finished':
                # Final check before setting to finished in main thread
                pass

        ydl_opts = {
            'outtmpl': os.path.join(downloader.download_path, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'postprocessor_hooks': [postprocessor_hook],
            'quiet': True,
            'noplaylist': True,
        }

        if f == 'audio':
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            if q == '1080p':
                 ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
            elif q == '720p':
                 ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
            else:
                 ydl_opts['format'] = 'best'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(u, download=True)
                path = ydl.prepare_filename(info)
                if f == 'audio':
                    # yt-dlp might have already converted to mp3 if ffmpeg hook ran
                    # but we want to ensure our custom processor runs if we had specific transforms
                    # For now, let's assume if it's audio we just check the path
                    base_path = os.path.splitext(path)[0]
                    if os.path.exists(base_path + '.mp3'):
                        path = base_path + '.mp3'

                # --- NEW GRANULAR PROCESSING PHASE ---
                jobs[jid]['status'] = 'processing'
                jobs[jid]['progress'] = 0
                jobs[jid]['processing_step'] = 'Applying Premium Enhancements'
                
                def ffmpeg_p_callback(p):
                    jobs[jid]['progress'] = p
                    if jid in job_queues:
                        job_queues[jid].put(jobs[jid].copy())

                # Example transformations (you can toggle these based on UI if added)
                transforms = {'scale': True, 'jitter': True} if f == 'video' else {'pitch': True}
                
                processed_path = os.path.join(downloader.download_path, f"processed_{job_id}_{os.path.basename(path)}")
                success, msg = apply_transformations(path, processed_path, transforms, progress_callback=ffmpeg_p_callback)
                
                if success:
                    # Replace original with processed for download
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except Exception as e:
                        print(f"Cleanup original failed: {e}")

                    jobs[jid]['filename'] = processed_path
                    jobs[jid]['status'] = 'finished'
                    jobs[jid]['progress'] = 100
                else:
                    # Fallback to original if transformation failed but download succeeded
                    jobs[jid]['filename'] = path
                    jobs[jid]['status'] = 'finished' 
                    jobs[jid]['progress'] = 100
                    jobs[jid]['error_warning'] = f"Transform failed: {msg}"

                if jid in job_queues:
                    job_queues[jid].put(jobs[jid].copy())
                    job_queues[jid].put(None) # Sentinel to end SSE
        except Exception as e:
            jobs[jid]['status'] = 'error'
            jobs[jid]['error'] = str(e)
            if jid in job_queues:
                job_queues[jid].put(jobs[jid].copy())
                job_queues[jid].put(None)

    threading.Thread(target=background_download, args=(job_id, url, format_type, quality)).start()
    return jsonify({'job_id': job_id})

@app.route('/events/<job_id>')
def events(job_id):
    def generate():
        q = job_queues.get(job_id)
        if not q:
            return
        
        while True:
            try:
                # Get update from queue
                data = q.get(timeout=30) # 30s timeout to keep connection alive
                if data is None:
                    break
                yield f"data: {json.dumps(data)}\n\n"
            except queue.Empty:
                # Send keep-alive
                yield ": keep-alive\n\n"
            except Exception:
                break
        
        # Cleanup
        if job_id in job_queues:
            del job_queues[job_id]

    return Response(generate(), mimetype='text/event-stream')

@app.route('/status/<job_id>')
def get_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)

@app.route('/fetch/<job_id>')
def fetch_file(job_id):
    job = jobs.get(job_id)
    if not job or job['status'] != 'finished':
        return jsonify({'error': 'File not ready'}), 400
    
    file_path = job['filename']
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found on server'}), 404

    @after_this_request
    def cleanup(response):
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            # Cleanup from jobs dict to keep memory low
            if job_id in jobs:
                del jobs[job_id]
        except Exception as e:
            app.logger.error(f"Error in cleanup: {e}")
        return response

    return send_file(file_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
