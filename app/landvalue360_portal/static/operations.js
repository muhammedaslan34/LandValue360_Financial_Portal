const $ = (id) => document.getElementById(id);
function message(text, ok = false) {
  const element = $('formMessage');
  if (!element) return;
  element.textContent = text || '';
  element.style.color = ok ? '#2e7d5b' : '#a83f43';
}

document.addEventListener('DOMContentLoaded', async () => {
  await lv360.me();
  const search = $('opsSearch');
  const status = $('opsStatus');
  function filter() {
    document.querySelectorAll('#opsCards .project-card').forEach((card) => {
      const query = (search?.value || '').toLowerCase();
      const selectedStatus = status?.value || '';
      card.hidden = !(card.dataset.name.toLowerCase().includes(query) && (!selectedStatus || card.dataset.status === selectedStatus));
    });
  }
  search?.addEventListener('input', filter);
  status?.addEventListener('change', filter);

  const root = $('operationsProject');
  if (!root) return;
  const id = root.dataset.projectId;
  $('statusForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      await lv360.api(`/api/operations/projects/${id}/status`, { method: 'POST', body: JSON.stringify(payload) });
      location.reload();
    } catch (error) { message(error.message); }
  });
  $('assignmentForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      await lv360.api(`/api/operations/projects/${id}/assign`, { method: 'POST', body: JSON.stringify(payload) });
      location.reload();
    } catch (error) { message(error.message); }
  });
  $('infoRequestForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      await lv360.api(`/api/operations/projects/${id}/information-requests`, { method: 'POST', body: JSON.stringify(payload) });
      location.reload();
    } catch (error) { message(error.message); }
  });
  $('internalNoteForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      await lv360.api(`/api/operations/projects/${id}/notes`, { method: 'POST', body: JSON.stringify(payload) });
      event.currentTarget.reset();
      message('تم حفظ الملاحظة الداخلية.', true);
    } catch (error) { message(error.message); }
  });
  $('analysisImportForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      await lv360.api(`/api/operations/projects/${id}/analysis-imports`, { method: 'POST', body: JSON.stringify(payload) });
      event.currentTarget.reset();
      message('تم تسجيل مرجع نتيجة التحليل الداخلي.', true);
    } catch (error) { message(error.message); }
  });
  $('reportUploadForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await lv360.api(`/api/operations/projects/${id}/reports`, { method: 'POST', body: new FormData(event.currentTarget) });
      location.reload();
    } catch (error) { message(error.message); }
  });
  document.querySelectorAll('.report-review').forEach((button) => button.addEventListener('click', async () => {
    try {
      await lv360.api(`/api/operations/report-versions/${button.dataset.versionId}/review`, {
        method: 'POST', body: JSON.stringify({ action: button.dataset.action }),
      });
      location.reload();
    } catch (error) { message(error.message); }
  }));
  $('publishReports')?.addEventListener('click', async () => {
    try {
      await lv360.api(`/api/operations/projects/${id}/publish`, { method: 'POST' });
      location.reload();
    } catch (error) { message(error.message); }
  });
});
