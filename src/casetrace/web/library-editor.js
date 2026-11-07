'use strict';

// 编辑草稿仅驻留本页；来源和修订的最终校验由整案保存接口完成。
const editor = document.getElementById('case-editor');
const editCaseButton = document.getElementById('edit-case');
let editSession = null;
let editGeneration = 0;

function editControl(parent, key, label, value, options = {}) {
  const wrap = element('div');
  wrap.className = 'field';
  const heading = element('label', label);
  const input = element(options.choices ? 'select' : options.multiline ? 'textarea' : 'input');
  input.id = `edit-${++editControl.sequence}`;
  heading.htmlFor = input.id;
  input.dataset.field = key;
  if (options.choices) {
    input.multiple = Boolean(options.multiple);
    if (input.multiple) input.size = Math.min(6, Math.max(3, options.choices.length));
    else input.add(new Option('请选择', ''));
    const selected = options.multiple ? value || [] : [value];
    for (const [id, text] of options.choices) input.add(new Option(text, id, false, selected.includes(id)));
    for (const id of selected.filter(id => id && !options.choices.some(([key]) => key === id))) {
      input.add(new Option(`${id}（当前主数据无此选项，请核对）`, id, false, true));
    }
  } else {
    if (options.multiline) input.rows = 3;
    else input.type = options.type || 'text';
    input.value = value ?? '';
    if (options.type === 'number') { input.min = 1; input.max = 2147483647; input.step = 1; }
  }
  input.required = !options.optional;
  wrap.append(heading, input);
  if (options.multiple) {
    const help = element('p', '可多选：按住 Ctrl（Mac 为 ⌘）点击；键盘可用 Shift + 方向键连续选择。');
    help.className = 'hint'; help.id = `${input.id}-help`; input.setAttribute('aria-describedby', help.id); wrap.append(help);
  }
  parent.append(wrap);
  return input;
}
editControl.sequence = 0;

function originForm(parent, recordType, recordId, isNew, source = null) {
  const box = element('details');
  box.className = 'block edit-origin';
  box.open = isNew;
  box.append(element('summary', isNew ? '新增记录来源（必填）' : '原来源自动沿用；需要换出处时展开修改'));
  const previous = source?.web_provenance?.origins?.find(item => item.record_type === recordType && item.record_id === recordId)
    || source?.records?.find(item => item.record_type === recordType && item.record_id === recordId);
  const useLabel = element('label', isNew ? '登记首次来源' : '本次明确修改这条记录的来源');
  useLabel.className = 'declaration';
  const use = element('input'); use.type = 'checkbox'; use.checked = isNew; use.disabled = isNew;
  useLabel.prepend(use); box.append(useLabel);
  const fields = element('fieldset');
  fields.disabled = !isNew;
  const kind = editControl(fields, 'kind', '来源类别', previous?.kind || (source?.records ? 'report' : 'synthetic'),
    {choices: [['report', '报告'], ['synthetic', '人工合成']]});
  const reportId = editControl(fields, 'source_document_id', '报告编号', previous?.source_document_id);
  const reportName = editControl(fields, 'source_document_name', '完整报告名称', previous?.source_document_name);
  const description = editControl(fields, 'description', '人工合成说明', previous?.description, {multiline: true});
  const location = editControl(fields, 'source_location', '本条记录具体位置', previous?.source_location);
  fields.append(element('p', '同报告可复用编号与名称，具体位置须对应本条记录。网页新增来源不填写 CSV 文件或行号。'));
  function update() {
    fields.disabled = !use.checked;
    reportId.closest('.field').hidden = reportName.closest('.field').hidden = kind.value !== 'report';
    reportId.disabled = reportName.disabled = kind.value !== 'report';
    description.closest('.field').hidden = kind.value !== 'synthetic';
    description.disabled = kind.value !== 'synthetic';
  }
  use.addEventListener('change', update); kind.addEventListener('change', update); update();
  box.append(fields); parent.append(box);
  return path => {
    if (!use.checked) return undefined;
    const result = {kind: kind.value, source_location: location.value};
    const values = kind.value === 'report' ? {source_document_id: reportId.value, source_document_name: reportName.value} : {description: description.value};
    for (const control of fields.querySelectorAll('[data-field]')) editSession.paths.set(`${path}.origin.${control.dataset.field}`, control);
    editSession.paths.set(`${path}.origin`, kind);
    return {...result, ...values};
  };
}

