/**
 * P6/P7 UI 实测：角色化首页、统一工作区、报表收紧与任务可见性。
 *
 * 这些改动大多是"行为规则"层面的，单元测试能覆盖权限，
 * 但"登录后落到哪个页面、按钮在不在正确位置"必须用真实浏览器确认。
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

  // ---------- 0. 登录字段统一使用“账号” ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
    const label = await page.locator('label[for="id_username"]').innerText();
    if (label.includes('账号') && !label.includes('学号')) ok('登录字段使用「账号」');
    else bad('登录字段使用「账号」', label);
    await ctx.close();
  }

  // ---------- 1. 干事登录后直达“我的工作”，不再弹说明 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-MEMBER2');

    if (page.url().includes('/my/tasks/')) ok('干事登录后直达「我的工作」', page.url());
    else bad('干事登录后直达「我的工作」', page.url());
    const hasWelcome = await page.evaluate(() => !!document.getElementById('ppWelcome'));
    if (!hasWelcome) ok('登录后不再出现欢迎弹窗');
    else bad('登录后不再出现欢迎弹窗', '仍存在 #ppWelcome');

    await ctx.close();
  }

  // ---------- 2. 管理员登录后看到概览 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    const text = await page.evaluate(() => document.body.innerText);
    if (text.includes('概览') && text.includes('待审核记录')) {
      ok('管理员登录后看到待办概览');
    } else {
      bad('管理员登录后看到待办概览', text.slice(0, 100));
    }
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

  // ---------- 4. 导航：普通干事只保留日常工作入口 ----------
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
    if (!navText.includes('审核')) ok('没有审核授权时不显示「审核」', navText.slice(0, 60));
    else bad('没有审核授权时不显示「审核」', navText);
    if (navText.includes('我的工作')) ok('待办与提交合并为「我的工作」');
    else bad('待办与提交合并为「我的工作」', navText);
    if (!navText.includes('工作类型')) ok('顶层导航不再单独列「工作类型」');
    else bad('顶层导航不再单独列「工作类型」', navText);
    await ctx.close();
  }

  // ---------- 5. 管理员导航把报表归入管理 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const navText = await page.evaluate(() =>
      document.getElementById('ppNav').innerText.replace(/\s+/g, ' '));
    if (navText.includes('管理')) ok('管理员导航使用「管理」入口');
    else bad('管理员导航使用「管理」入口', navText);
    if (!navText.includes('统计报表')) ok('低频报表不再占顶级导航');
    else bad('低频报表不再占顶级导航', navText);
    await ctx.close();
  }

  // ---------- 6. 管理员首页是可行动的概览 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await login(page, 'D-ADMIN');
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    const body = await page.evaluate(() => document.body.innerText);
    if (body.includes('概览') && body.includes('待审核记录')) ok('管理员首页展示待办概览');
    else bad('管理员首页展示待办概览', body.slice(0, 120));
    if (!body.includes('工程底座')) ok('工作台不再出现工程术语');
    else bad('工作台不再出现工程术语', '仍出现"工程底座"');
    await ctx.close();
  }

  // ---------- 6b. 社团管理员可直接管理成员账号 ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await login(page, 'D-ADMIN');
    await page.goto(BASE + '/manage/members/', { waitUntil: 'networkidle' });
    const body = await page.evaluate(() => document.body.innerText);
    if (body.includes('成员管理') && body.includes('新建成员')) ok('管理员可进入成员管理');
    else bad('管理员可进入成员管理', body.slice(0, 120));
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
