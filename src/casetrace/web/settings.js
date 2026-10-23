'use strict';

const stateLine = document.getElementById('dataset-state');
const details = document.getElementById('dataset-details');
const uploadForm = document.getElementById('upload-form');
const uploadButton = document.getElementById('upload-button');
const uploadStatus = document.getElementById('upload-status');
const uploadErrors = document.getElementById('upload-errors');
const searchLink = document.getElementById('search-uploaded');
const referenceDetails = document.getElementById('reference-details');
let uploading = false;
let referenceLoaded = false;

function element(tag, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = String(text);
  return item;
}

function showDataset(data) {
  stateLine.textContent = `${data.name} · ${data.case_count} 个案例`;
  details.replaceChildren();
  for (const [label, value] of [
    ['Detail / 调查项目', `${data.detail_count} / ${data.evidence_count}`],
    ['更新时间', data.updated_at], ['完整可用日期', data.known_at],
    ['可用性依据（登记声明）', data.basis], ['审阅状态', 'draft · 待人工审阅'],
  ]) {
    details.append(element('dt', label), element('dd', value));
  }
}

async function refreshDataset() {
  try {
    const response = await fetch('/dataset');
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || '读取失败');
    showDataset(data);
  } catch (error) {
    stateLine.textContent = '无法读取当前数据状态，请检查服务与数据库后刷新。';
    details.replaceChildren();
  }
}

function addTable(parent, title, headers, rows) {
  parent.append(element('h3', title));
  const wrap = element('div');
  wrap.className = 'table-scroll';
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

async function loadReference() {
  if (!referenceDetails.open || referenceLoaded) return;
  const line = document.getElementById('reference-state');
  line.textContent = '正在读取合法主数据…';
  try {
    const response = await fetch('/dataset/reference');
    const data = await response.json();
    if (!response.ok) throw new Error('unavailable');
    const body = document.getElementById('reference-body');
    body.replaceChildren();
    addTable(body, '产品与路线', ['product_id', '名称', '客户', '路线'],
      data.products.map(item => [item.product_id, item.product_name, item.customer_id, item.package_route]));
    addTable(body, '异常类型', ['failure_mode_id', '名称', '适用路线'],
      data.failure_modes.map(item => [item.failure_mode_id, item.failure_mode, item.applicable_package]));
    addTable(body, '异常工序与路线', ['路线', '允许的 process_id 与名称'],
      Object.entries(data.route_processes).map(([route, ids]) => [route,
        ids.map(id => `${id} · ${data.processes[id]}`).join('；')]));
    for (const [label, key] of [['发现阶段', 'detection_stages'], ['调查类型', 'checkpoint_types'], ['相关标记', 'relevance_values']]) {
      body.append(element('p', `${label}：${data[key].join('；')}`));
    }
    line.textContent = '使用下列原始 ID 和枚举值；不要把名称填写到 ID 列。';
    referenceLoaded = true;
  } catch (error) {
    line.textContent = '无法读取主数据，收起后重新展开可重试。';
  }
}

uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (uploading || !uploadForm.reportValidity()) return;
  const file = document.getElementById('csv-file').files[0];
  uploadErrors.replaceChildren();
  searchLink.hidden = true;
  if (!file || file.size > 5 * 1024 * 1024) {
    uploadStatus.textContent = '请选择不超过 5 MiB 的 CSV 文件。';
    return;
  }
  uploading = true;
  uploadButton.disabled = true;
  uploadStatus.textContent = '正在校验并替换，请等待结果…';
  const params = new URLSearchParams({
    dataset_name: document.getElementById('dataset-name').value,
    known_at: document.getElementById('available-on').value,
    basis: document.getElementById('availability-basis').value,
    filename: file.name,
  });
  try {
    const response = await fetch(`/dataset?${params}`, {
      method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: file,
    });
    const data = await response.json();
    if (response.ok && data.status === 'imported') {
      showDataset(data.dataset);
      uploadStatus.textContent = `替换成功：${data.dataset.name}，共 ${data.dataset.case_count} 个案例。`;
      searchLink.hidden = false;
    } else {
      uploadStatus.textContent = data.message || '上传参数校验失败，未导入。';
      for (const error of data.errors || data.detail || []) {
        const location = error.row ? `第 ${error.row} 行` : '文件／数据集';
        const column = error.column || (error.loc || []).join('.');
        uploadErrors.append(element('li', `${location} · ${column}：${error.message || error.msg}`));
      }
    }
  } catch (error) {
    uploadStatus.textContent = '未收到可确认的上传结果。请核对当前数据状态后再操作，页面不会自动重传。';
    await refreshDataset();
  } finally {
    uploading = false;
    uploadButton.disabled = false;
  }
});

document.getElementById('refresh-dataset').addEventListener('click', refreshDataset);
referenceDetails.addEventListener('toggle', loadReference);
refreshDataset();
