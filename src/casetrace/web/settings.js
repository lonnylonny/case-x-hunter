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

const settingsForm = document.getElementById('settings-form');
const settingsFields = document.getElementById('settings-fields');
const settingsStatus = document.getElementById('settings-status');
const reloadSettings = document.getElementById('reload-settings');
const topK = document.getElementById('top-k');
const maxTokens = document.getElementById('max-tokens');
let settingsBusy = false;

function showSettings(data) {
  topK.value = String(data.top_k);
  maxTokens.value = String(data.max_tokens);
  settingsStatus.textContent = `已保存：Top K ${data.top_k}，输出上限 ${data.max_tokens} tokens。下一次请求生效。`;
}

async function readSettings() {
  if (settingsBusy) return;
  settingsBusy = true;
  settingsFields.disabled = true;
  reloadSettings.disabled = true;
  settingsStatus.textContent = '正在读取设置…';
  try {
    const response = await fetch('/runtime-settings', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error('unavailable');
    showSettings(data);
    settingsFields.disabled = false;
  } catch (error) {
    topK.value = '';
    maxTokens.value = '';
    settingsStatus.textContent = '无法读取实例设置，请检查服务与数据库后重新读取。';
  } finally {
    settingsBusy = false;
    reloadSettings.disabled = false;
  }
}

settingsForm.addEventListener('input', () => {
  settingsStatus.textContent = '有未保存的修改。点击“保存设置”后才会生效。';
});

settingsForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (settingsBusy || settingsFields.disabled || !settingsForm.reportValidity()) return;
  const settings = { top_k: Number(topK.value), max_tokens: Number(maxTokens.value) };
  settingsBusy = true;
  settingsFields.disabled = true;
  reloadSettings.disabled = true;
  settingsStatus.textContent = '正在保存设置…';
  try {
    const response = await fetch('/runtime-settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    const data = await response.json();
    if (response.ok) {
      showSettings(data);
    } else if (response.status === 422) {
      settingsStatus.textContent = `未保存：${(data.detail || []).map(item =>
        `${item.loc.join('.')}：${item.msg}`).join('；')}`;
    } else {
      settingsStatus.textContent = '无法确认设置已保存，请重新读取以核对实际值。';
    }
  } catch (error) {
    settingsStatus.textContent = '未收到可确认的保存结果，请重新读取以核对实际值；不会自动重传。';
  } finally {
    settingsBusy = false;
    settingsFields.disabled = false;
    reloadSettings.disabled = false;
  }
});

async function readModelStatus() {
  const line = document.getElementById('model-state');
  try {
    const response = await fetch('/model-status', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || typeof data.configured !== 'boolean') throw new Error('unavailable');
    line.textContent = data.configured ? '密钥已配置（未验证连接）。' : '密钥未配置，仍可搜索历史案例。';
  } catch (error) {
    line.textContent = '无法读取密钥配置状态，请刷新后重试。';
  }
}

reloadSettings.addEventListener('click', readSettings);
readSettings();
readModelStatus();
