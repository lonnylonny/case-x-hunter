// Real search requests plus offline answer fixtures; never calls /answer on the server.
// Usage: node scripts/web_demo_browser_check.mjs http://127.0.0.1:8010/ [screenshot-dir]
// Requires Node 22+ and Chrome; run against web_demo_smoke.py's temporary database.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const pageUrl = process.argv[2] || 'http://127.0.0.1:8010/';
const screenshotDir = process.argv[3];
const chromePath = process.env.CASETRACE_CHROME_BINARY
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const saved = JSON.parse(readFileSync(
  new URL('../results/m4/dev-v3-answer-v10/q005.json', import.meta.url), 'utf8'));
const examples = JSON.parse(readFileSync(
  new URL('../data/dev/demo-v3.json', import.meta.url), 'utf8')).queries.slice(0, 3);
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

async function screenshot(name) {
  if (!screenshotDir) return;
  mkdirSync(screenshotDir, {recursive: true});
  const size = await evaluate('({width: window.innerWidth, height: document.documentElement.scrollHeight})');
  const result = await send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true,
    clip: {x: 0, y: 0, width: size.width, height: size.height, scale: 1},
  });
  writeFileSync(join(screenshotDir, name), Buffer.from(result.data, 'base64'));
}

try {
  let port;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (launchError) throw launchError;
    try { port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; }
    catch { await sleep(100); }
    if (port) break;
  }
  if (!port) throw new Error('Chrome did not start');
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {
    method: 'PUT', signal: AbortSignal.timeout(5000),
  })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  });
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await send('Page.navigate', { url: pageUrl });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    ready = await evaluate('document.readyState === "complete" && typeof present === "function"');
    if (ready) break;
    await sleep(100);
  }
  if (!ready) throw new Error('Web page did not load');
  await waitUntil('!modelStatusLine.textContent.includes("正在")');
  const failures = [];
  let checks = 0;
  const check = (name, passed) => {
    checks += 1;
    console.log(`${passed ? 'PASS' : 'FAIL'} ${name}`);
    if (!passed) failures.push(name);
  };
  check('initial download is invisible', await evaluate('downloadButton.getClientRects().length === 0'));
  check('initial query is empty and search is enabled', await evaluate('queryInput.value === "" && !submitButton.disabled'));
  check('examples start collapsed after the main input and actions', await evaluate(
    '!exampleDisclosure.open && Boolean(submitButton.compareDocumentPosition(exampleDisclosure) & Node.DOCUMENT_POSITION_FOLLOWING)'));
  await evaluate(`window.__realFetch = window.fetch;
    window.__requestUrls = [];
    window.fetch = async function (url, options) {
      window.__requestUrls.push(url);
      if (url === '/answer') throw new Error('Real model requests are forbidden in this check');
      return window.__realFetch(url, options);
    };`);
  const initiallyConfigured = await evaluate('modelConfigured');
  if (!initiallyConfigured) {
    check('unconfigured model has explicit notice and disabled generation', await evaluate(
      'generateButton.disabled && modelStatusLine.textContent.includes("模型生成未配置")'));
  }
  for (const example of examples) {
    await evaluate(`exampleDisclosure.querySelector('summary').click();
      exampleSelect.value = ${JSON.stringify(example.query_id)}; exampleButton.click();`);
    check(`${example.query_id} fills exact query without submitting`, await evaluate(
      `queryInput.value === ${JSON.stringify(example.text)} && window.__requestUrls.length === 0
      && !exampleDisclosure.open && document.activeElement === queryInput`));
  }
  for (const example of examples) {
    await evaluate(`exampleDisclosure.querySelector('summary').click();
      exampleSelect.value = ${JSON.stringify(example.query_id)}; exampleButton.click(); form.requestSubmit(submitButton);`);
    await waitUntil('!inFlight');
    const view = await evaluate(`(async () => ({kind: statusLine.dataset.kind, text: resultBody.textContent,
      record: JSON.parse(await (await window.__realFetch(downloadButton.href)).text())}))()`);
    check(`${example.query_id} retrieves real database evidence without generation`, view.kind === 'ok'
      && view.record.status === 'retrieved' && view.record.query.text === example.text
      && view.record.response.called_model === false && view.record.answer === null
      && view.text.includes('未生成调查参考') && view.record.context.cases.length > 0);
    check(`${example.query_id} source is expandable`, await evaluate(`
      document.querySelector('.context-case').open = true;
      document.querySelector('.context-case').innerText.includes('Evidence（检查结果）')`));
  }
  check('only search requests were sent', await evaluate(
    'window.__requestUrls.length === 3 && window.__requestUrls.every(url => url === "/search")'));
  check('negative evidence survives rendering', await evaluate(
    'resultBody.textContent.includes("未发现碰伤、缺球或掉球痕迹")'));
  await screenshot('02-search-evidence.png');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  check('narrow viewport has no horizontal overflow', await evaluate(
    'document.documentElement.scrollWidth <= window.innerWidth'));
  await screenshot('02-search-mobile.png');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await evaluate('queryInput.value = "zzzxqv987654"; form.requestSubmit(submitButton);');
  await waitUntil('!inFlight');
  check('real empty search is distinct from generation failure', await evaluate(
    'statusLine.dataset.kind === "warning" && resultBody.textContent.includes("没有检索候选") && !resultBody.textContent.includes("采用的历史案例")'));
  await execute(`window.fetch = async function () {
      return {ok: true, json: async function () {return {configured: true};}};
    }; await loadModelStatus();`);
  check('configured model enables explicit generation action', await evaluate('!generateButton.disabled'));
  await evaluate(`
    window.__runResponse = async function (httpStatus, body) {
      window.fetch = async function () {
        return {status: httpStatus, text: async function () {return JSON.stringify(body);}};
      };
      queryInput.value = body.record?.query?.text || '浏览器离线回归';
      await submitRequest({preventDefault: function () {}, submitter: generateButton});
      return {kind: statusLine.dataset.kind, visible: downloadButton.getClientRects().length > 0,
        href: downloadButton.getAttribute('href'), busy: submitButton.disabled,
        text: resultBody.textContent};
    };`);
  for (const [name, status, body, kind, visible] of [
    ['success', 200, { status: 'ok', record: saved }, 'ok', true],
    ['422 after success', 422, { detail: [] }, 'error', false],
    ['503 without record', 503, { status: 'service_unavailable', record: null }, 'error', false],
  ]) {
    const view = await evaluate(`window.__runResponse(${status}, ${JSON.stringify(body)})`);
    check(name, view.kind === kind && view.visible === visible && !view.busy);
  }
  const nullable = structuredClone(saved);
  nullable.answer.insufficiency = null;
  const nullableView = await evaluate(`window.__runResponse(200, ${JSON.stringify({status: 'ok', record: nullable})})`);
  check('valid nullable insufficiency', nullableView.kind === 'ok' && nullableView.visible);
  await evaluate(`window.__runResponse(200, ${JSON.stringify({status: 'ok', record: saved})})`);
  const firstAdopted = saved.answer.case_answers[0];
  const firstContext = saved.context.cases.find(value => value.case_id === firstAdopted.case_id);
  check('overview precedes collapsed case descriptions', await evaluate(`
    document.querySelector('.case-overview tbody').rows.length === ${saved.answer.case_answers.length}
    && document.querySelectorAll('.case-detail[open]').length === 0
    && !document.querySelector('.retrieval-audit').open
    && document.querySelector('#result-body > section > h3').textContent.includes('采用的历史案例')`));
  check('overview uses corresponding historical product, lots and detection facts', await evaluate(`
    document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstAdopted.case_id)})
    && document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstContext.details[0].product_id)})
    && document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstContext.details[0].production_lot)})
    && document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstContext.details[0].customer_lot)})
    && document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstContext.details[0].detection_stage)})
    && document.querySelector('.case-overview tbody tr').textContent.includes(${JSON.stringify(firstContext.case.abnormal_description)})`));
  await screenshot('02-answer-overview.png');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  check('narrow answer table scrolls inside its region', await evaluate(`
    document.documentElement.scrollWidth <= window.innerWidth
    && document.querySelector('.table-scroll').scrollWidth > document.querySelector('.table-scroll').clientWidth`));
  await screenshot('02-answer-mobile.png');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  await evaluate('document.querySelector(".case-link").click()');
  check('case link opens only its matching description', await evaluate(`
    document.querySelectorAll('.case-detail[open]').length === 1
    && document.querySelector('.case-detail[open] > summary').textContent.includes(${JSON.stringify(firstAdopted.case_id)})
    && document.querySelector('.case-detail[open]').innerText.includes(${JSON.stringify(firstAdopted.relevance_reason)})
    && document.querySelector('.case-detail[open]').innerText.includes('当前侧已知事实')
    && document.querySelector('.case-detail[open]').innerText.includes('历史侧事实')`));
  await screenshot('02-answer-detail.png');
  await evaluate(`document.querySelector('.sources').open = true;
    document.querySelector('.sources .context-case').open = true;`);
  check('generated answer source fields are locatable', await evaluate(
    'document.querySelector(".sources").innerText.includes("checkpoint:") && document.querySelector(".sources").innerText.includes("Evidence（检查结果）")'));
  await screenshot('02-answer-replay.png');
  await evaluate('document.querySelector(".case-detail > summary").click()');
  check('case details can be collapsed again', await evaluate('document.querySelectorAll(".case-detail[open]").length === 0'));
  for (const [name, change] of [
    ['empty record', () => ({})],
    ['missing answer', (record) => { delete record.answer; }],
    ['missing case_answers', (record) => { delete record.answer.case_answers; }],
    ['invalid ranking', (record) => { record.ranking = null; }],
    ['missing context', (record) => { delete record.context; }],
    ['mismatched status', (record) => { record.status = 'model_failed'; }],
  ]) {
    const record = structuredClone(saved);
    const changed = change(record) || record;
    const view = await evaluate(`window.__runResponse(200, ${JSON.stringify({status: 'ok', record: changed})})`);
    check(name, view.kind === 'error' && !view.visible && !view.href && !view.busy
      && !view.text.includes('本次未采用历史案例'));
  }
  const zero = structuredClone(saved);
  zero.answer.case_answers = [];
  zero.answer.current_gaps = ['未提供异常现象和位置，无法比较历史异常。'];
  zero.answer.insufficiency = '候选不足以支持调查参考。';
  const zeroView = await evaluate(`window.__runResponse(200, ${JSON.stringify({status: 'ok', record: zero})})`);
  check('insufficient support with hits is distinct from no_hits', zeroView.kind === 'warning' && zeroView.visible
    && zeroView.text.includes('未提供异常现象和位置')
    && zeroView.text.includes('本次未采用历史案例'));
  const noHits = structuredClone(zero);
  noHits.status = 'no_hits';
  noHits.ranking = [];
  noHits.context.cases = [];
  const noHitsView = await evaluate(`window.__runResponse(200, ${JSON.stringify({status: 'no_hits', record: noHits})})`);
  check('valid no_hits', noHitsView.kind === 'warning' && noHitsView.visible);
  const badNoHits = await evaluate('window.__runResponse(200, {status: "no_hits", record: {}})');
  check('incomplete no_hits', badNoHits.kind === 'error' && !badNoHits.visible);
  for (const status of ['model_failed', 'format_failed', 'citation_failed']) {
    const failed = {...structuredClone(saved), status, answer_text: 'DO_NOT_RENDER_MODEL_TEXT'};
    const view = await evaluate(`window.__runResponse(502, ${JSON.stringify({status, message: status, record: failed})})`);
    check(`${status} is a failure without exposing model output`, view.kind === 'error' && view.visible
      && !view.text.includes('DO_NOT_RENDER_MODEL_TEXT') && !view.text.includes('采用的历史案例'));
  }
  await execute(`window.fetch = async function () {throw new Error('offline');};
    await submitRequest({preventDefault: function () {}, submitter: generateButton});`);
  check('network failure clears stale result and download', await evaluate(
    'statusLine.dataset.kind === "error" && downloadButton.hidden && !inFlight && !resultBody.textContent.includes("采用的历史案例")'));
  await execute(`window.fetch = async function () {return {status: 200, text: async function () {return 'bad-json';}};};
    await submitRequest({preventDefault: function () {}, submitter: generateButton});`);
  check('invalid JSON is explicit', await evaluate('statusLine.dataset.kind === "error" && downloadButton.hidden && !inFlight'));
  await execute(`window.fetch = async function () {return {ok: true, json: async function () {return {configured: false};}};};
    await loadModelStatus();`);
  check('unconfigured model disables generation while preserving search', await evaluate(
    'generateButton.disabled && !submitButton.disabled && modelStatusLine.textContent.includes("模型生成未配置")'));
  await execute(`window.fetch = async function () {throw new Error('offline');}; await loadModelStatus();`);
  check('unknown configuration keeps search available', await evaluate(
    'generateButton.disabled && !submitButton.disabled && modelStatusLine.textContent.includes("无法读取")'));
  await evaluate(`window.__validationRequests = 0; window.fetch = async function () {window.__validationRequests += 1;};
    queryInput.value = '   '; form.requestSubmit(submitButton);`);
  check('blank query does not send a request', await evaluate(
    'window.__validationRequests === 0 && statusLine.dataset.kind === "error" && !inFlight'));
  await send('Page.navigate', { url: new URL('/settings', pageUrl).href });
  await waitUntil('document.readyState === "complete" && document.getElementById("settings-fields") && !document.getElementById("settings-fields").disabled');
  await waitUntil('!document.getElementById("model-state").textContent.includes("正在")');
  check('settings share the upload page and never accept a key', await evaluate(
    'Boolean(document.getElementById("upload-form")) && !document.querySelector("input[type=password]") && document.getElementById("model-state").textContent.includes("未配置")'));
  await evaluate('document.querySelector("#settings-form details").open = true; topK.value = "1"; maxTokens.value = "2048"; settingsForm.requestSubmit();');
  await waitUntil('!settingsBusy');
  check('settings save returns the effective values', await evaluate(
    'settingsStatus.textContent.includes("已保存：Top K 1") && settingsStatus.textContent.includes("2048")'));
  await screenshot('04-settings-desktop.png');
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  });
  check('settings fit a narrow viewport', await evaluate('document.documentElement.scrollWidth <= window.innerWidth'));
  await screenshot('04-settings-mobile.png');
  await send('Page.reload');
  await waitUntil('document.getElementById("top-k")?.value === "1" && !settingsFields.disabled');
  check('settings survive page reload', await evaluate('maxTokens.value === "2048"'));
  await evaluate('topK.value = "11"; settingsForm.requestSubmit();');
  check('invalid Top K is rejected in the form', await evaluate('!topK.checkValidity() && !settingsBusy'));
  await execute('await readSettings();');
  await evaluate(`window.__settingsFetch = window.fetch;
    window.fetch = async function () {return {ok: false, status: 503, json: async () => ({})};};
    settingsForm.requestSubmit();`);
  await waitUntil('!settingsBusy');
  check('save failure is not reported as success', await evaluate('settingsStatus.textContent.includes("无法确认")'));
  await execute('await readSettings();');
  check('read failure disables settings without showing defaults', await evaluate('settingsFields.disabled && topK.value === "" && maxTokens.value === ""'));
  await execute('window.fetch = window.__settingsFetch; await readSettings();');
  await send('Page.navigate', { url: pageUrl });
  await waitUntil('typeof datasetReady !== "undefined" && datasetReady && document.getElementById("runtime-settings").textContent.includes("Top K 1")');
  await evaluate('queryInput.value = "焊线脱落"; form.requestSubmit(submitButton);');
  await waitUntil('!inFlight');
  const configuredSearch = await evaluate(`(async () => JSON.parse(await (await fetch(downloadButton.href)).text()))()`);
  check('browser search uses the saved Top K', configuredSearch.retrieval.top_k === 1 && configuredSearch.ranking.length === 1);
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
  await evaluate('document.querySelector("#result-body details").open = true;');
  await screenshot('04-search-settings.png');
  await execute(`const response = await fetch('/runtime-settings', {method: 'PUT',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({top_k: 4, max_tokens: 4096})});
    if (!response.ok) throw new Error('Could not restore demo defaults');`);
  console.log(`${checks - failures.length}/${checks} passed; ${failures.length} failed`);
  process.exitCode = failures.length ? 1 : 0;
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  if (socket) socket.close();
  chrome.kill('SIGKILL');
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
