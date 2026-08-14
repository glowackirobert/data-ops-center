// First-visit welcome modal, reopenable any time via the "?" header button.
const SEEN_KEY = 'dataOpsCenter.introSeen';

export function initHelp() {
  const modal = document.getElementById('help-modal');
  const open = () => modal.classList.remove('hidden');
  const close = () => {
    modal.classList.add('hidden');
    localStorage.setItem(SEEN_KEY, '1');
  };

  document.getElementById('help-btn').onclick = open;
  document.getElementById('help-close').onclick = close;
  document.getElementById('help-dismiss').onclick = close;
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) close();
  });

  if (!localStorage.getItem(SEEN_KEY)) open();
}
