// Focused regression: real Chromium, real pages, HTTP fixtures; no database or model calls.
// Usage: CASETRACE_CHROME_BINARY=/usr/bin/chromium node scripts/web_dataset_browser_check.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { createServer } from 'node:http';

const root = fileURLToPath(new URL('../src/casetrace/web/', import.meta.url));
const answerFixture = JSON.parse(readFileSync(
  new URL('../results/m4/dev-v3-answer-v10/q005.json', import.meta.url), 'utf8'));
let dataset = {name: 'OLD', snapshot_id: 'old', case_count: 9, updated_at: '2026-09-15',
  known_at: '2026-09-15', is_upload: false};
let datasetReads = 0;
let restores = 0;
let restoreFailure = 0;
let searchRequests = 0;
let answerRequests = 0;
let submittedDate;
let holdDataset = false;
const pendingDatasets = [];
let pendingAnswer;
function json(res, data, status = 200) {
  res.writeHead(status, {'Content-Type': 'application/json'});
  res.end(JSON.stringify(data));
}
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/dataset/restore-demo') {
    restores++;
    let raw = '';
    for await (const chunk of req) raw += chunk;
    if (restoreFailure || JSON.parse(raw).expected_snapshot_id !== dataset.snapshot_id) {
      if (restoreFailure === 422) json(res, {status: 'invalid_dataset', message: 'C001 来源候选不兼容'}, 422);
      else json(res, {status: 'snapshot_conflict', message: '资料已变化，请刷新核对后再次确认恢复。'}, 409);
    } else {
      dataset = {...dataset, name: '完整演示数据（九案）', snapshot_id: `restored-${restores}`,
        case_count: 9, detail_count: 9, evidence_count: 9, known_at: '2026-09-15', is_upload: false};
      json(res, {status: 'restored', dataset});
    }
    return;
  }
  if (url.pathname === '/dataset' && req.method === 'POST') {
    for await (const chunk of req) { /* consume the uploaded fixture */ }
    if (url.searchParams.get('expected_snapshot_id') !== dataset.snapshot_id) {
      json(res, {status: 'snapshot_conflict', message: '资料已变化'}, 409); return;
    }
    dataset = {...dataset, name: url.searchParams.get('dataset_name'), snapshot_id: 'uploaded',
      case_count: 1, known_at: url.searchParams.get('known_at'), is_upload: true};
    json(res, {status: 'imported', dataset});
    return;
  }
  if (url.pathname === '/dataset') {
    datasetReads++;
    if (holdDataset) pendingDatasets.push({res, data: {...dataset}});
    else json(res, dataset);
    return;
  }
  if (url.pathname === '/cases') {
    const data = {dataset: {...dataset}, items: [], total_count: dataset.case_count,
      matched_count: 0, page: 1, page_size: 20};
    if (holdDataset) pendingDatasets.push({res, data});
    else json(res, data);
    return;
  }
  if (url.pathname === '/dataset/reference') {
    json(res, {snapshot_id: dataset.snapshot_id, products: [], customers: {}, product_families: {},
      failure_modes: [], processes: {}, route_processes: {}, detection_stages: [],
      checkpoint_types: [], relevance_values: []});
    return;
  }
  if (url.pathname === '/model-status') { json(res, {configured: true}); return; }
  if (url.pathname === '/runtime-settings') { json(res, {top_k: 4, max_tokens: 4096}); return; }
  if (url.pathname === '/search' || url.pathname === '/answer') {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const request = JSON.parse(raw);
    submittedDate = request.known_at;
    if (url.pathname === '/answer') {
      answerRequests++;
      const record = structuredClone(answerFixture);
      record.snapshot.snapshot_id = dataset.snapshot_id;
      pendingAnswer = {res, record};
    } else {
      searchRequests++;
      if (submittedDate !== dataset.known_at) {
        json(res, {detail: [{loc: ['body', 'known_at'], msg: '登记日期已改变'}]}, 422);
      } else {
        json(res, {status: 'no_hits', record: {status: 'no_hits',
          query: {text: request.query, known_at: request.known_at}, ranking: [],
          context: {cases: []}, snapshot: {snapshot_id: dataset.snapshot_id},
          response: {called_model: false}}});
      }
    }
    return;
  }
  const file = url.pathname === '/' ? 'index.html'
    : url.pathname === '/settings' ? 'settings.html' : url.pathname === '/library' ? 'library.html'
    : url.pathname.replace('/static/', '');
  try {
    let content = readFileSync(join(root, file));
    if (file === 'index.html') content = content.toString().replace('</head>',
      '<script>window.restoreEvents=[]; window.addEventListener("pageshow", e=>restoreEvents.push(e.persisted));</script></head>');
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript'
      : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(content);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const pageUrl = `http://127.0.0.1:${server.address().port}/`;
const chromePath = process.env.CASETRACE_CHROME_BINARY || '/usr/bin/chromium';
const profile = mkdtempSync(join(tmpdir(), 'casetrace-web-regression-'));
const chrome = spawn(chromePath, [
  '--headless=new', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' });
let launchError;
chrome.on('error', (error) => { launchError = error; });
let socket;
let nextId = 0;
const pending = new Map();

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP timeout: ${method}`));
    }, 10000);
    pending.set(id, (message) => {
      clearTimeout(timer);
      if (message.error) reject(new Error(JSON.stringify(message.error)));
      else resolve(message.result);
    });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}

async function execute(script) {
  await evaluate(`(async () => {${script}})()`);
}

async function waitUntil(expression) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await evaluate(expression)) return;
    await sleep(100);
  }
  throw new Error(`Page condition timed out: ${expression}`);
}


let checks = 0;
async function check(name, expression) {
  assert.equal(await evaluate(expression), true, name);
  checks++;
  console.log(`PASS ${name}`);
}
async function restore() {
  await evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}))');
}
async function search() {
  await evaluate('form.requestSubmit(submitButton)');
  await waitUntil('!inFlight && !downloadButton.hidden');
}
async function uploadExample(name, knownAt) {
  await waitUntil('typeof currentDataset !== "undefined" && currentDataset !== null');
  const example = readFileSync(join(root, 'import-example.csv'), 'utf8');
  await execute(`document.getElementById('dataset-name').value = ${JSON.stringify(name)};
    document.getElementById('available-on').value = ${JSON.stringify(knownAt)};
    document.getElementById('availability-basis').value = '合成验收数据完整可用';
    const transfer = new DataTransfer();
    transfer.items.add(new File([${JSON.stringify(example)}], 'example.csv', {type: 'text/csv'}));
    document.getElementById('csv-file').files = transfer.files;
    document.querySelector('#upload-form input[type=checkbox]').checked = true;
    uploadForm.requestSubmit();`);
  await waitUntil('!uploading && uploadStatus.textContent.includes("替换成功")');
}
try {
  let port;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (launchError) throw launchError;
    try { port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; }
    catch { await sleep(100); }
    if (port) break;
  }
  if (!port) throw new Error('Chrome did not start');
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  });
  await send('Page.enable');
  await send('Page.navigate', {url: pageUrl});
  await waitUntil('typeof datasetReady !== "undefined" && datasetReady && modelConfigured');
  assert.equal(datasetReads, 1, 'initial load reads dataset once');
  checks++;
  await evaluate('queryInput.value = "保留用户输入"');
  await search();
  await evaluate('Array.from(document.links).find(link => link.pathname === "/library").click()');
  await waitUntil('location.pathname === "/library" && typeof uploading !== "undefined"');
  await uploadExample('NEW', '2026-10-20');
  await evaluate('history.back()');
  await waitUntil('location.pathname === "/" && typeof datasetReady !== "undefined"');
  await check('history.back uses BFCache', 'restoreEvents.includes(true)');
  await waitUntil('datasetReady && knownAtInput.value === "2026-10-20"');
  await check('restore updates dataset and clears old results, preserving input',
    'document.getElementById("current-dataset").textContent.includes("NEW")'
    + ' && exampleDisclosure.hidden && resultBody.childElementCount === 0'
    + ' && downloadButton.hidden && queryInput.value === "保留用户输入"');
  assert.equal(searchRequests, 1, 'restore does not automatically search');
  assert.equal(answerRequests, 0, 'restore does not automatically generate');
  checks++;
  await search();
  assert.equal(submittedDate, '2026-10-20');
  await evaluate('window.previousDownload = downloadButton.href');
  await restore();
  await waitUntil('datasetReady');
  await check('unchanged snapshot preserves result and download',
    '!downloadButton.hidden && downloadButton.href === window.previousDownload');

  dataset = {...dataset, name: 'SAME DATE', snapshot_id: 'same-date-new'};
  await restore();
  await waitUntil('datasetReady');
  await check('new snapshot with same date clears old result',
    'resultBody.childElementCount === 0 && downloadButton.hidden'
    + ' && document.getElementById("current-dataset").textContent.includes("SAME DATE")');

  holdDataset = true;
  await restore();
  await waitUntil('!datasetReady');
  const searchesBefore = searchRequests;
  await evaluate('form.requestSubmit(submitButton)');
  await check('refresh blocks submission without losing input',
    '!datasetReady && !inFlight && queryInput.value === "保留用户输入"');
  assert.equal(searchRequests, searchesBefore);
  json(pendingDatasets.shift().res, {}, 503);
  await waitUntil('document.getElementById("current-dataset").textContent.includes("无法读取")');
  await check('failed refresh stays blocked', '!datasetReady');
  holdDataset = false;
  await restore();
  await waitUntil('datasetReady');

  // Two overlapping reads: exercise both stale success and stale failure.
  for (const oldFails of [false, true]) {
    holdDataset = true;
    dataset = {...dataset, name: 'STALE', snapshot_id: 'stale'};
    await restore();
    while (pendingDatasets.length < 1) await sleep(20);
    dataset = {...dataset, name: 'LATEST', snapshot_id: `latest-${oldFails}`};
    await restore();
    while (pendingDatasets.length < 2) await sleep(20);
    const older = pendingDatasets.shift();
    const latest = pendingDatasets.shift();
    json(latest.res, latest.data);
    await waitUntil('datasetReady');
    json(older.res, oldFails ? {} : older.data, oldFails ? 503 : 200);
    await sleep(100);
    await check(`stale ${oldFails ? 'failure' : 'success'} cannot overwrite latest state`,
      'datasetReady && document.getElementById("current-dataset").textContent.includes("LATEST")');
    holdDataset = false;
  }

  await evaluate('form.requestSubmit(generateButton)');
  while (!pendingAnswer) await sleep(20);
  dataset = {...dataset, name: 'AFTER MODEL START', snapshot_id: 'after-model'};
  await restore();
  await waitUntil('datasetReady');
  await check('dataset refresh preserves the running request', 'inFlight');
  json(pendingAnswer.res, {status: 'ok', record: pendingAnswer.record});
  pendingAnswer = null;
  await waitUntil('!inFlight');
  await check('late answer retains download and visibly identifies its previous dataset',
    '!downloadButton.hidden && resultBody.firstElementChild.textContent.includes("更新前的数据集")');
  assert.equal(answerRequests, 1, 'the model request is not retried');
  checks++;
  await send('Page.navigate', {url: `${pageUrl}library`});
  await waitUntil('typeof currentDataset !== "undefined" && currentDataset !== null');
  // A read started before a successful write must not roll the page back afterward.
  for (const oldFails of [false, true]) {
    holdDataset = true;
    await evaluate('void readCases()');
    while (pendingDatasets.length < 1) await sleep(20);
    const older = pendingDatasets.shift();
    holdDataset = false;
    await uploadExample('UPLOADED LATEST', '2026-10-20');
    json(older.res, oldFails ? {} : older.data, oldFails ? 503 : 200);
    await sleep(100);
    await check(`pre-upload ${oldFails ? 'failure' : 'success'} cannot overwrite uploaded state`,
      'currentDataset?.snapshot_id === "uploaded" && !restoreButton.disabled'
      + ' && stateLine.textContent.includes("UPLOADED LATEST")');
    holdDataset = false;
  }
  await check('format example is clearly named',
    'Array.from(document.links).find(link => link.pathname === "/dataset/example").textContent.includes("上传格式示例")');
  await evaluate('window.confirm = () => false; restoreButton.click()');
  assert.equal(restores, 0, 'cancel sends no restore request');
  checks++;
  await evaluate('window.confirm = message => {window.confirmText = message; return true;}; restoreButton.click(); restoreButton.click()');
  await waitUntil('!uploading && restoreStatus.textContent.includes("恢复成功")');
  assert.equal(restores, 1, 'double click sends only one request');
  checks++;
  await check('confirmation and restored state',
    'confirmText.includes("整体替换") && confirmText.includes("修订记录")'
    + ' && currentDataset.case_count === 9 && currentDataset.known_at === "2026-09-15"');
  restoreFailure = 409;
  await evaluate('restoreButton.click()');
  await waitUntil('!uploading && restoreStatus.textContent.includes("资料已变化")');
  await check('conflict requires refresh', 'restoreButton.disabled && currentDataset === null');
  restoreFailure = 0;
  await evaluate('refreshDataset()');
  await waitUntil('currentDataset !== null');
  restoreFailure = 422;
  await evaluate('restoreButton.click()');
  await waitUntil('!uploading && restoreStatus.textContent.includes("C001")');
  await check('business validation error preserves displayed state', 'currentDataset.case_count === 9');
  restoreFailure = 0;
  await evaluate('window.realFetch = window.fetch; window.fetch = () => Promise.reject(new Error("offline")); restoreButton.click()');
  await waitUntil('!uploading && restoreStatus.textContent.includes("未收到可确认")');
  await check('unknown response disables restore until refreshed', 'currentDataset === null && restoreButton.disabled');
  await evaluate('window.fetch = window.realFetch; refreshDataset()');
  await waitUntil('currentDataset !== null');
  await evaluate('restoreButton.click()');
  await waitUntil('!uploading && restoreStatus.textContent.includes("恢复成功")');
  // Screen captures are optional artifacts inside the repository.
  if (process.env.CASETRACE_SCREENSHOT_DIR) {
    const {mkdirSync, writeFileSync} = await import('node:fs');
    mkdirSync(process.env.CASETRACE_SCREENSHOT_DIR, {recursive: true});
    for (const [name, width] of [['desktop', 1280], ['narrow', 390]]) {
      await send('Emulation.setDeviceMetricsOverride', {width, height: 950, deviceScaleFactor: 1, mobile: false});
      const shot = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true});
      writeFileSync(join(process.env.CASETRACE_SCREENSHOT_DIR, `restore-${name}.png`), Buffer.from(shot.data, 'base64'));
    }
  }
  console.log(`${checks} focused checks passed`);
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (socket) socket.close();
  for (const item of pendingDatasets) item.res.destroy();
  if (pendingAnswer) pendingAnswer.res.destroy();
  chrome.kill('SIGKILL');
  await sleep(300);
  rmSync(profile, {recursive: true, force: true});
  server.closeAllConnections();
  server.close();
}
