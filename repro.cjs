// Real wizard-ux shell (routed, no backend) + real Movies frontend/server under /movies/. Tap Reply, report.
const fs = require('fs'), path = require('path');
const { chromium } = require(process.env.HOME + '/work/pw/node_modules/playwright');
const UX = process.env.UX || process.env.HOME + '/work/ux-m15';
const MOVIES = process.env.MOVIES || 'http://127.0.0.1:18765';
const OUT = process.env.OUT || __dirname;
const ORIGIN = 'http://localhost:9481';
const type = f => f.endsWith('.js') || f.endsWith('.mjs') ? 'application/javascript' : f.endsWith('.css') ? 'text/css' : f.endsWith('.html') ? 'text/html' : f.endsWith('.json') ? 'application/json' : f.endsWith('.png') ? 'image/png' : 'image/svg+xml';
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const res = [];
  for (const desktop of (process.env.ONLY ? [process.env.ONLY === 'desktop'] : [false, true])) {
    const ctx = await b.newContext({ viewport: desktop ? { width: 1440, height: 900 } : { width: 390, height: 844 }, isMobile: !desktop, hasTouch: !desktop, serviceWorkers: 'block' });
    const sends = [];
    await ctx.route('**/*', async r => {
      const q = r.request(), u = new URL(q.url()), p = u.pathname, json = (o, s = 200) => r.fulfill({ status: s, contentType: 'application/json', body: JSON.stringify(o) });
      if (u.origin !== ORIGIN) return r.abort();
      if (p.startsWith('/movies/') || p === '/movies') {
        const resp = await r.fetch({ url: MOVIES + (p.slice('/movies'.length) || '/') + u.search });
        return r.fulfill({ response: resp });
      }
      if (q.method() === 'GET' && p.startsWith('/wizard-os-home/')) {
        let f = p.slice('/wizard-os-home/'.length) || 'index.html'; f = path.join(UX, 'web', f);
        if (fs.existsSync(f) && fs.statSync(f).isFile()) return r.fulfill({ body: fs.readFileSync(f), contentType: type(f) });
        return r.fulfill({ status: 404, body: '' });
      }
      if (p === '/wizard/v1/setup') return json({ first_run: false });
      if (p.endsWith('/bootstrap')) return json({ bootstrap: 1, display: { agent_name: 'Wizard', owner_name: 'Tester' }, endpoints: { apps: '/wizard/v1/apps' } });
      if (p === '/wizard/v1/apps') return json({ apps: [{ id: 'movies', name: 'Movies', url: process.env.APPURL || '/movies/', source: 'apps.json', type: 'view', category: 'app' }] });
      if (/\/runs|\/messages|\/send/.test(p) && q.method() === 'POST') sends.push(p);
      if (p.endsWith('/api/sessions')) return json({ sessions: [], has_more: false });
      if (p.endsWith('/agents')) return json({ agents: [{ id: 'hermes', label: 'Wizard', is_default: true, status: 'running' }] });
      if (p.endsWith('/api/jobs')) return json({ jobs: [] });
      if (q.method() !== 'GET') return json({ accepted: true });
      return json({});
    });
    await ctx.addInitScript(() => { for (const [k, v] of Object.entries({ 'wos.wsync.off': 'true', 'wos.tour.v1': 'true', 'wos.tour.done': '1', 'wos.coached': 'true', 'wos.welcome.v1': 'done' })) localStorage.setItem(k, v); });
    const p = await ctx.newPage(); const errors = [];
    p.on('pageerror', e => errors.push(String(e)));
    await p.goto(ORIGIN + '/wizard-os-home/' + (desktop ? 'desktop.html' : ''));
    await p.waitForFunction(() => !!(window.__wos || window.__wux), null, { timeout: 60000 });
    await p.waitForTimeout(1500);
    await p.evaluate(() => WizCore.deeplink.route('/wizard-os-home/?open=movies'));
    await p.waitForFunction(() => [...document.querySelectorAll('iframe')].some(f => f.src.includes('/movies/')), null, { timeout: 30000 });
    await p.waitForTimeout(2500);
    await p.evaluate(() => document.getElementById('wosWelcome')?.remove());
    const f = p.frames().find(f => f.url().includes('/movies/'));
    const hostInfo = await f.evaluate(() => ({ hosted: window.WizardApp?.hosted, threadDrafts: window.WizardApp?.nav?.threadDrafts, href: location.href }));
    await f.locator('article.movie').first().click();
    await p.waitForTimeout(800);
    if (process.env.RELOAD) {
      await p.reload(); await p.waitForFunction(() => !!(window.__wos || window.__wux), null, { timeout: 60000 }); await p.waitForTimeout(3000);
      await p.evaluate(() => document.getElementById('wosWelcome')?.remove());
      if (!p.frames().some(x => x.url().includes('/movies/'))) { await p.evaluate(() => WizCore.deeplink.route('/wizard-os-home/?open=movies')); await p.waitForTimeout(3000); }
      var f2 = p.frames().find(x => x.url().includes('/movies/'));
      hostInfo.afterReload = await f2.evaluate(() => ({ hosted: window.WizardApp?.hosted, threadDrafts: window.WizardApp?.nav?.threadDrafts, href: location.href, detailOpen: !document.querySelector('#detail').classList.contains('hidden') }));
      if (!hostInfo.afterReload.detailOpen) { await f2.locator('article.movie').first().click(); await p.waitForTimeout(800); }
    }
    const ff = typeof f2 !== 'undefined' && f2 ? f2 : f;
    const afterOpen = await ff.evaluate(() => ({ threadDrafts: window.WizardApp?.nav?.threadDrafts, href: location.href }));
    const reply = ff.locator('.detail-reply');
    const shown = await reply.count() > 0 && await reply.isVisible();
    let status = null;
    if (shown) { await reply.click(); await p.waitForTimeout(1000); status = await ff.locator('.detail-reply-status').innerText().catch(() => null); }
    const draft = await p.evaluate(() => JSON.parse(localStorage.getItem('wos.chat.drafts.v1') || '{}'));
    const name = (desktop ? 'desktop' : 'phone') + (process.env.TAG ? '-' + process.env.TAG : '');
    await p.screenshot({ path: path.join(OUT, name + '.png') });
    res.push({ surface: name, hostInfo, afterOpen, replyShown: shown, status, draftReply: draft.new?.reply || null, draftText: draft.new?.text ?? null, sends, errors: errors.slice(0, 5) });
    await ctx.close();
  }
  console.log(JSON.stringify(res, null, 2));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