function editableRecord(parent, kind, item, isNew = false, source = null, caseRecordId = null, legendText = null) {
  const card = element('fieldset');
  card.className = 'edit-record block';
  const controls = {};
  const idField = kind === 'details' ? 'detail_id' : kind === 'evidences' ? 'checkpoint_id' : null;
  card.append(element('legend', legendText || (idField ? item[idField] || `新增${kind === 'details' ? '明细' : '调查项'}（保存后分配编号）` : '历史结论')));
  const text = (key, label, options = {}) => controls[key] = editControl(card, key, label, item[key], options);
  if (kind === 'case') {
    text('abnormal_description', '异常描述', {multiline: true});
    text('root_cause', '历史结案原因', {multiline: true});
    text('corrective_action', '历史纠正措施', {multiline: true});
    text('investigation_others', '其他调查说明', {multiline: true, optional: true});
    text('abnormal_processes', '已确认异常工序（可多选）', {choices: Object.entries(referenceData.processes).map(([id, name]) => [id, `${name} · ${id}`]), multiple: true});
    card.append(element('p', '工序须根据调查明确选择；产品路线和发现阶段不能代填异常工序。'));
  } else if (kind === 'details') {
    text('product_id', '产品', {choices: referenceData.products.map(p => [p.product_id, `${p.product_name} · ${p.product_id}`])});
    const background = element('p'); background.className = 'hint';
    const showProduct = () => {
      const product = referenceData.products.find(p => p.product_id === controls.product_id.value);
      background.textContent = product ? `客户：${product.customer_name} (${product.customer_id})；产品族：${product.product_family} (${product.product_family_id})；路线：${product.package_route}` : '请选择并核对合法产品；不自动清除其他选择。';
    };
    controls.product_id.addEventListener('change', showProduct); showProduct(); card.append(background);
    text('production_lot', '生产批（保留前导零）'); text('customer_lot', '客户批');
    text('production_time', '投批日期', {type: 'date'}); text('detection_time', '发现日期', {type: 'date'});
    text('detection_stage', '发现阶段', {choices: referenceData.detection_stages.map(value => [value, value])});
    text('abnormal_types', '异常类型（可多选）', {choices: referenceData.failure_modes.map(m => [m.failure_mode_id, `${m.failure_mode} · ${m.failure_mode_id}`]), multiple: true});
    text('affected_qty', '影响数量（ea）', {type: 'number'}); text('disposition', '最终处置', {multiline: true});
  } else {
    text('checkpoint_type', '调查类型', {choices: referenceData.checkpoint_types.map(value => [value, value])});
    text('custom_name', '自定义调查名称（Other 时必填）', {optional: true});
    text('result', '调查结果', {multiline: true});
    text('relevance', '相关标记', {choices: referenceData.relevance_values.map(value => [value, value])});
    card.append(element('p', '调查项属于整案，不绑定某条明细。related 不代表证据已获语义确认。'));
  }
  const recordType = kind === 'case' ? 'case' : kind === 'details' ? 'detail' : 'evidence';
  const origin = originForm(card, recordType, idField ? item[idField] : caseRecordId, isNew, source);
  if (idField) {
    const remove = element('button', '移除这条记录'); remove.type = 'button'; remove.className = 'secondary';
    remove.addEventListener('click', () => { card.remove(); }); card.append(remove);
  }
  parent.append(card);
  card.collect = path => {
    const result = {};
    if (idField && item[idField]) result[idField] = item[idField];
    for (const [key, input] of Object.entries(controls)) {
      editSession.paths.set(`${path}.${key}`, input);
      let value = input.multiple ? Array.from(input.selectedOptions, option => option.value) : input.value;
      if (input.type === 'number') value = value === '' ? null : Number(value);
      if (['custom_name', 'investigation_others'].includes(key) && value === '') value = null;
      result[key] = value;
    }
    const value = origin(path); if (value) result.origin = value;
    return result;
  };
  return card;
}

