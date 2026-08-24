const $ = (id) => document.getElementById(id);
const numeric = (value) => Number(value || 0);
let autosaveTimer = null;
let savePromise = null;
let editRevision = 0;
let savedRevision = 0;
let immutableProject = false;

function setMessage(text, ok = false) {
  const element = $('formMessage');
  if (!element) return;
  element.textContent = text || '';
  element.style.color = ok ? '#2e7d5b' : '#a83f43';
}

function input(name) {
  return document.querySelector(`[name="${name}"]`);
}
function value(name) {
  return input(name)?.value ?? '';
}
function setValue(name, valueToSet) {
  const element = input(name);
  if (element) element.value = valueToSet ?? '';
}
function rowInput(key, valueToSet, type = 'text', attributes = '') {
  return `<input data-key="${key}" type="${type}" value="${lv360.esc(valueToSet)}" ${attributes}>`;
}

function scheduleAutosave(markDirty = true) {
  if (immutableProject || !$('projectApp')) return;
  if (markDirty) editRevision += 1;
  clearTimeout(autosaveTimer);
  const state = $('saveState');
  if (state) state.textContent = 'تعديلات غير محفوظة';
  autosaveTimer = setTimeout(async () => {
    if (!canAutosave()) {
      if (state) state.textContent = 'بانتظار اكتمال الحقول';
      return;
    }
    try {
      await saveProject(true);
    } catch {
      // Explicit save exposes the error. Autosave remains non-blocking while a row is incomplete.
    }
  }, 1800);
}

function wireRow(row) {
  row.querySelector('.remove-row')?.addEventListener('click', () => {
    row.remove();
    renderPreview();
    scheduleAutosave();
  });
  row.querySelectorAll('input,select,textarea').forEach((element) => {
    element.addEventListener('input', () => {
      renderPreview();
      scheduleAutosave();
    });
    element.addEventListener('change', () => {
      renderPreview();
      scheduleAutosave();
    });
  });
}

function addLandUse(row = { code: 'INVESTMENT', name: 'أرض استثمارية', percentage: '0' }) {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${rowInput('code', row.code, 'text', 'required maxlength="50"')}</td>
    <td>${rowInput('name', row.name, 'text', 'required maxlength="160"')}</td>
    <td>${rowInput('percentage', row.percentage, 'number', 'required min="0" max="100" step="0.01"')}</td>
    <td data-computed="area_sqm">—</td>
    <td><button type="button" class="remove-row" aria-label="حذف الاستخدام">×</button></td>`;
  $('landUseRows').appendChild(tr);
  wireRow(tr);
}

function addProduct(row = {
  code: 'RESIDENTIAL', name: 'سكني', allocation_percentage: '0',
  sellable_efficiency_percentage: '80', unit_selling_price: '', currency: 'USD',
}) {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${rowInput('code', row.code, 'text', 'required maxlength="50"')}</td>
    <td>${rowInput('name', row.name, 'text', 'required maxlength="160"')}</td>
    <td>${rowInput('allocation_percentage', row.allocation_percentage, 'number', 'required min="0" max="100" step="0.01"')}</td>
    <td data-computed="product_gfa_sqm">—</td>
    <td>${rowInput('sellable_efficiency_percentage', row.sellable_efficiency_percentage, 'number', 'required min="0.01" max="100" step="0.01"')}</td>
    <td data-computed="sellable_area_sqm">—</td>
    <td>${rowInput('unit_selling_price', row.unit_selling_price, 'number', 'required min="0.01" step="0.01" inputmode="decimal"')}</td>
    <td data-computed="gross_sales">—</td>
    <td><button type="button" class="remove-row" aria-label="حذف المنتج">×</button></td>`;
  $('productRows').appendChild(tr);
  wireRow(tr);
}

