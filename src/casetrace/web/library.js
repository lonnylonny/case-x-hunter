'use strict';

const stateLine = document.getElementById('dataset-state');
const details = document.getElementById('dataset-details');
const identities = document.getElementById('dataset-identities');
const uploadForm = document.getElementById('upload-form');
const uploadButton = document.getElementById('upload-button');
const uploadStatus = document.getElementById('upload-status');
const uploadErrors = document.getElementById('upload-errors');
const searchLink = document.getElementById('search-uploaded');
const referenceDetails = document.getElementById('reference-details');
const referenceBody = document.getElementById('reference-body');
const referenceState = document.getElementById('reference-state');
const restoreButton = document.getElementById('restore-demo');
const restoreStatus = document.getElementById('restore-status');
const refreshButton = document.getElementById('refresh-dataset');
const filterForm = document.getElementById('filter-form');
const filterFields = document.getElementById('filter-fields');
const browserStatus = document.getElementById('browser-status');
const listView = document.getElementById('list-view');
const detailView = document.getElementById('detail-view');
const caseList = document.getElementById('case-list');
const detailBody = document.getElementById('case-detail-body');
const counts = document.getElementById('case-counts');
const pagination = document.getElementById('pagination');
const previousPage = document.getElementById('previous-page');
const nextPage = document.getElementById('next-page');
const firstPage = document.getElementById('first-page');
const pageSize = document.getElementById('page-size');
const newCaseButton = document.getElementById('new-case');
const deleteCaseButton = document.getElementById('delete-case');
const deletePanel = document.getElementById('case-delete');
let currentDataset = null;
let referenceData = null;
let listData = null;
let datasetRead = 0;
let viewRead = 0;
let page = 1;
let uploading = false;
let browsing = false;
let editing = false;
let currentCase = null;
let savingCase = false;
let deleting = false;
let deletePending = false;

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function status(text, kind = 'pending') {
  browserStatus.textContent = text;
  browserStatus.dataset.kind = kind;
}

function updateDatasetButtons() {
  uploadButton.disabled = uploading || editing || savingCase || deleting || !currentDataset;
  restoreButton.disabled = uploading || editing || savingCase || deleting || !currentDataset;
  newCaseButton.disabled = uploading || editing || savingCase || browsing || deleting || !currentDataset || !referenceData;
  refreshButton.disabled = uploading || savingCase || deletePending;
  document.getElementById('back-to-list').disabled = deletePending;
  filterFields.disabled = uploading || editing || savingCase || deleting || !currentDataset || !referenceData;
  document.getElementById('edit-case').disabled = uploading || editing || savingCase || browsing || deleting || !currentCase;
  deleteCaseButton.disabled = uploading || editing || savingCase || browsing || deleting || !currentCase;
  pageSize.disabled = uploading || editing || savingCase || browsing || !referenceData;
  previousPage.disabled = uploading || browsing || !listData || page <= 1;
  nextPage.disabled = uploading || browsing || !listData || page * listData.page_size >= listData.matched_count;
}

function fillDescription(parent, entries) {
  parent.replaceChildren();
  for (const [label, value] of entries) {
    parent.append(element('dt', label), element('dd', value ?? '未登记'));
  }
}

function showDataset(data) {
  currentDataset = data;
  stateLine.textContent = `${data.name} · ${data.case_count} 个案例`;
  const origins = {builtin_demo: '内置合成演示资料', csv_upload: 'CSV 上传资料', user_maintained: '用户维护资料（原出处见详情）', other: '其他运行资料'};
  fillDescription(details, [
    ['来源类型', origins[data.source_type] || '其他运行资料'],
    ['案例 / 明细 / 调查证据', `${data.case_count} / ${data.detail_count} / ${data.evidence_count}`],
    ['更新时间', data.updated_at], ['完整可用日期', data.known_at],
    ['可用性依据（登记声明）', data.basis],
    ['审阅状态', data.review_status === 'draft_pending_human_review' ? 'draft · 待人工审阅' : data.review_status],
  ]);
  fillDescription(identities, [
    ['运行快照', data.snapshot_id], ['基线来源', data.baseline_snapshot_id || '未单独登记'],
    ['原文件摘要', data.dataset_sha256], ['当前内容摘要', data.content_digest],
  ]);
  updateDatasetButtons();
}