function editorChrome(legendText, messageText, onCancel) {
  const form = element('form'); form.id = 'case-edit-form';
  const fields = element('fieldset'); fields.id = 'edit-fields';
  fields.append(element('legend', legendText));
  const save = element('button', '保存整案'); save.id = 'save-case'; save.type = 'submit';
  const cancel = element('button', '取消编辑'); cancel.id = 'cancel-edit'; cancel.type = 'button'; cancel.className = 'secondary';
  cancel.addEventListener('click', () => { if (leaveEditing()) onCancel(); });
  const message = element('p', messageText); message.id = 'edit-status'; message.className = 'status'; message.setAttribute('role', 'status');
  const errors = element('ul'); errors.id = 'edit-errors'; errors.className = 'text-list';
  const actions = element('div'); actions.className = 'edit-actions';
  actions.setAttribute('role', 'region'); actions.setAttribute('aria-label', '整案保存操作');
  const buttons = element('div'); buttons.className = 'actions'; buttons.append(save, cancel);
  actions.append(buttons, message);
  form.append(actions, fields, errors);
  const nav = element('nav'); nav.className = 'edit-nav'; nav.setAttribute('aria-label', '编辑分区'); actions.append(nav);
  return {form, fields, message, errors};
}

function availabilitySection(parent) {
  const availability = section(parent, '整份资料完整可用日期');
  availability.append(element('p', `当前日期：${currentDataset.known_at}；依据：${currentDataset.basis}。普通保存自动沿用。`));
  const changeLabel = element('label', '本次同时调整整份资料日期或依据'); changeLabel.className = 'declaration';
  const changeDate = element('input'); changeDate.type = 'checkbox'; changeDate.id = 'edit-change-availability'; changeLabel.prepend(changeDate); availability.append(changeLabel);
  const dateFields = element('fieldset'); dateFields.disabled = true;
  const knownAt = editControl(dateFields, 'known_at', '完整可用日期', currentDataset.known_at, {type: 'date'});
  const basis = editControl(dateFields, 'basis', '整份资料可用性依据', currentDataset.basis, {multiline: true});
  const completeLabel = element('label', '我确认整份资料在新日期前已结案且内容完整可用'); completeLabel.className = 'declaration';
  const complete = element('input'); complete.type = 'checkbox'; complete.required = true; completeLabel.prepend(complete); dateFields.append(completeLabel);
  changeDate.addEventListener('change', () => { dateFields.disabled = !changeDate.checked; }); availability.append(dateFields);
  return {changeDate, knownAt, basis, complete};
}

function collectAvailability(part) {
  if (!part.changeDate.checked) return null;
  editSession.paths.set('availability.known_at', part.knownAt); editSession.paths.set('availability.basis', part.basis);
  editSession.paths.set('availability.confirmed_complete', part.complete);
  return {known_at: part.knownAt.value, basis: part.basis.value, confirmed_complete: part.complete.checked};
}

function collectChildren(collections) {
  const body = {};
  for (const kind of ['details', 'evidences']) body[kind] = Array.from(collections[kind].children, (card, index) => card.collect(`${kind}[${index}]`));
  return body;
}

function startEditing() {
  if (!currentCase || uploading || browsing || editing || savingCase) return;
  editing = true; editGeneration++; viewRead++;
  editor.replaceChildren(); editor.hidden = false; detailBody.hidden = true;
  editSession = {generation: editGeneration, mode: 'edit', caseId: currentCase.case.case_id,
    expected: currentDataset.snapshot_id, saving: false, blocked: false, paths: new Map()};
  const chrome = editorChrome(`编辑案例 ${editSession.caseId}`,
    '统一保存后立即供新搜索使用；原来源自动沿用，资料仍待人工审阅。',
    () => renderDetail(currentCase));
  const fields = chrome.fields;
  const basic = editableRecord(fields, 'case', currentCase.case, false, currentCase.source,
    currentCase.case.case_id);
  const closure = editControl(fields, 'closure_status', '来源结案声明（与审阅状态分开）', currentCase.source.closure_status,
    {choices: [['confirmed', 'confirmed · 确认原因'], ['NDF', 'NDF · 未确认原因']]});
  const collections = {};
  for (const [kind, label] of [['details', '批次明细'], ['evidences', 'Case 级调查证据']]) {
    const sectionNode = section(fields, label);
    const list = element('div'); sectionNode.append(list); collections[kind] = list;
    for (const item of currentCase[kind]) editableRecord(list, kind, item, false, currentCase.source);
    const add = element('button', `添加${label}`); add.type = 'button'; add.className = 'secondary'; add.dataset.add = kind;
    add.addEventListener('click', () => editableRecord(list, kind, {}, true, currentCase.source)); sectionNode.append(add);
  }
  const availability = availabilitySection(fields);
  chrome.form.addEventListener('submit', saveEditing);
  editor.append(chrome.form);
  for (const [index, target] of Array.from(fields.children).filter(child => child.matches('fieldset, section')).entries()) {
    target.id = `edit-section-${index}`;
    const link = element('a', target.querySelector('legend, h3')?.textContent || '记录'); link.href = `#${target.id}`;
    link.addEventListener('click', event => { event.preventDefault(); CaseUI.focus(target); });
    chrome.form.querySelector('.edit-nav').append(link);
  }
  editSession.collect = () => {
    editSession.paths.clear();
    const body = {expected_snapshot_id: editSession.expected, case: basic.collect('case'), closure_status: closure.value};
    editSession.paths.set('closure_status', closure);
    Object.assign(body, collectChildren(collections));
    const changed = collectAvailability(availability); if (changed) body.availability = changed;
    return body;
  };
  editSession.initial = JSON.stringify(editSession.collect());
  updateDatasetButtons(); status(`正在编辑 ${editSession.caseId}，尚未保存。`);
  CaseUI.enhance(editor);
  CaseUI.focus(editor);
}

