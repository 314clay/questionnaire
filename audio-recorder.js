/**
 * Questionnaire Audio Recorder
 * Simple MediaRecorder wrapper — no dependencies, works on older browsers.
 * Falls back gracefully (hides UI) if MediaRecorder is unavailable.
 */
const AudioRecorder = (() => {
  // Detect best supported MIME type
  function getMimeType() {
    const types = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/ogg;codecs=opus',
      'audio/ogg',
    ];
    if (typeof MediaRecorder === 'undefined') return null;
    for (const t of types) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return null;
  }

  const mimeType = getMimeType();
  const supported = !!mimeType;

  let mediaRecorder = null;
  let chunks = [];
  let stream = null;
  let startTime = 0;
  let timerInterval = null;
  let onTick = null;

  function formatTime(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m + ':' + String(sec).padStart(2, '0');
  }

  async function start(tickCallback) {
    if (!supported) return false;
    onTick = tickCallback || null;
    chunks = [];

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      console.warn('Microphone access denied:', e.message);
      return false;
    }

    mediaRecorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.start(250); // collect in 250ms chunks for progress
    startTime = Date.now();

    if (onTick) {
      onTick(formatTime(0));
      timerInterval = setInterval(() => {
        onTick(formatTime(Date.now() - startTime));
      }, 500);
    }

    return true;
  }

  function stop() {
    return new Promise((resolve) => {
      if (!mediaRecorder || mediaRecorder.state === 'inactive') {
        resolve(null);
        return;
      }

      if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
      }

      mediaRecorder.onstop = () => {
        // Stop mic access
        if (stream) {
          stream.getTracks().forEach(t => t.stop());
          stream = null;
        }

        const blob = new Blob(chunks, { type: mimeType });
        const duration = Date.now() - startTime;
        chunks = [];

        // Convert to base64
        const reader = new FileReader();
        reader.onloadend = () => {
          resolve({
            base64: reader.result,  // data:audio/webm;base64,...
            mimeType,
            duration,
            durationFormatted: formatTime(duration),
            sizeBytes: blob.size
          });
        };
        reader.readAsDataURL(blob);
      };

      mediaRecorder.stop();
    });
  }

  function isRecording() {
    return mediaRecorder && mediaRecorder.state === 'recording';
  }

  return { supported, start, stop, isRecording, getMimeType: () => mimeType };
})();