function closeDeleteConfirmation() {
  deleting = false;
  deletePanel.hidden = true;
  deletePanel.replaceChildren();
}

function clearViews() {
  closeDeleteConfirmation();
  currentCase = null;
  listData = null;
  referenceData = null;
  caseList.replaceChildren();
  detailBody.replaceChildren();
  referenceBody.replaceChildren();
  referenceState.textContent = '主数据尚未载入，请刷新资料。';
  counts.textContent = '';
  pagination.hidden = true;
  firstPage.hidden = true;
  detailView.hidden = true;
  listView.hidden = false;
}

function invalidateDataset(message) {
  datasetRead++;
  viewRead++;
  currentDataset = null;
  browsing = false;
  clearViews();
  details.replaceChildren();
  identities.replaceChildren();
  stateLine.textContent = message;
  status(message, 'error');
  updateDatasetButtons();
}

async function readJSON(url) {
  const response = await fetch(url, {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error('read failed');
    error.status = response.status;
    throw error;
  }
  return data;
}

function requireIdentity(actual, expected) {
  if (actual !== expected) {
    const error = new Error('snapshot changed');
    error.status = 409;
    throw error;
  }
}

function readError(error) {
  invalidateDataset(error.status === 409 ? '资料已变化，请刷新核对。'
    : '无法读取案例资料，请检查服务与数据库后点击“刷新数据状态”重试。');
}

function caseParams(expected) {
  const params = new URLSearchParams();
  for (const input of filterForm.querySelectorAll('[name]')) params.set(input.name, input.value);
  params.set('page', page);
  params.set('page_size', pageSize.value);
  if (expected) params.set('snapshot_id', expected);
  return params;
}

function populateSelect(id, options) {
  const select = document.getElementById(id);
  const previous = select.value;
  while (select.options.length > 1) select.remove(1);
  for (const [value, label] of options) select.add(new Option(label, value));
  if (previous && !options.some(([value]) => value === previous)) {
    select.add(new Option(`${previous}（当前资料无此选项）`, previous));
  }
  select.value = previous;
}

function showReference(data) {
  populateSelect('filter-product', data.products.map(item => [item.product_id, `${item.product_name} · ${item.product_id}`]));
  populateSelect('filter-customer', Object.entries(data.customers).map(([id, name]) => [id, `${name} · ${id}`]));
  populateSelect('filter-process', Object.entries(data.processes).map(([id, name]) => [id, `${name} · ${id}`]));
  populateSelect('filter-stage', data.detection_stages.map(value => [value, value]));
  referenceBody.replaceChildren();
  addTable(referenceBody, '产品、客户与产品族', ['产品编号 / 名称', '客户编号 / 名称', '产品族', '路线'],
    data.products.map(item => [`${item.product_id} · ${item.product_name}`,
      `${item.customer_id} · ${item.customer_name}`, `${item.product_family_id} · ${item.product_family}`, item.package_route]));
  addTable(referenceBody, '异常类型', ['异常编号', '名称', '适用路线'],
    data.failure_modes.map(item => [item.failure_mode_id, item.failure_mode, item.applicable_package]));
  addTable(referenceBody, '工序与路线', ['路线', '允许的工序编号 / 名称'],
    Object.entries(data.route_processes).map(([route, ids]) => [route,
      ids.map(id => `${id} · ${data.processes[id]}`).join('；')]));
  for (const [label, key] of [['发现阶段', 'detection_stages'], ['调查类型', 'checkpoint_types'], ['相关标记', 'relevance_values']]) {
    referenceBody.append(element('p', `${label}：${data[key].join('；')}`));
  }
  referenceState.textContent = '主数据只读。路线上的允许工序不等于本案已确认异常工序；填写 CSV 时使用编号。';
}

function addTable(parent, title, headers, rows) {
  parent.append(element('h3', title));
  const wrap = element('div');
  wrap.className = 'table-scroll';
  wrap.tabIndex = 0;
  wrap.setAttribute('aria-label', title);
  const table = element('table');
  table.className = 'ranking-table';
  const head = element('thead');
  const headRow = element('tr');
  for (const header of headers) headRow.append(element('th', header));
  head.append(headRow);
  const body = element('tbody');
  for (const row of rows) {
    const tr = element('tr');
    for (const value of row) tr.append(element('td', value));
    body.append(tr);
  }
  table.append(head, body);
  wrap.append(table);
  parent.append(wrap);
}

async function refreshDataset(expected = null) {
  if (deletePending) return;
  const generation = ++datasetRead;
  viewRead++;
  currentDataset = null;
  clearViews();
  browsing = true;
  page = 1;
  stateLine.textContent = '正在读取数据状态…';
  details.replaceChildren();
  identities.replaceChildren();
  status('正在读取案例资料…');
  updateDatasetButtons();
  try {
    const data = await readJSON(`/cases?${caseParams(expected)}`);
    if (generation !== datasetRead) return;
    if (expected) requireIdentity(data.dataset.snapshot_id, expected);
    showDataset(data.dataset);
    const reference = await readJSON(`/dataset/reference?${new URLSearchParams({snapshot_id: currentDataset.snapshot_id})}`);
    if (generation !== datasetRead) return;
    requireIdentity(reference.snapshot_id, data.dataset.snapshot_id);
    referenceData = reference;
    showReference(reference);
    renderList(data);
  } catch (error) {
    if (generation === datasetRead) readError(error);
  } finally {
    if (generation === datasetRead) { browsing = false; updateDatasetButtons(); }
  }
}

function renderList(data) {
  closeDeleteConfirmation();
  listData = data;
  page = data.page;
  listView.hidden = false;
  detailView.hidden = true;
  detailBody.replaceChildren();
  counts.textContent = `整库 ${data.total_count} 案 · 筛选匹配 ${data.matched_count} 案 · 本页 ${data.items.length} 案`;
  caseList.replaceChildren();
  pagination.hidden = data.matched_count === 0;
  firstPage.hidden = data.items.length > 0 || data.matched_count === 0;
  document.getElementById('page-state').textContent = `第 ${page} / ${Math.max(1, Math.ceil(data.matched_count / data.page_size))} 页`;
  if (!data.total_count) {
    status('案例资料库为空。可点击“新增案例”，或在下方导入 CSV／载入完整演示数据。', 'warning');
  } else if (!data.matched_count) {
    status('筛选无结果。请调整条件或清空筛选。', 'warning');
  } else if (!data.items.length) {
    status('页码超出当前结果范围，请回到第一页。', 'warning');
  } else {
    status('点击案例编号查看详情，或点击“编辑整案”直接修改。浏览和编辑无需模型密钥。', 'ok');
    const table = element('table');
    table.className = 'library-table';
    const head = element('thead');
    const row = element('tr');
    for (const label of ['编号 / 异常摘要', '产品 / 客户', '已确认异常站点', '发现日期范围', '子记录']) row.append(element('th', label));
    head.append(row);
    const body = element('tbody');
    for (const item of data.items) {
      const tr = element('tr');
      const values = [null, item.products.map(product => `${product.product_name} (${product.product_id})\n${product.customer_name} (${product.customer_id})`).join('\n\n'),
        item.abnormal_processes.map(process => `${process.process} (${process.process_id})`).join('；'),
        `${item.detection_start} ～ ${item.detection_end}`, `${item.detail_count} 条明细\n${item.evidence_count} 项证据`];
      values.forEach((value, index) => {
        const td = element('td');
        td.dataset.label = ['编号 / 异常摘要', '产品 / 客户', '已确认异常站点', '发现日期范围', '子记录'][index];
        if (index === 0) {
          const button = element('button', item.case_id);
          button.className = 'case-link';
          button.type = 'button';
          button.addEventListener('click', () => openCase(item.case_id));
          const description = element('p', item.abnormal_description);
          description.className = 'case-summary';
          const editButton = element('button', '编辑整案');
          editButton.type = 'button';
          editButton.className = 'secondary';
          editButton.dataset.editCase = item.case_id;
          editButton.setAttribute('aria-label', `编辑整案 ${item.case_id}`);
          editButton.addEventListener('click', () => openCase(item.case_id, true));
          td.append(button, description, editButton);
        } else td.textContent = value;
        tr.append(td);
      });
      body.append(tr);
    }
    table.append(head, body);
    caseList.append(table);
  }
  updateDatasetButtons();
}

async function readCases() {
  if (uploading || editing || savingCase || deletePending || !currentDataset || !referenceData) return;
  closeDeleteConfirmation();
  const expected = currentDataset.snapshot_id;
  const read = ++viewRead;
  const generation = datasetRead;
  browsing = true;
  listView.hidden = false;
  detailView.hidden = true;
  detailBody.replaceChildren();
  caseList.replaceChildren();
  counts.textContent = '';
  status('正在筛选案例…');
  updateDatasetButtons();
  try {
    const data = await readJSON(`/cases?${caseParams(expected)}`);
    if (generation !== datasetRead || read !== viewRead) return;
    requireIdentity(data.dataset.snapshot_id, expected);
    renderList(data);
  } catch (error) {
    if (generation === datasetRead && read === viewRead) readError(error);
  } finally {
    if (generation === datasetRead && read === viewRead) { browsing = false; updateDatasetButtons(); }
  }
}

function field(parent, label, value) {
  const block = element('div');
  block.className = 'field-block';
  const heading = element('div', label);
  heading.className = 'field-label';
  const content = element('p', value ?? '未填写');
  content.className = 'field-value';
  block.append(heading, content);
  parent.append(block);
}

function section(parent, title) {
  const node = element('section');
  node.className = 'card block';
  node.append(element('h3', title));
  parent.append(node);
  return node;
}

function revisionFieldLabel(path) {
  const labels = {abnormal_description: '异常描述', root_cause: '历史结案原因', corrective_action: '历史纠正措施',
    investigation_others: '其他调查说明', abnormal_processes: '已确认异常工序', product_id: '产品',
    production_lot: '生产批', customer_lot: '客户批', production_time: '投批日期', detection_time: '发现日期',
    detection_stage: '发现阶段', abnormal_types: '异常类型', affected_qty: '影响数量', disposition: '最终处置',
    checkpoint_type: '调查类型', custom_name: '自定义调查名称', result: '调查结果', relevance: '相关标记',
    origin: '记录来源', closure_status: '来源结案声明', known_at: '整份资料完整可用日期', basis: '可用性依据'};
  const dot = path.lastIndexOf('.');
  const owner = path.slice(0, dot).replace(/^case$/, '案例').replace(/^source$/, '来源')
    .replace(/^availability$/, '整份资料').replace(/^details/, '明细').replace(/^evidences/, '调查项');
  return `${owner} · ${labels[path.slice(dot + 1)] || path.slice(dot + 1)}`;
}

function renderDetail(data) {
  currentCase = data;
  detailBody.replaceChildren(element('h2', `案例 ${data.case.case_id}`));
  const basic = section(detailBody, '历史结论');
  field(basic, '异常描述', data.case.abnormal_description);
  field(basic, '历史结案原因', data.case.root_cause);
  field(basic, '历史纠正措施', data.case.corrective_action);
  field(basic, '其他调查说明', data.case.investigation_others);
  field(basic, '已确认异常工序', data.case.processes.map(item => `${item.process} · ${item.process_id}`).join('；'));
  field(basic, '来源结案声明', data.source.closure_status);
  field(basic, '资料审阅状态', currentDataset.review_status === 'draft_pending_human_review' ? '待人工审阅（与结案声明分开）' : currentDataset.review_status);
  const children = section(detailBody, `批次明细（${data.details.length} 条）`);
  for (const item of data.details) {
    const card = section(children, item.detail_id);
    field(card, '产品 / 客户', `${item.product.product_name} (${item.product_id}) / ${item.product.customer_name} (${item.product.customer_id})`);
    field(card, '产品族 / 路线', `${item.product.product_family} (${item.product.product_family_id}) / ${item.product.package_route}`);
    for (const [label, key] of [['客户批', 'customer_lot'], ['生产批', 'production_lot'], ['投批日期', 'production_time'], ['发现阶段', 'detection_stage'], ['发现日期', 'detection_time'], ['影响数量（ea）', 'affected_qty'], ['处置', 'disposition']]) field(card, label, item[key]);
    field(card, '异常类型', item.failure_modes.map(mode => `${mode.failure_mode} · ${mode.failure_mode_id}`).join('；'));
  }
  const evidences = section(detailBody, `Case 级调查证据（${data.evidences.length} 项）`);
  evidences.append(element('p', '调查证据属于整案，不与某条明细强行配对。'));
  for (const item of data.evidences) {
    const card = section(evidences, item.checkpoint_id);
    field(card, '调查类型', item.checkpoint_type);
    if (item.custom_name !== null) field(card, '自定义调查名称', item.custom_name);
    field(card, '调查结果', item.result);
    field(card, '相关标记（不代表语义已确认）', item.relevance);
  }
  const groups = section(detailBody, `所属分组（${data.groups.length} 个）`);
  if (!data.groups.length) groups.append(element('p', '未加入分组。'));
  for (const group of data.groups) {
    const card = section(groups, group.group_id);
    field(card, '分组类型', group.group_type.join('；'));
    field(card, '分组说明', group.description);
    if (group.other_type_description !== null) field(card, '其他类型说明', group.other_type_description);
    field(card, '本案关联理由', group.association_reason);
    field(card, '成员编号', group.member_ids.join('、'));
  }
  const source = section(detailBody, '来源沿革');
  if (data.source.web_provenance) {
    source.append(element('p', '网页人工修订。原报告、CSV 行号或合成候选仅表示原始出处，不是修订正文的逐字证明。'));
    for (const origin of data.source.web_provenance.origins || []) {
      const card = section(source, `${origin.record_type} · ${origin.record_id} 的当前来源`);
      field(card, '来源类型', origin.kind === 'report' ? '报告' : '人工合成');
      for (const [label, key] of [['报告编号', 'source_document_id'], ['报告名称', 'source_document_name'], ['合成说明', 'description'], ['具体位置', 'source_location']]) {
        if (origin[key] !== undefined) field(card, label, origin[key]);
      }
    }
  }
  if (data.source.records) {
    source.append(element('p', '下列报告位置与原 CSV 行号用于定位导入出处，来源真实性仍待人工核查。'));
    for (const record of data.source.records) {
      const card = section(source, `${record.record_type} · ${record.record_id}`);
      for (const [label, key] of [['报告编号', 'source_document_id'], ['报告名称', 'source_document_name'], ['具体位置', 'source_location'], ['原 CSV 文件', 'filename'], ['原 CSV 行号', 'csv_line']]) field(card, label, record[key]);
    }
  } else if (data.source.sheet !== undefined) {
    source.append(element('p', '人工合成示例。下列主数据候选说明生成沿革，不是真实调查报告或已验证调查证据。'));
    for (const [label, key] of [['主数据表', 'sheet'], ['异常编号', 'failure_mode_id'], ['选用原因候选', 'root_cause'], ['选用措施候选', 'corrective_action']]) field(source, label, data.source[key]);
    for (const [label, key] of [['已有模拟修订说明', 'simulation_revision'], ['生成说明', 'generation_note']]) {
      const note = data.source[key];
      if (!note) continue;
      const card = section(source, label);
      for (const [title, name] of [['登记日期', 'date'], ['说明', 'description'], ['合成家族', 'family'], ['同义来源案例', 'synonym_of'], ['覆盖说明', 'coverage']]) {
        if (note[name] !== undefined && note[name] !== null) field(card, title, note[name]);
      }
      if (note.evidence_ids) field(card, '对应证据编号', note.evidence_ids.join('、'));
    }
  } else if (!data.source.web_provenance) {
    source.append(element('p', '未登记原始文件候选；来源沿用当前记录出处。'));
  }
  const revisions = element('details');
  revisions.id = 'case-revisions';
  revisions.className = 'block';
  revisions.append(element('summary', `网页修订记录（${(data.revisions || []).length} 次）`));
  if (!(data.revisions || []).length) revisions.append(element('p', '暂无网页修订。'));
  for (const revision of data.revisions || []) {
    const entry = section(revisions, revision.saved_at);
    field(entry, '操作', revision.operation === 'create' ? '新建案例' : '编辑');
    field(entry, '变更字段', revision.changed_fields.map(revisionFieldLabel).join('、')
      || (revision.operation === 'create' ? '新建整案及子记录' : '子记录增删'));
    const recordLabel = item => `${{case: '案例', detail: '明细', evidence: '调查项'}[item.record_type] || item.record_type} · ${item.record_id}`;
    field(entry, '新增记录', revision.added_records.map(recordLabel).join('、') || '无');
    field(entry, '移除记录', revision.removed_records.map(recordLabel).join('、') || '无');
  }
  detailBody.append(revisions);
}

async function openCase(caseId, edit = false) {
  if (uploading || editing || savingCase || deletePending || !currentDataset || !referenceData) return;
  closeDeleteConfirmation();
  const generation = datasetRead;
  const read = ++viewRead;
  const expected = currentDataset.snapshot_id;
  browsing = true;
  listView.hidden = true;
  detailView.hidden = false;
  detailBody.replaceChildren();
  status(`正在读取案例 ${caseId}…`);
  updateDatasetButtons();
  try {
    const data = await readJSON(`/case?${new URLSearchParams({case_id: caseId, snapshot_id: expected})}`);
    if (generation !== datasetRead || read !== viewRead) return;
    requireIdentity(data.snapshot_id, expected);
    renderDetail(data);
    status(`正在查看 ${caseId} 的完整记录。`, 'ok');
    detailBody.focus();
    if (edit) {
      browsing = false;
      startEditing();
    }
  } catch (error) {
    if (generation !== datasetRead || read !== viewRead) return;
    if (error.status === 404) status('当前资料中没有该案例，请返回列表或刷新核对。', 'error');
    else readError(error);
  } finally {
    if (generation === datasetRead && read === viewRead) { browsing = false; updateDatasetButtons(); }
  }
}

function startDeleting() {
  if (!currentCase || uploading || editing || savingCase || browsing || deleting) return;
  deleting = true;
  const data = currentCase;
  const caseId = data.case.case_id;
  const expected = currentDataset.snapshot_id;
  deletePanel.replaceChildren();
  deletePanel.hidden = false;
  deletePanel.append(element('h2', `确认删除案例 ${caseId}`));
  const info = section(deletePanel, '将删除的内容');
  field(info, '案例编号', caseId);
  field(info, '异常摘要', data.case.abnormal_description);
  field(info, '子记录', `${data.details.length} 条明细、${data.evidences.length} 项调查证据`);
  const groups = section(deletePanel, `受影响分组（${data.groups.length} 个）`);
  if (!data.groups.length) groups.append(element('p', '本案未加入分组。'));
  for (const group of data.groups) {
    const remaining = group.member_ids.filter(id => id !== caseId).length;
    field(groups, `${group.group_id} · ${group.description || '分组'}`,
      `当前成员 ${group.member_ids.length} 案；删除后剩余 ${remaining} 案${remaining < 2 ? '（少于两案，删除会被业务规则拒绝）' : ''}`);
  }
  const warning = element('p', '删除会移除该案、明细、调查证据、入组关系、来源映射及该案的网页修订记录；不提供回收站，不能撤销。进行中的旧回答仍按原快照保留。');
  warning.className = 'callout error';
  deletePanel.append(warning);
  const actions = element('div'); actions.className = 'actions';
  const confirm = element('button', '确认删除'); confirm.id = 'confirm-delete'; confirm.type = 'button';
  const cancel = element('button', '取消'); cancel.id = 'cancel-delete'; cancel.type = 'button'; cancel.className = 'secondary';
  cancel.addEventListener('click', () => {
    if (deletePending) return;
    closeDeleteConfirmation(); updateDatasetButtons();
  });
  confirm.addEventListener('click', () => confirmDelete(caseId, expected, confirm));
  actions.append(confirm, cancel);
  deletePanel.append(actions);
  const message = element('p', '删除前会重新核对当前资料身份和完整候选规则。');
  message.id = 'delete-status'; message.className = 'status'; message.setAttribute('role', 'status');
  deletePanel.append(message);
  updateDatasetButtons();
  deletePanel.scrollIntoView({block: 'start'});
  confirm.focus();
}

async function confirmDelete(caseId, expected, button) {
  if (!deleting || deletePending || !button.isConnected) return;
  deletePending = true;
  button.disabled = true;
  const cancel = document.getElementById('cancel-delete');
  cancel.disabled = true;
  updateDatasetButtons();
  const message = document.getElementById('delete-status');
  document.getElementById('delete-errors')?.remove();
  message.textContent = '正在核对资料身份并删除…';
  try {
    const response = await fetch(`/case?${new URLSearchParams({case_id: caseId})}`, {
      method: 'DELETE', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expected_snapshot_id: expected, confirmed: true}),
    });
    const data = await response.json();
    if (response.ok && data.status === 'deleted' && data.case_id === caseId) {
      deletePending = false;
      closeDeleteConfirmation();
      currentCase = null; listData = null;
      await refreshDataset(data.dataset.snapshot_id);
      // 空库由空态说明引导新增/导入/恢复；成功提示不能盖掉它。
      if (currentDataset && currentDataset.case_count > 0) {
        status(`案例 ${caseId} 已删除；列表与新搜索不再包含该案。`, 'ok');
      }
      return;
    }
    if (response.status === 409) {
      deleting = false;
      invalidateDataset('资料已变化，本次未删除；请刷新核对后重新读取影响。');
      return;
    }
    if (response.status === 422) {
      message.textContent = data.message || '未删除：删除后的资料不满足业务规则。';
      const errors = element('ul'); errors.id = 'delete-errors'; errors.className = 'text-list';
      for (const error of data.errors || []) {
        errors.append(element('li', `${error.path || error.field || '整份资料'}：${error.message}`));
      }
      deletePanel.append(errors);
      button.disabled = false;
      return;
    }
    message.textContent = data.message || `删除未完成（HTTP ${response.status}），请刷新核对当前资料。`;
    button.disabled = false;
  } catch (error) {
    message.textContent = '未收到可确认的删除结果，可能已删除。请刷新核对当前资料，不会自动重删。';
  } finally {
    deletePending = false;
    cancel.disabled = false;
    updateDatasetButtons();
  }
}

