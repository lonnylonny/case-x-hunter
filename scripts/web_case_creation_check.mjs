// 复用浏览器驱动与读取回归，附加新增案例专项；HTTP 夹具不是数据库验收。
process.env.CASETRACE_CHECK_CREATION = '1';
await import('./web_case_browser_check.mjs');
