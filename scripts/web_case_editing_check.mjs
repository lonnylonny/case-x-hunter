// 复用浏览器驱动与读取回归，附加编辑专项；HTTP夹具不是数据库验收。
process.env.CASETRACE_CHECK_EDITING = '1';
await import('./web_case_browser_check.mjs');
