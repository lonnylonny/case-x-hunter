// Real Chromium + HTTP fixtures: verifies UI only, never a PostgreSQL substitute.
// Usage: CASETRACE_SCREENSHOT_DIR=docs/plans/dataset-management/evidence/02 node scripts/web_case_browser_check.mjs
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {createServer} from 'node:http';

const root = fileURLToPath(new URL('../src/casetrace/web/', import.meta.url));
const product = id => ({product_id: id, product_name: `合成产品 ${id}`, customer_id: id === 'P3' ? 'CUS2' : 'CUS1',
  customer_name: id === 'P3' ? '另一客户' : '合成客户', product_family_id: 'PF1', product_family: '合成产品族', package_route: 'LF_WB'});
const reference = {products: ['P1', 'P2', 'P3'].map(product), customers: {CUS1: '合成客户', CUS2: '另一客户'},
  product_families: {PF1: '合成产品族'}, processes: {P004: 'Wire Bond', P007: 'Molding'},
  route_processes: {LF_WB: ['P004', 'P007']}, detection_stages: ['OQC', 'Customer'],
  checkpoint_types: ['QC', 'Other'], relevance_values: ['related', 'uncertain'],
  failure_modes: [{failure_mode_id: '00010', failure_mode: '焊线脱落', applicable_package: 'LF_WB'}]};
const records = Array.from({length: 21}, (_, index) => {
  const id = `C${String(index + 1).padStart(3, '0')}`;
  const child = {detail_id: `D${id}`, case_id: id, product_id: index === 1 ? 'P3' : 'P1',
    customer_lot: `CL${id}`, production_lot: `PL${id}`, production_time: '2026-06-01',
    detection_stage: 'OQC', detection_time: '2026-06-02', abnormal_types: ['00010'],
    failure_modes: reference.failure_modes, affected_qty: 30, disposition: '整批隔离并复核。'};
  child.product = product(child.product_id);
  const details = [child];
  if (index === 0) details.push({...child, detail_id: 'D001b', product_id: 'P2', product: product('P2'),
    production_lot: '0000123', customer_lot: '0000456', detection_stage: 'Customer', detection_time: '2026-06-08'});
  const evidences = Array.from({length: index === 0 ? 3 : 1}, (_, number) => ({
    checkpoint_id: `E${id}-${number}`, case_id: id, checkpoint_type: number === 2 ? 'Other' : 'QC',
    custom_name: number === 2 ? '补充断口检查' : null, relevance: 'related',
    result: `第 ${number + 1} 项调查结果：已发现焊点界面剥离。\n${'保留完整观察内容与批次对应关系。'.repeat(index === 0 ? 12 : 1)}`,
  }));
  return {case: {case_id: id, abnormal_description: index === 0
    ? '焊线脱落与界面剥离。<img src=x onerror="window.__injected=true">\n' + '长文本调查摘要。'.repeat(20)
    : `第 ${index + 1} 案焊线脱落`, root_cause: '结案确认：表面污染。', corrective_action: '改善清洁与来料管控。',
    investigation_others: '保留批次记录。', abnormal_processes: ['P004'], processes: [{process_id: 'P004', process: 'Wire Bond'}]},
  details, evidences, groups: index === 0 ? [{group_id: 'G1', group_type: ['same_abnormal_process'],
    description: '共同异常站点调查', other_type_description: null, association_reason: '已确认涉及 Wire Bond。', member_ids: ['C001', 'C003']}] : [],
  source: index === 1 ? {closure_status: 'NDF', records: [
    {record_type: 'case', record_id: id, source_document_id: 'SYN-R001', source_document_name: '人工合成调查报告',
      source_location: '第 2 页结论段', filename: 'synthetic.csv', csv_line: 2},
    {record_type: 'detail', record_id: child.detail_id, source_document_id: 'SYN-R001', source_document_name: '人工合成调查报告',
      source_location: '第 1 页批次表', filename: 'synthetic.csv', csv_line: 3},
  ]} : {closure_status: 'confirmed', sheet: 'failure_modes', failure_mode_id: '00010',
    root_cause: '表面污染', corrective_action: '改善清洁',
    simulation_revision: {date: '2026-09-19', description: '已有合成事实说明，不是网页修订。'}}};
});
let version = 'fixture-1';
let empty = false;
let failure = 0;
let forbiddenRequests = 0;
const held = new Set(['/cases']);
const pendingReads = [];
const dataset = () => ({name: '资料库浏览验收（HTTP 夹具）', snapshot_id: version,
  case_count: empty ? 0 : records.length,
  detail_count: empty ? 0 : records.reduce((total, item) => total + item.details.length, 0),
  evidence_count: empty ? 0 : records.reduce((total, item) => total + item.evidences.length, 0),
  known_at: '2026-09-15', updated_at: '2026-11-05T12:00:00', basis: '人工合成验收快照，资料在登记日期前完整可用。',
  review_status: 'draft_pending_human_review', source_type: 'builtin_demo', dataset_sha256: 'fixture-file-digest', content_digest: version});
