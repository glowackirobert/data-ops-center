// The open/close/submit/stop/clear behaviour shared by the Ask and Filter
// boxes. `submit(text, signal)` performs the request; `showResult(result)`
// and `showError(err)` render into the result node; `idleLabel` is the
// submit button's text when nothing is in flight (it reads "Stop" otherwise).

export function initPanel({ btn, box, form, input, result, clearBtn, idleLabel,
                            submit, showResult, showError }) {
  const submitBtn = form.querySelector('button[type="submit"]');
  let controller = null; // non-null exactly while a request is in flight

  const setBusy = busy => {
    input.disabled = busy;
    submitBtn.textContent = busy ? 'Stop' : idleLabel;
  };
  const clearResult = () => { result.innerHTML = ''; };

  // Also drops the "Thinking…" placeholder: the box stays open after Stop,
  // and closing mid-request must not let a stale response land later.
  const stop = () => {
    if (!controller) return;
    controller.abort();
    controller = null;
    setBusy(false);
    clearResult();
  };

  const open = () => {
    box.classList.remove('hidden');
    btn.classList.add('active');
    input.focus();
  };
  const close = () => {
    stop();
    box.classList.add('hidden');
    btn.classList.remove('active');
  };

  btn.onclick = () => (box.classList.contains('hidden') ? open() : close());
  box.querySelector('.close').onclick = close;
  clearBtn.onclick = () => {
    stop();
    input.value = '';
    input.focus();
  };

  form.onsubmit = async e => {
    e.preventDefault();
    if (controller) { stop(); return; } // the button reads "Stop" while busy
    const text = input.value.trim();
    if (!text) return;
    controller = new AbortController();
    setBusy(true);
    result.innerHTML = '<div class="pending">Thinking…</div>';
    try {
      showResult(await submit(text, controller.signal));
    } catch (err) {
      if (err.name !== 'AbortError') showError(err);
    } finally {
      controller = null;
      setBusy(false);
    }
  };

  return { clearResult };
}