function addCost(row = {
  name: '', category: 'CONSTRUCTION', amount: '', quantity_basis: '', quantity: '', unit_cost: '',
  developer_share_percentage: '100', net_sales_deductible: false, currency: 'USD',
}) {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${rowInput('name', row.name, 'text', 'required maxlength="200"')}</td>
    <td><select data-key="category">${lv360.optionsHtml('cost_category', { selected: row.category || 'CONSTRUCTION' })}</select></td>
    <td>${rowInput('amount', row.amount ?? '', 'number', 'min="0" step="0.01"')}</td>
    <td>${rowInput('quantity', row.quantity ?? '', 'number', 'min="0" step="0.01"')}</td>
    <td>${rowInput('unit_cost', row.unit_cost ?? '', 'number', 'min="0" step="0.01"')}</td>
    <td>${rowInput('developer_share_percentage', row.developer_share_percentage, 'number', 'required min="0" max="100" step="0.01"')}</td>
    <td data-computed="landowner_share_percentage">${lv360.number(100 - numeric(row.developer_share_percentage))}%</td>
    <td class="legacy-net-sales-deduction"><input data-key="net_sales_deductible" type="checkbox" ${row.net_sales_deductible ? 'checked' : ''}></td>
    <td><button type="button" class="remove-row" aria-label="حذف بند الكلفة">×</button></td>`;
  tr.querySelector('[data-key=category]').value = row.category || 'OTHER';
  $('costRows').appendChild(tr);
  wireRow(tr);
}

function tableRows(id) {
  return [...$(id).querySelectorAll('tr')].map((tr) => Object.fromEntries(
    [...tr.querySelectorAll('[data-key]')].map((element) => [
      element.dataset.key,
      element.type === 'checkbox' ? element.checked : element.value,
    ]),
  ));
}

function buildPayload() {
  return {
    name: value('name'),
    description: value('description') || null,
    currency: value('currency') || 'USD',
    gross_land_area_sqm: value('gross_land_area_sqm') || '0',
    excluded_land_area_sqm: value('excluded_land_area_sqm') || '0',
    title_reference: value('title_reference') || null,
    location: value('location') || null,
    current_land_value: value('current_land_value') || null,
    far: value('far') || '0',
    bcr: value('bcr') === '' ? null : String(Number(value('bcr')) / 100),
    planning_status: value('planning_status') || null,
    project_duration_months: value('project_duration_months') ? Number(value('project_duration_months')) : null,
    sales_duration_months: value('sales_duration_months') ? Number(value('sales_duration_months')) : null,
    land_uses: tableRows('landUseRows'),
    products: tableRows('productRows').map((row) => ({
      ...row,
      currency: value('currency') || 'USD',
      price_source: null,
      evidence_confidence: null,
    })),
    costs: tableRows('costRows').map((row) => ({
      ...row,
      amount: row.amount || null,
      quantity: row.quantity || null,
      unit_cost: row.unit_cost || null,
      currency: value('currency') || 'USD',
      quantity_basis: null,
      notes: null,
      source: null,
      evidence_confidence: null,
    })),
  };
}

function finiteInRange(raw, minimum, maximum = Number.POSITIVE_INFINITY, allowBlank = false) {
  if (raw === '' || raw === null || raw === undefined) return allowBlank;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum;
}
function canAutosave() {
  const form = $('projectForm');
  if (!form || immutableProject) return false;
  const active = document.activeElement;
  if (active && form.contains(active) && active.matches('input[type="number"]') && active.value === '') return false;
  if (value('name').trim().length < 2) return false;
  if (!finiteInRange(value('gross_land_area_sqm') || '0', 0)) return false;
  if (!finiteInRange(value('excluded_land_area_sqm') || '0', 0)) return false;
  if (numeric(value('excluded_land_area_sqm')) > numeric(value('gross_land_area_sqm'))) return false;
  if (!finiteInRange(value('far') || '0', 0)) return false;
  if (!finiteInRange(value('bcr'), 0, 100, true)) return false;
  if (!finiteInRange(value('current_land_value'), 0, Number.POSITIVE_INFINITY, true)) return false;
  if (!finiteInRange(value('project_duration_months'), 1, 600, true)) return false;
  if (!finiteInRange(value('sales_duration_months'), 1, 600, true)) return false;
  for (const row of tableRows('landUseRows')) {
    if (!row.code?.trim() || !row.name?.trim() || !finiteInRange(row.percentage, 0, 100)) return false;
  }
  for (const row of tableRows('productRows')) {
    if (!row.code?.trim() || !row.name?.trim()) return false;
    if (!finiteInRange(row.allocation_percentage, 0, 100)) return false;
    if (!finiteInRange(row.sellable_efficiency_percentage, 0.000001, 100)) return false;
    if (!finiteInRange(row.unit_selling_price, 0.000001)) return false;
  }
  for (const row of tableRows('costRows')) {
    if (!row.name?.trim() || !finiteInRange(row.developer_share_percentage, 0, 100)) return false;
    const hasAmount = finiteInRange(row.amount, 0);
    const hasQuantityRate = finiteInRange(row.quantity, 0) && finiteInRange(row.unit_cost, 0);
    const amountBlank = row.amount === '' || row.amount === null;
    const quantityBlank = row.quantity === '' || row.quantity === null;
    const unitCostBlank = row.unit_cost === '' || row.unit_cost === null;
    if (!hasAmount && !hasQuantityRate) return false;
    if ((!amountBlank && !hasAmount) || (!quantityBlank && !finiteInRange(row.quantity, 0)) || (!unitCostBlank && !finiteInRange(row.unit_cost, 0))) return false;
  }
  return true;
}
function validateBeforeExplicitSave() {
  if (canAutosave()) return true;
  const form = $('projectForm');
  const invalid = form?.querySelector(':invalid');
  if (invalid) { invalid.reportValidity(); invalid.focus(); }
  setMessage(lv360.t('أكمل الحقول غير المكتملة أو صحح القيم قبل الحفظ. سعر المتر البيعي يجب أن يكون أكبر من صفر.', 'Complete or correct the highlighted inputs before saving. Unit selling price must be greater than zero.'));
  return false;
}

function renderPreview() {
  const gross = numeric(value('gross_land_area_sqm'));
  const excluded = numeric(value('excluded_land_area_sqm'));
  const net = gross - excluded;
  const far = numeric(value('far'));
  const gfa = net * far;
  const currency = value('currency') || 'USD';

  let landUseTotal = 0;
  [...($('landUseRows')?.children || [])].forEach((tr) => {
    const percentage = numeric(tr.querySelector('[data-key=percentage]').value);
    landUseTotal += percentage;
    tr.querySelector('[data-computed=area_sqm]').textContent = lv360.number(net * percentage / 100);
  });
  if ($('landUseTotal')) {
    $('landUseTotal').textContent = `${lv360.number(landUseTotal)}%`;
    $('landUseTotal').style.color = Math.abs(landUseTotal - 100) < 0.001 ? '#2e7d5b' : '#a83f43';
  }

  let productTotal = 0;
  let sellable = 0;
  let sales = 0;
  [...($('productRows')?.children || [])].forEach((tr) => {
    const percentage = numeric(tr.querySelector('[data-key=allocation_percentage]').value);
    const efficiency = numeric(tr.querySelector('[data-key=sellable_efficiency_percentage]').value);
    const price = numeric(tr.querySelector('[data-key=unit_selling_price]').value);
    const productGfa = gfa * percentage / 100;
    const productSellable = productGfa * efficiency / 100;
    const productSales = productSellable * price;
    productTotal += percentage;
    sellable += productSellable;
    sales += productSales;
    tr.querySelector('[data-computed=product_gfa_sqm]').textContent = lv360.number(productGfa);
    tr.querySelector('[data-computed=sellable_area_sqm]').textContent = lv360.number(productSellable);
    tr.querySelector('[data-computed=gross_sales]').textContent = lv360.money(productSales, currency);
  });
  if ($('productTotal')) {
    $('productTotal').textContent = `${lv360.number(productTotal)}%`;
    $('productTotal').style.color = Math.abs(productTotal - 100) < 0.001 ? '#2e7d5b' : '#a83f43';
  }

  let costs = 0;
  let deductible = 0;
  [...($('costRows')?.children || [])].forEach((tr) => {
    const amount = numeric(tr.querySelector('[data-key=amount]').value)
      || numeric(tr.querySelector('[data-key=quantity]').value) * numeric(tr.querySelector('[data-key=unit_cost]').value);
    const developerShare = numeric(tr.querySelector('[data-key=developer_share_percentage]').value);
    costs += amount;
    if (tr.querySelector('[data-key=net_sales_deductible]').checked) deductible += amount;
    tr.querySelector('[data-computed=landowner_share_percentage]').textContent = `${lv360.number(100 - developerShare)}%`;
  });

  if ($('calculationCards')) {
    $('calculationCards').innerHTML = [
      ['صافي الأرض', `${lv360.number(net)} م²`],
      ['إجمالي GFA', `${lv360.number(gfa)} م²`],
      ['المساحة البيعية', `${lv360.number(sellable)} م²`],
      ['المبيعات الاسمية', lv360.money(sales, currency)],
      ['إجمالي الكلف', lv360.money(costs, currency)],
      ['بنود قابلة للخصم', lv360.money(deductible, currency)],
    ].map(([label, displayedValue]) => `<div class="metric"><span>${label}</span><strong>${displayedValue}</strong></div>`).join('');
  }
}

async function saveProject(silent = false) {
  if (immutableProject) return null;
  // Wait for an earlier request instead of navigating away or dropping the
  // user's latest edit. If new edits were made while it was running, save a
  // fresh snapshot immediately afterwards.
  if (savePromise) {
    await savePromise;
    if (savedRevision >= editRevision) return null;
  }
  if (!silent && !validateBeforeExplicitSave()) throw new Error(lv360.t('بيانات المشروع غير مكتملة.', 'Project data is incomplete.'));
  if (silent && !canAutosave()) return null;
  const id = $('projectApp').dataset.projectId;
  const revisionToSave = editRevision;
  const payload = buildPayload();
  savePromise = (async () => {
    $('saveState').textContent = 'جارٍ الحفظ';
    const response = await lv360.api(`/api/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    savedRevision = Math.max(savedRevision, revisionToSave);
    $('saveState').textContent = 'تم الحفظ تلقائياً';
    renderServerChecks(response.calculations);
    if (!silent) setMessage('تم حفظ المسودة.', true);
    return response;
  })();
  try {
    return await savePromise;
  } catch (error) {
    $('saveState').textContent = silent ? 'بانتظار تصحيح الحقول' : 'تعذر الحفظ';
    if (!silent) setMessage(error.message);
    throw error;
  } finally {
    savePromise = null;
    if (editRevision > savedRevision && canAutosave()) {
      clearTimeout(autosaveTimer);
      autosaveTimer = setTimeout(() => saveProject(true).catch(() => {}), 250);
    }
  }
}