function startCreating() {
  if (uploading || browsing || editing || savingCase || !currentDataset || !referenceData) return;
  editing = true; editGeneration++; viewRead++;
  listView.hidden = true; detailView.hidden = false;
  editor.replaceChildren(); editor.hidden = false; detailBody.hidden = true;
  editSession = {generation: editGeneration, mode: 'create', caseId: null,
    expected: currentDataset.snapshot_id, saving: false, blocked: false, paths: new Map()};
  const chrome = editorChrome('新增完整案例（编号由系统生成）',
    '保存后立即供新搜索使用；每条记录须登记首次来源，资料仍待人工审阅。',
    () => { viewRead++; if (listData) renderList(listData); else readCases(); });
  const fields = chrome.fields;
  const basic = editableRecord(fields, 'case', {abnormal_description: '', root_cause: '',
    corrective_action: '', investigation_others: null, abnormal_processes: []},
    true, null, null, '新案例结论');
  const closure = editControl(fields, 'closure_status', '来源结案声明（与审阅状态分开）', '',
    {choices: [['confirmed', 'confirmed · 确认原因'], ['NDF', 'NDF · 未确认原因']]});
  const collections = {};
  for (const [kind, label] of [['details', '批次明细'], ['evidences', 'Case 级调查证据']]) {
    const sectionNode = section(fields, label);
    const list = element('div'); sectionNode.append(list); collections[kind] = list;
    editableRecord(list, kind, {}, true);
    const add = element('button', `添加${label}`); add.type = 'button'; add.className = 'secondary'; add.dataset.add = kind;
    add.addEventListener('click', () => editableRecord(list, kind, {}, true)); sectionNode.append(add);
  }
  const availability = availabilitySection(fields);
  chrome.form.addEventListener('submit', saveEditing);
  editor.append(chrome.form);
  for (const [index, target] of Array.from(fields.children).filter(child => child.matches('fieldset, section')).entries()) {
    target.id = `edit-section-${index}`;
    const link = element('a', target.querySelector('legend, h3')?.textContent || '记录'); link.href = `#${target.id}`;
    link.addEventListener('click', event => { event.preventDefault(); CaseUI.focus(target); });
    chrome.form.querySelector('.edit-nav').append(link);
  }
  editSession.collect = () => {
    editSession.paths.clear();
    const body = {expected_snapshot_id: editSession.expected, case: basic.collect('case'), closure_status: closure.value};
    editSession.paths.set('closure_status', closure);
    Object.assign(body, collectChildren(collections));
    const changed = collectAvailability(availability); if (changed) body.availability = changed;
    return body;
  };
  editSession.initial = JSON.stringify(editSession.collect());
  updateDatasetButtons(); status('正在新增案例，尚未保存。');
  CaseUI.enhance(editor);
  CaseUI.focus(editor);
}

function editIsDirty() {
  return editing && JSON.stringify(editSession.collect()) !== editSession.initial;
}

function leaveEditing() {
  if (!editing) return true;
  if (editSession.saving) return false;
  if (editIsDirty() && !window.confirm('有未保存的修改。确认放弃这些输入？')) return false;
  editing = false; editSession = null; editGeneration++;
  editor.hidden = true; editor.replaceChildren(); detailBody.hidden = false;
  updateDatasetButtons(); return true;
}

function markEditConflict() {
  if (!editing) return;
  editSession.blocked = true;
  document.getElementById('save-case').disabled = true;
  document.getElementById('edit-status').textContent = '资料身份需要重新核对。输入已保留；请刷新核对后重新打开编辑，不会自动合并。';
}

