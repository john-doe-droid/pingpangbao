/**
 * P6 改动专项实测：入口迁移、欢迎弹窗、报表收紧、任务可见性、新导航名。
 *
 * 这些改动大多是"行为规则"层面的，单元测试能覆盖权限，
 * 但"登录后到底弹不弹、弹几次、按钮在不在正确的位置"必须用真实浏览器确认。
 *
 * 用法：node ui_p6_check.js [基地址]
 * 退出码：0 全部通过 / 1 有失败项
 */
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8011';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PASS = 'DemoPassw0rd!2026';

const results = [];
const ok = (n, d = '') => results.push(['PASS', n, d]);
const bad = (n, d) => results.push(['FAIL', n, d]);

async function login(page, user) {
  await page.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
  await page.waitForSelector('input[name="username"]', { state: 'visible' });
  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', PASS);
  await Promise.all([
    page.waitForLoadState('networkidle'),
    page.click('#main-content form button[type="submit"]'),
  ]);
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });

  // ---------- 1. 登录后弹一次，且只弹一次 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-MEMBER2');

    const dlgOpen = await page.evaluate(() => {
      const d = document.getElementById('ppWelcome');
      return d ? d.open : false;
    });
    if (dlgOpen) ok('登录后弹出使用说明');
    else bad('登录后弹出使用说明', '未找到打开的欢迎对话框');

    // 内容应面向干事，且不出现管理员才需要的话
    const text = await page.evaluate(() => {
      const d = document.getElementById('ppWelcome');
      return d ? d.innerText : '';
    });
    if (text.includes('我的任务')) ok('弹窗内容按身份（干事）', text.slice(0, 40).replace(/\n/g, ' '));
    else bad('弹窗内容按身份（干事）', text.slice(0, 80));

    // 点「知道了」关闭
    await page.click('#ppWelcomeOk');
    await page.waitForTimeout(220);
    const closed = await page.evaluate(() => {
      const d = document.getElementById('ppWelcome');
      return d ? d.open : false;
    });
    if (!closed) ok('点「知道了」可关闭弹窗');
    else bad('点「知道了」可关闭弹窗', '对话框仍处于打开状态');

    // 跳转到别的页面，不应再弹（只弹一次）
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const again = await page.evaluate(() => !!document.getElementById('ppWelcome'));
    if (!again) ok('后续页面不再弹（只弹一次）');
    else bad('后续页面不再弹（只弹一次）', '又出现了欢迎对话框');

    await ctx.close();
  }

  // ---------- 2. 管理员弹窗内容不同 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    const text = await page.evaluate(() => {
      const d = document.getElementById('ppWelcome');
      return d ? d.innerText : '';
    });
    if (text.includes('管理员')) ok('管理员看到的说明不同', text.slice(0, 40).replace(/\n/g, ' '));
    else bad('管理员看到的说明不同', text.slice(0, 80));
    await ctx.close();
  }

  // ---------- 3. 提交入口在「我的任务」，不在「我的工作记录」 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-MEMBER1');

    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const taskPageHasSubmit = await page.evaluate(() =>
      !!document.querySelector('a[href*="/my/submissions/new/"]'));
    const taskPageShowsQuota = await page.evaluate(() =>
      /\d+\s*\/\s*\d+/.test(document.body.innerText));
    if (taskPageHasSubmit) ok('「我的任务」有提交入口');
    else bad('「我的任务」有提交入口', '未找到提交成果按钮');
    if (taskPageShowsQuota) ok('「我的任务」显示已交/上限');
    else bad('「我的任务」显示已交/上限', '未找到 x / y 形式额度');

    await page.goto(BASE + '/my/submissions/', { waitUntil: 'networkidle' });
    const recPageHasSubmit = await page.evaluate(() =>
      !!document.querySelector('a[href*="/my/submissions/new/"]'));
    if (!recPageHasSubmit) ok('「我的工作记录」不再有提交入口（只留一处）');
    else bad('「我的工作记录」不再有提交入口（只留一处）', '仍存在提交按钮');

    // 任务详情页也应能直接提交
    // 任务详情链接：模板里任务名本身就是链接（pp-cell-primary）。
    // 用 href 前缀匹配，不依赖具体 class。
    // 注意：上一步已经导航到「我的工作记录」，这里必须先回到「我的任务」，
    // 否则在错误的页面上找链接，必然找不到（第一版就犯了这个错）。
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    // 必须挑一个"还有剩余额度"的任务：额度用满时按钮会变成「查看记录」，
    // 那是正确行为，不能当成缺陷（这条断言第一版就误判过）。
    const withSubmit = await page.evaluate(() => {
      const a = [...document.querySelectorAll('a')]
        .find(x => (x.getAttribute('href') || '').startsWith('/my/submissions/new/'));
      if (!a) return null;
      // 从该行的任务名链接反推出详情页地址
      const row = a.closest('tr');
      const t = row && row.querySelector('a[href^="/my/tasks/mine/"]');
      return t ? t.getAttribute('href') : null;
    });
    if (withSubmit) {
      await page.goto(BASE + withSubmit, { waitUntil: 'networkidle' });
      const hasBtn = await page.evaluate(() =>
        !!document.querySelector('a[href*="/my/submissions/new/"]'));
      if (hasBtn) ok('任务详情页有提交入口', withSubmit);
      else bad('任务详情页有提交入口', '详情页未出现提交成果按钮');
    } else {
      bad('任务详情页有提交入口', '当前没有剩余额度的任务（测试数据已用尽）');
    }
    await ctx.close();
  }

  // ---------- 4. 导航：无「统计报表」给干事，有「审核」 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-MEMBER2');
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const navText = await page.evaluate(() =>
      document.getElementById('ppNav').innerText.replace(/\s+/g, ' '));
    if (!navText.includes('统计报表')) ok('干事导航里没有统计报表', navText.slice(0, 60));
    else bad('干事导航里没有统计报表', navText);
    if (navText.includes('审核')) ok('导航里有「审核」（已改名）', navText.slice(0, 60));
    else bad('导航里有「审核」（已改名）', navText);
    if (!navText.includes('工作类型')) ok('顶层导航不再单独列「工作类型」');
    else bad('顶层导航不再单独列「工作类型」', navText);
    await ctx.close();
  }

  // ---------- 5. 管理员导航仍有报表 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const navText = await page.evaluate(() =>
      document.getElementById('ppNav').innerText.replace(/\s+/g, ' '));
    if (navText.includes('统计报表')) ok('管理员导航仍有统计报表');
    else bad('管理员导航仍有统计报表', navText);
    await ctx.close();
  }

  // ---------- 6. 工作台不再有「系统能做什么」 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    const body = await page.evaluate(() => document.body.innerText);
    if (!body.includes('系统能做什么')) ok('工作台已移除能力说明清单');
    else bad('工作台已移除能力说明清单', '仍存在"系统能做什么"');
    if (!body.includes('工程底座')) ok('工作台不再出现工程术语');
    else bad('工作台不再出现工程术语', '仍出现"工程底座"');
    await ctx.close();
  }

  // ---------- 7. 干事看比赛详情：只看到自己的任务 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-MEMBER2');
    await page.goto(BASE + '/competitions/', { waitUntil: 'networkidle' });
    const compHref = await page.evaluate(() => {
      const a = [...document.querySelectorAll('a')]
        .find(x => /\/competitions\/\d+\/$/.test(x.getAttribute('href') || ''));
      return a ? a.getAttribute('href') : null;
    });
    if (!compHref) {
      bad('干事看比赛详情只显示自己的任务', '找不到比赛详情链接');
    } else {
      await page.goto(BASE + compHref, { waitUntil: 'networkidle' });
      const body = await page.evaluate(() => document.body.innerText);
      // MEMBER2 只被分配了「制作赛前海报」
      if (body.includes('制作赛前海报') && !body.includes('撰写赛后推文')) {
        ok('干事看比赛详情只显示自己的任务');
      } else {
        bad('干事看比赛详情只显示自己的任务',
          `海报=${body.includes('制作赛前海报')} 推文=${body.includes('撰写赛后推文')}`);
      }
    }
    await ctx.close();
  }

  await browser.close();

  const failed = results.filter(r => r[0] === 'FAIL');
  console.log('='.repeat(72));
  for (const [st, n, d] of results) {
    console.log(`${st === 'PASS' ? '✅' : '❌'} ${n}${d ? ' —— ' + d : ''}`);
  }
  console.log('='.repeat(72));
  console.log(`通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  process.exit(failed.length ? 1 : 0);
})();
