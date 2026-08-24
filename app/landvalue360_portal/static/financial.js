const $f = (id) => document.getElementById(id);
const CURVES = [
  ['LINEAR', ['خطي', 'Linear']], ['FRONT_LOADED', ['مكثف في البداية', 'Front Loaded']],
  ['BACK_LOADED', ['مكثف في النهاية', 'Back Loaded']], ['BELL', ['جرسي', 'Bell']],
  ['S_CURVE', ['منحنى S', 'S-Curve']], ['ACCELERATED_S_CURVE', ['منحنى S متسارع', 'Accelerated S-Curve']],
  ['DELAYED_RAMP', ['تصاعد متأخر', 'Delayed Ramp']],
];
const METHOD_LABELS = {
  GROSS_SALES: ['نسبة من إجمالي المبيعات', 'Gross Sales Share'],
  NET_SALES: ['نسبة من صافي المبيعات', 'Net Sales Share'],
  PROFIT_SHARE: ['مشاركة الربح', 'Profit Share'],
  UPFRONT: ['دفعة أرض ثابتة', 'Upfront Payment'],
  HYBRID: ['عقد هجين', 'Hybrid'],
  MINIMUM_GUARANTEE: ['ضمان أدنى مع حصة متغيرة', 'Minimum Guarantee'],
};
const STATUS_LABELS = {
  VALID_RANGE: ['نطاق صالح', 'Valid Range'],
  POLICY_CAP_REACHED: ['تم بلوغ حد السياسة', 'Policy Cap Reached'],
  PUBLIC_VALUE_FLOOR_EXCEEDS_CEILING: ['قيمة الأرض المطلوبة تتجاوز السقف الفني', 'Required Landowner Value Exceeds Ceiling'],
  NO_FEASIBLE_RANGE: ['لا يوجد نطاق قابل للتطبيق', 'No Feasible Range'],
  NUMERICALLY_UNRESOLVED: ['غير محسوم عددياً', 'Numerically Unresolved'],
  SUPPORTED: ['مدعوم', 'Supported'],
  PASS: ['ناجح', 'Pass'], FAIL: ['فشل', 'Fail'], COMPLETED: ['مكتمل', 'Completed'],
};
const CONSTRAINT_LABELS = {
  PROJECT_PROFIT_NONNEGATIVE: ['ربح المشروع غير سالب', 'Non-negative Project Profit'],
  DEVELOPER_PROFIT_NONNEGATIVE: ['ربح المطور غير سالب', 'Non-negative Developer Profit'],
  MIN_DEVELOPER_IRR: ['الحد الأدنى لعائد حقوق ملكية المطور', 'Minimum Developer Equity IRR'],
  MIN_DEVELOPER_NPV: ['الحد الأدنى للقيمة الحالية للمطور', 'Minimum Developer NPV'],
  MIN_PROFIT_ON_COST: ['الحد الأدنى للربح على الكلفة', 'Minimum Profit on Cost'],
  MIN_DEVELOPER_MULTIPLE: ['الحد الأدنى لمضاعف حقوق الملكية', 'Minimum Developer Multiple'],
  MAX_RESIDUAL_FUNDING_GAP: ['الحد الأقصى لعجز التمويل', 'Maximum Funding Gap'],
  COMPLETE_SCOPE: ['اكتمال نطاق المشروع', 'Complete Project Scope'],
  MANDATORY_PAYMENT_SHORTFALL: ['عدم وجود نقص في الالتزامات الإلزامية', 'No Mandatory Payment Shortfall'],
  TERMINAL_DEBT: ['إقفال الدين عند نهاية المشروع', 'Terminal Debt Cleared'],
  PROFIT_SHARE_CONVERGENCE: ['تقارب تدفقات مشاركة الربح', 'Profit Share Convergence'],
  MONTHLY_CASH_RECONCILIATION: ['المصالحة النقدية الشهرية', 'Monthly Cash Reconciliation'],
  MIN_GOVERNMENT_VALUE_NPV: ['الحد الأدنى للقيمة الحالية لصاحب الأرض', 'Minimum Landowner NPV'],
  MAX_LANDOWNER_SHARE_POLICY_CAP: ['الحد الأعلى المسموح لحصة صاحب الأرض في السياسة', 'Maximum Landowner Share Policy Cap'],
};
const state = {
  projectId: null, data: null, model: null, run: null, runs: [], editable: false,
  advanced: false, currency: 'USD', selectedNegotiationMethod: null, selectedPolicyVersionId: null,
};
function t(ar,en){ return lv360.t(ar,en); }
function label(pair, fallback=''){ return pair ? t(pair[0],pair[1]) : fallback; }
function num(value){ const n=Number(value ?? 0); return Number.isFinite(n) ? n : 0; }
function pct(value,digits=2){ return value===null||value===undefined||value==='' ? '—' : `${lv360.number(num(value)*100,digits)}%`; }
function multiple(value){ return value===null||value===undefined||value==='' ? '—' : `${lv360.number(value,2)}x`; }
function money(value){ return value===null||value===undefined||value==='' ? '—' : lv360.money(num(value), state.currency || 'USD'); }
function signedClass(value){ return num(value)<0?'negative-number':''; }
function methodLabel(method){ return label(METHOD_LABELS[method], method || '—'); }
function statusLabel(status){ return label(STATUS_LABELS[status], status || '—'); }
function constraintLabel(id){ return label(CONSTRAINT_LABELS[id], t('فحص تقني','Technical Check') + (id ? ` (${id})` : '')); }
function showMessage(text,ok=false){ const node=$f('financialMessage'); if(!node)return; node.textContent=text||''; node.className=`form-message${ok?' ok-message':''}`; }
function setBusy(busy,labelText=''){ ['saveFinancial','runFinancial'].forEach(id=>{const el=$f(id);if(el)el.disabled=busy;}); if(labelText&&$f('financialRunState'))$f('financialRunState').textContent=labelText; if(!busy)updateFormLock(); }
function getByPath(object,path){ return path.split('.').reduce((value,key)=>value?.[key],object); }
function setByPath(object,path,value){ const parts=path.split('.');let target=object;parts.slice(0,-1).forEach(part=>{target[part]||={};target=target[part];});target[parts.at(-1)]=value; }
function metric(labelText,value,note='',helpKey=''){ return `<div class="metric"><span>${lv360.esc(labelText)}${helpKey?lv360.help(helpKey):''}</span><strong>${value??'—'}</strong>${note?`<small>${lv360.esc(note)}</small>`:''}</div>`; }
function fillSelects(){ document.querySelectorAll('[data-curve-select]').forEach(select=>{const current=select.value;select.innerHTML=CURVES.map(([value,pair])=>`<option value="${value}">${lv360.esc(label(pair))}</option>`).join('');if(current)select.value=current;}); }
function collectionRow(row={}){
  const tr=document.createElement('tr');
  tr.innerHTML=`<td><input data-field="label" value="${lv360.esc(row.label||'')}"></td><td><input data-field="lag_months" type="number" min="0" max="600" step="1" value="${num(row.lag_months)}"></td><td><input data-field="weight" type="number" min="0" max="100" step="0.01" value="${lv360.number(num(row.weight)*100,4)}"></td><td><button class="remove-row" type="button">${t('حذف','Remove')}</button></td>`;
  tr.querySelector('.remove-row').onclick=()=>{tr.remove();updateCollectionTotal();};
  tr.querySelectorAll('input').forEach(input=>input.addEventListener('input',updateCollectionTotal));
  return tr;
}
function renderCollections(rows){const body=$f('collectionRows');if(!body)return;body.innerHTML='';(rows||[]).forEach(row=>body.appendChild(collectionRow(row)));updateCollectionTotal();}
function updateCollectionTotal(){const body=$f('collectionRows');if(!body)return;const total=[...body.querySelectorAll('[data-field="weight"]')].reduce((sum,input)=>sum+num(input.value),0);$f('collectionTotal').textContent=`${lv360.number(total,4)}%`;$f('collectionTotal').className=Math.abs(total-100)<0.0001?'value-pass':'value-warn';}
function setFormModel(model){
  state.model=structuredClone(model);
  const form=$f('financialForm');
  [...form.elements].forEach(field=>{
    if(!field.name)return;
    const value=getByPath(model,field.name);
    if(field.type==='checkbox')field.checked=Boolean(value);
    else if(field.dataset.percent!==undefined)field.value=lv360.number(num(value)*100,6);
    else field.value=value??'';
  });
  renderCollections(model.sales?.collection_rules||[]);
  renderEquityExplainer();updateAdvancedVisibility();updateFormLock();
}
function renderEquityExplainer(){
  if(!$f('equityExplainer'))return;
  const opening=num(document.querySelector('[name="funding.opening_cash"]')?.value);
  const total=num(document.querySelector('[name="funding.total_developer_equity"]')?.value);
  const remaining=Math.max(total-opening,0);
  const invalid=total+1e-9<opening;
  $f('equityExplainer').className=`equity-explainer ${invalid?'invalid':''}`;
  $f('equityExplainer').innerHTML=invalid
    ? `<strong>${t('تنبيه: إجمالي الالتزام لا يمكن أن يكون أقل من المساهمة الافتتاحية.','Warning: Total commitment cannot be below the initial contribution.')}</strong>`
    : `${t('المساهمة الافتتاحية','Initial contribution')}: <b>${money(opening)}</b> · ${t('إجمالي الالتزام','Total commitment')}: <b>${money(total)}</b> · ${t('القدرة الإضافية المتبقية','Remaining additional capacity')}: <b>${money(remaining)}</b>`;
}
function readFormModel(){
  if(!$f('financialForm').reportValidity())throw new Error(t('أكمل الحقول المالية المطلوبة بقيم صحيحة.','Complete the required financial fields with valid values.'));
  const model=structuredClone(state.model||{});
  [...$f('financialForm').elements].forEach(field=>{
    if(!field.name)return;
    const inAdvanced=Boolean(field.closest('#advancedFinancialPanel'));
    if(inAdvanced&&!state.advanced)return;
    let value;
    if(field.type==='checkbox')value=field.checked;
    else if(field.type==='number')value=String(num(field.value));
    else value=field.value;
    if(field.dataset.percent!==undefined)value=String(num(field.value)/100);
    setByPath(model,field.name,value);
  });
  const opening=num(getByPath(model,'funding.opening_cash'));
  const total=num(getByPath(model,'funding.total_developer_equity'));
  if(total<opening)throw new Error(t('إجمالي التزام حقوق الملكية يجب أن يشمل المساهمة الافتتاحية وألا يقل عنها.','Total developer equity commitment must include and cannot be below the initial contribution.'));
  if(state.advanced&&model.advanced_overrides_enabled){
    const rows=[...$f('collectionRows').querySelectorAll('tr')].map(tr=>({label:tr.querySelector('[data-field="label"]').value.trim(),lag_months:Number(tr.querySelector('[data-field="lag_months"]').value),weight:String(num(tr.querySelector('[data-field="weight"]').value)/100)}));
    const totalWeight=rows.reduce((sum,row)=>sum+num(row.weight),0);
    if(!rows.length||totalWeight<=0)throw new Error(t('خطة التحصيل تحتاج وزناً موجباً واحداً على الأقل.','Collection schedule requires at least one positive weight.'));
    model.sales.collection_rules=rows;
  }
  model.finance ||= {};model.finance.allow_negative_cash=false;
  return model;
}
function updateAdvancedVisibility(){
  const panel=$f('advancedFinancialPanel');if(!panel)return;
  panel.hidden=!state.advanced;
  const defaultsPanel=$f('standardDefaultsPanel');if(defaultsPanel)defaultsPanel.hidden=!state.advanced;
  $f('provenanceDetails').hidden=!state.advanced;
  if(state.advanced){
    const enabled=Boolean(document.querySelector('[name="advanced_overrides_enabled"]')?.checked);
    $f('advancedFields')?.querySelectorAll('input,select,button').forEach(field=>{field.disabled=!state.editable||!enabled;});
    const toggle=document.querySelector('[name="advanced_overrides_enabled"]');if(toggle)toggle.disabled=!state.editable;
  }
}
function updateFormLock(){
  const form=$f('financialForm');
  [...form.elements].forEach(field=>{
    if(field.closest('.locked-control'))return;
    if(field.closest('#advancedFinancialPanel'))return;
    field.disabled=!state.editable;
  });
  updateAdvancedVisibility();
  $f('saveFinancial').hidden=!state.data?.permissions?.includes('financial.edit');
  $f('saveFinancial').disabled=!state.editable;
  $f('runFinancial').hidden=!state.data?.permissions?.includes('financial.run');
}
function renderVersionSelector(data){const select=$f('projectVersionSelect');select.innerHTML=(data.project_versions||[]).map(v=>`<option value="${v.id}">${t('الإصدار','Version')} ${v.version_number} · ${lv360.esc(v.status)} · ${v.immutable?t('ثابت','Frozen'):t('مسودة','Draft')}</option>`).join('');select.value=data.project_version.id;}
function renderPolicyVersionSelector(data){
  const select=$f('policyVersionSelect');if(!select)return;
  select.innerHTML=(data.policy_versions||[]).map(row=>{const name=lv360.lang()==='en'?(row.display_name_en||`Policy v${row.version_number}`):(row.display_name_ar||`السياسة v${row.version_number}`);return `<option value="${row.id}">${lv360.esc(name)} · v${row.version_number}${row.is_default?` · ${t('الافتراضية','Default')}`:''}</option>`;}).join('');
  select.value=data.policy.id;state.selectedPolicyVersionId=data.policy.id;
  const description=lv360.lang()==='en'?(data.policy.description_en||''):(data.policy.description_ar||'');
  $f('policyVersionDescription').textContent=description;
}
function renderCurrentProvenance(data){const v=data.project_version,p=data.policy,e=data.engine;const policyName=lv360.lang()==='en'?(p.display_name_en||p.display_name_ar):(p.display_name_ar||p.display_name_en);$f('currentProvenance').innerHTML=`<span>${t('المشروع','Project')} v${v.version_number}</span><span>${t('السياسة','Policy')}: ${lv360.esc(policyName||'—')} · v${p.version_number}</span><span>${t('المحرك','Engine')} ${lv360.esc(e.engine_version)}</span>`;}
function renderDefaultAssumptions(controls){
  const target=$f('defaultAssumptionsSummary');if(!target)return;
  const a=controls.advanced_defaults||{};
  const rows=[
    [t('التمويل الافتراضي','Default Financing'),a.finance_enabled?t('مفعّل','On'):t('غير مفعّل','Off')],
    [t('منحنى المبيعات','Sales Curve'),label(CURVES.find(x=>x[0]===a.sales_curve_type)?.[1],a.sales_curve_type||'—')],
    [t('منحنى كلف الإنشاء','Construction Cost Curve'),label(CURVES.find(x=>x[0]===a.construction_curve_type)?.[1],a.construction_curve_type||'—')],
    [t('ترتيب التمويل','Funding Order'),a.funding_draw_order==='EQUITY_FIRST'?t('حقوق الملكية أولاً','Equity First'):a.funding_draw_order||'—'],
    [t('خطة التحصيل','Collection Schedule'),`${(a.collection_rules||[]).length} ${t('دفعات','stages')}`],
    [t('التمديد الأقصى','Maximum Extension'),`${a.maximum_extension_months??'—'} ${t('شهر','months')}`],
  ];
  target.innerHTML=rows.map(([k,v])=>`<span>${lv360.esc(k)}<b>${lv360.esc(v)}</b></span>`).join('');
}
function renderPolicySummary(controls){
  const methods=(controls.allowed_contract_methods||[]).map(methodLabel).join(t('، ', ', '));
  $f('policySummary').innerHTML=`<div class="policy-chip-grid">
    <span>${t('الحد الأدنى IRR للمطور','Minimum Developer IRR')}<b>${pct(controls.minimum_developer_equity_irr)}</b></span>
    <span>${t('IRR المستهدف','Target Developer IRR')}<b>${pct(controls.target_developer_irr)}</b></span>
    <span>${t('الحد الأدنى للربح على الكلفة','Minimum Profit on Cost')}<b>${pct(controls.minimum_profit_on_cost)}</b></span>
    <span>${t('الحد الأدنى MOIC','Minimum MOIC')}<b>${multiple(controls.minimum_developer_multiple)}</b></span>
    <span>${t('معدل الخصم','Discount Rate')}<b>${pct(controls.discount_rate)}</b></span>
    <span>${t('استرداد قيمة الأرض المرجعية','Reference Land Value Recovery')}<b>${multiple(controls.minimum_landowner_value_recovery)}</b></span>
  </div><small>${t('الآليات المسموحة','Allowed mechanisms')}: ${lv360.esc(methods)}</small>`;
  renderDefaultAssumptions(controls);
  const select=$f('contractMethod');const current=select.value;select.innerHTML=(controls.allowed_contract_methods||Object.keys(METHOD_LABELS)).map(value=>`<option value="${value}">${lv360.esc(methodLabel(value))}</option>`).join('');if(current&&[...select.options].some(o=>o.value===current))select.value=current;
}
function activateTab(tab){document.querySelectorAll('[data-financial-tab]').forEach(b=>b.classList.toggle('active',b.dataset.financialTab===tab));document.querySelectorAll('[data-financial-panel]').forEach(p=>p.classList.toggle('active',p.dataset.financialPanel===tab));}
function badge(textValue,kind){return `<span class="result-badge ${kind}">${lv360.esc(textValue)}</span>`;}
function renderSummary(run){
  const s=run.summary||{},truth=run.financial_truth||{},audit=run.financial_audit||{},rec=run.recommendation_validation||{};
  const locale=lv360.lang()==='en'?'en-US':'ar-SY';
  $f('resultSubtitle').textContent=`${t('آخر تحليل','Analysis')} ${new Date(run.completed_at||run.started_at).toLocaleString(locale)} · ${run.duration_ms||0} ms`;
  $f('resultBadges').innerHTML=badge(audit.validation_status==='VALIDATED'?t('النتائج مدققة','Validated Results'):audit.validation_status==='BLOCKED'?t('النتائج محجوبة','Results Blocked'):t('نتائج مشروطة','Conditional Results'),audit.validation_status==='VALIDATED'?'pass':audit.validation_status==='BLOCKED'?'fail':'warn')+badge(rec.status==='SUPPORTED'?t('التوصية مدعومة','Recommendation Supported'):rec.status==='BLOCKED'?t('التوصية محجوبة','Recommendation Blocked'):t('التوصية مشروطة','Recommendation Conditional'),rec.status==='SUPPORTED'?'pass':rec.status==='BLOCKED'?'fail':'warn');
  const recReason=lv360.lang()==='en'?rec.reason_en:rec.reason_ar;
  $f('auditSummary').className=`decision-status ${audit.validation_status==='VALIDATED'?'pass':audit.validation_status==='BLOCKED'?'fail':'warn'}`;
  $f('auditSummary').innerHTML=`<strong>${audit.validation_status==='VALIDATED'?t('تمت مطابقة النتائج مع دفتر التدفق الشهري وتدقيق XNPV/XIRR بصورة مستقلة.','Results reconcile to the monthly ledger and independent XNPV/XIRR validation.'):t('تحتاج النتيجة مراجعة قبل الاعتماد النهائي.','The result requires review before final reliance.')}</strong>${recReason?`<span>${lv360.esc(recReason)}</span>`:''}`;

  $f('projectKpis').innerHTML=[
    [t('إجمالي المبيعات','Gross Sales'),money(s.gross_sales),'','gross_sales'], [t('صافي المبيعات','Net Sales'),money(s.net_sales),'','net_sales'], [t('تكلفة التطوير','Development Cost'),money(s.development_cost),'','development_cost'],
    [t('ربح المشروع','Project Profit'),money(s.project_profit),'','project_profit'], [t('الربح على الكلفة','Profit on Cost'),pct(s.project_profit_on_cost),'','profit_on_cost'], [t('الربح على الإيراد','Profit on Revenue'),pct(s.project_profit_on_revenue),'','profit_on_revenue'],
    [t('IRR المشروع','Project IRR'),pct(s.project_irr),'','project_irr'], [t('NPV المشروع','Project NPV'),money(s.project_npv),'','project_npv'], [t('مدة المشروع','Project Duration'),`${s.adjusted_project_duration_months??s.project_duration_months??'—'} ${t('شهر','months')}`,'','project_duration'],
  ].map(x=>metric(...x)).join('');
  $f('developerKpis').innerHTML=[
    [t('إجمالي التزام حقوق الملكية','Total Equity Commitment'),money(s.total_developer_equity_commitment),t('يشمل المساهمة الافتتاحية','Includes initial contribution'),'total_equity_commitment'],
    [t('المساهمة الافتتاحية','Initial Equity Contribution'),money(s.initial_equity_contribution),'','opening_equity'], [t('القدرة الإضافية المتاحة','Additional Equity Capacity'),money(s.additional_equity_capacity),'','total_equity_commitment'],
    [t('حقوق الملكية المضخوخة فعلياً','Actual Equity Contributions'),money(s.developer_equity_contributions),'','equity_contributions'], [t('توزيعات حقوق الملكية','Equity Distributions'),money(s.developer_equity_distributions),'','equity_distributions'],
    [t('ربح المطور','Developer Profit'),money(s.developer_profit),'','developer_profit'], [t('IRR حقوق ملكية المطور','Developer Equity IRR'),pct(s.developer_equity_irr),'','developer_irr'], [t('NPV حقوق ملكية المطور','Developer Equity NPV'),money(s.developer_equity_npv),'','developer_npv'],
    [t('مضاعف حقوق الملكية MOIC','Equity Multiple / MOIC'),multiple(s.developer_equity_multiple),'','moic'], [t('ذروة حقوق الملكية المطلوبة','Peak Equity Requirement'),money(s.peak_equity),'','peak_equity'],
    [t('ذروة الدين','Peak Debt'),money(s.peak_debt),'','peak_debt'], [t('الفوائد ورسوم التمويل','Interest & Financing Fees'),money(num(s.interest_total)+num(s.financing_fees_total)),'','interest'],
  ].map(x=>metric(...x)).join('');
  const method=truth.method||s.method;
  $f('landownerKpis').innerHTML=[
    [t('الآلية المختارة','Selected Mechanism'),methodLabel(method),'','contract_method'], [t('الحصة أو القيمة المعتمدة','Approved Measure'),truth.approved_measure_type==='AMOUNT'?money(truth.approved_share):pct(truth.approved_share),'','current_offer'],
    [t('مقابل صاحب الأرض الاسمي','Nominal Landowner Consideration'),money(s.government_consideration),'','landowner_consideration'], [t('القيمة الحالية لصاحب الأرض','Landowner NPV'),money(s.government_consideration_npv),'','landowner_npv'],
    [t('المتحصل النقدي الفعلي','Actual Landowner Cash Receipts'),money(truth.landowner_cash_receipts??s.landowner_cash_receipts),'','landowner_consideration'],
  ].map(x=>metric(...x)).join('');
  const r=run.residual_valuation||{};
  $f('landValuationKpis').innerHTML=[
    [t('القيمة المتبقية التطويرية','Residual Land Value'),money(r.residual_land_value),'','residual_land_value'], [t('قدرة الأرض بطريقة DCF','Land Capacity DCF'),money(r.land_capacity_dcf),'','land_capacity_dcf'], [t('قيمة التطوير الإجمالية قبل الأرض','Pre-Land GDV'),money(r.gross_development_value),'','gross_sales'],
    [t('ربح المطور المستهدف على الكلفة','Target Developer Profit on Cost'),pct(r.target_developer_profit_on_cost),'','profit_on_cost'],
  ].map(x=>metric(...x)).join('');
  $f('residualExplainer').innerHTML=`<strong>${t('كيف يقرأ هذا المؤشر؟','How should this be read?')}</strong><span>${t('القيمة المتبقية هي قدرة المشروع النظرية على تحمل قيمة الأرض بعد كلف التطوير والعائد المستهدف. لا تعني تلقائياً أن هذه هي المقابل الموصى به لصاحب الأرض، ولا تستبدل المجال التفاوضي أو التقييم السوقي بالمقارنات.','Residual land value is the project’s theoretical land-paying capacity after development costs and the target developer return. It is not automatically the recommended landowner consideration and does not replace the negotiation range or a comparable-based market valuation.')}</span>`;
  renderNegotiations(run.negotiation_results||[]);
  renderConstraints(run.constraints||truth.constraints||[]);
  renderAuditChecks(audit.checks||[]);
  renderAnnualCashflow(run.annual_cashflow||[]);renderMonthlyCashflow(run.monthly_cashflow||[]);renderRunProvenance(run);lv360.initHelp(document);
  const canExport=state.data?.permissions?.includes('financial.export')&&run.status==='COMPLETED';
  $f('reportActions').hidden=!canExport;
  if(canExport){$f('downloadPdf').href=`/api/projects/${state.projectId}/financial/runs/${run.id}/report.pdf?lang=${lv360.lang()}`;$f('downloadExcel').href=`/api/projects/${state.projectId}/financial/runs/${run.id}/report.xlsx?lang=${lv360.lang()}`;}
}
function currentOfferFor(method,measureType){const c=state.model?.contract||{};if(method==='UPFRONT')return num(c.upfront_amount);if(method==='MINIMUM_GUARANTEE')return num(c.minimum_guarantee_amount);return num(c.share_rate);}
function measure(value,type){return value===null||value===undefined||value===''?t('غير محدد','Not established'):(type==='AMOUNT'?money(value):pct(value,4));}
function renderNegotiations(rows){
  const select=$f('negotiationMethodSelect');
  const prior=state.selectedNegotiationMethod;
  select.innerHTML=rows.map(row=>`<option value="${row.method}">${lv360.esc(methodLabel(row.method))}</option>`).join('');
  const preferred=prior&&rows.some(r=>r.method===prior)?prior:(state.model?.contract?.method&&rows.some(r=>r.method===state.model.contract.method)?state.model.contract.method:rows[0]?.method);
  if(preferred){select.value=preferred;state.selectedNegotiationMethod=preferred;}
  const chosen=rows.find(r=>r.method===state.selectedNegotiationMethod)||rows[0];
  renderNegotiationChart(chosen);
  $f('negotiationRows').innerHTML=rows.length?rows.map(row=>{
    const bc=row.balanced_case||row.recommended_case||{};
    return `<tr><td><strong>${lv360.esc(methodLabel(row.method))}</strong></td><td>${lv360.esc(statusLabel(row.status))}</td><td>${measure(row.fair_floor,row.measure_type)}</td><td>${measure(row.balanced??row.recommended,row.measure_type)}</td><td>${measure(row.policy_adjusted_ceiling??row.risk_adjusted_ceiling,row.measure_type)}</td><td>${measure(row.technical_ceiling,row.measure_type)}</td><td>${money(bc.government_gross_npv??bc.government_npv)}</td><td>${pct(bc.developer_equity_irr??bc.developer_irr)}</td><td>${money(bc.developer_npv)}</td><td>${lv360.esc(constraintLabel(row.governing_constraint_id))}</td></tr>`;
  }).join(''):`<tr><td colspan="10" class="empty-cell">${t('لا توجد نتائج تفاوضية.','No negotiation results.')}</td></tr>`;
}
function nullableNumber(value){
  if(value===null||value===undefined||value==='')return null;
  const parsed=Number(value);return Number.isFinite(parsed)?parsed:null;
}
function explanationFor(row,code){return (row?.negotiation_explanations||[]).find(item=>item.code===code)||{};}
function localizedExplanation(item){return lv360.lang()==='en'?(item.detail_en||item.detail_ar||''):(item.detail_ar||item.detail_en||'');}
function localizedSummary(row){return lv360.lang()==='en'?(row?.negotiation_summary_en||row?.negotiation_summary_ar||''):(row?.negotiation_summary_ar||row?.negotiation_summary_en||'');}
function constraintMetric(value,id){
  if(value===null||value===undefined||value==='')return '—';
  const code=String(id||'').toUpperCase();
  if(code.includes('IRR')||code.includes('PROFIT_ON_COST')||code.includes('SHARE'))return pct(value,4);
  if(code.includes('MULTIPLE'))return multiple(value);
  if(code.includes('NPV')||code.includes('GAP')||code.includes('DEBT')||code.includes('PROFIT')||code.includes('PAYMENT'))return money(value);
  return lv360.number(value,6);
}
function assignNegotiationLanes(markers){
  const laneRanges=[[],[],[],[]];
  const sorted=[...markers].sort((a,b)=>a.position-b.position);
  sorted.forEach(marker=>{
    const width=Math.min(24,Math.max(10,7+Math.max(marker.label.length,marker.valueText.length)*0.52));
    const left=marker.position-width/2,right=marker.position+width/2;
    let lane=laneRanges.findIndex(ranges=>ranges.every(range=>right<range.left-1.5||left>range.right+1.5));
    if(lane<0){
      lane=laneRanges.map((ranges,index)=>({index,load:ranges.length})).sort((a,b)=>a.load-b.load)[0].index;
    }
    laneRanges[lane].push({left,right});marker.lane=lane;
  });
  return markers;
}
function negotiationExplanationCard({tone,title,value,body,evidence}){
  return `<article class="neg-explanation-card ${tone}"><header><span>${lv360.esc(title)}</span><strong>${value}</strong></header><p>${lv360.esc(body||'')}</p>${evidence?`<small>${lv360.esc(evidence)}</small>`:''}</article>`;
}
function renderNegotiationExplanations(row,{type,floor,balanced,policyCeiling,technical,offer,residual}){
  const host=$f('negotiationExplanation');if(!host)return;
  if(!row){host.innerHTML='';return;}
  const floorInfo=explanationFor(row,'PUBLIC_VALUE_FLOOR');
  const balancedInfo=explanationFor(row,'BALANCED_POINT');
  const riskInfo=explanationFor(row,'RISK_ADJUSTMENT');
  const technicalInfo=explanationFor(row,'TECHNICAL_CEILING');
  const offerInfo=explanationFor(row,'OFFER_POSITION');
  const minimumCase=row.minimum_case||{};
  const balancedCase=row.balanced_case||row.recommended_case||{};
  const policyCase=row.policy_adjusted_ceiling_case||row.risk_adjusted_ceiling_case||{};
  const ceilingCase=row.ceiling_case||{};
  const offerCase=row.offer_case||{};
  const governing=technicalInfo.governing_constraint||{};
  const eligibleBase=nullableNumber(row.eligible_base_total);
  const residualValue=nullableNumber(row.residual_land_value);
  const baseLabel=lv360.lang()==='en'?(row.basis_label_en||row.basis_label||t('وعاء العقد المؤهل','Eligible contract base')):(row.basis_label_ar||row.basis_label||t('وعاء العقد المؤهل','Eligible contract base'));
  const floorBody=localizedExplanation(floorInfo)||(floor===null?(lv360.lang()==='en'?row.fair_floor_reason_en:row.fair_floor_reason_ar):t('أقل مقابل يحقق الحد الأدنى المطلوب لقيمة صاحب الأرض مع بقاء جميع شروط الجدوى ناجحة.','The lowest consideration satisfying the required landowner value while all feasibility constraints remain satisfied.'));
  const floorEvidence=floor===null?'':`${t('NPV صاحب الأرض','Landowner NPV')}: ${money(minimumCase.government_gross_npv??minimumCase.government_npv??floorInfo.actual_public_npv)} · ${t('الحد المطلوب','Required')}: ${money(floorInfo.required_public_npv)}`;
  const balancedBody=localizedExplanation(balancedInfo)||t('نقطة توصية داخل المجال المتحفظ، تحددها نسخة السياسة المختارة ولا تساوي السقف الفني تلقائياً.','A recommended point inside the policy-adjusted range, determined by the selected policy version and not automatically equal to the technical ceiling.');
  const balancedEvidence=`${t('IRR المطور','Developer IRR')}: ${pct(balancedCase.developer_equity_irr??balancedCase.developer_irr)} · ${t('NPV صاحب الأرض','Landowner NPV')}: ${money(balancedCase.government_gross_npv??balancedCase.government_npv)} · ${t('فجوة التمويل','Funding Gap')}: ${money(balancedCase.peak_funding_gap)}`;
  let residualBody=t('تحويل القيمة المتبقية التطويرية إلى قيمة مكافئة على وعاء الآلية المختارة لسهولة المقارنة. وهي مؤشر قدرة تطويرية وليست تقييماً سوقياً مستقلاً.','Converts development residual land value to an equivalent measure on the selected mechanism basis for comparison. It is a development-capacity indicator, not an independent market valuation.');
  let residualEvidence='';
  if(residualValue!==null&&eligibleBase&&type==='RATE')residualEvidence=`${money(residualValue)} ÷ ${baseLabel} ${money(eligibleBase)} = ${measure(residual,type)}`;
  else if(residualValue!==null)residualEvidence=`${t('القيمة المتبقية التطويرية','Residual Land Value')}: ${money(residualValue)}`;
  const policyBody=localizedExplanation(riskInfo)||t('حد تفاوضي متحفظ داخل القدرة الفنية، تحدده معاملات التحفظ وهامش أمان المطور في نسخة السياسة.','A conservative negotiating limit within technical capacity, set by policy conservatism and developer safety factors.');
  const policyEvidence=`${t('IRR المطور','Developer IRR')}: ${pct(policyCase.developer_equity_irr??policyCase.developer_irr)} · ${t('NPV صاحب الأرض','Landowner NPV')}: ${money(policyCase.government_gross_npv??policyCase.government_npv)}${riskInfo.capacity_factor!==undefined?` · ${t('معامل القدرة المطبق','Applied capacity factor')}: ${pct(riskInfo.capacity_factor)}`:''}`;
  const technicalBody=localizedExplanation(technicalInfo)||t('أعلى مقابل يظل عنده المشروع مستوفياً لجميع قيود الجدوى قبل أول نقطة فشل. وهو حد قدرة وليس توصية تفاوضية.','The highest consideration at which all feasibility constraints remain satisfied before the first failing point. It is a capacity limit, not a negotiation recommendation.');
  const technicalEvidence=governing.id?`${constraintLabel(governing.id)}: ${constraintMetric(governing.actual,governing.id)} ${governing.operator||''} ${constraintMetric(governing.threshold,governing.id)}`:`${t('القيد الحاكم','Governing constraint')}: ${constraintLabel(row.governing_constraint_id)} · ${t('IRR المطور','Developer IRR')}: ${pct(ceilingCase.developer_equity_irr??ceilingCase.developer_irr)}`;
  const offerBody=localizedExplanation(offerInfo)||t('القيمة المدخلة حالياً في العقد، ويقارنها النظام بالحدود المالية والتفاوضية على الوعاء نفسه.','The currently entered contract value, compared against financial and negotiation boundaries on the same calculation basis.');
  const offerEvidence=`${t('المقابل الاسمي','Nominal consideration')}: ${money(offerCase.government_value)} · ${t('NPV صاحب الأرض','Landowner NPV')}: ${money(offerCase.government_gross_npv??offerCase.government_npv)} · ${t('IRR المطور','Developer IRR')}: ${pct(offerCase.developer_equity_irr??offerCase.developer_irr)}`;
  const summary=localizedSummary(row);
  const offerDetail=localizedExplanation(offerInfo);
  host.innerHTML=`<div class="neg-explanation-head"><div><h3>${t('كيف تم تحديد هذا النطاق؟','How Was This Range Determined?')}</h3><p>${lv360.esc(summary||t('تُحسب كل نقطة بإعادة تشغيل النموذج الشهري والتحقق من اقتصاديات المشروع والمطور وصاحب الأرض وقيود السيولة والإقفال.','Every point is calculated by rerunning the monthly model and validating project, developer and landowner economics plus liquidity and closing constraints.'))}</p></div></div>${offerDetail?`<div class="negotiation-reading"><strong>${t('قراءة النتيجة','Decision Reading')}</strong><span>${lv360.esc(offerDetail)}</span></div>`:''}<div class="neg-explanation-grid">${[
    negotiationExplanationCard({tone:'minimum',title:t('الحد الأدنى المقبول','Minimum Acceptable'),value:floor===null?t('غير مثبت','Not established'):measure(floor,type),body:floorBody,evidence:floorEvidence}),
    negotiationExplanationCard({tone:'balanced',title:t('النقطة المتوازنة','Balanced Point'),value:balanced===null?'—':measure(balanced,type),body:balancedBody,evidence:balancedEvidence}),
    negotiationExplanationCard({tone:'residual',title:t('القيمة المتبقية المكافئة','Residual Equivalent'),value:residual===null?'—':measure(residual,type),body:residualBody,evidence:residualEvidence}),
    negotiationExplanationCard({tone:'policy',title:t('السقف المتحفظ وفق السياسة','Policy-Adjusted Ceiling'),value:policyCeiling===null?'—':measure(policyCeiling,type),body:policyBody,evidence:policyEvidence}),
    negotiationExplanationCard({tone:'technical',title:t('السقف الفني','Technical Ceiling'),value:technical===null?'—':measure(technical,type),body:technicalBody,evidence:technicalEvidence}),
    negotiationExplanationCard({tone:'offer',title:t('العرض الحالي','Current Offer'),value:measure(offer,type),body:offerBody,evidence:offerEvidence}),
  ].join('')}</div><details class="neg-calculation-details"><summary>${t('عرض تفاصيل الحساب والافتراضات الحاكمة','Show Calculation Details and Governing Assumptions')}</summary><dl><dt>${t('وعاء الحساب','Calculation Base')}</dt><dd>${lv360.esc(baseLabel)}${eligibleBase!==null?` · ${money(eligibleBase)}`:''}</dd><dt>${t('نسخة السياسة','Policy Version')}</dt><dd>${lv360.esc(state.data?.policy?.display_name_ar||state.data?.policy?.display_name_en||'—')} · v${lv360.esc(state.data?.policy?.version_number||'—')}</dd><dt>${t('القيد الحاكم للسقف','Ceiling Governing Constraint')}</dt><dd>${lv360.esc(constraintLabel(row.governing_constraint_id))}</dd></dl></details>`;
}
function renderNegotiationChart(row){
  const chart=$f('negotiationChart'),details=$f('negotiationDetails');
  if(!row){chart.innerHTML=`<div class="empty">${t('شغّل التحليل المالي لإظهار المجال التفاوضي.','Run the financial analysis to display the negotiation range.')}</div>`;details.innerHTML='';renderNegotiationExplanations(null,{});return;}
  const type=row.measure_type||'RATE';
  const technical=nullableNumber(row.technical_ceiling),policyCeiling=nullableNumber(row.policy_adjusted_ceiling??row.risk_adjusted_ceiling),balanced=nullableNumber(row.balanced??row.recommended),floor=nullableNumber(row.fair_floor),offer=currentOfferFor(row.method,type),residual=nullableNumber(row.residual_equivalent_measure);
  const visible=[technical,policyCeiling,balanced,offer,floor,residual].filter(v=>v!==null&&Number.isFinite(v));
  const axisStart=floor!==null?floor:Math.min(...visible,0);
  const rawMax=Math.max(...visible,axisStart+(type==='RATE'?0.01:1));
  const baseSpan=Math.max(rawMax-axisStart,type==='RATE'?0.01:Math.max(1,Math.abs(rawMax)*0.05));
  const axisEnd=rawMax+baseSpan*0.055;
  const pos=value=>Math.max(0,Math.min(100,((num(value)-axisStart)/(axisEnd-axisStart))*100));
  const capReached=row.ceiling_kind==='POLICY_CAP_REACHED';
  const markers=[];
  const add=(kind,value,labelText,valueText)=>{if(value!==null)markers.push({kind,value,label:labelText,valueText,position:pos(value)});};
  add('floor',floor,t('الحد الأدنى المقبول','Minimum Acceptable'),floor===null?'':measure(floor,type));
  add('balanced',balanced,t('النقطة المتوازنة','Balanced'),balanced===null?'':measure(balanced,type));
  add('policy-adjusted',policyCeiling,t('السقف المتحفظ','Policy Ceiling'),policyCeiling===null?'':measure(policyCeiling,type));
  add('residual',residual,t('القيمة المتبقية','Residual Equivalent'),residual===null?'':measure(residual,type));
  add(`ceiling${capReached?' policy-cap':''}`,technical,capReached?t('حد البحث','Search Cap'):t('السقف الفني','Technical Ceiling'),technical===null?'':`${capReached?'≥ ':''}${measure(technical,type)}`);
  add('offer',offer,t('العرض الحالي','Current Offer'),measure(offer,type));
  assignNegotiationLanes(markers);
  const markerHtml=markers.map(marker=>`<span class="neg-marker ${marker.kind} lane-${marker.lane}${marker.value<axisStart?' outside-left':''}" style="left:${marker.position}%"><i></i><span class="neg-marker-card"><b>${lv360.esc(marker.label)}</b><em>${marker.valueText}</em></span></span>`).join('');
  const floorPos=floor===null?0:pos(floor),policyPos=policyCeiling===null?floorPos:pos(policyCeiling),technicalPos=technical===null?policyPos:pos(technical),offerPos=pos(offer);
  const recommendedWidth=Math.max(0,policyPos-floorPos);
  const capacityWidth=Math.max(0,technicalPos-policyPos);
  const breachWidth=offer>technical&&technical!==null?Math.max(0,offerPos-technicalPos):0;
  chart.innerHTML=`<div class="neg-chart-title"><strong>${lv360.esc(methodLabel(row.method))}</strong><span>${lv360.esc(statusLabel(row.status))}</span></div><div class="neg-scale"><div class="neg-track"></div><div class="neg-zone recommended" style="left:${floorPos}%;width:${recommendedWidth}%"></div><div class="neg-zone capacity" style="left:${policyPos}%;width:${capacityWidth}%"></div>${breachWidth?`<div class="neg-zone breach" style="left:${technicalPos}%;width:${breachWidth}%"></div>`:''}${markerHtml}<span class="neg-axis-label start">${t('بداية المحور: الحد الأدنى المقبول','Axis starts at minimum acceptable')} · ${floor===null?'—':measure(floor,type)}</span><span class="neg-axis-label end">${t('نهاية العرض','Displayed maximum')} · ${measure(rawMax,type)}</span></div><div class="neg-zone-legend"><span class="recommended">${t('المجال الموصى به','Recommended Range')}</span><span class="capacity">${t('قدرة فنية خارج السقف المتحفظ','Technical Capacity Beyond Policy Ceiling')}</span>${breachWidth?`<span class="breach">${t('تجاوز السقف الفني','Above Technical Ceiling')}</span>`:''}</div>${capReached?`<div class="neg-warning">${t('تم بلوغ الحد الأعلى الذي تسمح به السياسة للبحث؛ السقف الفني الحقيقي لم يُثبت وقد يكون أعلى.','The policy search cap was reached; the true technical ceiling was not established and may be higher.')}</div>`:''}${floor===null?`<div class="neg-warning">${lv360.esc(lv360.lang()==='en'?row.fair_floor_reason_en:row.fair_floor_reason_ar)}</div>`:''}`;
  const bc=row.balanced_case||row.recommended_case||{};
  const offerCode=row.offer_position;
  const offerStatus={BELOW_MINIMUM:t('أقل من الحد الأدنى المقبول','Below Minimum Acceptable'),WITHIN_RECOMMENDED_RANGE:t('ضمن المجال الموصى به','Within Recommended Range'),ABOVE_RISK_ADJUSTED_CEILING:t('أعلى من السقف المتحفظ ودون السقف الفني','Above Policy Ceiling, Below Technical Ceiling'),ABOVE_TECHNICAL_CEILING:t('فوق السقف الفني','Above Technical Ceiling'),NO_FEASIBLE_RANGE:t('لا يوجد نطاق صالح','No Feasible Range')}[offerCode]||t('يتطلب مراجعة','Requires Review');
  details.innerHTML=[
    [t('حالة العرض الحالي','Current Offer Position'),offerStatus], [capReached?t('حد البحث الحاكم','Governing Search Limit'):t('القيد الحاكم للسقف الفني','Technical Ceiling Constraint'),capReached?t('الحد الأعلى للبحث في السياسة','Policy Search Maximum'):constraintLabel(row.governing_constraint_id)],
    [t('الحد الأدنى المقبول','Minimum Acceptable'),floor===null?t('غير مثبت','Not established'):measure(floor,type)], [t('النقطة المتوازنة','Balanced Point'),balanced===null?'—':measure(balanced,type)],
    [t('السقف المتحفظ وفق السياسة','Policy-Adjusted Ceiling'),policyCeiling===null?'—':measure(policyCeiling,type)], [t('السقف الفني','Technical Ceiling'),technical===null?'—':measure(technical,type)],
    [t('القيمة المتبقية للأرض','Residual Land Value'),money(row.residual_land_value)], [t('مكافئ القيمة المتبقية على نفس الوعاء','Residual Equivalent on Same Basis'),residual===null?'—':measure(residual,type)],
    [t('NPV صاحب الأرض عند النقطة المتوازنة','Landowner NPV at Balanced'),money(bc.government_gross_npv??bc.government_npv)], [t('IRR المطور عند النقطة المتوازنة','Developer IRR at Balanced'),pct(bc.developer_equity_irr??bc.developer_irr)],
  ].map(([k,v])=>`<div><span>${lv360.esc(k)}</span><strong>${v}</strong></div>`).join('');
  renderNegotiationExplanations(row,{type,floor,balanced,policyCeiling,technical,offer,residual});
}
function renderConstraints(rows){$f('constraintsList').innerHTML=rows.length?rows.map(row=>{const passed=Boolean(row.passed);return `<div class="constraint-row ${passed?'pass':'fail'}"><div><strong>${lv360.esc(constraintLabel(row.constraint_id))}</strong><small>${t('القيمة','Actual')}: ${lv360.esc(row.actual??'—')} · ${t('الحد','Limit')}: ${lv360.esc(row.threshold??'—')}</small></div><b>${passed?t('ناجح','PASS'):t('فشل','FAIL')}</b></div>`;}).join(''):`<div class="empty">${t('لا توجد فحوص تفصيلية.','No detailed checks.')}</div>`;}
function renderAuditChecks(rows){$f('auditChecks').innerHTML=rows.length?rows.map(row=>`<div class="constraint-row ${row.passed?'pass':'fail'}"><div><strong>${lv360.esc(row.check_id)}</strong><small>${t('المعاد احتسابه','Recalculated')}: ${lv360.esc(row.expected??'—')} · ${t('المحرك','Engine')}: ${lv360.esc(row.actual??'—')}</small></div><b>${row.passed?t('مطابق','MATCH'):t('غير مطابق','MISMATCH')}</b></div>`).join(''):`<div class="empty">${t('لا يوجد تدقيق مستقل لهذا التشغيل.','No independent audit is available for this run.')}</div>`;}
function cash(row,field){return `<td class="${signedClass(row[field])}">${money(row[field])}</td>`;}
function renderAnnualCashflow(rows){$f('annualCashflowRows').innerHTML=rows.length?rows.map(row=>`<tr><td><strong>${row.year}</strong></td>${cash(row,'gross_contracted_sales')}${cash(row,'gross_collections')}${cash(row,'planned_cost')}${cash(row,'actual_cost')}${cash(row,'deferred_cost')}${cash(row,'equity_contribution')}${cash(row,'financing_draw')}<td>${money(num(row.interest_paid)+num(row.financing_fees))}</td>${cash(row,'financing_repayment')}<td>${money(row.landowner_cash_receipt??row.government_payment)}</td>${cash(row,'developer_distribution')}${cash(row,'ending_cash')}${cash(row,'ending_debt')}${cash(row,'unsupported_funding_gap')}${cash(row,'contractual_arrears')}</tr>`).join(''):`<tr><td colspan="16" class="empty-cell">${t('لا يوجد تدفق سنوي.','No annual cash flow.')}</td></tr>`;}
function renderMonthlyCashflow(rows){$f('monthlyCashflowRows').innerHTML=rows.length?rows.map(row=>`<tr><td>${row.month}</td><td>${lv360.esc(row.date||'')}</td>${cash(row,'opening_cash')}${cash(row,'gross_contracted_sales')}${cash(row,'gross_collections')}${cash(row,'net_collections')}${cash(row,'planned_cost')}${cash(row,'actual_cost')}${cash(row,'deferred_cost')}${cash(row,'equity_contribution')}${cash(row,'financing_draw')}${cash(row,'interest_paid')}${cash(row,'financing_fees')}${cash(row,'financing_repayment')}<td>${money(row.landowner_cash_receipt??row.government_payment)}</td>${cash(row,'developer_distribution')}${cash(row,'ending_cash')}${cash(row,'ending_debt')}${cash(row,'unsupported_funding_gap')}<td>${money(row.government_payment_arrears??row.contractual_arrears)}</td>${cash(row,'cash_balance_variance')}</tr>`).join(''):`<tr><td colspan="21" class="empty-cell">${t('لا يوجد تدفق شهري.','No monthly cash flow.')}</td></tr>`;}
function renderRunProvenance(run){const policyName=lv360.lang()==='en'?(run.financial_policy_display_name_en||run.financial_policy_display_name_ar):(run.financial_policy_display_name_ar||run.financial_policy_display_name_en);const policyValue=run.financial_policy_version_number?`${policyName||t('سياسة مالية','Financial Policy')} · v${run.financial_policy_version_number} · ${run.financial_policy_version_id}`:run.financial_policy_version_id;const entries=[[t('معرف التشغيل','Calculation Run ID'),run.id],[t('إصدار المشروع','Project Version'),run.project_version_number?`v${run.project_version_number} · ${run.project_version_id}`:run.project_version_id],[t('السياسة المالية','Financial Policy'),policyValue],[t('إصدار المحرك','Engine Version'),run.engine_version_label||run.engine_version_id],[t('إصدار المهايئ','Adapter Version'),run.engine_adapter_version],[t('بصمة المدخلات','Input Hash'),run.input_hash],[t('بصمة النتائج','Result Hash'),run.result_hash]];$f('runProvenance').innerHTML=entries.map(([k,v])=>`<dt>${lv360.esc(k)}</dt><dd><code>${lv360.esc(v||'—')}</code></dd>`).join('');}
function renderRunHistory(){if(!state.advanced)return;const canExport=state.data?.permissions?.includes('financial.export');$f('runHistory').innerHTML=state.runs.length?state.runs.map(run=>{const policyName=lv360.lang()==='en'?(run.financial_policy_display_name_en||run.financial_policy_display_name_ar):(run.financial_policy_display_name_ar||run.financial_policy_display_name_en);return `<article class="run-record ${state.run?.id===run.id?'selected':''}"><div><strong>${new Date(run.completed_at||run.started_at).toLocaleString(lv360.lang()==='en'?'en-US':'ar-SY')}</strong><small>${t('إصدار المشروع','Project')} v${run.project_version_number||'?'} · ${lv360.esc(policyName||t('السياسة','Policy'))} v${run.financial_policy_version_number||'?'}</small></div><div class="actions"><button type="button" class="button small secondary load-run" data-id="${run.id}">${t('عرض','View')}</button>${canExport&&run.status==='COMPLETED'?`<a class="button small secondary" href="/api/projects/${state.projectId}/financial/runs/${run.id}/report.pdf?lang=${lv360.lang()}">PDF</a>`:''}</div></article>`;}).join(''):`<div class="empty">${t('لا توجد تشغيلات سابقة.','No previous runs.')}</div>`;document.querySelectorAll('.load-run').forEach(button=>button.onclick=()=>loadRun(button.dataset.id));}
async function loadState(versionId='',policyId=''){
  const params=new URLSearchParams();if(versionId)params.set('project_version_id',versionId);if(policyId)params.set('policy_version_id',policyId);
  const query=params.toString()?`?${params.toString()}`:'';
  const data=await lv360.api(`/api/projects/${state.projectId}/financial${query}`);state.data=data;state.selectedPolicyVersionId=data.policy.id;localStorage.setItem(`lv360:policy:${state.projectId}`,data.policy.id);state.currency=data.project?.currency||'USD';state.advanced=Boolean(data.advanced_financial_access);state.editable=!data.project_version.immutable&&data.permissions.includes('financial.edit')&&data.project_version.id===data.current_project_version_id;
  renderVersionSelector(data);renderPolicyVersionSelector(data);renderCurrentProvenance(data);renderPolicySummary(data.policy.controls||{});setFormModel(data.financial_model);
  if(data.latest_run)await loadRun(data.latest_run.id,false);else clearResults();
  if(state.advanced)await loadRuns();
}
function clearResults(){state.run=null;$f('financialRunState').textContent=t('لم يتم التشغيل بعد','Not run yet');$f('resultSubtitle').textContent=t('لا توجد نتيجة بعد.','No result yet.');$f('resultBadges').innerHTML='';$f('auditSummary').innerHTML='';['projectKpis','developerKpis','landownerKpis','landValuationKpis'].forEach(id=>$f(id).innerHTML=`<div class="empty">${t('شغّل التحليل المالي لعرض النتائج.','Run the financial analysis to display results.')}</div>`);$f('residualExplainer').innerHTML='';renderNegotiations([]);renderConstraints([]);renderAuditChecks([]);renderAnnualCashflow([]);renderMonthlyCashflow([]);renderRunProvenance({});$f('reportActions').hidden=true;}
async function saveModel(){if(!state.editable)throw new Error(t('إصدار المشروع ثابت ولا يمكن تعديل افتراضاته.','This project version is frozen and cannot be edited.'));const model=readFormModel();setBusy(true,t('جارٍ حفظ الافتراضات...','Saving assumptions...'));const result=await lv360.api(`/api/projects/${state.projectId}/financial?policy_version_id=${encodeURIComponent(state.selectedPolicyVersionId||state.data.policy.id)}`,{method:'PUT',body:JSON.stringify(model)});state.model=result.financial_model;setFormModel(state.model);state.data.project_version.snapshot_hash=result.snapshot_hash;renderCurrentProvenance(state.data);showMessage(t('تم حفظ الافتراضات على إصدار المشروع الحالي.','Assumptions saved to the current project version.'),true);setBusy(false,t('الافتراضات محفوظة','Assumptions saved'));}
async function runModel(){if(state.editable)await saveModel();setBusy(true,t('جارٍ تنفيذ التحليل المالي الشهري...','Running the monthly financial analysis...'));showMessage('');const run=await lv360.api(`/api/projects/${state.projectId}/financial/runs`,{method:'POST',body:JSON.stringify({project_version_id:state.data.project_version.id,policy_version_id:state.selectedPolicyVersionId||state.data.policy.id})});state.run=run;state.currency=run.currency||state.currency;renderSummary(run);if(state.advanced)await loadRuns();activateTab('results');setBusy(false,t('اكتمل التحليل','Analysis completed'));showMessage(t('اكتمل التحليل المالي وحُفظت النتيجة بصورة غير قابلة للتغيير.','Financial analysis completed and the result was stored immutably.'),true);}
async function loadRun(id,switchTab=true){setBusy(true,t('جارٍ تحميل النتيجة...','Loading result...'));const run=await lv360.api(`/api/projects/${state.projectId}/financial/runs/${id}`);state.run=run;state.currency=run.currency||state.currency;renderSummary(run);renderRunHistory();$f('financialRunState').textContent=t('تحليل مكتمل','Analysis complete');setBusy(false);if(switchTab)activateTab('results');}
async function loadRuns(){if(!state.advanced)return;state.runs=await lv360.api(`/api/projects/${state.projectId}/financial/runs`);renderRunHistory();}
function csvEscape(value){const text=String(value??'');return /[",\n]/.test(text)?`"${text.replaceAll('"','""')}"`:text;}
function downloadCashflowCsv(){const rows=state.run?.monthly_cashflow||[];if(!rows.length){showMessage(t('لا يوجد تدفق شهري للتنزيل.','No monthly cash flow is available.'));return;}const fields=['month','date','opening_cash','gross_contracted_sales','gross_collections','net_collections','planned_cost','actual_cost','deferred_cost','equity_contribution','financing_draw','interest_paid','financing_fees','financing_repayment','landowner_cash_receipt','developer_distribution','ending_cash','ending_debt','unsupported_funding_gap','government_payment_arrears','cash_balance_variance'];const content=[fields.join(','),...rows.map(row=>fields.map(field=>csvEscape(field==='landowner_cash_receipt'?row[field]??row.government_payment:row[field])).join(','))].join('\n');const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([`\uFEFF${content}`],{type:'text/csv;charset=utf-8'}));link.download=`${state.data.project.reference}-monthly-cashflow-${state.run.id.slice(0,8)}.csv`;link.click();URL.revokeObjectURL(link.href);}
function rerenderLanguage(){lv360.applyLanguage();fillSelects();if(state.data){renderVersionSelector(state.data);renderPolicyVersionSelector(state.data);renderCurrentProvenance(state.data);renderPolicySummary(state.data.policy.controls||{});const method=state.model?.contract?.method;const select=$f('contractMethod');if(method)select.value=method;renderEquityExplainer();}if(state.run)renderSummary(state.run);renderRunHistory();}
document.addEventListener('DOMContentLoaded',async()=>{
  await lv360.me();const app=$f('financialApp');if(!app)return;state.projectId=app.dataset.projectId;fillSelects();document.querySelectorAll('[data-financial-tab]').forEach(button=>button.onclick=()=>activateTab(button.dataset.financialTab));
  $f('addCollectionRule').onclick=()=>{$f('collectionRows').appendChild(collectionRow({weight:'0'}));updateCollectionTotal();};
  document.querySelector('[name="advanced_overrides_enabled"]').addEventListener('change',updateAdvancedVisibility);
  document.querySelector('[name="funding.opening_cash"]').addEventListener('input',renderEquityExplainer);document.querySelector('[name="funding.total_developer_equity"]').addEventListener('input',renderEquityExplainer);
  $f('negotiationMethodSelect').addEventListener('change',event=>{state.selectedNegotiationMethod=event.target.value;renderNegotiationChart((state.run?.negotiation_results||[]).find(row=>row.method===event.target.value));});
  $f('saveFinancial').onclick=async()=>{try{await saveModel();}catch(error){setBusy(false,t('تعذر الحفظ','Save failed'));showMessage(error.message);}};
  $f('runFinancial').onclick=async()=>{try{await runModel();}catch(error){setBusy(false,t('فشل التحليل','Analysis failed'));showMessage(error.message);}};
  $f('projectVersionSelect').onchange=async event=>{try{await loadState(event.target.value,state.selectedPolicyVersionId);}catch(error){showMessage(error.message);}};
  $f('policyVersionSelect').onchange=async event=>{try{state.selectedPolicyVersionId=event.target.value;localStorage.setItem(`lv360:policy:${state.projectId}`,event.target.value);await loadState($f('projectVersionSelect').value,event.target.value);}catch(error){showMessage(error.message);}};
  $f('reloadRuns').onclick=async()=>{try{await loadRuns();}catch(error){showMessage(error.message);}};$f('downloadCashflowCsv').onclick=downloadCashflowCsv;
  window.addEventListener('lv360:languagechange',rerenderLanguage);
  const rememberedPolicy=localStorage.getItem(`lv360:policy:${state.projectId}`)||'';try{await loadState('',rememberedPolicy);}catch(error){localStorage.removeItem(`lv360:policy:${state.projectId}`);try{await loadState();}catch(fallbackError){showMessage(fallbackError.message);}}
});
