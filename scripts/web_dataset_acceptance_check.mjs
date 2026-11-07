// 真实 FastAPI + 真实 PostgreSQL（隔离 schema）+ 真实 Chromium 的完整资料库验收。
// 只操作本次创建的 schema；不调用模型；不写日常实例。
// Usage: CASETRACE_TEST_DATABASE_URL=... node scripts/web_dataset_acceptance_check.mjs [screenshot-dir]
import assert from 'node:assert/strict';
import {spawn, execFileSync} from 'node:child_process';
import {mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {randomBytes} from 'node:crypto';
import {createServer as createNetServer} from 'node:net';
import {createConnection} from 'node:net';

// 与 casetrace.env 相同的兜底：环境变量优先，其次项目根 .env；只补缺失键，不覆盖。
for (const line of readFileSync(new URL('../.env', import.meta.url), {encoding: 'utf8', flag: 'a+'}).split('\n')) {
  const text = line.trim();
  if (!text || text.startsWith('#') || !text.includes('=')) continue;
  const [key, ...rest] = text.split('=');
  const value = rest.join('=').trim().replace(/^['"]|['"]$/g, '');
  if (key.trim() && process.env[key.trim()] === undefined) process.env[key.trim()] = value;
}
const testUrl = process.env.CASETRACE_TEST_DATABASE_URL;
if (!testUrl) {
  console.error('缺少 CASETRACE_TEST_DATABASE_URL，拒绝在没有独立测试库时运行删除/替换验收。');
  process.exit(2);
}
const screenshotDir = process.argv[2] || process.env.CASETRACE_SCREENSHOT_DIR || '';
const chromePath = process.env.CASETRACE_CHROME_BINARY || '/usr/bin/chromium';
const schema = `casetrace_accept_${randomBytes(4).toString('hex')}`;
const project = new URL('..', import.meta.url).pathname;

function uvRun(args, extraEnv = {}) {
  return spawn('uv', ['run', '--locked', '--no-sync', ...args], {
    cwd: project, env: {...process.env, ...extraEnv}, stdio: ['ignore', 'pipe', 'pipe'],
  });
}
function pickPort() {
  return new Promise((resolve, reject) => {
    const probe = createNetServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const {port} = probe.address();
      probe.close(() => resolve(port));
    });
  });
}
async function waitForHealth(base, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    try {
      const response = await fetch(`${base}/health`);
      if (response.ok) return;
    } catch { /* 服务尚未监听 */ }
    await sleep(200);
  }
  throw new Error('验收 API 未在时限内通过 /health');
}
async function waitForExit(child, timeout = 15000) {
  if (child.exitCode !== null) return;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('子进程未按时退出')), timeout);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

let apiProcess = null, apiLog = '';
function startApi(port) {
  const launcher = [
    'import uvicorn',
    'from casetrace.api import create_app',
    `uvicorn.run(create_app(db_schema=${JSON.stringify(schema)}), host='127.0.0.1', port=${port}, log_level='warning')`,
  ].join('; ');
  apiProcess = uvRun(['python', '-c', launcher], {
    CASETRACE_DATABASE_URL: testUrl,
    CASETRACE_CONTAINER: '',
    DEEPSEEK_API_KEY: '',
  });
  apiLog = '';
  apiProcess.stderr.on('data', data => { apiLog += data.toString(); });
  apiProcess.stdout.on('data', data => { apiLog += data.toString(); });
  return apiProcess;
}

const port = await pickPort();
const base = `http://127.0.0.1:${port}`;
const checks = [];

function finish(label) {
  checks.push(label);
  console.log(`PASS ${label}`);
}