function beginWrite() {
  datasetRead++;
  viewRead++;
  uploading = true;
  browsing = false;
  clearViews();
  status('正在整体替换资料，请等待结果…');
  updateDatasetButtons();
  searchLink.hidden = true;
}

uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (uploading || editing || savingCase || !currentDataset || !uploadForm.reportValidity()) return;
  const expected = currentDataset.snapshot_id;
  const file = document.getElementById('csv-file').files[0];
  uploadErrors.replaceChildren();
  if (!file || file.size > 5 * 1024 * 1024) {
    uploadStatus.textContent = '请选择不超过 5 MiB 的 CSV 文件。';
    return;
  }
  beginWrite();
  uploadStatus.textContent = '正在校验并替换，请等待结果…';
  const params = new URLSearchParams({
    dataset_name: document.getElementById('dataset-name').value,
    known_at: document.getElementById('available-on').value,
    basis: document.getElementById('availability-basis').value, filename: file.name,
    expected_snapshot_id: expected,
  });
  try {
    const response = await fetch(`/dataset?${params}`, {
      method: 'POST', headers: {'Content-Type': 'text/csv'}, body: file,
    });
    const data = await response.json();
    if (response.ok && data.status === 'imported') {
      await refreshDataset(data.dataset.snapshot_id);
      uploadStatus.textContent = `替换成功：${data.dataset.name}，共 ${data.dataset.case_count} 个案例。`;
      searchLink.hidden = false;
    } else {
      uploadStatus.textContent = data.message || '上传参数校验失败，未导入。';
      for (const error of data.errors || data.detail || []) {
        const location = error.row ? `第 ${error.row} 行` : '文件／数据集';
        uploadErrors.append(element('li', `${location} · ${error.column || (error.loc || []).join('.')}：${error.message || error.msg}`));
      }
      status('资料替换未完成，请刷新核对后继续浏览。', 'warning');
      if (response.status === 409) invalidateDataset('资料已变化，请刷新核对。');
    }
  } catch (error) {
    uploadStatus.textContent = '未收到可确认的上传结果。请刷新核对当前数据状态后再操作，页面不会自动重传。';
    invalidateDataset('资料状态待核对，请刷新数据状态。');
  } finally {
    uploading = false;
    updateDatasetButtons();
  }
});

