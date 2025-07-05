from flask import Flask, Response, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import cv2
from datetime import datetime
import threading
import queue
import pyaudio
import base64
import json
import time
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables for camera and audio
camera = None
audio_stream = None
is_streaming = True  # Always streaming
audio_thread = None

# Audio configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# Initialize PyAudio
p = pyaudio.PyAudio()

class CameraStreamer:
    def __init__(self):
        self.camera = cv2.VideoCapture(0)
        self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.camera.set(cv2.CAP_PROP_FPS, 30)
        
    def __del__(self):
        if self.camera:
            self.camera.release()
    
    def get_frame(self):
        success, frame = self.camera.read()
        if not success:
            return None
        
        # Add timestamp and status to frame
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"LIVE - {timestamp}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "🔴 STREAMING", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        
        # Encode frame as JPEG
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ret:
            return buffer.tobytes()
        return None

def generate_frames():
    """Generate video frames for streaming"""
    global camera
    if not camera:
        camera = CameraStreamer()
    
    while True:
        frame = camera.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            # If camera fails, wait and retry
            time.sleep(0.1)

def audio_streaming_callback():
    """Capture and stream audio data via WebSocket - runs continuously"""
    global audio_stream, is_streaming
    
    while True:  # Keep trying to restart audio if it fails
        try:
            print("🎵 Starting audio stream...")
            audio_stream = p.open(format=FORMAT,
                                channels=CHANNELS,
                                rate=RATE,
                                input=True,
                                frames_per_buffer=CHUNK)
            
            print("🎵 Audio stream active")
            
            while is_streaming:
                try:
                    data = audio_stream.read(CHUNK, exception_on_overflow=False)
                    # Convert to base64 for WebSocket transmission
                    audio_b64 = base64.b64encode(data).decode('utf-8')
                    
                    # Emit audio data to all connected clients
                    socketio.emit('audio_data', {
                        'data': audio_b64,
                        'timestamp': time.time(),
                        'format': 'pcm_s16le',
                        'channels': CHANNELS,
                        'rate': RATE,
                        'chunk_size': CHUNK
                    })
                    
                    # Small delay to prevent overwhelming the client
                    time.sleep(0.01)
                    
                except Exception as e:
                    print(f"Audio read error: {e}")
                    break
                    
        except Exception as e:
            print(f"Audio stream error: {e}")
            socketio.emit('audio_error', {'error': str(e)})
            
        finally:
            if audio_stream:
                audio_stream.stop_stream()
                audio_stream.close()
                
        if not is_streaming:
            break
            
        # Wait before retrying
        print("🔄 Retrying audio stream in 2 seconds...")
        time.sleep(2)