function renderServerChecks(calculations) {
  if (!$('checksList')) return;
  $('checksList').innerHTML = (calculations.checks || []).map((check) => `
    <div class="check ${check.status.toLowerCase()}">
      <span>${lv360.esc(check.message_ar)}<small> الفعلي: ${lv360.esc(check.actual_value)} · المطلوب: ${lv360.esc(check.required_value)}</small></span>
      <strong>${check.status === 'PASS' ? 'متحقق' : 'غير متحقق'}</strong>
    </div>`).join('');
}

function renderDocuments(documents) {
  if (!$('documentsList')) return;
  if (!documents?.length) {
    $('documentsList').innerHTML = '<p>لم ترفع مستندات لهذه النسخة بعد.</p>';
    return;
  }
  $('documentsList').innerHTML = documents.map((documentRow) => `
    <div class="download-row">
      <span>${lv360.esc(documentRow.category)} · ${lv360.esc(documentRow.name)}</span>
      <div class="actions">
        <a class="button small secondary" href="/api/documents/${documentRow.id}/download">تنزيل</a>
        ${immutableProject ? '' : `<button type="button" class="button danger small delete-document" data-id="${documentRow.id}">حذف</button>`}
      </div>
    </div>`).join('');
  document.querySelectorAll('.delete-document').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!confirm('حذف المستند من المسودة؟')) return;
      try {
        await lv360.api(`/api/documents/${button.dataset.id}`, { method: 'DELETE' });
        await reloadDocuments();
      } catch (error) {
        setMessage(error.message);
      }
    });
  });
}

