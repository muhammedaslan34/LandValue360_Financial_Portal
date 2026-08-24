const $ = (id) => document.getElementById(id);
function cleanPolicyNumber(value, digits=10){ const n=Number(value); return Number.isFinite(n)?String(Number(n.toFixed(digits))):''; }

function message(text, ok = false) {
  const element = $('formMessage');
  if (!element) return;
  element.textContent = text || '';
  element.style.color = ok ? '#2e7d5b' : '#a83f43';
}

let cache = { users: [], organizations: [], roles: [] };

function populateSelectors() {
  const userPh = lv360.t('اختر المستخدم', 'Select User');
  const orgPh = lv360.t('اختر المؤسسة', 'Select Organization');
  $('membershipUser').innerHTML = `<option value="" disabled selected hidden>${lv360.esc(userPh)}</option>` + cache.users.map((u) => `<option value="${u.id}">${lv360.esc(u.full_name)} — ${lv360.esc(u.email)}</option>`).join('');
  $('membershipOrganization').innerHTML = `<option value="" disabled selected hidden>${lv360.esc(orgPh)}</option>` + cache.organizations.map((o) => `<option value="${o.id}">${lv360.esc(o.name)}</option>`).join('');
  $('membershipRole').innerHTML = cache.roles.map((r) => `<option value="${r.code}">${lv360.esc(r.name_ar)} (${lv360.esc(r.code)})</option>`).join('');
  lv360.syncSelectPlaceholder($('membershipUser'));
  lv360.syncSelectPlaceholder($('membershipOrganization'));
}

function renderSettings(settings) {
  $('systemSettings').innerHTML = Object.entries(settings).map(([key, value]) => `
    <form class="setting-form panel compact" data-key="${lv360.esc(key)}">
      <strong>${lv360.esc(key)}</strong>
      <textarea name="value" rows="5">${lv360.esc(JSON.stringify(value, null, 2))}</textarea>
      <button class="button small secondary">حفظ الإعداد</button>
    </form>`).join('');
  document.querySelectorAll('.setting-form').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        const value = JSON.parse(form.elements.value.value);
        await lv360.api(`/api/admin/settings/${encodeURIComponent(form.dataset.key)}`, { method: 'PUT', body: JSON.stringify(value) });
        message('تم حفظ إعداد النظام.', true);
      } catch (error) { message(error.message || 'JSON غير صالح'); }
    });
  });
}

function renderFilePolicies(rows) {
  $('filePolicies').innerHTML = rows.map((row) => `<tr data-extension="${lv360.esc(row.extension)}">
    <td><strong>${lv360.esc(row.extension)}</strong></td>
    <td><input data-field="mime_types" value="${lv360.esc((row.mime_types || []).join(', '))}"></td>
    <td><input data-field="max_mb" type="number" min="1" max="250" step="1" value="${Math.round(row.max_size_bytes / 1024 / 1024)}"></td>
    <td><input data-field="active" type="checkbox" ${row.active ? 'checked' : ''}></td>
    <td><button class="button small save-file-policy">حفظ</button></td>
  </tr>`).join('');
  document.querySelectorAll('.save-file-policy').forEach((button) => {
    button.onclick = async () => {
      const row = button.closest('tr');
      const extension = row.dataset.extension;
      const payload = {
        mime_types: row.querySelector('[data-field="mime_types"]').value,
        max_size_bytes: Number(row.querySelector('[data-field="max_mb"]').value) * 1024 * 1024,
        active: row.querySelector('[data-field="active"]').checked,
      };
      try {
        await lv360.api(`/api/admin/file-policies/${encodeURIComponent(extension)}`, { method: 'PUT', body: JSON.stringify(payload) });
        message(`تم حفظ سياسة ${extension}.`, true);
      } catch (error) { message(error.message); }
    };
  });
}

function renderEmailTemplates(rows) {
  $('emailTemplates').innerHTML = rows.map((row) => `<details class="registry-item email-template" data-code="${lv360.esc(row.code)}">
    <summary><strong>${lv360.esc(row.code)}</strong> — ${row.active ? 'مفعل' : 'متوقف'}</summary>
    <form class="form-grid compact">
      <label>العنوان العربي<input name="subject_ar" value="${lv360.esc(row.subject_ar)}" required></label>
      <label>العنوان الإنجليزي<input name="subject_en" value="${lv360.esc(row.subject_en)}" required></label>
      <label class="full">النص العربي<textarea name="body_ar" rows="4" required>${lv360.esc(row.body_ar)}</textarea></label>
      <label class="full">النص الإنجليزي<textarea name="body_en" rows="4" required>${lv360.esc(row.body_en)}</textarea></label>
      <label class="checkbox-label"><input type="checkbox" name="active" ${row.active ? 'checked' : ''}> مفعل</label>
      <button class="button small secondary full">حفظ القالب</button>
    </form>
  </details>`).join('');
  document.querySelectorAll('.email-template form').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const container = form.closest('.email-template');
      const data = new FormData(form);
      const payload = Object.fromEntries(data);
      payload.active = form.elements.active.checked;
      try {
        await lv360.api(`/api/admin/email-templates/${encodeURIComponent(container.dataset.code)}`, { method: 'PUT', body: JSON.stringify(payload) });
        message('تم حفظ قالب البريد.', true);
        await loadAdmin();
      } catch (error) { message(error.message); }
    });
  });
}

