'use strict';

// 三个原生页面共用的 Tabler 样式与可访问性行为；不接触业务状态。
const CaseUI = {
  focus(target) {
    if (!target) return;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    target.tabIndex = -1;
    target.focus({preventScroll: true});
    target.scrollIntoView({block: 'start', behavior: 'instant'});
  },
  label(value) {
    return ({confirmed: '已确认原因（confirmed）', NDF: '未确认原因（NDF）',
      related: '相关（related，仍需核对支持程度）', uncertain: '待确认（uncertain）',
      unrelated: '不相关（unrelated）', same_abnormal_process: '相同异常站点',
      same_root_cause: '相同历史原因', same_failure_mode: '相同异常类型',
      draft_pending_human_review: '待人工审阅'})[value] || value;
  },
  date(value) { return typeof value === 'string' ? value.replace('T', ' ').replace(/(\.\d+)?(Z|\+00:00)$/, ' UTC') : value; },
  enhance(root) {
    const decorate = element => {
      if (element.matches('button, a.button')) {
        element.classList.add('btn');
        if (!element.matches('.case-link, .error-link, .source-link, .nav-link')) {
          element.classList.add(element.classList.contains('secondary') ? 'btn-outline-secondary' : 'btn-primary');
        }
      }
      if (element.matches('select')) element.classList.add('form-select');
      if (element.matches('textarea, input:not([type="checkbox"]):not([type="radio"])')) element.classList.add('form-control');
      if (element.matches('input[type="checkbox"], input[type="radio"]')) element.classList.add('form-check-input');
      if (element.matches('table')) element.classList.add('table', 'table-vcenter');
    };
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    decorate(root);
    root.querySelectorAll('button, a.button, input, select, textarea, table').forEach(decorate);
  },
};
CaseUI.enhance(document.body);
new MutationObserver(records => {
  for (const record of records) for (const added of record.addedNodes) CaseUI.enhance(added);
}).observe(document.body, {childList: true, subtree: true});