# HTML template for the auto-streaming page
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>🔴 LIVE Stream</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: Arial, sans-serif;
            background: #000;
            color: white;
            overflow: hidden;
            height: 100vh;
        }
        
        .video-container {
            position: relative;
            width: 100vw;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        
        .video-stream {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .overlay {
            position: absolute;
            top: 20px;
            left: 20px;
            background: rgba(0, 0, 0, 0.7);
            padding: 15px 20px;
            border-radius: 10px;
            backdrop-filter: blur(10px);
            z-index: 10;
        }
        
        .live-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 10px;
        }
        
        .live-dot {
            width: 8px;
            height: 8px;
            background: #ff4444;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(1.1); }
            100% { opacity: 1; transform: scale(1); }
        }
        
        .status-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 5px;
            font-size: 14px;
        }
        
        .status-indicator {
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }
        
        .status-green { background: #00ff88; }
        .status-red { background: #ff4444; }
        .status-yellow { background: #ffaa00; }
        
        .status-text {
            font-size: 12px;
            opacity: 0.9;
        }
    </style>
</head>
<body>
    <div class="video-container">
        <img src="{{ url_for('video_feed') }}" class="video-stream" alt="Live Video Stream">
        
        <div class="overlay">
            <div class="live-indicator">
                <div class="live-dot"></div>
                <strong>LIVE</strong>
            </div>
            
            <div class="status-row">
                <div class="status-indicator status-red" id="videoStatus"></div>
                <span class="status-text" id="videoStatusText">Video: Connecting...</span>
            </div>
            
            <div class="status-row">
                <div class="status-indicator status-red" id="audioStatus"></div>
                <span class="status-text" id="audioStatusText">Audio: Connecting...</span>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let audioContext;
        
        // Initialize audio context
        async function initAudioContext() {
            try {
                audioContext = new (window.AudioContext || window.webkitAudioContext)();
                console.log('Audio context initialized');
            } catch (e) {
                console.error('Audio context initialization failed:', e);
                updateAudioStatus('error', 'Audio: Error');
            }
        }
        
        // Update video status
        function updateVideoStatus(status, text) {
            const indicator = document.getElementById('videoStatus');
            const statusText = document.getElementById('videoStatusText');
            
            indicator.className = 'status-indicator';
            if (status === 'connected') {
                indicator.classList.add('status-green');
            } else if (status === 'error') {
                indicator.classList.add('status-yellow');
            } else {
                indicator.classList.add('status-red');
            }
            
            statusText.textContent = text;
        }
        
        // Update audio status
        function updateAudioStatus(status, text) {
            const indicator = document.getElementById('audioStatus');
            const statusText = document.getElementById('audioStatusText');
            
            indicator.className = 'status-indicator';
            if (status === 'connected') {
                indicator.classList.add('status-green');
            } else if (status === 'error') {
                indicator.classList.add('status-yellow');
            } else {
                indicator.classList.add('status-red');
            }
            
            statusText.textContent = text;
        }
        
        // Play audio data
        async function playAudioData(base64Data) {
            if (!audioContext) {
                await initAudioContext();
            }
            
            try {
                const binaryString = atob(base64Data);
                const arrayBuffer = new ArrayBuffer(binaryString.length);
                const uint8Array = new Uint8Array(arrayBuffer);
                
                for (let i = 0; i < binaryString.length; i++) {
                    uint8Array[i] = binaryString.charCodeAt(i);
                }
                
                const int16Array = new Int16Array(arrayBuffer);
                const float32Array = new Float32Array(int16Array.length);
                
                for (let i = 0; i < int16Array.length; i++) {
                    float32Array[i] = int16Array[i] / 32768.0;
                }
                
                const audioBuffer = audioContext.createBuffer(1, float32Array.length, 44100);
                audioBuffer.getChannelData(0).set(float32Array);
                
                const source = audioContext.createBufferSource();
                const gainNode = audioContext.createGain();
                
                source.buffer = audioBuffer;
                gainNode.gain.value = 0.7;
                
                source.connect(gainNode);
                gainNode.connect(audioContext.destination);
                
                source.start();
                
            } catch (e) {
                console.error('Audio playback error:', e);
                updateAudioStatus('error', 'Audio: Playback Error');
            }
        }
        
        // Socket event handlers
        socket.on('connect', function() {
            console.log('WebSocket connected');
            updateAudioStatus('connected', 'Audio: Connected');
            initAudioContext();
        });
        
        socket.on('disconnect', function() {
            console.log('WebSocket disconnected');
            updateAudioStatus('disconnected', 'Audio: Disconnected');
        });
        
        socket.on('audio_data', function(data) {
            playAudioData(data.data);
            updateAudioStatus('connected', 'Audio: Streaming');
        });
        
        socket.on('audio_error', function(data) {
            console.error('Audio error:', data.error);
            updateAudioStatus('error', 'Audio: Error');
        });
        
        // Check video stream status
        const videoElement = document.querySelector('.video-stream');
        
        videoElement.onload = function() {
            updateVideoStatus('connected', 'Video: Streaming');
        };
        
        videoElement.onerror = function() {
            updateVideoStatus('error', 'Video: Error');
        };
        
        // Auto-connect audio context on first user interaction
        document.addEventListener('click', function() {
            if (!audioContext) {
                initAudioContext();
            }
        }, { once: true });
        
        // Initialize
        window.onload = function() {
            console.log('Live stream page loaded');
            updateVideoStatus('connected', 'Video: Streaming');
        };
    </script>
</body>

</html>
"""

@app.route('/')
def index():
    """Main page with auto-streaming video and audio"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/video_feed')
def video_feed():
    """Video streaming endpoint - always active"""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/status')
def get_status():
    """Get current streaming status"""
    return jsonify({
        'status': 'streaming',
        'camera_active': camera is not None,
        'audio_streaming': is_streaming,
        'uptime_seconds': time.time() - start_time,
        'audio_format': {
            'sample_rate': RATE,
            'channels': CHANNELS,
            'format': 'PCM 16-bit',
            'chunk_size': CHUNK
        },
        'video_format': {
            'resolution': '640x480',
            'fps': 30,
            'format': 'MJPEG'
        },
        'timestamp': time.time()
    })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'streaming': True,
        'uptime_seconds': time.time() - start_time,
        'services': {
            'camera': camera is not None,
            'audio': is_streaming,
            'websocket': True
        },
        'timestamp': time.time()
    })

# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    print('🔗 Client connected to audio stream')
    emit('connected', {'message': 'Connected to live audio stream'})

@socketio.on('disconnect')
def handle_disconnect():
    print('❌ Client disconnected from audio stream')

def initialize_streaming():
    """Initialize camera and audio streaming on startup"""
    global camera, audio_thread, start_time
    
    print("🎥 Initializing camera...")
    camera = CameraStreamer()
    
    print("🎵 Starting audio streaming thread...")
    audio_thread = threading.Thread(target=audio_streaming_callback)
    audio_thread.daemon = True
    audio_thread.start()
    
    start_time = time.time()
    print("✅ Streaming initialized successfully!")

if __name__ == '__main__':
    try:
        print("🚀 Starting Live Camera & Audio Streaming Server...")
        print("📡 Server will be available at: http://localhost:5000")
        print("🔴 LIVE streaming starts automatically!")
        print("🎬 Video: Real-time MJPEG stream")
        print("🎵 Audio: Real-time PCM stream via WebSocket")
        
        # Initialize streaming before starting the server
        initialize_streaming()
        
        print("\n✅ All systems ready - Starting server...")
        print("⚠️  Make sure to allow microphone access in your browser!")
        
        socketio.run(app, debug=False, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        # Cleanup
        is_streaming = False
        if camera:
            del camera
        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
        p.terminate()
        print("🔄 Cleanup completed")