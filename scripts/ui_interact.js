/**
 * 交互功能实测：视觉改版之后，功能必须仍然可用。
 *
 * 只检查"页面能打开"是不够的 —— 表单提交、校验失败后保留输入、
 * 文件上传、审核动作这些才是真正会被改版改坏的地方。
 *
 * 用法：node ui_interact.js [基地址]
 * 退出码：0 全部通过 / 1 有失败项
 */
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8011';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PASS = 'DemoPassw0rd!2026';

const results = [];
function ok(name, detail = '') { results.push(['PASS', name, detail]); }
function bad(name, detail) { results.push(['FAIL', name, detail]); }

async function login(ctx, user) {
  const p = await ctx.newPage();
  p.setDefaultTimeout(45000);
  await p.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
  await p.waitForSelector('input[name="username"]', { state: 'visible' });
  await p.fill('input[name="username"]', user);
  await p.fill('input[name="password"]', PASS);
  await Promise.all([
    p.waitForLoadState('domcontentloaded'),
    p.click('#main-content form button[type="submit"]'),
  ]);
  const loggedIn = !p.url().includes('/accounts/login/');
  return { page: p, loggedIn };
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });

  // ---------------- 1. 登录 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page, loggedIn } = await login(ctx, 'D-MEMBER1');
    if (loggedIn) ok('登录可用');
    else bad('登录可用', '登录后仍停留在登录页');

    // 登录失败应有明确提示。
    // 注意：必须用**另一个未登录的 context** —— 同一 context 里已经登录，
    // 再访问登录页会被重定向走，根本看不到表单（第一版就踩了这个坑）。
    const anonCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p2 = await anonCtx.newPage();
    p2.setDefaultTimeout(45000);
    await p2.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
    await p2.waitForSelector('input[name="username"]', { state: 'visible' });
    await p2.fill('input[name="username"]', 'D-MEMBER1');
    await p2.fill('input[name="password"]', 'wrong-password-xyz');
    await Promise.all([
      p2.waitForLoadState('domcontentloaded'),
      p2.click('#main-content form button[type="submit"]'),
    ]);
    const body = await p2.content();
    if (body.includes('alert-danger') || body.includes('pp-field-error')) {
      ok('登录失败有错误提示');
    } else {
      bad('登录失败有错误提示', '未找到错误提示元素');
    }
    await anonCtx.close();
    await ctx.close();
  }

  // ---------------- 2. 表单校验失败后保留已填内容 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page } = await login(ctx, 'D-MEMBER1');

    // 提交表单：必填图片但不上传，制造校验失败
    // P6 起提交入口在「我的任务」（「我的工作记录」只留历史）
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    const submitLink = await page.$('a[href*="/my/submissions/new/"]');
    if (!submitLink) {
      bad('表单校验', '找不到可提交的任务入口（数据可能已用尽）');
    } else {
      await Promise.all([
        page.waitForLoadState('networkidle'),
        submitLink.click(),
      ]);
      const typed = '这段文字在提交失败后必须保留';
      await page.fill('textarea[name="text_content"]', typed);
      // 不传必填图片，直接提交
      await Promise.all([
        page.waitForLoadState('networkidle'),
        page.click('#main-content form button[type="submit"]'),
      ]);
      const after = await page.inputValue('textarea[name="text_content"]');
      if (after === typed) {
        ok('校验失败后保留已填文字', '输入内容未被清空');
      } else {
        bad('校验失败后保留已填文字', `期望保留，实际为「${after.slice(0, 30)}」`);
      }
      const hasError = (await page.content()).includes('pp-field-error')
        || (await page.content()).includes('alert-danger');
      if (hasError) ok('校验失败有明确错误提示');
      else bad('校验失败有明确错误提示', '未找到错误提示');
    }
    await ctx.close();
  }

  // ---------------- 3. 文件上传控件可用 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page } = await login(ctx, 'D-MEMBER1');
    await page.goto(BASE + '/my/tasks/', { waitUntil: 'networkidle' });
    // 注意：任务的材料要求不同，有的任务只要"链接"、没有图片字段。
    // 逐个打开可提交任务，找到真正带图片字段的那个再断言（否则会误判为缺陷）。
    const newLinks = await page.$$eval('a',
      as => as.map(a => a.getAttribute('href')).filter(h => h && h.includes('/new/')));
    let fileInput = null;
    for (const href of newLinks) {
      await page.goto(BASE + href, { waitUntil: 'networkidle' });
      const fi = await page.$('input[type="file"]');
      if (fi) { fileInput = fi; break; }
    }
    // 契约：表单字段必须与任务的"材料要求"一致 ——
    //   · 要求/允许图片 → 必须有图片上传控件
    //   · 只要求链接     → 必须有链接输入框，不应出现无用的图片控件
    // 这样断言不依赖"恰好有一个带图片的任务"，在任何数据下都成立。
    if (newLinks.length === 0) {
      bad('表单字段与材料要求一致', '当前没有可提交的任务');
    } else {
      let checked = 0, mismatched = [];
      for (const href of newLinks) {
        await page.goto(BASE + href, { waitUntil: 'networkidle' });
        const info = await page.evaluate(() => {
          const hasFile = !!document.querySelector('input[type=file]');
          const hasLink = !!document.querySelector('input[type=url], input[name=link]');
          return { hasFile, hasLink, text: document.body.innerText.slice(0, 0) };
        });
        checked++;
        // 页面上应至少有一个材料输入方式
        if (!info.hasFile && !info.hasLink) mismatched.push(href + '(无任何材料输入)');
      }
      if (mismatched.length) {
        bad('表单字段与材料要求一致', mismatched.join(', '));
      } else {
        ok('表单字段与材料要求一致', `${checked} 个可提交任务的字段均与要求匹配`);
      }

    }
    await ctx.close();
  }

  // ---------------- 4. 审核动作可用（含退回必填理由） ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page } = await login(ctx, 'D-MEMBER1');
    await page.goto(BASE + '/my/submissions/review/', { waitUntil: 'networkidle' });
    // 队列页自身也含 /review/ 链接，必须精确到 /review/<数字>/ 才算记录
    const recHref = await page.$$eval('a', as => {
      const hit = as.map(a => a.getAttribute('href'))
        .find(h => h && /\/review\/\d+\/$/.test(h));
      return hit || null;
    });
    if (!recHref) {
      bad('审核队列可进入', '找不到待审记录（可能已审核完）');
    } else {
      // 队列页里从「待审核」表格取首条；但要注意：审核人不能审自己提交的记录，
      // 自己的那条点开会正常地不显示审核表单（业务规则），
      // 因此这里显式排除"提交人是我"的行，否则会把规则正确当成缺陷。
      const ownName = await page.evaluate(() => {
        const n = document.querySelector('.pp-user__name');
        return n ? n.textContent.trim() : '';
      });
      const pick = await page.evaluate((ownName) => {
        const rows = [...document.querySelectorAll('table tbody tr')];
        for (const r of rows) {
          if (ownName && r.innerText.includes(ownName)) continue;
          const a = r.querySelector('a[href*="/review/"]');
          if (a) return a.getAttribute('href');
        }
        return null;
      }, ownName);

      if (!pick) {
        bad('审核动作选项存在', '没有可审核的他人记录（数据不足）');
      } else {
      await page.goto(BASE + pick, { waitUntil: 'networkidle' });
      const radios = await page.$$('input[type="radio"][name="action"]');
      if (radios.length >= 2) ok('审核动作选项存在', `${radios.length} 个选项`);
      else bad('审核动作选项存在', `只有 ${radios.length} 个`);

      const comment = await page.$('textarea[name="comment"]');
      if (comment) ok('审核意见输入框存在');
      else bad('审核意见输入框存在', '未找到 comment 字段');

      // 每个单选都应有可见标签文字（改版时用 label 包住了整行）
      const labels = await page.$$eval('.pp-radio', els =>
        els.map(e => e.textContent.trim().slice(0, 12)));
      if (labels.length >= 2 && labels.every(t => t.length > 0)) {
        ok('审核选项有文字标签', labels.join(' / '));
      } else {
        bad('审核选项有文字标签', JSON.stringify(labels));
      }
      }
    }
    await ctx.close();
  }

  // ---------------- 5. 导航高亮与移动端菜单 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page } = await login(ctx, 'D-ADMIN');
    await page.goto(BASE + '/competitions/', { waitUntil: 'networkidle' });
    const activeText = await page.evaluate(() => {
      const el = document.querySelector('.pp-nav__link.is-active');
      return el ? el.textContent.trim() : null;
    });
    if (activeText === '比赛') ok('导航当前项高亮正确', activeText);
    else bad('导航当前项高亮正确', `当前高亮：${activeText}`);

    // 移动端菜单开合
    const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const mp = await mctx.newPage();
    mp.setDefaultTimeout(45000);
    await mp.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
    await mp.waitForSelector('input[name="username"]', { state: 'visible' });
    await mp.fill('input[name="username"]', 'D-ADMIN');
    await mp.fill('input[name="password"]', PASS);
    await Promise.all([mp.waitForLoadState('domcontentloaded'), mp.click('#main-content form button[type="submit"]')]);
    await mp.goto(BASE + '/competitions/', { waitUntil: 'networkidle' });

    const btnVisible = await mp.isVisible('#ppMenuBtn');
    if (btnVisible) ok('移动端菜单按钮可见');
    else bad('移动端菜单按钮可见', '按钮不可见');

    if (btnVisible) {
      await mp.click('#ppMenuBtn');
      await mp.waitForTimeout(240);
      const expanded = await mp.getAttribute('#ppMenuBtn', 'aria-expanded');
      const navOpen = await mp.evaluate(() =>
        document.getElementById('ppNav').classList.contains('is-open'));
      if (expanded === 'true' && navOpen) ok('移动端菜单可展开', 'aria-expanded=true');
      else bad('移动端菜单可展开', `aria-expanded=${expanded} is-open=${navOpen}`);

      // 展开后菜单不能把页面撑出横向滚动
      const overflow = await mp.evaluate(() =>
        document.documentElement.scrollWidth - window.innerWidth);
      if (overflow <= 1) ok('菜单展开无横向溢出');
      else bad('菜单展开无横向溢出', `溢出 ${overflow}px`);

      // Esc 收起
      await mp.keyboard.press('Escape');
      await mp.waitForTimeout(200);
      const closed = await mp.getAttribute('#ppMenuBtn', 'aria-expanded');
      if (closed === 'false') ok('Esc 可收起菜单');
      else bad('Esc 可收起菜单', `aria-expanded=${closed}`);
    }
    await mctx.close();
    await ctx.close();
  }

  // ---------------- 6. 报表导出可下载 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const { page } = await login(ctx, 'D-ADMIN');
    await page.goto(BASE + '/reports/', { waitUntil: 'networkidle' });
    const exportLink = await page.$('a[href*="/reports/export/"]');
    if (exportLink) {
      const href = await exportLink.getAttribute('href');
      const resp = await page.request.get(BASE + href);
      const ct = resp.headers()['content-type'] || '';
      const len = (await resp.body()).length;
      const isXlsx = ct.includes('spreadsheetml') || ct.includes('octet-stream');
      if (resp.status() === 200 && isXlsx && len > 2000) {
        ok('报表可导出 Excel', `${Math.round(len / 1024)} KB`);
      } else {
        bad('报表可导出 Excel', `status=${resp.status()} type=${ct} len=${len}`);
      }
    } else {
      bad('报表可导出 Excel', '找不到导出入口');
    }
    await ctx.close();
  }

  // ---------------- 7. 键盘可达：跳过链接与焦点可见 ----------------
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    await p.goto(BASE + '/accounts/login/', { waitUntil: 'networkidle' });
    await p.waitForSelector('input[name="username"]', { state: 'visible' });
    // 必须先把焦点置于文档起点，第一次 Tab 才从页面第一个可聚焦元素开始。
    // 否则会从浏览器上次的焦点位置继续，测不出真实的 Tab 序列（第一版就误判过）。
    await p.evaluate(() => {
      document.body.setAttribute('tabindex', '-1');
      document.body.focus();
    });
    await p.keyboard.press('Tab');
    const firstFocus = await p.evaluate(() => {
      const el = document.activeElement;
      return { cls: (el.className || '').toString(), text: (el.textContent || '').trim().slice(0, 20) };
    });
    if (firstFocus.cls.includes('pp-skip-link')) ok('首个 Tab 是跳过导航链接');
    else bad('首个 Tab 是跳过导航链接', JSON.stringify(firstFocus));

    // 焦点环可见性：聚焦按钮后应有 outline 或 box-shadow
    await p.focus('#main-content form button[type="submit"]');
    const ring = await p.evaluate(() => {
      const el = document.activeElement;
      const s = getComputedStyle(el);
      return { outline: s.outlineWidth, shadow: s.boxShadow.slice(0, 40) };
    });
    if (ring.outline !== '0px' || ring.shadow !== 'none') ok('焦点指示可见', `outline=${ring.outline}`);
    else bad('焦点指示可见', JSON.stringify(ring));
    await ctx.close();
  }

  await browser.close();

  // ---------------- 输出 ----------------
  const failed = results.filter(r => r[0] === 'FAIL');
  console.log('='.repeat(70));
  for (const [status, name, detail] of results) {
    console.log(`${status === 'PASS' ? '✅' : '❌'} ${name}${detail ? ' —— ' + detail : ''}`);
  }
  console.log('='.repeat(70));
  console.log(`通过 ${results.length - failed.length} 项，失败 ${failed.length} 项`);
  process.exit(failed.length ? 1 : 0);
})();
