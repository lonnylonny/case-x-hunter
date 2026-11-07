// 真实 Chromium 缓存升级回归：HTTP 夹具模拟已缓存的旧页，页面/资源来自当前 FastAPI。
// 数据读取为固定夹具；不连接用户数据库，不调用模型，不保存案例。
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';

const project = fileURLToPath(new URL('../', import.meta.url));
const api = spawn(join(project, '.venv/bin/python'), ['-c', `
import socket
import uvicorn
sock = socket.socket()
sock.bind(('127.0.0.1', 0))
print(sock.getsockname()[1], flush=True)
uvicorn.Server(uvicorn.Config('casetrace.api:app', log_level='error')).run(sockets=[sock])
`], {cwd: project, stdio: ['ignore', 'pipe', 'pipe']});
let apiPort, apiError = '';
api.stdout.on('data', data => { if (!apiPort) apiPort = Number(data.toString().trim().split('\n')[0]); });
api.stderr.on('data', data => { apiError += data.toString(); });
let legacy = true, checks = 0;
const hits = new Map();
const dataset = {name: '缓存升级只读夹具', snapshot_id: 'cache-fixture', known_at: '2026-09-15',
  basis: '人工合成测试资料', case_count: 9, detail_count: 9, evidence_count: 9, source_type: 'builtin_demo',
  review_status: 'draft_pending_human_review', updated_at: '2026-11-06', is_upload: false};
function json(res, body) { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); }
const server = createServer(async (req, res) => {
  hits.set(req.url, (hits.get(req.url) || 0) + 1);
  const path = new URL(req.url, 'http://localhost').pathname;
  if (legacy && ['/', '/settings'].includes(path)) {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'public, max-age=86400');
    res.end(`<!DOCTYPE html><html><body><h1>${path === '/' ? '旧版搜索' : '旧版设置'}</h1><nav><a href="/">查询</a><a href="/settings">设置</a></nav><p>当前数据集、下载 CSV 模板、上传并整体替换</p></body></html>`);
    return;
  }
  if (path === '/dataset') { json(res, dataset); return; }
  if (path === '/cases') { json(res, {dataset, items: [], total_count: 9, matched_count: 0, page: 1, page_size: 20}); return; }
  if (path === '/dataset/reference') {
    json(res, {snapshot_id: dataset.snapshot_id, products: [], customers: {}, product_families: {}, failure_modes: [], processes: {}, route_processes: {}, detection_stages: [], checkpoint_types: [], relevance_values: []}); return;
  }
  if (path === '/search') { json(res, {status: 'empty_dataset', message: '当前资料库为空：请先到案例资料库新增案例、导入 CSV 或恢复完整演示数据；本次未构建检索，也未调用模型。', record: null}); return; }
  if (path === '/model-status') { json(res, {configured: false}); return; }
  if (path === '/runtime-settings') { json(res, {top_k: 4, max_tokens: 4096}); return; }
  try {
    const response = await fetch(`http://127.0.0.1:${apiPort}${req.url}`, {headers: req.headers});
    res.statusCode = response.status;
    for (const [key, value] of response.headers) {
      if (!['connection', 'transfer-encoding', 'content-length', 'content-encoding'].includes(key)) res.setHeader(key, value);
    }
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch { res.writeHead(502); res.end(); }
});
const profile = mkdtempSync(join(tmpdir(), 'casetrace-cache-'));
let chrome, socket, launchError, nextId = 0;
const commands = new Map();
async function eventually(predicate) {
  for (let i = 0; i < 150; i++) { if (await predicate()) return; await sleep(100); }
  throw new Error('Browser cache condition timed out');
}
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { commands.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    commands.set(id, message => { clearTimeout(timer); message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result); });
    socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
  if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
  return response.result.value;
}
async function check(name, expression) {
  assert.equal(await evaluate(expression), true, name); checks++; console.log(`PASS ${name}`);
}
try {
  await eventually(async () => {
    if (api.exitCode !== null) throw new Error(`Temporary API failed: ${apiError}`);
    if (!apiPort) return false;
    try { return (await fetch(`http://127.0.0.1:${apiPort}/health`)).ok; } catch { return false; }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  chrome = spawn(process.env.CASETRACE_CHROME_BINARY || '/usr/bin/chromium', ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], {stdio: 'ignore'});
  chrome.on('error', error => { launchError = error; });
  let port;
  await eventually(() => {
    if (launchError) throw launchError;
    try { port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; return Boolean(port); } catch { return false; }
  });
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method: 'PUT'})).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  socket.addEventListener('message', event => { const message = JSON.parse(event.data); if (commands.has(message.id)) { commands.get(message.id)(message); commands.delete(message.id); } });
  await send('Page.enable');
  async function go(path, expectedHeading) {
    await send('Page.navigate', {url: base + path});
    await eventually(() => evaluate(`document.querySelector('h1')?.textContent === ${JSON.stringify(expectedHeading)}`));
  }
  await go('/settings', '旧版设置');
  await go('/', '旧版搜索');
  legacy = false;
  await go('/?ui_probe=fresh', '搜索历史案例');
  await check('fresh URL gets the new page', '!!document.getElementById("manage-cases")');
  const oldSettingsHits = hits.get('/settings');
  await go('/settings', '旧版设置');
  assert.equal(hits.get('/settings'), oldSettingsHits, 'old unversioned URL actually came from browser cache');
  console.log('REPRODUCED: navigation to the old URL reuses the cached old page');
  checks++;
  await go('/?refresh_ui=1', '搜索历史案例');
  await go('/settings', '设置');
  await check('cache repair replaces the already cached settings page', 'document.querySelectorAll(".page-header nav a").length===3 && !document.getElementById("upload-form")');
  await go('/', '搜索历史案例');
  await check('ordinary startup URL is new after cache repair', '!!document.getElementById("manage-cases")');
  await eventually(() => evaluate('typeof datasetReady !== "undefined" && datasetReady'));
  await evaluate('document.getElementById("query").value="焊线脱落"; document.getElementById("answer-form").requestSubmit()');
  await eventually(() => evaluate('document.getElementById("result-body").textContent.includes("当前资料库为空")'));
  await check('empty dataset is a business state with a library entry', 'document.getElementById("status").textContent.includes("empty_dataset") && Array.from(document.querySelectorAll("a")).some(a => a.getAttribute("href") === "/library") && document.getElementById("download-button").hidden');
  for (const [path, heading] of [['/settings', '设置'], ['/', '搜索历史案例'], ['/library', '案例资料库'], ['/', '搜索历史案例']]) {
    await evaluate(`Array.from(document.querySelectorAll('nav a')).find(link=>link.pathname===${JSON.stringify(path)}).click()`);
    await eventually(() => evaluate(`document.querySelector('h1')?.textContent===${JSON.stringify(heading)}`));
    await check(`navigation stays current at ${path}`, 'new URLSearchParams(location.search).has("ui") && document.querySelectorAll(".page-header nav a").length===3');
  }
  await check('loaded assets use the same UI content version', 'Array.from(document.querySelectorAll("script[src],link[rel=stylesheet]")).every(el=>new URL(el.src||el.href).searchParams.get("ui")===new URLSearchParams(location.search).get("ui"))');
  console.log(`${checks} browser cache upgrade checks passed (real FastAPI pages, synthetic read APIs)`);
} catch (error) { console.error(error); process.exitCode = 1; }
finally {
  if (socket) socket.close();
  if (chrome) chrome.kill('SIGKILL');
  api.kill('SIGTERM');
  server.closeAllConnections(); server.close();
  await sleep(300); rmSync(profile, {recursive: true, force: true});
}
