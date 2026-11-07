'use strict';

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
