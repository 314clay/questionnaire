/**
 * Audio Widget — auto-injects mic button + clip list into any template.
 * Include after audio-recorder.js. Call AudioWidget.init() after your
 * submit button exists in the DOM.
 *
 * Usage:
 *   AudioWidget.init();                    // wraps #submit with mic button
 *   const clips = AudioWidget.getAudio();  // [] or [{ base64, mimeType, duration }, ...]
 *   AudioWidget.lock();                    // disable after submit
 */
const AudioWidget = (() => {
  const clips = [];
  let listEl = null;
  let statusEl = null;
  let btn = null;

  const micSVG = `<svg class="mic-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z"/>
    <path d="M19 11a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.93V21h-3a1 1 0 0 0 0 2h8a1 1 0 0 0 0-2h-3v-3.07A7 7 0 0 0 19 11z"/>
  </svg>`;

  function renderList() {
    listEl.innerHTML = '';
    if (clips.length === 0) {
      btn.classList.remove('has-audio');
      return;
    }
    btn.classList.add('has-audio');
    clips.forEach((clip, i) => {
      const row = document.createElement('div');
      row.className = 'audio-clip';
      const kb = Math.round(clip.sizeBytes / 1024);
      row.innerHTML = `
        <span class="audio-clip-label">Clip ${i + 1}</span>
        <span class="audio-clip-meta">${clip.durationFormatted}, ${kb}KB</span>
        <button class="audio-remove" type="button">&times;</button>
      `;
      row.querySelector('.audio-remove').addEventListener('click', () => {
        clips.splice(i, 1);
        renderList();
      });
      listEl.appendChild(row);
    });
  }

  function init() {
    const submitBtn = document.getElementById('submit');
    if (!submitBtn) return;

    // Wrap submit button in a row with the mic button
    const row = document.createElement('div');
    row.className = 'submit-row';
    submitBtn.parentNode.insertBefore(row, submitBtn);
    row.appendChild(submitBtn);

    // Mic button
    btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'audio-btn' + (AudioRecorder.supported ? '' : ' unsupported');
    btn.innerHTML = micSVG;
    btn.title = AudioRecorder.supported ? 'Record audio clip' : 'Audio not supported in this browser';
    row.appendChild(btn);

    // Status line (recording indicator)
    statusEl = document.createElement('div');
    statusEl.className = 'audio-status' + (AudioRecorder.supported ? '' : ' unsupported');
    row.parentNode.insertBefore(statusEl, row.nextSibling);

    // Clip list (below status)
    listEl = document.createElement('div');
    listEl.className = 'audio-clip-list';
    if (!AudioRecorder.supported) listEl.classList.add('unsupported');
    statusEl.parentNode.insertBefore(listEl, statusEl.nextSibling);

    if (!AudioRecorder.supported) return;

    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (AudioRecorder.isRecording()) {
        await stopRecording();
      } else {
        await startRecording();
      }
    });
  }

  async function startRecording() {
    const ok = await AudioRecorder.start((time) => {
      statusEl.innerHTML = `<span class="rec-dot"></span> Recording ${time}`;
    });
    if (ok) {
      btn.classList.add('recording');
    } else {
      statusEl.textContent = 'Mic access denied';
      setTimeout(() => { statusEl.textContent = ''; }, 3000);
    }
  }

  async function stopRecording() {
    btn.classList.remove('recording');
    statusEl.innerHTML = 'Processing...';
    const data = await AudioRecorder.stop();
    statusEl.textContent = '';
    if (data) {
      clips.push(data);
      renderList();
    }
  }

  function getAudio() {
    if (clips.length === 0) return null;
    return clips.map(c => ({
      base64: c.base64,
      mimeType: c.mimeType,
      duration: c.duration
    }));
  }

  function lock() {
    if (btn) {
      btn.disabled = true;
      btn.style.opacity = '0.4';
      btn.style.pointerEvents = 'none';
    }
    if (listEl) {
      listEl.querySelectorAll('.audio-remove').forEach(b => {
        b.disabled = true;
        b.style.display = 'none';
      });
    }
  }

  return { init, getAudio, lock };
})();