const browserProfile = mkdtempSync(join(tmpdir(), 'casetrace-accept-'));
let chrome = null, socket = null, nextId = 0;
const commands = new Map();
let saves = 0;

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { commands.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    commands.set(id, message => {
      clearTimeout(timer);
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
    });
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function eventually(predicate, label = 'browser condition', timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await predicate()) return;
    await sleep(100);
  }
  throw new Error(`${label} timed out`);
}
const waitUntil = (expression, label) => eventually(() => evaluate(expression), label || expression);
async function check(label, expression) {
  assert.equal(await evaluate(expression), true, label);
  finish(label);
}
async function shot(name, selector) {
  if (!screenshotDir) return;
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block: 'start'})`);
  await sleep(120);
  const data = await send('Page.captureScreenshot', {format: 'png'});
  mkdirSync(screenshotDir, {recursive: true});
  writeFileSync(join(screenshotDir, name), Buffer.from(data.data, 'base64'));
}
async function goto(path) {
  await send('Page.navigate', {url: base + path});
  await waitUntil('document.readyState === "complete"', 'document ready');
}
const libraryReady = () => waitUntil(
  'typeof currentDataset !== "undefined" && currentDataset !== null && !browsing && referenceData !== null && listData !== null',
  'library ready');
const dataset = () => evaluate('(async () => (await fetch("/dataset", {cache: "no-store"})).json())()');

const FILL_DEFINITION = `(() => {
    window.acceptanceFill = (cfg) => {
      const form = document.getElementById('case-edit-form');
      const field = (root, key) => {
        const input = root.querySelector('[data-field="' + key + '"]');
        if (!input) throw new Error('missing field ' + key);
        return input;
      };
      const set = (input, value) => { input.value = value; input.dispatchEvent(new Event('change')); };
      const records = Array.from(form.querySelectorAll('.edit-record'));
      const basic = records[0];
      const details = records.slice(1, 1 + cfg.detailCount);
      const evidences = records.slice(1 + cfg.detailCount);
      set(field(basic, 'abnormal_description'), cfg.description);
      set(field(basic, 'root_cause'), cfg.rootCause);
      set(field(basic, 'corrective_action'), cfg.action);
      const processes = field(basic, 'abnormal_processes');
      Array.from(processes.options).forEach(option => { option.selected = cfg.processes.includes(option.value); });
      set(form.querySelector('[data-field="closure_status"]'), 'confirmed');
      details.forEach((detail, index) => {
        set(field(detail, 'product_id'), cfg.productId);
        set(field(detail, 'production_lot'), cfg.lots[index]);
        set(field(detail, 'customer_lot'), cfg.customerLot);
        set(field(detail, 'production_time'), '2026-06-01');
        set(field(detail, 'detection_time'), '2026-06-0' + (index + 2));
        set(field(detail, 'detection_stage'), cfg.stage);
        const types = field(detail, 'abnormal_types');
        Array.from(types.options).forEach(option => { option.selected = cfg.failureModes.includes(option.value); });
        set(field(detail, 'affected_qty'), '10');
        set(field(detail, 'disposition'), '隔离并复核');
      });
      evidences.forEach((evidence, index) => {
        set(field(evidence, 'checkpoint_type'), 'QC');
        set(field(evidence, 'result'), cfg.description + ' 调查 ' + (index + 1));
        set(field(evidence, 'relevance'), 'related');
      });
      form.querySelectorAll('.edit-origin').forEach((box, index) => {
        set(box.querySelector('[data-field="kind"]'), 'synthetic');
        set(box.querySelector('[data-field="description"]'), '验收人工合成来源说明');
        set(box.querySelector('[data-field="source_location"]'), '验收来源位置 ' + (index + 1));
      });
    };
  })()`;

try {
  console.log(`隔离 schema：${schema}（仅本次创建，结束后删除）`);
  const prepare = uvRun(['python', '-m', 'casetrace.storage', 'prepare', '--test', '--schema', schema],
    {CASETRACE_TEST_DATABASE_URL: testUrl});
  let prepareOut = '';
  prepare.stdout.on('data', data => { prepareOut += data.toString(); });
  prepare.stderr.on('data', data => { prepareOut += data.toString(); });
  const prepareCode = await new Promise(resolve => prepare.once('exit', resolve));
  assert.equal(prepareCode, 0, `准备隔离 schema 失败：${prepareOut}`);
  console.log('PASS 隔离 schema 首次导入九案');
  checks.push('隔离 schema 首次导入九案');

  startApi(port);
  await waitForHealth(base);
  console.log('PASS 隔离 API 健康检查（真实 PostgreSQL，未加载模型）');

  chrome = spawn(chromePath, [
    '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
    '--remote-debugging-port=0', `--user-data-dir=${browserProfile}`, 'about:blank',
  ], {stdio: 'ignore'});
  let launchError;
  chrome.on('error', error => { launchError = error; });
  let devtoolsPort;
  await eventually(() => {
    if (launchError) throw launchError;
    try { devtoolsPort = readFileSync(join(browserProfile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; return Boolean(devtoolsPort); }
    catch { return false; }
  }, 'chromium devtools');
  const target = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (commands.has(message.id)) { commands.get(message.id)(message); commands.delete(message.id); }
  });
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1100, deviceScaleFactor: 1, mobile: false});

  // 首次九案、浏览与筛选
  await goto('/library');
  await libraryReady();
  await check('九案首次载入可按整库计数浏览', 'currentDataset.case_count === 9 && listData.total_count === 9 && counts.textContent.includes("整库 9 案")');
  await shot('01-initial-list.png', '#browser-heading');
  await evaluate('document.getElementById("filter-product").value = referenceData.products[0].product_id; filterForm.requestSubmit()');
  await waitUntil('!browsing', 'filter applied');
  await check('产品筛选有匹配并保留整案计数', 'listData.matched_count >= 1 && listData.items.every(item => item.detail_count >= 1)');
  await evaluate('document.getElementById("clear-filters").click()');
  await waitUntil('!browsing', 'filters cleared');

  // 编辑 → 修订 → 新搜索
  const editTag = `验收修订根因-${Date.now()}`;
  await evaluate('void openCase("C001")');
  await waitUntil('!browsing && currentCase?.case?.case_id === "C001"', 'C001 detail');
  await evaluate('document.getElementById("edit-case").click()');
  await waitUntil('editing && editSession.mode === "edit"', 'edit mode');
  const beforeEditIdentity = await evaluate('currentDataset.snapshot_id');
  await evaluate(`editor.querySelector('[data-field="root_cause"]').value = ${JSON.stringify(editTag)}; document.getElementById("case-edit-form").requestSubmit()`);
  await waitUntil('!editing && currentCase?.case?.root_cause === ' + JSON.stringify(editTag), 'edit saved');
  await check('编辑保存产生新身份与折叠修订', 'currentDataset.snapshot_id !== ' + JSON.stringify(beforeEditIdentity)
    + ' && currentCase.case.root_cause === ' + JSON.stringify(editTag)
    + ' && document.getElementById("case-revisions").textContent.includes("历史结案原因")');
  await shot('02-edited-detail.png', '#case-detail-body');

  await goto('/');
  await waitUntil('typeof datasetReady !== "undefined" && datasetReady', 'search page ready');
  await evaluate(`document.getElementById("query").value = ${JSON.stringify(editTag)}; document.getElementById("answer-form").requestSubmit()`);
  await waitUntil('document.getElementById("result-body").textContent.includes("C001")', 'search shows edited case');
  await check('新搜索读取编辑后的内容', 'document.getElementById("result-body").textContent.includes(' + JSON.stringify(editTag) + ')');

  // 新增 2 明细 / 3 证据
  await goto('/library');
  await libraryReady();
  const template = await evaluate('(async () => (await fetch("/case?case_id=C002&snapshot_id=" + encodeURIComponent(currentDataset.snapshot_id))).json())()');
  await evaluate(FILL_DEFINITION);
  await evaluate('newCaseButton.click()');
  await waitUntil('editing && editSession.mode === "create"', 'create mode');
  await evaluate('editor.querySelector("[data-add=details]").click(); editor.querySelector("[data-add=evidences]").click(); editor.querySelector("[data-add=evidences]").click()');
  const createTag = `验收新增根因-${Date.now()}`;
  const createCfg = {
    description: '验收新增案例：焊线界面异常',
    rootCause: createTag,
    action: '验收新增措施',
    productId: template.details[0].product_id,
    stage: template.details[0].detection_stage,
    failureModes: template.details[0].abnormal_types,
    processes: template.case.abnormal_processes,
    customerLot: 'CL-ACC-1',
    lots: ['0009001', '0009002'],
    detailCount: 2,
  };
  await evaluate(`window.acceptanceFill(${JSON.stringify(createCfg)})`);
  await shot('03-create-form.png', '#case-editor');
  const acceptBody = await evaluate('editSession.collect()');
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await waitUntil('!editing && currentCase?.details?.length === 2', 'creation saved');
  await check('新增 2 明细/3 证据并立即显示详情', 'currentCase.details.length === 2 && currentCase.evidences.length === 3 && currentCase.details[0].production_lot === "0009001" && currentCase.source.web_provenance.origins.length === 6');
  const createdCaseId = await evaluate('currentCase.case.case_id');
  await check('新增后列表与新搜索使用新资料', 'currentDataset.snapshot_id === currentCase.snapshot_id');
  await shot('04-created-detail.png', '#case-detail-body');

  // 重启 API 后数据仍在
  const firstPid = apiProcess.pid;
  apiProcess.kill('SIGTERM');
  await waitForExit(apiProcess);
  apiProcess = null;
  startApi(port);
  await waitForHealth(base);
  await goto('/library');
  await libraryReady();
  await check('重启 API 保留编辑与新增', 'currentDataset.case_count === 10');
  await evaluate(`void openCase(${JSON.stringify(createdCaseId)})`);
  await waitUntil('!browsing && currentCase?.case?.case_id === ' + JSON.stringify(createdCaseId), 'created case after restart');
  await check('重启后新增案详情与新搜索一致', 'currentCase.case.root_cause === ' + JSON.stringify(createTag));

  // 旧身份的保存被拒绝
  await evaluate('void openCase("C002")');
  await waitUntil('!browsing && currentCase?.case?.case_id === "C002"', 'C002 detail');
  await evaluate('document.getElementById("edit-case").click()');
  await waitUntil('editing && editSession.mode === "edit"', 'edit C002');
  await evaluate('editor.querySelector(\'[data-field="root_cause"]\').value = "过期的 C002 修改"');
  const conflictStatus = await evaluate(`(async () => {
    const body = {...${JSON.stringify(acceptBody)}, expected_snapshot_id: currentDataset.snapshot_id};
    const response = await fetch('/cases', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    return response.status;
  })()`);
  assert.equal(conflictStatus, 201, '并发写入应先成功');
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await waitUntil('editSession && editSession.blocked', 'stale save blocked');
  await check('旧身份保存被 409 拒绝并保留草稿', 'editSession.blocked && document.getElementById("save-case").disabled && editor.querySelector("[data-field=root_cause]").value === "过期的 C002 修改"');
  await shot('05-conflict-draft.png', '#case-editor');
  await evaluate('window.confirm = () => true; document.getElementById("cancel-edit").click()');

  // 删除：三成员组合法删一案，两成员组被拦
  await goto('/library');
  await libraryReady();
  await evaluate('void openCase("C001")');
  await waitUntil('!browsing && currentCase?.case?.case_id === "C001"', 'C001 for delete');
  const beforeDelete = await evaluate('currentDataset.case_count');
  await evaluate('deleteCaseButton.click()');
  await check('删除确认展示影响与剩余成员数', 'deleting && deletePanel.textContent.includes("确认删除案例 C001") && deletePanel.textContent.includes("当前成员 3 案") && deletePanel.textContent.includes("删除后剩余 2 案") && deletePanel.textContent.includes("不提供回收站")');
  await shot('06-delete-confirm.png', '#case-delete');
  await evaluate('document.getElementById("confirm-delete").click()');
  await waitUntil(`!deleting && !browsing && currentDataset !== null && currentDataset.case_count === ${beforeDelete - 1}`, 'delete succeeded');
  await check('合法删除刷新列表与计数', `currentDataset.case_count === ${beforeDelete - 1} && browserStatus.textContent.includes("已删除")`);
  await evaluate('void openCase("C003")');
  await waitUntil('!browsing && currentCase?.case?.case_id === "C003"', 'C003 detail');
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await eventually(() => evaluate('document.getElementById("delete-errors")?.textContent.includes("CR-38") || false'), 'CR-38 error');
  await check('两成员组删除被业务规则拦住', 'deleting && document.getElementById("delete-errors").textContent.includes("CR-38") && currentDataset.case_count >= 1');
  await shot('07-delete-blocked.png', '#case-delete');
  await evaluate('document.getElementById("cancel-delete").click()');

  // CSV 单案 → 删空 → 空库状态
  const upload = await evaluate(`(async () => {
    const csv = await (await fetch('/dataset/example')).blob();
    const params = new URLSearchParams({dataset_name: '验收单案 CSV', known_at: '2026-09-15',
      basis: '人工合成验收整库声明', filename: 'example.csv', expected_snapshot_id: currentDataset.snapshot_id});
    const response = await fetch('/dataset?' + params, {method: 'POST', headers: {'Content-Type': 'text/csv'}, body: csv});
    return {status: response.status, body: await response.json()};
  })()`);
  assert.equal(upload.status, 200, JSON.stringify(upload.body));
  assert.equal(upload.body.dataset.case_count, 1, 'CSV 单案载入');
  await goto('/library');
  await libraryReady();
  await evaluate('void openCase("UPLOAD_C001")');
  await waitUntil('!browsing && currentCase?.case?.case_id === "UPLOAD_C001"', 'CSV case detail');
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await waitUntil('!deleting && !browsing && currentDataset !== null && currentDataset.case_count === 0', 'empty after delete');
  await check('删空后界面进入空库状态并提供三个入口', 'browserStatus.textContent.includes("资料库为空") && !newCaseButton.disabled && !uploadButton.disabled && !restoreButton.disabled');
  await shot('08-empty-library.png', '#browser-heading');
  await goto('/');
  await waitUntil('typeof datasetReady !== "undefined" && datasetReady', 'search page ready on empty');
  await evaluate('document.getElementById("query").value = "焊线脱落"; document.getElementById("answer-form").requestSubmit()');
  await waitUntil('document.getElementById("result-body").textContent.includes("当前资料库为空")', 'empty search state');
  await check('空库搜索为业务状态、可进入资料库且无下载记录', 'document.getElementById("status").textContent.includes("empty_dataset") && document.getElementById("download-button").hidden && Array.from(document.querySelectorAll("a")).some(a => a.getAttribute("href") === "/library")');
  await shot('09-empty-search.png', '#result-body');

  // 空库 → 新增 → 恢复搜索
  const emptyCreateTag = `空库新增根因-${Date.now()}`;
  await goto('/library');
  await libraryReady();
  await evaluate('newCaseButton.click()');
  await waitUntil('editing && editSession.mode === "create"', 'empty create mode');
  await evaluate(FILL_DEFINITION);
  await evaluate(`window.acceptanceFill(${JSON.stringify({
    ...createCfg, rootCause: emptyCreateTag, customerLot: 'CL-EMPTY-1',
    lots: ['0007001', '0007002'], detailCount: 1,
  })})`);
  await evaluate('document.getElementById("case-edit-form").requestSubmit()');
  await waitUntil('!editing && currentCase?.details?.length >= 1', 'first case from empty');
  await check('空库可用新增创建首案', 'currentDataset.case_count === 1 && currentCase.details.length >= 1');
  await goto('/');
  await waitUntil('typeof datasetReady !== "undefined" && datasetReady', 'search ready after empty create');
  await evaluate(`document.getElementById("query").value = ${JSON.stringify(emptyCreateTag)}; document.getElementById("answer-form").requestSubmit()`);
  await waitUntil('document.getElementById("result-body").textContent.includes("检索") || document.getElementById("result-body").textContent.includes("候选")', 'search after empty create');
  await check('空库新增后恢复搜索', 'document.getElementById("status").textContent.includes("retrieved") || document.getElementById("result-body").textContent.includes(' + JSON.stringify(emptyCreateTag) + ')');

  // 再次删空 → CSV → 删空 → 恢复九案
  await goto('/library');
  await libraryReady();
  await evaluate('void openCase("' + createdCaseId + '")').catch(() => {});
  await evaluate('(async () => { const id = listData.items[0].case_id; await openCase(id); })()');
  await waitUntil('!browsing && currentCase !== null', 'current case to delete');
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await waitUntil('!deleting && !browsing && currentDataset !== null && currentDataset.case_count === 0', 'empty again');
  await check('空库新增的案例可再次删空', 'currentDataset.case_count === 0');
  const uploadToEmpty = await evaluate(`(async () => {
    const csv = await (await fetch('/dataset/example')).blob();
    const params = new URLSearchParams({dataset_name: '验收空库 CSV', known_at: '2026-09-15',
      basis: '人工合成验收整库声明', filename: 'example.csv', expected_snapshot_id: currentDataset.snapshot_id});
    const response = await fetch('/dataset?' + params, {method: 'POST', headers: {'Content-Type': 'text/csv'}, body: csv});
    return response.status;
  })()`);
  assert.equal(uploadToEmpty, 200, '空库 CSV 导入');
  await goto('/library');
  await libraryReady();
  await check('空库可导入 CSV 单案', 'currentDataset.case_count === 1');
  await evaluate('(async () => { const id = listData.items[0].case_id; await openCase(id); })()');
  await waitUntil('!browsing && currentCase !== null', 'CSV case for delete');
  await evaluate('deleteCaseButton.click(); document.getElementById("confirm-delete").click()');
  await waitUntil('!deleting && !browsing && currentDataset !== null && currentDataset.case_count === 0', 'empty after CSV delete');
  await check('空库 CSV 案例可再次删空', 'currentDataset.case_count === 0');
  await evaluate('window.confirm = () => true; restoreButton.click()');
  await waitUntil('!uploading && !browsing && currentDataset !== null && currentDataset.case_count === 9', 'restore nine');
  await check('空库可恢复九案完整演示库', 'currentDataset.case_count === 9 && currentDataset.detail_count === 9 && currentDataset.evidence_count === 9');
  await shot('10-restored-nine.png', '#browser-heading');

  // 再次重启：恢复后的九案仍在
  apiProcess.kill('SIGTERM');
  await waitForExit(apiProcess);
  apiProcess = null;
  startApi(port);
  await waitForHealth(base);
  await goto('/library');
  await libraryReady();
  await check('重启后恢复的九案与身份保留', 'currentDataset.case_count === 9 && listData.total_count === 9');
  const finalDataset = await dataset();
  assert.equal(finalDataset.case_count, 9);
  console.log(`最终资料身份：${finalDataset.snapshot_id}`);
  finish('真实 API/浏览器闭环完成（无模型调用）');
} catch (error) {
  console.error(error);
  if (apiLog) console.error(`API 日志（尾部）：\n${apiLog.split('\n').slice(-20).join('\n')}`);
  process.exitCode = 1;
} finally {
  if (socket) socket.close();
  if (chrome) chrome.kill('SIGKILL');
  if (apiProcess) {
    apiProcess.kill('SIGTERM');
    try { await waitForExit(apiProcess, 5000); } catch { apiProcess.kill('SIGKILL'); }
  }
  rmSync(browserProfile, {recursive: true, force: true});
  try {
    execFileSync('uv', ['run', '--locked', '--no-sync', 'python', '-c',
      `import os, psycopg\nconn = psycopg.connect(os.environ["CASETRACE_TEST_DATABASE_URL"], autocommit=True)\nconn.execute("DROP SCHEMA IF EXISTS ${schema} CASCADE")\nconn.close()\n`],
      {cwd: project, env: process.env, stdio: 'ignore'});
    console.log(`PASS 已删除本次隔离 schema ${schema}`);
  } catch (error) {
    console.error(`隔离 schema 清理失败；请手工执行：DROP SCHEMA IF EXISTS ${schema} CASCADE;（${error.message}）`);
  }
  if (process.exitCode) console.error('验收未通过：以上失败项不得写成通过。');
  else console.log(`${checks.length} 项真实实例验收检查通过（真实 PostgreSQL + 真实 API + Chromium，未调用模型）`);
}