function json(res, data, status = 200) {
  res.writeHead(status, {'Content-Type': 'application/json'});
  res.end(JSON.stringify(data));
}
function respond(path, res, data, status = 200) {
  if (held.has(path)) pendingReads.push({res, data: structuredClone(data), status});
  else json(res, data, status);
}
let saves = 0, saveFailure = 0, holdSave = false, pendingSave;
let creates = 0, createFailure = 0, holdCreate = false, pendingCreate;
let deletes = 0, deleteFailure = 0, holdDelete = false, pendingDelete;
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const path = url.pathname;
  if (path === '/case' && req.method === 'PUT') {
    saves++;
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    if (body.expected_snapshot_id !== version || saveFailure === 409) {
      json(res, {status: 'snapshot_conflict', message: '资料已变化'}, 409); return;
    }
    if (saveFailure === 422) {
      json(res, {message: '字段错误，未保存', errors: [{path: 'details[0]', field: 'production_lot', message: '批号不一致'}]}, 422); return;
    }
    if (saveFailure === 500) { res.destroy(); return; }
    const item = records.find(item => item.case.case_id === url.searchParams.get('case_id'));
    const finish = () => {
      version = `edited-${saves}`;
      item.case = {...body.case, case_id: item.case.case_id, processes: item.case.processes};
      item.details = body.details.map((child, i) => ({...child, detail_id: child.detail_id || `NEW-D${i}`,
        case_id: item.case.case_id, product: product(child.product_id), failure_modes: reference.failure_modes}));
      item.evidences = body.evidences.map((child, i) => ({...child, checkpoint_id: child.checkpoint_id || `NEW-E${i}`, case_id: item.case.case_id}));
      item.source.web_provenance = {modified_records: [{record_type: 'case', record_id: item.case.case_id}], origins: []};
      item.revisions = [{saved_at: '2026-11-06T12:00:00+00:00', operation: 'edit', changed_fields: ['case.root_cause'], added_records: [], removed_records: []}];
      json(res, {status: 'saved', dataset: {...dataset(), source_type: 'user_maintained'}, detail: {...item, snapshot_id: version}});
    };
    if (holdSave) pendingSave = finish; else finish();
    return;
  }
  if (path === '/cases' && req.method === 'POST') {
    creates++;
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    if (body.expected_snapshot_id !== version || createFailure === 409) {
      json(res, {status: 'snapshot_conflict', message: '资料已变化'}, 409); return;
    }
    if (createFailure === 422) {
      json(res, {message: '字段错误，未保存', errors: [{path: 'details[0]', field: 'production_lot', message: '批号不一致'}]}, 422); return;
    }
    if (createFailure === 500) { res.destroy(); return; }
    const finish = () => {
      const id = `C900${creates}`;
      version = `created-${creates}`;
      const item = {
        case: {...body.case, case_id: id, processes: body.case.abnormal_processes.map(process_id => ({process_id, process: reference.processes[process_id]}))},
        details: body.details.map((child, i) => ({...child, detail_id: `NEW-D${i}`, case_id: id,
          product: product(child.product_id), failure_modes: reference.failure_modes})),
        evidences: body.evidences.map((child, i) => ({...child, checkpoint_id: `NEW-E${i}`, case_id: id})),
        groups: [],
        source: {closure_status: body.closure_status, web_provenance: {
          modified_records: [],
          origins: [{record_type: 'case', record_id: id, ...body.case.origin}]}},
        revisions: [{saved_at: '2026-11-06T12:00:00+00:00', operation: 'create', changed_fields: [],
          added_records: [{record_type: 'case', record_id: id}], removed_records: []}],
      };
      records.push(item);
      json(res, {status: 'created', case_id: id, dataset: {...dataset(), source_type: 'user_maintained'},
        detail: {...item, snapshot_id: version}});
    };
    if (holdCreate) pendingCreate = finish; else finish();
    return;
  }
  if (path === '/case' && req.method === 'DELETE') {
    deletes++;
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    if (body.confirmed !== true) { json(res, {message: '须确认删除；不提供回收站'}, 422); return; }
    if (body.expected_snapshot_id !== version || deleteFailure === 409) {
      json(res, {status: 'snapshot_conflict', message: '资料已变化'}, 409); return;
    }
    if (deleteFailure === 422) {
      json(res, {message: '未删除，删除后的资料不满足业务规则。', errors: [{path: 'CaseGroup[G1]',
        field: 'memberships', message: 'CR-38 | CaseGroup[G1] | memberships | 至少需要两个不同的有效 Case'}]}, 422); return;
    }
    if (deleteFailure === 500) { res.destroy(); return; }
    const caseId = url.searchParams.get('case_id');
    const index = records.findIndex(item => item.case.case_id === caseId);
    if (index < 0) { json(res, {status: 'case_not_found', message: '当前资料中没有该案例。'}, 404); return; }
    const group = records[index].groups[0];
    if (group && group.member_ids.length - 1 < 2) {
      json(res, {message: '未删除，删除后的资料不满足业务规则。', errors: [{path: `CaseGroup[${group.group_id}]`,
        field: 'memberships', message: `CR-38 | CaseGroup[${group.group_id}] | memberships | 至少需要两个不同的有效 Case，当前为 ${group.member_ids.length - 1} 个`}]}, 422);
      return;
    }
    records.splice(index, 1);
    version = `deleted-${deletes}`;
    const finish = () => json(res, {status: 'deleted', case_id: caseId, dataset: {...dataset(), source_type: 'user_maintained'}});
    if (holdDelete) pendingDelete = finish; else finish();
    return;
  }
  if (path === '/search' || path === '/answer' || path === '/model-status' || path === '/runtime-settings') forbiddenRequests++;
  if (path === '/cases' || path === '/case' || path === '/dataset/reference') {
    if (failure) { respond(path, res, {status: 'service_unavailable'}, failure); return; }
    if (url.searchParams.has('snapshot_id') && url.searchParams.get('snapshot_id') !== version) {
      respond(path, res, {status: 'snapshot_conflict'}, 409); return;
    }
    if (path === '/dataset/reference') { respond(path, res, {...reference, snapshot_id: version}); return; }
    if (path === '/case') {
      const record = records.find(item => item.case.case_id === url.searchParams.get('case_id'));
      respond(`/case:${url.searchParams.get('case_id')}`, res, {...record, snapshot_id: version}, record && !empty ? 200 : 404); return;
    }
    const query = url.searchParams;
    const keyword = (query.get('keyword') || '').trim().toLowerCase();
    const selected = (empty ? [] : records).filter(item => {
      const text = [item.case.case_id, item.case.abnormal_description, item.case.root_cause,
        ...item.details.flatMap(detail => [detail.production_lot, detail.customer_lot])];
      return (!keyword || text.some(value => value.toLowerCase().includes(keyword)))
        && (!query.get('abnormal_process_id') || item.case.abnormal_processes.includes(query.get('abnormal_process_id')))
        && item.details.some(detail => (!query.get('product_id') || detail.product_id === query.get('product_id'))
          && (!query.get('customer_id') || detail.product.customer_id === query.get('customer_id'))
          && (!query.get('detection_stage') || detail.detection_stage === query.get('detection_stage')));
    });
    const page = Number(query.get('page') || 1), size = Number(query.get('page_size') || 20);
    respond(path, res, {dataset: dataset(), total_count: empty ? 0 : records.length, matched_count: selected.length,
      page, page_size: size, items: selected.slice((page - 1) * size, page * size).map(item => ({
        case_id: item.case.case_id, abnormal_description: item.case.abnormal_description,
        products: item.details.map(detail => detail.product), abnormal_processes: item.case.processes,
        detection_start: '2026-06-02', detection_end: item.details.at(-1).detection_time,
        detail_count: item.details.length, evidence_count: item.evidences.length,
      }))});
    return;
  }
  const files = {'/library': 'library.html', '/settings': 'settings.html', '/': 'index.html'};
  const file = files[path] || (path.startsWith('/static/') ? path.slice(8) : null);
  if (!file || file.includes('..')) { res.writeHead(404); res.end(); return; }
  try {
    const body = readFileSync(join(root, file));
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const pageUrl = `http://127.0.0.1:${server.address().port}/library`;

const profile = mkdtempSync(join(tmpdir(), 'casetrace-browser-'));
const chrome = spawn(process.env.CASETRACE_CHROME_BINARY || '/usr/bin/chromium', [
  '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], {stdio: 'ignore'});
let launchError, socket, nextId = 0, checks = 0;
chrome.on('error', error => { launchError = error; });
const commands = new Map();
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { commands.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    commands.set(id, message => { clearTimeout(timer); message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result); });
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function eventually(predicate) {
  for (let i = 0; i < 100; i++) { if (await predicate()) return; await sleep(100); }
  throw new Error('Browser condition timed out');
}
const waitUntil = expression => eventually(() => evaluate(expression));
async function check(name, expression) {
  assert.equal(await evaluate(expression), true, name);
  checks++;
  console.log(`PASS ${name}`);
}
async function applyFilters(script = '') {
  await evaluate(`${script}; filterForm.requestSubmit()`);
  await waitUntil('!browsing');
}
async function shot(name, selector) {
  if (!process.env.CASETRACE_SCREENSHOT_DIR) return;
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'start'})`);
  await sleep(100);
  const data = await send('Page.captureScreenshot', {format: 'png'});
  mkdirSync(process.env.CASETRACE_SCREENSHOT_DIR, {recursive: true});
  writeFileSync(join(process.env.CASETRACE_SCREENSHOT_DIR, name), Buffer.from(data.data, 'base64'));
}
try {
  let port;
  await eventually(() => {
    if (launchError) throw launchError;
    try { port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; return Boolean(port); }
    catch { return false; }
  });
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); });
  socket.addEventListener('message', event => { const message = JSON.parse(event.data); if (commands.has(message.id)) { commands.get(message.id)(message); commands.delete(message.id); } });
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await send('Page.navigate', {url: pageUrl});
  await waitUntil('typeof filterFields !== "undefined" && filterFields.disabled');
  await check('loading is distinct from zero cases', 'browserStatus.textContent.includes("正在读取") && counts.textContent === ""');
  await eventually(() => pendingReads.length === 1);
  held.clear();
  const first = pendingReads.shift(); json(first.res, first.data, first.status);
  await waitUntil('!browsing && referenceData !== null');
  await check('overview, navigation and collapsed identity', 'currentDataset.case_count === 21 && counts.textContent.includes("筛选匹配 21") && !document.getElementById("dataset-technical").open && document.querySelectorAll(".page-header nav a").length === 3');
  await check('dynamic text is safe and summary keeps full data', '!window.__injected && !document.querySelector("#case-list img") && caseList.textContent.includes("<img")');
  await evaluate('pageSize.value="10"; pageSize.dispatchEvent(new Event("change"))');
  await waitUntil('!browsing');
  await check('paging counts separate total from current page', 'listData.total_count === 21 && listData.items.length === 10 && page === 1');
  await shot('list-desktop.png', '#browser-heading');
  await evaluate('nextPage.click()'); await waitUntil('!browsing');
  await check('next page is stable', 'page === 2 && listData.items[0].case_id === "C011"');
  await check('pagination is represented in the URL', 'new URLSearchParams(location.search).get("page") === "2" && new URLSearchParams(location.search).get("page_size") === "10"');
  await evaluate('window.__beforeReload = true');
  await send('Page.reload');
  await waitUntil('!window.__beforeReload && typeof page !== "undefined" && typeof listData !== "undefined" && listData?.page === 2 && !browsing');
  await check('reload restores pagination and page size', 'page === 2 && pageSize.value === "10" && listData.items[0].case_id === "C011"');
  await evaluate('previousPage.click()'); await waitUntil('!browsing');
  await applyFilters('document.getElementById("filter-product").value="P1"; document.getElementById("filter-stage").value="Customer"');
  await check('different details cannot satisfy one filter combination', 'listData.matched_count === 0 && browserStatus.textContent.includes("筛选无结果")');
  await applyFilters('document.getElementById("filter-product").value="P2"');
  await check('matching detail preserves whole-case counts', 'listData.matched_count === 1 && listData.items[0].detail_count === 2 && listData.items[0].evidence_count === 3');
  await evaluate('void openCase("C001")'); await waitUntil('!browsing');
  await check('complete case, child records, sources and group reasons', 'detailBody.textContent.includes("0000123") && detailBody.textContent.includes("补充断口检查") && detailBody.textContent.includes("已确认涉及 Wire Bond") && detailBody.textContent.includes("已有模拟修订说明") && detailBody.textContent.includes("待人工审阅")');
  await shot('detail-desktop.png', '#case-detail-body');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 1000, deviceScaleFactor: 1, mobile: false});
  await check('long detail fits narrow viewport', 'document.documentElement.scrollWidth <= innerWidth');
  await shot('detail-narrow.png', '#case-detail-body');
  await shot('detail-evidence-narrow.png', '#case-detail-body > section:nth-of-type(3)');
  await shot('detail-source-narrow.png', '#case-detail-body > section:last-of-type');
  await evaluate('document.getElementById("back-to-list").click()');
  await check('return preserves applied filters and page', 'page === 1 && listData.matched_count === 1 && document.getElementById("filter-product").value === "P2"');
  await shot('list-narrow.png', '#browser-heading');
  await evaluate('referenceDetails.open=true');
  await check('reference remains read-only and includes names', 'referenceBody.textContent.includes("合成产品族") && referenceBody.textContent.includes("合成客户") && !referenceBody.querySelector("input,select,button")');
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await evaluate('void openCase("C002")'); await waitUntil('!browsing');
  await check('CSV provenance stays separate from closure and review', 'detailBody.textContent.includes("SYN-R001") && detailBody.textContent.includes("第 2 页结论段") && detailBody.textContent.includes("synthetic.csv") && detailBody.textContent.includes("NDF") && detailBody.textContent.includes("待人工审阅")');
  const specialRecord = records.at(-1);
  for (const id of ['report/001', '.', '..']) {
    specialRecord.case.case_id = id;
    for (const item of [...specialRecord.details, ...specialRecord.evidences]) item.case_id = id;
    await applyFilters(`document.getElementById('filter-keyword').value=${JSON.stringify(id)}`);
    await evaluate(`Array.from(caseList.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(id)}).click()`);
    await waitUntil('!browsing');
    await check(`legal text ID ${id} opens without URL normalization`, `detailBody.querySelector('h2').textContent === ${JSON.stringify(`案例 ${id}`)}`);
  }
  specialRecord.case.case_id = 'C021';
  for (const item of [...specialRecord.details, ...specialRecord.evidences]) item.case_id = 'C021';
  for (const oldFails of [false, true]) {
    held.add('/cases');
    await evaluate('document.getElementById("filter-keyword").value="C001"; filterForm.requestSubmit()');
    await eventually(() => pendingReads.length === 1);
    const old = pendingReads.shift(); held.clear();
    await applyFilters('document.getElementById("filter-keyword").value="C002"');
    json(old.res, oldFails ? {} : old.data, oldFails ? 503 : old.status); await sleep(150);
    await check(`stale list ${oldFails ? 'failure' : 'success'} ignored`, 'listData?.items[0].case_id === "C002" && currentDataset !== null');
  }
  held.add('/case:C001');
  await evaluate('void openCase("C001")'); await eventually(() => pendingReads.length === 1);
  const oldDetail = pendingReads.shift(); held.clear();
  await evaluate('void openCase("C002")'); await waitUntil('!browsing');
  json(oldDetail.res, oldDetail.data); await sleep(150);
  await check('stale detail cannot replace newly selected case', 'detailBody.querySelector("h2").textContent.includes("C002")');
  held.add('/dataset/reference');
  await evaluate('void refreshDataset()'); await eventually(() => pendingReads.length === 1);
  const oldReference = pendingReads.shift(); held.clear(); version = 'fixture-2';
  await evaluate('void refreshDataset()'); await waitUntil('!browsing && referenceData !== null');
  json(oldReference.res, oldReference.data); await sleep(150);
  await check('stale reference cannot mix identities', 'currentDataset.snapshot_id === "fixture-2" && referenceData.snapshot_id === "fixture-2"');
  version = 'fixture-3';
  await applyFilters();
  await check('replacement rejects old view until explicit refresh', 'currentDataset === null && filterFields.disabled && browserStatus.textContent.includes("资料已变化") && caseList.childElementCount === 0');
  await evaluate('void refreshDataset()'); await waitUntil('!browsing');
  await check('explicit refresh keeps visible filter aligned with results', 'document.getElementById("filter-keyword").value === "C002" && listData.matched_count === 1');
  failure = 503; await evaluate('void refreshDataset()'); await waitUntil('!browsing');
  await check('service failure is not an empty library', 'currentDataset === null && browserStatus.textContent.includes("无法读取") && !browserStatus.textContent.includes("为空")');
  failure = 0; empty = true; await evaluate('void refreshDataset()'); await waitUntil('!browsing');
  await check('zero-case snapshot offers import and restore', 'listData.total_count === 0 && browserStatus.textContent.includes("资料库为空") && !restoreButton.disabled && !uploadButton.disabled');
  empty = false; version = 'fixture-4';
  await evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted:true}))'); await waitUntil('!browsing');
  await check('cached page restore rechecks identity', 'currentDataset.snapshot_id === "fixture-4"');
  if (process.env.CASETRACE_CHECK_EDITING) {
    await runEditingChecks();
  }
  if (process.env.CASETRACE_CHECK_CREATION) {
    await runCreationChecks();
  }
  if (process.env.CASETRACE_CHECK_DELETION) {
    await runDeletionChecks();
  }
  assert.equal(forbiddenRequests, 0, 'browsing never calls models, retrieval or settings'); checks++;
  console.log(`${checks} case browser checks passed (HTTP fixtures, no database)`);
} catch (error) {
  console.error(error); process.exitCode = 1;
} finally {
  if (socket) socket.close();
  for (const item of pendingReads) item.res.destroy();
  chrome.kill('SIGKILL'); await sleep(300);
  rmSync(profile, {recursive: true, force: true});
  server.closeAllConnections(); server.close();
}

async function runEditingChecks() {
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await check('case list exposes a direct edit action', '!!caseList.querySelector("[data-edit-case=C001]")');
  await evaluate('caseList.querySelector("[data-edit-case=C001]").click()');
  await waitUntil('editing');
  await check('list edit action opens matching whole-case editor', 'editSession.caseId === "C001" && !editor.hidden && uploadButton.disabled');
  await evaluate('document.getElementById("cancel-edit").click()');
  await evaluate('void openCase("C001")'); await waitUntil('!browsing');
  await check('revision area starts collapsed with no fabricated history', '!document.getElementById("case-revisions").open && detailBody.textContent.includes("暂无网页修订")');
  await evaluate('document.getElementById("edit-case").click()');
  await check('Chinese section editor keeps provenance and master data background', 'editing && editor.textContent.includes("历史结论") && editor.textContent.includes("批次明细") && editor.textContent.includes("Case 级调查证据") && editor.textContent.includes("合成客户") && editor.textContent.includes("原来源自动沿用")');
  await check('save and cancel are visible when editing starts', "['save-case','cancel-edit'].every(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth && el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})");
  await check('ordinary edit needs no repeated origin confirmation', 'editor.querySelectorAll(".edit-origin[open]").length === 0 && !editIsDirty()');
  await shot('edit-desktop.png', '#case-editor');
  await evaluate(`window.__confirms=0; window.confirm=()=>{window.__confirms++; return false;};
    editor.querySelector('[data-field="root_cause"]').value='人工修订根因'; document.getElementById('back-to-list').click()`);
  await check('dirty navigation preserves draft when discard refused', 'editing && window.__confirms===1 && editor.querySelector("[data-field=root_cause]").value==="人工修订根因"');
  const beforeCancel = saves;
  await evaluate('window.confirm=()=>true; document.getElementById("cancel-edit").click()');
  assert.equal(saves, beforeCancel);
  await check('cancel returns to detail without saving', '!editing && !detailBody.hidden');
  await evaluate('document.getElementById("edit-case").click(); editor.querySelector("[data-add=details]").click(); editor.querySelector("[data-add=evidences]").click()');
  await shot('edit-details-desktop.png', '#case-editor > form > fieldset > section:first-of-type');
  await check('new child requires its own concrete origin', 'editor.querySelectorAll(".edit-origin[open]").length===2 && editor.textContent.includes("保存后分配编号") && editor.textContent.includes("本条记录具体位置")');
  await evaluate('editor.querySelectorAll(".edit-record > button")[2].click(); editor.querySelectorAll(".edit-record > button")[5].click()');
  // 新增后移除，回到初始完整内容。
  await check('adding then removing a child is not dirty', '!editIsDirty()');
  await evaluate('document.getElementById("edit-change-availability").click()');
  await check('changing date requires whole-dataset availability declaration', 'editor.querySelector("[data-field=known_at]").value === currentDataset.known_at && !editor.querySelector("[data-field=known_at]").disabled && editor.textContent.includes("整份资料在新日期前")');
  await evaluate('document.getElementById("edit-change-availability").click()');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 1000, deviceScaleFactor: 1, mobile: false});
  await check('multi-record editor fits narrow viewport', 'document.documentElement.scrollWidth <= innerWidth');
  await shot('edit-narrow.png', '#case-editor');
  await shot('edit-evidence-narrow.png', '#case-editor > form > fieldset > section:nth-of-type(2)');
  await check('save and cancel stay visible while scrolling a narrow editor', "['save-case','cancel-edit'].every(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth && el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})");
  saveFailure=422;
  await evaluate('editor.querySelector("[data-field=root_cause]").value="保留的人工输入"; document.getElementById("case-edit-form").requestSubmit()');
  await waitUntil('!editSession.saving');
  await check('field error keeps input and offers a focus target', 'editing && editor.querySelector("[data-field=root_cause]").value==="保留的人工输入" && document.getElementById("edit-errors").textContent.includes("批号不一致") && !!editor.querySelector("[aria-invalid=true]")');
  await shot('edit-error-narrow.png', '#edit-errors');
  saveFailure=409;
  await evaluate('document.getElementById("case-edit-form").requestSubmit()'); await waitUntil('!editSession.saving');
  await check('409 retains draft and blocks automatic resubmission', 'editing && editSession.blocked && document.getElementById("save-case").disabled && editor.querySelector("[data-field=root_cause]").value==="保留的人工输入"');
  await evaluate('window.confirm=()=>true; document.getElementById("refresh-dataset").click()'); await waitUntil('!browsing');
  saveFailure=500;
  await evaluate('void openCase("C001")'); await waitUntil('!browsing');
  await evaluate('document.getElementById("edit-case").click(); editor.querySelector("[data-field=root_cause]").value="未知结果输入"; document.getElementById("case-edit-form").requestSubmit()'); await waitUntil('!editSession.saving');
  await check('unknown network result keeps draft and requires reconciliation', 'editing && editSession.blocked && document.getElementById("edit-status").textContent.includes("可能已保存") && editor.querySelector("[data-field=root_cause]").value==="未知结果输入"');
  await evaluate('window.confirm=()=>true; document.getElementById("refresh-dataset").click()'); await waitUntil('!browsing');
  saveFailure=0; holdSave=true;
  await evaluate('void openCase("C001")'); await waitUntil('!browsing');
  await evaluate('document.getElementById("edit-case").click(); editor.querySelector("[data-field=root_cause]").value="成功保存的人工结论"; document.getElementById("case-edit-form").requestSubmit()');
  await eventually(()=>Boolean(pendingSave));
  const beforeRepeat=saves;
  await evaluate('document.getElementById("case-edit-form").dispatchEvent(new Event("submit", {cancelable:true})); document.getElementById("back-to-list").click()');
  assert.equal(saves,beforeRepeat);
  await check('pending save disables repeat submission and leaving', 'editing && editSession.saving && refreshButton.disabled && restoreButton.disabled && uploadButton.disabled');
  holdSave=false; pendingSave(); pendingSave=null;
  await waitUntil('!editing && !browsing');
  await check('successful save replaces identity and renders current detail', 'currentDataset.snapshot_id.startsWith("edited-") && detailBody.textContent.includes("成功保存的人工结论") && detailBody.textContent.includes("网页人工修订") && !document.getElementById("case-revisions").open');
  await evaluate('document.getElementById("case-revisions").open=true');
  await check('revision summary expands without storing old text', 'document.getElementById("case-revisions").textContent.includes("历史结案原因") && !document.getElementById("case-revisions").textContent.includes("成功保存的人工结论")');
  await shot('revisions-narrow.png', '#case-revisions');
  await evaluate('document.getElementById("back-to-list").click()'); await waitUntil('!browsing');
  await check('back after save reloads list with the new identity', '!listView.hidden && listData.dataset.snapshot_id===currentDataset.snapshot_id');

  for (const staleFails of [false, true]) {
    await evaluate('void openCase("C001")'); await waitUntil('!browsing');
    held.add('/dataset/reference');
    await evaluate('document.getElementById("edit-case").click(); editor.querySelector("[data-field=root_cause]").value+="再修改"; document.getElementById("case-edit-form").requestSubmit()');
    await eventually(()=>pendingReads.length===1);
    const oldRead=pendingReads.shift(); held.clear();
    await check('post-save reference load blocks replacing writes', 'savingCase && uploadButton.disabled && restoreButton.disabled');
    version=`after-edit-${staleFails}`;
    await evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))'); await waitUntil('!browsing && listData!==null');
    json(oldRead.res, staleFails ? {} : oldRead.data, staleFails ? 503 : 200); await sleep(150);
    await check(`post-save stale reference ${staleFails?'failure':'success'} ignored`, `currentDataset?.snapshot_id===${JSON.stringify(version)} && !listView.hidden && detailView.hidden && referenceData.snapshot_id===currentDataset.snapshot_id`);
  }
}

function fillCreationCode() { return `(() => {
  const field = (root, key) => root.querySelector('[data-field="' + key + '"]');
  const set = (el, value) => { el.value = value; el.dispatchEvent(new Event('change')); };
  const form = document.getElementById('case-edit-form');
  const [basic, detail, evidence] = form.querySelectorAll('.edit-record');
  set(field(basic, 'abnormal_description'), '浏览器夹具新增案例');
  set(field(basic, 'root_cause'), '夹具新增根因');
  set(field(basic, 'corrective_action'), '夹具新增措施');
  Array.from(field(basic, 'abnormal_processes').options).forEach(option => option.selected = option.value === 'P004');
  set(form.querySelector('[data-field="closure_status"]'), 'confirmed');
  set(field(detail, 'product_id'), 'P1');
  set(field(detail, 'production_lot'), '0007777');
  set(field(detail, 'customer_lot'), 'CL7');
  set(field(detail, 'production_time'), '2026-06-01');
  set(field(detail, 'detection_time'), '2026-06-10');
  set(field(detail, 'detection_stage'), 'OQC');
  Array.from(field(detail, 'abnormal_types').options).forEach(option => option.selected = option.value === '00010');
  set(field(detail, 'affected_qty'), '7');
  set(field(detail, 'disposition'), '隔离');
  set(field(evidence, 'checkpoint_type'), 'QC');
  set(field(evidence, 'result'), '夹具新增调查结果');
  set(field(evidence, 'relevance'), 'related');
  for (const box of form.querySelectorAll('.edit-origin')) {
    set(box.querySelector('[data-field="kind"]'), 'synthetic');
    set(box.querySelector('[data-field="description"]'), '夹具人工合成来源说明');
    set(box.querySelector('[data-field="source_location"]'), '夹具来源具体位置');
  }
  return true;
})()`; }

async function runCreationChecks() {
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await check('library exposes a create action without opening an old case', '!newCaseButton.disabled && newCaseButton.textContent === "新增案例"');
  await evaluate('newCaseButton.click()');
  await check('create mode starts with one detail and one evidence and open origins', 'editing && editSession.mode === "create" && editor.textContent.includes("新增完整案例") && editor.querySelectorAll(".edit-record").length === 3 && editor.querySelectorAll(".edit-origin[open]").length === 3');
  await check('create save and cancel are visible immediately', "['save-case','cancel-edit'].every(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})");
  await shot('create-desktop.png', '#case-editor');
  const beforeCreates = creates;
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await check('incomplete creation is not submitted', 'editing && !editSession.saving');
  assert.equal(creates, beforeCreates, 'native validation must not reach the server');
  await evaluate(fillCreationCode());
  await check('filled creation draft is dirty', 'editIsDirty()');
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await waitUntil('!editing && !browsing');
  await check('created case opens as current detail with the new identity', 'currentDataset.snapshot_id.startsWith("created-") && detailBody.querySelector("h2").textContent.includes("C9001") && detailBody.textContent.includes("浏览器夹具新增案例") && detailBody.textContent.includes("新建案例") && detailBody.textContent.includes("人工合成") && !document.getElementById("case-revisions").open');
  await check('creation clears the stale list and refreshes counts', 'listData === null && currentDataset.case_count === 22');
  await shot('create-success-desktop.png', '#case-detail-body');
  await evaluate('document.getElementById("case-revisions").open=true');
  await check('creation revision shows added records without old text', 'document.getElementById("case-revisions").textContent.includes("新建整案及子记录") && document.getElementById("case-revisions").textContent.includes("案例 · C9001") && !document.getElementById("case-revisions").textContent.includes("夹具新增根因")');
  await evaluate('document.getElementById("back-to-list").click()'); await waitUntil('!browsing');
  await evaluate('document.getElementById("filter-keyword").value="C9001"; filterForm.requestSubmit()'); await waitUntil('!browsing');
  await check('list after creation includes the new case and count', 'listData.total_count === 22 && listData.matched_count === 1 && listData.items[0].case_id === "C9001" && counts.textContent.includes("整库 22 案")');
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 1000, deviceScaleFactor: 1, mobile: false});
  await evaluate('newCaseButton.click()');
  await check('narrow creation fits and keeps save visible', "document.documentElement.scrollWidth <= innerWidth && ['save-case','cancel-edit'].every(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));})");
  await shot('create-narrow.png', '#case-editor');
  await evaluate(fillCreationCode());
  createFailure = 422;
  await evaluate('document.getElementById("case-edit-form").requestSubmit()'); await waitUntil('!editSession.saving');
  await check('creation field error keeps input and locates the field', 'editing && editor.querySelector("[data-field=abnormal_description]").value.includes("浏览器夹具") && document.getElementById("edit-errors").textContent.includes("批号不一致") && !!editor.querySelector("[aria-invalid=true]")');
  await shot('create-error-narrow.png', '#edit-errors');
  createFailure = 409;
  await evaluate('document.getElementById("case-edit-form").requestSubmit()'); await waitUntil('!editSession.saving');
  await check('creation 409 keeps the draft and blocks resubmission', 'editing && editSession.blocked && document.getElementById("save-case").disabled');
  await evaluate('window.confirm=()=>true; document.getElementById("cancel-edit").click()');
  await check('cancel after conflict returns to the list without writing', '!editing && !listView.hidden && detailView.hidden');
  createFailure = 500;
  await evaluate('newCaseButton.click()');
  await evaluate(fillCreationCode());
  await evaluate('document.getElementById("case-edit-form").requestSubmit()'); await waitUntil('!editSession.saving');
  await check('unknown creation result keeps the draft and asks for reconciliation', 'editing && editSession.blocked && document.getElementById("edit-status").textContent.includes("可能已保存")');
  await evaluate('window.confirm=()=>true; document.getElementById("cancel-edit").click()');
  createFailure = 0; holdCreate = true;
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await evaluate('newCaseButton.click()');
  await evaluate(fillCreationCode());
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await eventually(() => Boolean(pendingCreate));
  const beforeRepeat = creates;
  await evaluate('document.getElementById("case-edit-form").dispatchEvent(new Event("submit", {cancelable:true})); document.getElementById("back-to-list").click()');
  assert.equal(creates, beforeRepeat, 'pending creation must not be resubmitted');
  await check('pending creation disables repeat submission and leaving', 'editing && editSession.saving && uploadButton.disabled && restoreButton.disabled');
  holdCreate = false; pendingCreate(); pendingCreate = null;
  await waitUntil('!editing && !browsing');
  await check('created case after pending save shows the new identity', 'currentDataset.snapshot_id.startsWith("created-") && detailBody.textContent.includes("浏览器夹具新增案例")');
  await evaluate('document.getElementById("back-to-list").click()'); await waitUntil('!browsing');
  await check('list counts stay consistent after the pending creation', 'listData.total_count === 23 && counts.textContent.includes("整库 23 案")');
}

async function runDeletionChecks() {
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await evaluate('void openCase("C002")'); await waitUntil('!browsing');
  await check('detail view exposes a delete action', '!deleteCaseButton.disabled && deleteCaseButton.textContent === "删除案例"');
  await evaluate('deleteCaseButton.click()');
  await check('delete confirmation shows impact and no recycle bin', 'deleting && !deletePanel.hidden && deletePanel.textContent.includes("确认删除案例 C002") && deletePanel.textContent.includes("1 条明细") && deletePanel.textContent.includes("1 项调查证据") && deletePanel.textContent.includes("不提供回收站") && deletePanel.textContent.includes("本案未加入分组")');
  await shot('delete-confirm-desktop.png', '#case-delete');
  const beforeDeletes = deletes;
  await evaluate('document.getElementById("cancel-delete").click()');
  assert.equal(deletes, beforeDeletes, 'cancel must not call the delete endpoint');
  await check('cancel keeps the case and writes nothing', '!deleting && deletePanel.hidden && detailBody.textContent.includes("案例 C002") && currentDataset.case_count === 21');
  await evaluate('deleteCaseButton.click(); window.oldDeleteButton = document.getElementById("confirm-delete")');
  version = 'delete-confirm-refresh';
  await evaluate('refreshButton.click()'); await waitUntil('!browsing && referenceData !== null');
  await check('refresh discards old deletion confirmation and restores actions', '!deleting && deletePanel.hidden && !document.getElementById("confirm-delete") && !newCaseButton.disabled && !uploadButton.disabled');
  await evaluate('void openCase("C003")'); await waitUntil('!browsing');
  await evaluate('window.oldDeleteButton.click()');
  await check('another case cannot revive or submit the old deletion', 'currentCase.case.case_id === "C003" && deletePanel.hidden');
  assert.equal(deletes, beforeDeletes, 'discarded confirmation must not submit deletion');
  await evaluate('deleteCaseButton.click(); document.getElementById("back-to-list").click()');
  await check('returning to the list discards deletion confirmation', '!deleting && deletePanel.hidden && !newCaseButton.disabled');
  await evaluate('void openCase("C002")'); await waitUntil('!browsing');
  await evaluate('deleteCaseButton.click()');
  version = 'delete-confirm-changed';
  await evaluate('document.getElementById("confirm-delete").click()');
  await waitUntil('currentDataset === null');
  assert.equal(records.length, 21, 'changed dataset must reject deletion');
  await check('changed dataset rejects the displayed confirmation', 'deletePanel.hidden && !deleting && browserStatus.textContent.includes("资料已变化")');
  await evaluate('refreshButton.click()'); await waitUntil('!browsing && referenceData !== null');
  await evaluate('void openCase("C001")'); await waitUntil('!browsing');
  await evaluate('deleteCaseButton.click()');
  await check('grouped deletion impact shows remaining members', 'deletePanel.textContent.includes("当前成员 2 案") && deletePanel.textContent.includes("删除后剩余 1 案") && deletePanel.textContent.includes("少于两案")');
  await evaluate('document.getElementById("confirm-delete").click()');
  await eventually(() => evaluate('document.getElementById("delete-status")?.textContent.includes("未删除") || false'));
  await check('group rule blocks deletion and keeps the case', 'deleting && document.getElementById("delete-errors").textContent.includes("CR-38") && detailBody.textContent.includes("案例 C001") && currentDataset.case_count === 21');
  await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 1000, deviceScaleFactor: 1, mobile: false});
  await shot('delete-error-narrow.png', '#case-delete');
  await evaluate('document.getElementById("cancel-delete").click()');
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});
  await evaluate('void openCase("C002")'); await waitUntil('!browsing');
  holdDelete = true;
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await eventually(() => Boolean(pendingDelete));
  await check('pending deletion blocks cancel, refresh and switching views', 'document.getElementById("cancel-delete").disabled && refreshButton.disabled && document.getElementById("back-to-list").disabled && newCaseButton.disabled');
  await evaluate('document.getElementById("cancel-delete").click(); refreshButton.click(); document.getElementById("back-to-list").click(); void openCase("C003")');
  await check('pending deletion keeps its original confirmation', 'deleting && !deletePanel.hidden && currentCase.case.case_id === "C002"');
  holdDelete = false; pendingDelete(); pendingDelete = null;
  await waitUntil('!browsing && currentDataset !== null && currentDataset.snapshot_id.startsWith("deleted-")');
  await check('successful deletion refreshes list without the case', 'currentDataset.snapshot_id.startsWith("deleted-") && currentDataset.case_count === 20 && listData.total_count === 20 && !listData.items.some(item => item.case_id === "C002") && counts.textContent.includes("整库 20 案")');
  await evaluate('document.getElementById("filter-keyword").value="C002"; filterForm.requestSubmit()'); await waitUntil('!browsing');
  await check('deleted case is absent from filters', 'listData.matched_count === 0 && listData.total_count === 20');
  await evaluate('document.getElementById("clear-filters").click()'); await waitUntil('!browsing');
  await evaluate('void openCase("C003")'); await waitUntil('!browsing');
  deleteFailure = 409;
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await eventually(() => evaluate('currentDataset === null'));
  await check('stale deletion forces explicit refresh', 'currentDataset === null && browserStatus.textContent.includes("资料已变化") && !browserStatus.textContent.includes("为空")');
  deleteFailure = 0;
  await evaluate('void refreshDataset()'); await waitUntil('!browsing');
  await check('refresh recovers after delete conflict', 'currentDataset !== null && listData.total_count === 20');
}