async function reloadDocuments() {
  const id = $('projectApp').dataset.projectId;
  const rows = await lv360.api(`/api/projects/${id}/documents`);
  renderDocuments(rows);
}

function fill(data) {
  const snapshot = data.snapshot || {};
  const identity = snapshot.identity || {};
  const land = snapshot.land || {};
  const planning = snapshot.planning || {};
  for (const [key, valueToSet] of Object.entries({ ...identity, ...land, ...planning })) setValue(key, key === 'bcr' && valueToSet !== null && valueToSet !== '' ? Number(valueToSet) * 100 : valueToSet);
  (snapshot.land_uses || []).forEach(addLandUse);
  (snapshot.products || []).forEach(addProduct);
  (snapshot.costs || []).forEach(addCost);
  if (!(snapshot.land_uses || []).length) {
    addLandUse({ code: 'INVESTMENT', name: 'أرض استثمارية', percentage: '60' });
    addLandUse({ code: 'ROADS', name: 'طرق وحركة', percentage: '20' });
    addLandUse({ code: 'GREEN', name: 'مساحات خضراء', percentage: '10' });
    addLandUse({ code: 'PUBLIC', name: 'مرافق عامة', percentage: '10' });
  }
  if (!(snapshot.products || []).length) addProduct({
    code: 'RESIDENTIAL', name: 'سكني', allocation_percentage: '100',
    sellable_efficiency_percentage: '80', unit_selling_price: '',
  });
  immutableProject = Boolean(data.immutable);
  editRevision = 0;
  savedRevision = 0;
  renderPreview();
  lv360.initHelp(document);
  renderServerChecks(data.calculations || {});
  renderDocuments(data.documents || []);
  if (immutableProject) {
    $('saveState').textContent = 'نسخة مرسلة غير قابلة للتعديل';
    $('saveProject').textContent = 'إنشاء إصدار جديد';
    [...document.querySelectorAll('#projectForm input,#projectForm select,#projectForm textarea')].forEach((element) => { element.disabled = true; });
  }
}

