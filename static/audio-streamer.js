/**
 * Questionnaire Audio Streamer
 * Streams mic audio as binary chunks over WebSocket.
 * Also provides real-time audio level via AnalyserNode.
 */
const AudioStreamer = (() => {
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
  let stream = null;
  let audioCtx = null;
  let analyser = null;
  let freqData = null;

  async function start(ws) {
    if (!supported) return false;

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      console.warn('Microphone access denied:', e.message);
      return false;
    }

    // Set up AnalyserNode for level metering
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaStreamSource(stream);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    freqData = new Uint8Array(analyser.frequencyBinCount);

    // Stream audio chunks over WebSocket
    mediaRecorder = new MediaRecorder(stream, { mimeType });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
        ws.send(e.data);
      }
    };

    mediaRecorder.start(250);
    return true;
  }

  function stop() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop();
    }
    mediaRecorder = null;

    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }

    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
      analyser = null;
      freqData = null;
    }
  }

  function isStreaming() {
    return mediaRecorder && mediaRecorder.state === 'recording';
  }

  /** Returns 0-100 normalized audio level, or 0 if not streaming */
  function getLevel() {
    if (!analyser || !freqData) return 0;
    analyser.getByteFrequencyData(freqData);
    let sum = 0;
    for (let i = 0; i < freqData.length; i++) sum += freqData[i];
    return Math.round((sum / freqData.length) * 100 / 255);
  }

  return { supported, start, stop, isStreaming, getLevel, getMimeType: () => mimeType };
})();