function showEditErrors(data) {
  const list = document.getElementById('edit-errors'); list.replaceChildren();
  for (const input of editor.querySelectorAll('[aria-invalid]')) input.removeAttribute('aria-invalid');
  for (const error of data.errors || data.detail || []) {
    let path = error.path;
    if (!path) path = (error.loc || []).filter(part => part !== 'body').map((part, i) => typeof part === 'number' ? `[${part}]` : (i ? '.' : '') + part).join('');
    const fieldPath = path?.endsWith(error.field) ? path : `${path}.${error.field}`;
    const input = editSession.paths.get(fieldPath) || editSession.paths.get(path);
    const text = `${path || error.field || '整案'}：${error.message || error.msg}`;
    const item = element('li');
    if (input) {
      input.setAttribute('aria-invalid', 'true');
      const link = element('button', text); link.type = 'button'; link.className = 'error-link';
      link.addEventListener('click', () => {
        for (let parent = input.parentElement; parent && parent !== editor; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
        input.focus(); input.scrollIntoView({block: 'center'});
      }); item.append(link);
    } else item.textContent = text;
    list.append(item);
  }
}

async function saveEditing(event) {
  event.preventDefault();
  if (!editing || editSession.saving || editSession.blocked) return;
  const session = editSession, generation = editGeneration;
  const body = session.collect();
  const fields = document.getElementById('edit-fields'), message = document.getElementById('edit-status');
  session.saving = true; savingCase = true; fields.disabled = true;
  document.getElementById('save-case').disabled = true; document.getElementById('cancel-edit').disabled = true;
  refreshButton.disabled = true;
  const creating = session.mode === 'create';
  message.textContent = '正在校验并保存，请等待结果…';
  try {
    const response = await fetch(creating ? '/cases' : `/case?${new URLSearchParams({case_id: session.caseId})}`, {
      method: creating ? 'POST' : 'PUT',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (generation !== editGeneration || editSession !== session) return;
    const accepted = creating ? data.status === 'created' : ['saved', 'unchanged'].includes(data.status);
    if (response.ok && accepted) {
      const detailCaseId = data.detail?.case?.case_id;
      if (data.dataset?.snapshot_id !== data.detail?.snapshot_id
          || (creating ? detailCaseId !== data.case_id : detailCaseId !== session.caseId)) throw new Error('unconfirmed save');
      editing = false; editSession = null; editGeneration++;
      editor.hidden = true; editor.replaceChildren(); detailBody.hidden = false;
      if (creating) listData = null;
      const readGeneration = ++datasetRead;
      viewRead++; clearViews();
      showDataset(data.dataset);
      browsing = true; updateDatasetButtons();
      try {
        const ref = await readJSON(`/dataset/reference?${new URLSearchParams({snapshot_id: data.dataset.snapshot_id})}`);
        if (readGeneration !== datasetRead) return;
        requireIdentity(ref.snapshot_id, data.dataset.snapshot_id);
        referenceData = ref; showReference(ref);
        listView.hidden = true; detailView.hidden = false; renderDetail(data.detail);
        status(creating ? '新案例已创建，列表与新搜索将使用当前资料。'
          : data.status === 'saved' ? '整案已保存，新搜索将使用当前资料。' : '内容未变化，未生成修订记录。', 'ok');
      } catch (error) { if (readGeneration === datasetRead) readError(error); }
      finally { if (readGeneration === datasetRead) { browsing = false; updateDatasetButtons(); } }
    } else {
      message.textContent = data.message || '未保存，请修正字段后重试。';
      showEditErrors(data);
      if (response.status === 409) markEditConflict();
    }
  } catch (error) {
    if (generation !== editGeneration || editSession !== session) return;
    markEditConflict();
    message.textContent = '未收到可确认的保存结果，可能已保存。输入已保留；请刷新核对当前身份和内容，不会自动重传。';
  } finally {
    savingCase = false;
    if (editSession === session) {
      session.saving = false; fields.disabled = false;
      document.getElementById('cancel-edit').disabled = false;
      document.getElementById('save-case').disabled = session.blocked;
    }
    updateDatasetButtons();
  }
}

editCaseButton.addEventListener('click', startEditing);
newCaseButton.addEventListener('click', startCreating);
window.addEventListener('beforeunload', event => {
  if (editing && (editSession.saving || editIsDirty())) { event.preventDefault(); event.returnValue = ''; }
});
document.querySelector('nav').addEventListener('click', event => {
  if (event.target.closest('a') && editing && !leaveEditing()) event.preventDefault();
});