function bindEditor() {
  const data = JSON.parse($('projectJson').value);
  fill(data);
  let currentStep = 0;
  const steps = [...document.querySelectorAll('.wizard-nav button')];
  function show(index) {
    currentStep = Math.max(0, Math.min(steps.length - 1, index));
    steps.forEach((button, buttonIndex) => button.classList.toggle('active', buttonIndex === currentStep));
    document.querySelectorAll('.wizard-step').forEach((panel, panelIndex) => panel.classList.toggle('active', panelIndex === currentStep));
    $('previousStep').disabled = currentStep === 0;
    $('nextStep').disabled = false;
    $('nextStep').textContent = currentStep === steps.length - 1
      ? (lv360.t ? lv360.t('الانتقال إلى التحليل المالي', 'Open Financial Analysis') : 'الانتقال إلى التحليل المالي')
      : (lv360.t ? lv360.t('التالي', 'Next') : 'التالي');
    scrollTo({ top: 0, behavior: 'smooth' });
  }
  steps.forEach((button, index) => { button.onclick = () => show(index); });
  $('previousStep').onclick = () => show(currentStep - 1);
  $('nextStep').onclick = async () => {
    if (currentStep < steps.length - 1) { show(currentStep + 1); return; }
    try {
      if (!immutableProject) await saveProject(false);
      location.href = `/portal/projects/${data.id}/financial`;
    } catch (error) { setMessage(error.message); }
  };
  $('addLandUse').onclick = () => { addLandUse(); renderPreview(); scheduleAutosave(); };
  $('addProduct').onclick = () => { addProduct(); renderPreview(); scheduleAutosave(); };
  $('addCost').onclick = () => { addCost(); renderPreview(); scheduleAutosave(); };
  $('saveProject').onclick = async () => {
    if (immutableProject) {
      const reason = prompt('سبب إنشاء الإصدار الجديد:', 'استكمال أو تعديل بيانات');
      if (!reason) return;
      await lv360.api(`/api/projects/${data.id}/revisions`, { method: 'POST', body: JSON.stringify({ reason }) });
      location.reload();
    } else {
      await saveProject(false);
    }
  };
  $('projectForm').addEventListener('input', () => { renderPreview(); scheduleAutosave(); });
  $('projectForm').addEventListener('change', () => { renderPreview(); scheduleAutosave(); });
  $('documentForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    try {
      await lv360.api(`/api/projects/${data.id}/documents`, { method: 'POST', body: formData });
      event.currentTarget.reset();
      await reloadDocuments();
      setMessage('تم رفع الملف بصورة خاصة.', true);
    } catch (error) {
      setMessage(error.message);
    }
  });
  show(0);
}

function bindNewProject() {
  const form = $('newProjectForm');
  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    try {
      const response = await lv360.api('/api/projects', { method: 'POST', body: JSON.stringify(payload) });
      location.href = `/portal/projects/${response.id}`;
    } catch (error) {
      setMessage(error.message);
    }
  });
}

function bindInformationRequests() {
  document.querySelectorAll('.infoResponseForm').forEach((form) => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await lv360.api(`/api/information-requests/${form.dataset.requestId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message: new FormData(form).get('message') }),
      });
      location.reload();
    } catch (error) {
      alert(error.message);
    }
  }));
}

document.addEventListener('DOMContentLoaded', async () => {
  await lv360.me();
  if ($('projectApp')) bindEditor();
  bindNewProject();
  bindInformationRequests();
});

window.addEventListener('lv360:languagechange', () => {
  document.querySelectorAll('#costRows select[data-key="category"]').forEach((select) => {
    const selected = select.value;
    select.innerHTML = lv360.optionsHtml('cost_category', { selected });
  });
});