restoreButton.addEventListener('click', async () => {
  if (uploading || editing || savingCase || !currentDataset) return;
  const expected = currentDataset.snapshot_id;
  if (!window.confirm(`将整体替换当前资料“${currentDataset.name}”，包括网页修订记录，载入九案完整演示数据。确认继续？`)) return;
  beginWrite();
  restoreStatus.textContent = '正在校验并恢复完整演示数据…';
  try {
    const response = await fetch('/dataset/restore-demo', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expected_snapshot_id: expected}),
    });
    const data = await response.json();
    if (response.ok && data.status === 'restored') {
      await refreshDataset(data.dataset.snapshot_id);
      restoreStatus.textContent = '恢复成功：9 个案例、9 条明细、9 项证据。';
      searchLink.hidden = false;
    } else {
      restoreStatus.textContent = data.message || '恢复失败，请检查服务后刷新核对。';
      if (response.status === 409) invalidateDataset('资料已变化，请刷新核对。');
      else status('恢复未完成，请刷新核对后继续浏览。', 'warning');
    }
  } catch (error) {
    restoreStatus.textContent = '未收到可确认的恢复结果，请刷新核对当前资料后再操作；不会自动重传。';
    invalidateDataset('资料状态待核对，请刷新数据状态。');
  } finally {
    uploading = false;
    updateDatasetButtons();
  }
});

filterForm.addEventListener('submit', event => { event.preventDefault(); page = 1; readCases(); });
document.getElementById('clear-filters').addEventListener('click', () => { filterForm.reset(); page = 1; readCases(); });
previousPage.addEventListener('click', () => { page--; readCases(); });
nextPage.addEventListener('click', () => { page++; readCases(); });
firstPage.addEventListener('click', () => { page = 1; readCases(); });
pageSize.addEventListener('change', () => { page = 1; readCases(); });
deleteCaseButton.addEventListener('click', startDeleting);
document.getElementById('back-to-list').addEventListener('click', () => {
  if (savingCase || deletePending) return;
  if (editing && !leaveEditing()) return;
  viewRead++;
  browsing = false;
  if (listData) renderList(listData);
  else readCases();
});
refreshButton.addEventListener('click', () => { if (!uploading && (!editing || leaveEditing())) refreshDataset(); });
window.addEventListener('pageshow', event => {
  if (event.persisted && !uploading) {
    if (editing) markEditConflict();
    else refreshDataset();
  }
});
refreshDataset();
