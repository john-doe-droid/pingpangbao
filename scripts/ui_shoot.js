/**
 * UI 验收：用真实浏览器逐页截图 + 做可访问性/响应式检查。
 *
 * 为什么用 Playwright 而不是"看 HTML 源码"：
 *   模板能渲染 ≠ 页面对。横向溢出、触控尺寸、对比度、焦点可见性
 *   都必须在真实浏览器里量，靠读模板看不出来。
 *
 * 用法：
 *   node shoot.js <基地址> <输出目录> [视口] [页面列表]
 *     node shoot.js http://127.0.0.1:8011 shots before
 *     node shoot.js http://127.0.0.1:8011 shots after
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2] || 'http://127.0.0.1:8011';
const OUT = process.argv[3] || 'shots';
const TAG = process.argv[4] || 'page';

const ACCOUNTS = {
  admin: { user: 'D-ADMIN', pass: 'DemoPassw0rd!2026' },
  member1: { user: 'D-MEMBER1', pass: 'DemoPassw0rd!2026' },
  member2: { user: 'D-MEMBER2', pass: 'DemoPassw0rd!2026' },
  viewer: { user: 'D-VIEWER', pass: 'DemoPassw0rd!2026' },
};

/** 页面清单：路径 + 用哪个账号 + 是否只看（不做写操作） */
function pagesFor(ids) {
  const sub = ids.sub || 1;
  const subRev = ids.sub_rev || sub;
  const subOk = ids.sub_approved || sub;
  const wt = ids.worktype || 1;
  const tpl = ids.template || 1;
  const task = ids.task || 1;
  const comp = ids.comp || 1;
  const member = ids.member || 2;
  return [
    { name: '01-login', url: '/accounts/login/', as: null },
    { name: '02-home', url: '/', as: 'member1' },
    { name: '03-my-tasks', url: '/my/tasks/', as: 'member1' },
    { name: '04-my-task-detail', url: `/my/tasks/mine/${task}/`, as: 'member1' },
    { name: '05-my-submissions', url: '/my/submissions/', as: 'member1' },
    { name: '06-submit-form', url: `/my/submissions/new/${task}/`, as: 'member1' },
    { name: '07-submission-detail', url: `/my/submissions/${sub}/`, as: 'member1' },
    { name: '08-review-queue', url: '/my/submissions/review/', as: 'member1' },
    { name: '09-review-detail', url: `/my/submissions/review/${subOk}/`, as: 'member1' },
    { name: '10-review-detail-page', url: `/my/submissions/review/${subOk}/detail/`, as: 'member1' },
    { name: '11-reopen-form', url: `/my/submissions/review/${subOk}/reopen/`, as: 'member1' },
    { name: '11b-submit-revision', url: `/my/submissions/${sub}/revise/`, as: 'member1' },
    { name: '12-competitions', url: '/competitions/', as: 'admin' },
    { name: '13-competition-detail', url: `/competitions/${comp}/`, as: 'admin' },
    { name: '14-competition-new', url: '/competitions/new/', as: 'admin' },
    { name: '15-competition-edit', url: `/competitions/${comp}/edit/`, as: 'admin' },
    { name: '16-reviewers', url: `/competitions/${comp}/reviewers/`, as: 'admin' },
    { name: '17-worktypes', url: '/manage/tasks/work-types/', as: 'admin' },
    { name: '18-worktype-new', url: '/manage/tasks/work-types/new/', as: 'admin' },
    { name: '18b-worktype-edit', url: `/manage/tasks/work-types/${wt}/edit/`, as: 'admin' },
    { name: '19-templates', url: '/manage/tasks/templates/', as: 'admin' },
    { name: '20-template-new', url: '/manage/tasks/templates/new/', as: 'admin' },
    { name: '20b-template-edit', url: `/manage/tasks/templates/${tpl}/edit/`, as: 'admin' },
    { name: '21-task-detail', url: `/manage/tasks/tasks/${task}/`, as: 'admin' },
    { name: '22-task-new', url: `/manage/tasks/competitions/${comp}/tasks/new/`, as: 'admin' },
    { name: '23-manage-records', url: '/manage/submissions/', as: 'admin' },
    { name: '24-reports', url: '/reports/', as: 'admin' },
    { name: '25-members', url: '/manage/members/', as: 'admin' },
    { name: '26-member-new', url: '/manage/members/new/', as: 'admin' },
    { name: '27-member-password', url: `/manage/members/${member}/reset-password/`, as: 'admin' },
    { name: '28-password-change', url: '/accounts/password/change/', as: 'member1' },
  ];
}

async function login(page, who) {
  if (!who) return true;
  const acc = ACCOUNTS[who];
  await page.goto(BASE + '/accounts/login/', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name="username"]', acc.user);
  await page.fill('input[name="password"]', acc.pass);
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.click('button[type="submit"]'),
  ]);
  return !page.url().includes('/accounts/login/');
}