function renderPrivacy(rows) {
  $('privacyRequests').innerHTML = rows.map((row) => `<tr>
    <td>${lv360.esc(row.full_name)}<small>${lv360.esc(row.email)}</small></td>
    <td>${lv360.esc(row.request_type)}</td>
    <td>${lv360.esc(row.status)}</td>
    <td>${new Date(row.created_at).toLocaleString('ar')}</td>
    <td><select class="privacy-status" data-id="${row.id}">${lv360.optionsHtml('privacy_status', { selected: row.status })}</select></td>
  </tr>`).join('');
  document.querySelectorAll('.privacy-status').forEach((select) => {
    const current = rows.find((row) => row.id === select.dataset.id);
    select.value = current.status;
    select.onchange = async () => {
      try {
        await lv360.api(`/api/admin/privacy-requests/${select.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ status: select.value }) });
        message('تم تحديث طلب الخصوصية.', true);
      } catch (error) { message(error.message); await loadAdmin(); }
    };
  });
}

function locale(){return lv360.lang()==='en'?'en-US':'ar-SY';}
function dateTime(value){return value?new Date(value).toLocaleString(locale()):'—';}
function t(ar,en){return lv360.t(ar,en);}
function openAdminModal(title,html){$('adminModalTitle').textContent=title;$('adminModalBody').innerHTML=html;$('adminModal').hidden=false;lv360.applyLanguage($('adminModal'));}
function closeAdminModal(){$('adminModal').hidden=true;$('adminModalBody').innerHTML='';}
function statusText(user){if(user.suspended)return t('معلّق','Suspended');if(!user.active)return t('متوقف','Inactive');if(user.must_change_password)return t('كلمة مؤقتة','Temporary Password');return t('نشط','Active');}
function renderAdminUsers(users){
  $('adminUsers').innerHTML=users.map((u)=>`<tr>
    <td><strong>${lv360.esc(u.full_name)}</strong><small>${t('أنشئ','Created')}: ${dateTime(u.created_at)}</small></td>
    <td>${lv360.esc(u.email)}</td><td>${lv360.esc(u.roles.join(', '))}</td><td>${dateTime(u.last_login_at)}</td>
    <td>${u.project_count}</td><td>${u.active_sessions}</td><td>${lv360.esc(statusText(u))}</td>
    <td><div class="admin-actions">
      <button class="button small secondary user-activity" data-id="${u.id}">${t('التفاصيل','Details')}</button>
      <button class="button small secondary user-reset-link" data-id="${u.id}">${t('رابط استعادة','Reset Link')}</button>
      <button class="button small secondary user-temp-password" data-id="${u.id}">${t('كلمة مؤقتة','Temporary Password')}</button>
      <button class="button small secondary user-revoke" data-id="${u.id}" ${u.active_sessions?'':'disabled'}>${t('إنهاء الجلسات','Revoke Sessions')}</button>
      <button class="button small toggle-user" data-id="${u.id}" data-suspended="${u.suspended}">${u.suspended?t('إلغاء التعليق','Unsuspend'):t('تعليق','Suspend')}</button>
    </div></td></tr>`).join('')||`<tr><td colspan="8" class="empty-cell">${t('لا يوجد مستخدمون.','No users found.')}</td></tr>`;
  document.querySelectorAll('.toggle-user').forEach((button)=>button.onclick=async()=>{try{await lv360.api(`/api/admin/users/${button.dataset.id}`,{method:'PATCH',body:JSON.stringify({suspended:button.dataset.suspended!=='true'})});await loadAdmin();}catch(error){message(error.message);}});
  document.querySelectorAll('.user-activity').forEach((button)=>button.onclick=()=>showUserActivity(button.dataset.id));
  document.querySelectorAll('.user-reset-link').forEach((button)=>button.onclick=()=>sendPasswordReset(button.dataset.id));
  document.querySelectorAll('.user-temp-password').forEach((button)=>button.onclick=()=>issueTemporaryPassword(button.dataset.id));
  document.querySelectorAll('.user-revoke').forEach((button)=>button.onclick=()=>revokeUserSessions(button.dataset.id));
}
function renderOrganizations(rows){
  $('adminOrganizations').innerHTML=rows.map((o)=>`<tr><td><strong>${lv360.esc(o.name)}</strong><small>${lv360.esc(o.slug)}</small></td><td>${lv360.esc(o.kind)}</td><td>${o.membership_count}</td><td>${o.project_count}</td><td>${o.active?t('نشطة','Active'):t('متوقفة','Inactive')}</td><td>${dateTime(o.created_at)}</td></tr>`).join('')||`<tr><td colspan="6" class="empty-cell">${t('لا توجد مؤسسات.','No organizations found.')}</td></tr>`;
}
function renderProjects(projects){
  cache.projects=projects;
  $('adminProjects').innerHTML=projects.map((p)=>`<tr>
    <td><strong>${lv360.esc(p.reference)}</strong></td><td>${lv360.esc(p.name)}<small>${t('آخر تحديث','Updated')}: ${dateTime(p.updated_at)}</small></td>
    <td>${lv360.esc(p.organization_name)}</td><td>${lv360.esc(p.owner_name)}<small>${lv360.esc(p.owner_email)}</small></td><td>${lv360.esc(p.status)}</td>
    <td>${p.latest_run?`${lv360.esc(p.latest_run.status)}<small>${dateTime(p.latest_run.completed_at||p.latest_run.created_at)}</small>`:t('لم يُشغّل','Not Run')}</td><td>${p.document_count}</td>
    <td><div class="admin-actions">
      <a class="button small secondary" href="${p.actions.project}">${t('فتح البيانات','Open Data')}</a>
      <a class="button small" href="${p.actions.financial}">${t('التحليل المالي','Financial Analysis')}</a>
      ${p.actions.financial_pdf?`<a class="button small secondary" href="${p.actions.financial_pdf}">PDF</a>`:''}
      ${p.actions.financial_excel?`<a class="button small secondary" href="${p.actions.financial_excel}">Excel</a>`:''}
      <button class="button small secondary project-details" data-id="${p.id}">${t('كل الملفات','All Files')}</button>
    </div></td></tr>`).join('')||`<tr><td colspan="8" class="empty-cell">${t('لا توجد مشاريع مطابقة.','No matching projects.')}</td></tr>`;
  document.querySelectorAll('.project-details').forEach((button)=>button.onclick=()=>showProjectOverview(button.dataset.id));
}
function renderAudit(rows){
  $('auditList').innerHTML=rows.map((a)=>`<div class="activity-row"><strong>${lv360.esc(a.action)}</strong> · ${lv360.esc(a.entity_type)}${a.project_name?` · ${lv360.esc(a.project_name)}`:''}<small>${lv360.esc(a.user_name||a.user_email||t('النظام','System'))} · ${dateTime(a.created_at)}${a.ip_address?` · ${lv360.esc(a.ip_address)}`:''}</small></div>`).join('')||`<div class="empty">${t('لا توجد أحداث ضمن التصفية.','No events match the filter.')}</div>`;
}
async function showUserActivity(userId){
  try{
    const data=await lv360.api(`/api/admin/users/${userId}/activity`),u=data.user;
    const memberships=(data.memberships||[]).map(m=>`<li>${lv360.esc(m.organization_name)} — ${lv360.esc(m.status)}</li>`).join('')||`<li>${t('لا توجد عضويات.','No memberships.')}</li>`;
    const sessions=(data.sessions||[]).map(s=>`<div class="activity-row"><strong>${s.revoked_at?t('جلسة منتهية','Revoked Session'):t('جلسة','Session')}</strong><small>${dateTime(s.created_at)} · ${lv360.esc(s.ip_address||'—')} · ${lv360.esc((s.user_agent||'').slice(0,120))}</small></div>`).join('');
    const logins=(data.login_attempts||[]).slice(0,12).map(a=>`<div class="activity-row"><strong>${a.success?t('دخول ناجح','Successful Login'):t('دخول فاشل','Failed Login')}</strong><small>${dateTime(a.attempted_at)} · ${lv360.esc(a.ip_address||'—')}</small></div>`).join('');
    const audit=(data.audit||[]).slice(0,30).map(a=>`<div class="activity-row"><strong>${lv360.esc(a.action)}</strong><small>${dateTime(a.created_at)}${a.project_id?` · ${lv360.esc(a.project_id)}`:''}</small></div>`).join('');
    openAdminModal(u.full_name,`<div class="grid-2"><div><p><strong>${lv360.esc(u.email)}</strong></p><p>${t('الأدوار','Roles')}: ${lv360.esc((u.roles||[]).join(', '))}</p><p>${t('آخر دخول','Last Login')}: ${dateTime(u.last_login_at)}</p><p>${t('آخر تغيير لكلمة المرور','Password Changed')}: ${dateTime(u.password_changed_at)}</p><p>${t('الحالة','Status')}: ${lv360.esc(statusText(u))}</p><h3>${t('العضويات','Memberships')}</h3><ul>${memberships}</ul></div><div><h3>${t('الجلسات','Sessions')}</h3><div class="activity-list">${sessions||t('لا توجد جلسات.','No sessions.')}</div></div></div><hr><h3>${t('محاولات الدخول','Login Attempts')}</h3><div class="activity-list">${logins||t('لا توجد محاولات.','No attempts.')}</div><hr><h3>${t('النشاطات الأخيرة','Recent Activity')}</h3><div class="activity-list">${audit||t('لا توجد نشاطات.','No activity.')}</div>`);
  }catch(error){message(error.message);}
}
async function sendPasswordReset(userId){
  if(!confirm(t('إرسال رابط آمن لإعادة تعيين كلمة المرور لهذا المستخدم؟','Send a secure password-reset link to this user?')))return;
  try{await lv360.api(`/api/admin/users/${userId}/send-password-reset`,{method:'POST',body:'{}'});message(t('تمت جدولة رابط إعادة التعيين للإرسال.','Password-reset link queued for delivery.'),true);}catch(error){message(error.message);}
}
async function issueTemporaryPassword(userId){
  if(!confirm(t('سيتم إنهاء جميع جلسات المستخدم وإجباره على تغيير كلمة المرور عند الدخول. متابعة؟','All user sessions will be revoked and a password change will be required at login. Continue?')))return;
  try{
    const result=await lv360.api(`/api/admin/users/${userId}/temporary-password`,{method:'POST',body:'{}'});
    openAdminModal(t('كلمة المرور المؤقتة','Temporary Password'),`<p class="admin-security-note">${t('تظهر هذه الكلمة مرة واحدة فقط. لا يستطيع النظام أو المدير استرجاع كلمة المرور الأصلية.','This password is shown once. Neither the system nor the administrator can retrieve the original password.')}</p><div class="one-time-secret"><code id="temporaryPasswordValue">${lv360.esc(result.temporary_password)}</code><button class="button small secondary" id="copyTemporaryPassword" type="button">${t('نسخ','Copy')}</button></div><p>${t('يجب على المستخدم تغييرها فور أول دخول.','The user must change it at first login.')}</p>`);
    $('copyTemporaryPassword').onclick=async()=>{await navigator.clipboard.writeText(result.temporary_password);$('copyTemporaryPassword').textContent=t('تم النسخ','Copied');};
    await loadAdmin();
  }catch(error){message(error.message);}
}
async function revokeUserSessions(userId){
  if(!confirm(t('إنهاء جميع الجلسات النشطة لهذا المستخدم؟','Revoke all active sessions for this user?')))return;
  try{const result=await lv360.api(`/api/admin/users/${userId}/revoke-sessions`,{method:'POST',body:'{}'});message(`${t('تم إنهاء الجلسات','Sessions revoked')}: ${result.revoked_sessions}`,true);await loadAdmin();}catch(error){message(error.message);}
}
async function showProjectOverview(projectId){
  try{
    const data=await lv360.api(`/api/admin/projects/${projectId}/overview`),p=data.project;
    const documents=(data.documents||[]).map(d=>`<div class="download-row"><span>${lv360.esc(d.name)}<small>${lv360.esc(d.category)} · ${lv360.number(d.size_bytes/1024/1024,2)} MB</small></span><a class="button small secondary" href="${d.download_url}">${t('تنزيل','Download')}</a></div>`).join('')||`<div class="empty">${t('لا توجد مستندات.','No documents.')}</div>`;
    const runs=(data.runs||[]).map(r=>`<div class="download-row"><span>${lv360.esc(r.status)}<small>${dateTime(r.completed_at||r.created_at)}</small></span><div class="admin-actions">${r.pdf_url?`<a class="button small secondary" href="${r.pdf_url}">PDF</a><a class="button small secondary" href="${r.excel_url}">Excel</a>`:''}</div></div>`).join('')||`<div class="empty">${t('لا توجد تشغيلات مالية.','No financial runs.')}</div>`;
    openAdminModal(p.name,`<p>${lv360.esc(p.reference)} · ${lv360.esc(p.organization_name)} · ${lv360.esc(p.owner_name)} (${lv360.esc(p.owner_email)})</p><div class="actions"><a class="button" href="${p.project_url}">${t('فتح بيانات المشروع','Open Project Data')}</a><a class="button secondary" href="${p.financial_url}">${t('فتح التحليل المالي','Open Financial Analysis')}</a></div><hr><h3>${t('المستندات','Documents')}</h3>${documents}<hr><h3>${t('التشغيلات المالية','Financial Runs')}</h3>${runs}`);
  }catch(error){message(error.message);}
}
async function loadProjectsSearch(){try{const q=$('adminProjectSearch').value.trim();renderProjects(await lv360.api(`/api/admin/projects?q=${encodeURIComponent(q)}`));}catch(error){message(error.message);}}
async function loadAuditFilter(){try{const action=$('auditActionFilter').value.trim();renderAudit(await lv360.api(`/api/admin/audit?limit=100&action=${encodeURIComponent(action)}`));}catch(error){message(error.message);}}

async function loadAdmin() {
  const [summary, users, organizations, roles, memberships, projects, audit, settings, filePolicies, emailTemplates, privacyRequests, financialPolicy] = await Promise.all([
    lv360.api('/api/admin/summary'), lv360.api('/api/admin/users'), lv360.api('/api/admin/organizations'),
    lv360.api('/api/admin/roles'), lv360.api('/api/admin/memberships'), lv360.api('/api/admin/projects'),
    lv360.api('/api/admin/audit?limit=80'), lv360.api('/api/admin/settings'), lv360.api('/api/admin/file-policies'),
    lv360.api('/api/admin/email-templates'), lv360.api('/api/admin/privacy-requests'), lv360.api('/api/admin/financial-policy'),
  ]);
  cache={users,organizations,roles,projects,audit};populateSelectors();
  $('adminSummary').innerHTML=Object.entries({[t('المستخدمون','Users')]:summary.users,[t('المؤسسات','Organizations')]:summary.organizations,[t('المشروعات','Projects')]:summary.projects,[t('طلبات نشطة','Active Submissions')]:summary.submitted}).map(([key,value])=>`<div class="metric"><span>${lv360.esc(key)}</span><strong>${value}</strong></div>`).join('');
  renderAdminUsers(users);renderOrganizations(organizations);
  $('adminMemberships').innerHTML=memberships.map((m)=>`<tr><td>${lv360.esc(m.organization_name)}</td><td>${lv360.esc(m.user_name)}<small>${lv360.esc(m.email)}</small></td><td>${lv360.esc(m.roles.join(', '))}</td><td>${lv360.esc(m.status)}</td><td><button class="button small toggle-membership" data-id="${m.id}" data-status="${m.status}">${m.status==='ACTIVE'?t('تعليق','Suspend'):t('تفعيل','Activate')}</button></td></tr>`).join('');
  renderProjects(projects);renderAudit(audit);renderSettings(settings);renderFilePolicies(filePolicies);renderEmailTemplates(emailTemplates);renderPrivacy(privacyRequests);renderFinancialPolicy(financialPolicy);
  document.querySelectorAll('.toggle-membership').forEach((button)=>button.onclick=async()=>{try{const status=button.dataset.status==='ACTIVE'?'SUSPENDED':'ACTIVE';await lv360.api(`/api/admin/memberships/${button.dataset.id}`,{method:'PATCH',body:JSON.stringify({status})});await loadAdmin();}catch(error){message(error.message);}});
  lv360.initHelp(document);
}

document.addEventListener('DOMContentLoaded', async () => {
  await lv360.me();
  if (!$('adminApp')) return;
  try { await loadAdmin(); } catch (error) { message(error.message); }
  $('reloadAdmin').onclick = loadAdmin;
  $('searchAdminProjects').onclick = loadProjectsSearch;
  $('adminProjectSearch').addEventListener('keydown',(event)=>{if(event.key==='Enter'){event.preventDefault();loadProjectsSearch();}});
  $('filterAudit').onclick = loadAuditFilter;
  $('closeAdminModal').onclick = closeAdminModal;
  $('adminModal').addEventListener('click',(event)=>{if(event.target===$('adminModal'))closeAdminModal();});
  $('staffForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    try {
      await lv360.api('/api/admin/users', { method: 'POST', body: JSON.stringify(payload) });
      form.reset();
      lv360.refreshSelectOptions(form);
      message('تم إنشاء عضو الفريق.', true);
      await loadAdmin();
    } catch (error) { message(error.message); }
  });
  $('organizationForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    try {
      await lv360.api('/api/admin/organizations', { method: 'POST', body: JSON.stringify(payload) });
      form.reset();
      message('تم إنشاء المؤسسة.', true);
      await loadAdmin();
    } catch (error) { message(error.message); }
  });
  $('financialPolicyForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const form = event.currentTarget;
      const controls = readFinancialPolicyControls();
      const changeReason = form.elements.change_reason.value.trim();
      const payload = {
        controls,
        change_reason: changeReason,
        source_version_id: form.elements.policy_source_version.value || null,
        activate: form.elements.activate_new_policy.checked,
      };
      const result = await lv360.api('/api/admin/financial-policy/versions', { method: 'POST', body: JSON.stringify(payload) });
      message(lv360.t(`تم نشر السياسة المالية v${result.version_number}.`, `Financial policy v${result.version_number} published.`), true);
      await loadAdmin();
    } catch (error) { message(error.message); }
  });
  $('loadPolicyVersion').onclick = loadSelectedPolicyVersion;
  $('activatePolicyVersion').onclick = activateSelectedPolicyVersion;
  $('membershipForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const payload = Object.fromEntries(form);
    payload.is_owner = form.get('is_owner') === 'true';
    try {
      await lv360.api('/api/admin/memberships', { method: 'POST', body: JSON.stringify(payload) });
      formElement.reset();
      message('تمت إضافة العضوية.', true);
      await loadAdmin();
    } catch (error) { message(error.message); }
  });
  window.addEventListener('lv360:languagechange', () => {
    if (cache.users.length) populateSelectors();
  });
});

function parseCategoryList(value) {
  return String(value || '').split(/[\n,]+/).map((item) => item.trim().toUpperCase()).filter(Boolean);
}
function policyVersionName(row) {
  if (!row) return '—';
  const name = lv360.lang() === 'en' ? (row.display_name_en || row.display_name_ar) : (row.display_name_ar || row.display_name_en);
  return `v${row.version_number} — ${name || lv360.t('سياسة مالية', 'Financial Policy')}`;
}
function populateFinancialPolicyForm(controls, version = null) {
  const form = $('financialPolicyForm');
  cache.policyDraftControls = structuredClone(controls || {});
  cache.policyDraftVersion = version || null;

  const directFields = [
    'display_name_ar','display_name_en','description_ar','description_en',
    'minimum_project_npv','minimum_developer_npv','minimum_developer_multiple','maximum_funding_gap',
    'minimum_landowner_npv','search_tolerance','proposal_selection_method','negotiation_recommendation_method',
  ];
  directFields.forEach((name) => {
    const field = form.elements[name];
    if (field) field.value = controls?.[name] ?? '';
  });
  form.elements.user_selectable.checked = controls?.user_selectable !== false;
  form.querySelectorAll('[data-policy-rate]').forEach((field) => {
    const value = controls?.[field.name];
    field.value = value === undefined || value === null || value === '' ? '' : cleanPolicyNumber(Number(value) * 100);
  });

  const allowed = new Set(controls?.allowed_contract_methods || []);
  form.querySelectorAll('input[name="allowed_contract_methods"]').forEach((box) => { box.checked = allowed.has(box.value); });
  form.elements.net_sales_deductible_categories.value = (controls?.net_sales_deductible_categories || []).join(', ');
  form.elements.profit_share_cost_categories.value = (controls?.profit_share_cost_categories || []).join(', ');

  const finance = controls?.finance_policy || {};
  form.elements.finance_allow_financing.checked = finance.allow_financing !== false;
  form.elements.finance_defer_unfunded_costs.checked = finance.defer_unfunded_costs !== false;

  const advanced = controls?.advanced_defaults || {};
  const advancedRates = [
    'annual_interest_rate','upfront_fee_rate','commitment_fee_rate','cash_sweep_share',
    'hybrid_minimum_execution_share','future_cost_reserve_share','maximum_monthly_execution_share',
    'commercial_discount_rate','buyer_incentive_rate','refund_rate','cost_escalation_rate',
    'cost_contingency_rate','distribution_share','remaining_cost_reserve_share',
  ];
  advancedRates.forEach((name) => {
    const field = form.elements[`advanced_${name}`];
    if (field) field.value = advanced[name] === undefined || advanced[name] === null || advanced[name] === '' ? '' : Number(advanced[name]) * 100;
  });
  const advancedDirect = {
    committed_financing: '0', minimum_cash_balance: '0', sales_curve_type: 'S_CURVE', sales_curve_intensity: '1',
    construction_curve_type: 'BELL', other_cost_curve_type: 'BELL', funding_draw_order: 'EQUITY_FIRST', spend_policy: 'CASH_DRIVEN',
    maximum_extension_months: 120, maximum_monthly_execution_amount: '0', horizon_buffer_months: 12,
    solver_grid_intervals: 12, distribution_frequency_code: 'ANNUAL', first_distribution_month: 12,
    distribution_reserve_months: 12, upfront_search_land_value_multiple: '4', upfront_search_cost_multiple: '2',
  };
  Object.entries(advancedDirect).forEach(([name, fallback]) => {
    const field = form.elements[`advanced_${name}`];
    if (field) field.value = advanced[name] ?? fallback;
  });
  const advancedBooleans = {
    finance_enabled: false, capitalize_interest: true, force_terminal_repayment: true,
    defer_contractual_payments: true, prohibit_distributions_while_debt_outstanding: true,
    recover_developer_advances_before_landowner_cash: true, settle_prior_obligations_before_distribution: true,
    prohibit_before_completion: false,
  };
  Object.entries(advancedBooleans).forEach(([name, fallback]) => {
    const field = form.elements[`advanced_${name}`];
    if (field) field.checked = advanced[name] === undefined ? fallback : advanced[name] === true;
  });
  form.elements.advanced_collection_rules.value = JSON.stringify(advanced.collection_rules || [], null, 2);
  form.elements.change_reason.value = '';
  if (version?.id) form.elements.policy_source_version.value = version.id;
}
function bindPolicyVersionRowActions() {
  document.querySelectorAll('.load-policy-version').forEach((button) => {
    button.onclick = async () => {
      $('policySourceVersion').value = button.dataset.id;
      await loadSelectedPolicyVersion();
    };
  });
  document.querySelectorAll('.activate-policy-version').forEach((button) => {
    button.onclick = async () => {
      $('policySourceVersion').value = button.dataset.id;
      await activateSelectedPolicyVersion();
    };
  });
  document.querySelectorAll('.policy-status-version').forEach((button) => {
    button.onclick = async () => {
      await changePolicyVersionStatus(button.dataset.id, button.dataset.status);
    };
  });
}
function renderFinancialPolicy(data) {
  cache.financialPolicy = data;
  const current = data.current || {};
  const versions = data.versions || [];
  $('financialPolicyCurrent').textContent = `${lv360.t('الافتراضية', 'Default')}: ${policyVersionName({...current, display_name_ar: current.controls?.display_name_ar, display_name_en: current.controls?.display_name_en})} · ${(current.snapshot_hash || '').slice(0, 12)}`;
  const source = $('policySourceVersion');
  source.innerHTML = versions.map((row) => `<option value="${lv360.esc(row.id)}">${lv360.esc(policyVersionName(row))}${row.is_current ? ` — ${lv360.t('الافتراضية', 'Default')}` : ''}${row.status === 'ARCHIVED' ? ` — ${lv360.t('مؤرشفة', 'Archived')}` : ''}</option>`).join('');
  source.value = current.id || versions[0]?.id || '';
  $('financialPolicyForm').elements.activate_new_policy.checked = true;
  populateFinancialPolicyForm(current.controls || {}, current);

  $('financialPolicyVersions').innerHTML = versions.map((row) => {
    const published = row.status === 'PUBLISHED';
    const statusBadge = row.is_current
      ? `<span class="result-badge pass">${lv360.t('افتراضية','Default')}</span>`
      : published
        ? `<span class="result-badge">${lv360.t('منشورة','Published')}</span>`
        : `<span class="result-badge warn">${lv360.t('مؤرشفة','Archived')}</span>`;
    const statusAction = row.is_current
      ? ''
      : published
        ? `<button type="button" class="button small secondary policy-status-version" data-id="${lv360.esc(row.id)}" data-status="ARCHIVED">${lv360.t('أرشفة','Archive')}</button>`
        : `<button type="button" class="button small secondary policy-status-version" data-id="${lv360.esc(row.id)}" data-status="PUBLISHED">${lv360.t('إعادة نشر','Republish')}</button>`;
    const activateAction = published && !row.is_current
      ? `<button type="button" class="button small activate-policy-version" data-id="${lv360.esc(row.id)}">${lv360.t('تفعيل','Activate')}</button>`
      : '';
    return `<tr>
      <td><strong>v${row.version_number}</strong><small><code>${lv360.esc((row.snapshot_hash || '').slice(0, 12))}</code></small></td>
      <td>${lv360.esc(lv360.lang() === 'en' ? (row.display_name_en || row.display_name_ar || '—') : (row.display_name_ar || row.display_name_en || '—'))}</td>
      <td>${row.effective_from ? new Date(row.effective_from).toLocaleString(locale()) : '—'}<small>${lv360.esc(row.change_reason || '—')}</small></td>
      <td>${row.user_selectable ? `<span class="result-badge pass">${lv360.t('متاحة','Selectable')}</span>` : `<span class="result-badge warn">${lv360.t('داخلية','Internal')}</span>`}</td>
      <td>${statusBadge}</td>
      <td><div class="admin-actions"><button type="button" class="button small secondary load-policy-version" data-id="${lv360.esc(row.id)}">${lv360.t('تحميل','Load')}</button>${activateAction}${statusAction}</div></td>
    </tr>`;
  }).join('');
  $('financialEngineVersions').innerHTML = (data.engines || []).map((row) => `<tr><td><strong>${lv360.esc(row.engine_version)}</strong></td><td>${lv360.esc(row.adapter_version)}</td><td>${row.active ? '<span class="result-badge pass">ACTIVE</span>' : 'INACTIVE'}</td><td><code>${lv360.esc((row.source_hash || '').slice(0, 16))}</code></td></tr>`).join('');
  bindPolicyVersionRowActions();
}
async function loadSelectedPolicyVersion() {
  const id = $('policySourceVersion').value;
  if (!id) return;
  try {
    const version = await lv360.api(`/api/admin/financial-policy/versions/${encodeURIComponent(id)}`);
    populateFinancialPolicyForm(version.controls || {}, version);
    message(lv360.t(`تم تحميل السياسة v${version.version_number} كأساس لنسخة جديدة.`, `Policy v${version.version_number} loaded as the source for a new version.`), true);
  } catch (error) { message(error.message); }
}
async function changePolicyVersionStatus(id, status) {
  const action = status === 'ARCHIVED' ? lv360.t('أرشفة', 'archive') : lv360.t('إعادة نشر', 'republish');
  if (!confirm(lv360.t(`تأكيد ${action} نسخة السياسة؟ التشغيلات والنتائج القديمة ستبقى مرتبطة بها دون تغيير.`, `Confirm ${action} of this policy version? Historical runs and results will remain linked to it unchanged.`))) return;
  try {
    const result = await lv360.api(`/api/admin/financial-policy/versions/${encodeURIComponent(id)}/status`, {
      method: 'PATCH', body: JSON.stringify({ status }),
    });
    message(lv360.t(`تم تحديث حالة السياسة v${result.version_number}.`, `Policy v${result.version_number} status updated.`), true);
    await loadAdmin();
  } catch (error) { message(error.message); }
}

async function activateSelectedPolicyVersion() {
  const id = $('policySourceVersion').value;
  if (!id) return;
  if (!confirm(lv360.t('تعيين هذه النسخة كسياسة افتراضية للتشغيلات الجديدة؟ لن تتغير النتائج القديمة.', 'Set this version as the default for new calculation runs? Historical results will not change.'))) return;
  try {
    const result = await lv360.api(`/api/admin/financial-policy/versions/${encodeURIComponent(id)}/activate`, { method: 'POST', body: '{}' });
    message(lv360.t(`تم تفعيل السياسة v${result.version_number}.`, `Policy v${result.version_number} activated.`), true);
    await loadAdmin();
  } catch (error) { message(error.message); }
}
function readFinancialPolicyControls() {
  const form = $('financialPolicyForm');
  if (!form.reportValidity()) throw new Error(lv360.t('أكمل جميع حقول السياسة المالية.', 'Complete all financial policy fields.'));
  const base = structuredClone(cache.policyDraftControls || cache.financialPolicy?.current?.controls || {});
  base.display_name_ar = form.elements.display_name_ar.value.trim();
  base.display_name_en = form.elements.display_name_en.value.trim();
  base.description_ar = form.elements.description_ar.value.trim();
  base.description_en = form.elements.description_en.value.trim();
  base.user_selectable = form.elements.user_selectable.checked;
  form.querySelectorAll('[data-policy-rate]').forEach((field) => { base[field.name] = String(Number(field.value) / 100); });
  ['minimum_project_npv','minimum_developer_npv','minimum_developer_multiple','maximum_funding_gap','minimum_landowner_npv','search_tolerance'].forEach((name) => { base[name] = String(Number(form.elements[name].value)); });
  base.proposal_selection_method = form.elements.proposal_selection_method.value;
  base.negotiation_recommendation_method = form.elements.negotiation_recommendation_method.value;
  base.allowed_contract_methods = [...form.querySelectorAll('input[name="allowed_contract_methods"]:checked')].map((box) => box.value);
  if (!base.allowed_contract_methods.length) throw new Error(lv360.t('يجب تفعيل آلية تعاقدية واحدة على الأقل.', 'Enable at least one contract mechanism.'));
  base.net_sales_deductible_categories = parseCategoryList(form.elements.net_sales_deductible_categories.value);
  base.profit_share_cost_categories = parseCategoryList(form.elements.profit_share_cost_categories.value);
  base.finance_policy = {
    ...(base.finance_policy || {}),
    allow_financing: form.elements.finance_allow_financing.checked,
    allow_negative_cash: false,
    defer_unfunded_costs: form.elements.finance_defer_unfunded_costs.checked,
    require_terminal_debt_zero: true,
    require_deferred_cost_zero: true,
    require_contractual_arrears_zero: true,
    require_monthly_cash_reconciliation: true,
  };

  let collectionRules;
  try { collectionRules = JSON.parse(form.elements.advanced_collection_rules.value); }
  catch { throw new Error(lv360.t('قواعد التحصيل الافتراضية يجب أن تكون JSON صالحة.', 'Default collection rules must be valid JSON.')); }
  if (!Array.isArray(collectionRules) || !collectionRules.length) throw new Error(lv360.t('قواعد التحصيل الافتراضية يجب أن تحتوي صفاً واحداً على الأقل.', 'Default collection rules must contain at least one row.'));
  const pct = (name) => String(Number(form.elements[`advanced_${name}`].value || 0) / 100);
  base.advanced_defaults = {
    ...(base.advanced_defaults || {}),
    finance_enabled: form.elements.advanced_finance_enabled.checked,
    committed_financing: String(Number(form.elements.advanced_committed_financing.value || 0)),
    annual_interest_rate: pct('annual_interest_rate'), upfront_fee_rate: pct('upfront_fee_rate'),
    commitment_fee_rate: pct('commitment_fee_rate'), cash_sweep_share: pct('cash_sweep_share'),
    capitalize_interest: form.elements.advanced_capitalize_interest.checked,
    force_terminal_repayment: form.elements.advanced_force_terminal_repayment.checked,
    minimum_cash_balance: String(Number(form.elements.advanced_minimum_cash_balance.value || 0)),
    funding_draw_order: form.elements.advanced_funding_draw_order.value,
    spend_policy: form.elements.advanced_spend_policy.value,
    hybrid_minimum_execution_share: pct('hybrid_minimum_execution_share'),
    future_cost_reserve_share: pct('future_cost_reserve_share'),
    defer_contractual_payments: form.elements.advanced_defer_contractual_payments.checked,
    sales_curve_type: form.elements.advanced_sales_curve_type.value,
    sales_curve_intensity: String(Number(form.elements.advanced_sales_curve_intensity.value || 1)),
    construction_curve_type: form.elements.advanced_construction_curve_type.value,
    other_cost_curve_type: form.elements.advanced_other_cost_curve_type.value,
    maximum_extension_months: Number(form.elements.advanced_maximum_extension_months.value || 0),
    maximum_monthly_execution_share: pct('maximum_monthly_execution_share'),
    maximum_monthly_execution_amount: String(Number(form.elements.advanced_maximum_monthly_execution_amount.value || 0)),
    commercial_discount_rate: pct('commercial_discount_rate'),
    buyer_incentive_rate: pct('buyer_incentive_rate'),
    refund_rate: pct('refund_rate'),
    cost_escalation_rate: pct('cost_escalation_rate'),
    cost_contingency_rate: pct('cost_contingency_rate'),
    horizon_buffer_months: Number(form.elements.advanced_horizon_buffer_months.value || 0),
    solver_grid_intervals: Number(form.elements.advanced_solver_grid_intervals.value || 12),
    distribution_frequency_code: form.elements.advanced_distribution_frequency_code.value,
    first_distribution_month: Number(form.elements.advanced_first_distribution_month.value || 1),
    distribution_share: pct('distribution_share'),
    distribution_reserve_months: Number(form.elements.advanced_distribution_reserve_months.value || 0),
    remaining_cost_reserve_share: pct('remaining_cost_reserve_share'),
    prohibit_distributions_while_debt_outstanding: form.elements.advanced_prohibit_distributions_while_debt_outstanding.checked,
    recover_developer_advances_before_landowner_cash: form.elements.advanced_recover_developer_advances_before_landowner_cash.checked,
    settle_prior_obligations_before_distribution: form.elements.advanced_settle_prior_obligations_before_distribution.checked,
    prohibit_before_completion: form.elements.advanced_prohibit_before_completion.checked,
    upfront_search_land_value_multiple: String(Number(form.elements.advanced_upfront_search_land_value_multiple.value || 0)),
    upfront_search_cost_multiple: String(Number(form.elements.advanced_upfront_search_cost_multiple.value || 0)),
    collection_rules: collectionRules,
  };
  base.default_timing = {
    sales_curve_type: base.advanced_defaults.sales_curve_type,
    cost_curve_type: base.advanced_defaults.construction_curve_type,
    funding_draw_order: base.advanced_defaults.funding_draw_order,
    spend_policy: base.advanced_defaults.spend_policy,
    defer_contractual_payments: base.advanced_defaults.defer_contractual_payments,
    collection_rules: collectionRules,
  };
  return base;
}