/** 在当前页面上量一批"只看源码发现不了"的指标 */
async function audit(page, label) {
  return page.evaluate((label) => {
    const doc = document.documentElement;
    const overflowX = doc.scrollWidth - window.innerWidth;

    // 触控目标过小的可交互元素（只统计可见的、且确实是"要点的"元素）
    const small = [];
    document.querySelectorAll('a, button, .btn, input[type="submit"]')
      .forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        const st = getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none') return;
        // 移动端菜单里的项在收起时不算
        if (st.position === 'absolute' && r.top < -1000) return;
        // 视觉隐藏的无障碍元素（skip-link 用 clip-path 裁成 1×1）不算触控目标 ——
        // 它只在获得焦点时才展开，平时不是一个"要被点击的目标"。
        if (r.width <= 4 && r.height <= 4) return;
        if (st.clipPath && st.clipPath !== 'none') return;
        if (r.height < 40 || r.width < 24) {
          small.push({
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').trim().slice(0, 24),
            w: Math.round(r.width), h: Math.round(r.height),
          });
        }
      });

    // 水平方向"逃逸"出视口的元素（常见于宽表格、长串文本）
    const escaped = [];
    document.querySelectorAll('body *').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0) return;
      const st = getComputedStyle(el);
      if (st.overflowX === 'auto' || st.overflowX === 'scroll') return;
      // 刻意移出视口做视觉隐藏的无障碍元素（如 skip-link）不算越界
      if (r.right < 0 || st.position === 'absolute' && r.left < -1000) return;
      if (el.closest('[aria-hidden="true"]')) return;
      if (r.right > window.innerWidth + 2 || r.left < -2) {
        escaped.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className || '').toString().slice(0, 40),
          right: Math.round(r.right), left: Math.round(r.left),
        });
      }
    });

    // 缺少可访问名称的表单控件（只能靠 placeholder 的不算合格标签）
    const unlabeled = [];
    document.querySelectorAll('input:not([type=hidden]), select, textarea').forEach((el) => {
      const id = el.id;
      const hasLabel = id && document.querySelector(`label[for="${id}"]`);
      const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
      if (!hasLabel && !aria) {
        unlabeled.push({ name: el.name, type: el.type || el.tagName.toLowerCase() });
      }
    });

    // 页面标题层级：是否只有一个 h1
    const h1 = document.querySelectorAll('h1').length;
    const cards = document.querySelectorAll('.card').length;
    const shadows = Array.from(document.querySelectorAll('*')).filter(
      (el) => {
        const s = getComputedStyle(el).boxShadow;
        return s && s !== 'none';
      }).length;

    return {
      label,
      url: location.pathname,
      title: document.title.slice(0, 40),
      overflowX,
      smallTargets: small.slice(0, 6),
      smallCount: small.length,
      escaped: escaped.slice(0, 5),
      escapedCount: escaped.length,
      unlabeled: unlabeled.slice(0, 5),
      unlabeledCount: unlabeled.length,
      h1Count: h1,
      cardCount: cards,
      shadowCount: shadows,
    };
  }, label);
}

(async () => {
  const idsPath = path.join(OUT, 'ids.json');
  const ids = fs.existsSync(idsPath)
    ? JSON.parse(fs.readFileSync(idsPath, 'utf8'))
    : { sub: 1, task: 1, comp: 1 };

  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  });

  const viewports = [
    { tag: 'desktop', width: 1440, height: 900 },
    { tag: 'mobile', width: 390, height: 844 },
  ];

  const report = [];

  for (const vp of viewports) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 1,
    });
    const page = await ctx.newPage();

    let currentUser = null;
    for (const item of pagesFor(ids)) {
      // 需要换账号时重登
      if (item.as !== currentUser) {
        currentUser = item.as;
        await ctx.clearCookies();
        if (item.as) {
          const ok = await login(page, item.as);
          if (!ok) {
            report.push({ name: item.name, vp: vp.tag, error: 'login failed' });
            continue;
          }
        }
      }

      try {
        const resp = await page.goto(BASE + item.url, {
          waitUntil: 'networkidle', timeout: 20000,
        });
        const status = resp ? resp.status() : 0;
        const a = await audit(page, item.name);
        a.vp = vp.tag;
        a.status = status;
        a.expected = item.name;
        report.push(a);

        if (vp.tag === 'desktop') {
          await page.screenshot({
            path: path.join(OUT, `${TAG}-${item.name}.png`),
            fullPage: true,
          });
        } else {
          // 移动端只截首屏，避免长图不便查看
          await page.screenshot({
            path: path.join(OUT, `${TAG}-${item.name}-mobile.png`),
          });
        }
      } catch (e) {
        report.push({ name: item.name, vp: vp.tag, error: String(e).slice(0, 160) });
      }
    }
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(
    path.join(OUT, `audit-${TAG}.json`),
    JSON.stringify(report, null, 2),
  );

  // 控制台摘要
  console.log('=== 页面数:', report.length, '===');
  for (const r of report) {
    if (r.error) { console.log('ERR  ', r.expected || r.name, r.vp, r.error); continue; }
    const flags = [];
    if (r.status !== 200) flags.push('HTTP' + r.status);
    if (r.overflowX > 1) flags.push('溢出+' + r.overflowX);
    if (r.escapedCount) flags.push('越界' + r.escapedCount);
    if (r.unlabeledCount) flags.push('无标签' + r.unlabeledCount);
    if (r.vp === 'mobile' && r.smallCount) flags.push('小目标' + r.smallCount);
    if (r.h1Count !== 1) flags.push('h1=' + r.h1Count);
    console.log(
      (flags.length ? 'WARN ' : 'ok   ') + (r.expected || r.name).padEnd(26) + r.vp.padEnd(8) +
      (flags.join(' ') || ''),
    );
  }
})();
