const { chromium } = require(process.env.HOME + '/work/pw/node_modules/playwright');
const fs=require('fs'),path=require('path');
(async()=>{const b=await chromium.launch();const c=await b.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});
await c.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!=='http://localhost:9481')return r.abort();
 if(u.pathname.startsWith('/wizard-os-home/')){const f=path.join(process.env.UX||process.env.HOME+'/work/ux-m15','web',u.pathname.slice(16));if(fs.existsSync(f))return r.fulfill({body:fs.readFileSync(f),contentType:'application/javascript'});return r.fulfill({status:404,body:''});}
 if(u.pathname.startsWith('/movies/')){return r.fulfill({response:await r.fetch({url:'http://127.0.0.1:18765'+u.pathname.slice(7)+u.search})});}
 return r.fulfill({status:404,body:''});});
const p=await c.newPage();await p.goto('http://localhost:9481/movies/');await p.waitForTimeout(2500);
const info=await p.evaluate(()=>({hosted:WizardApp.hosted,threadDrafts:WizardApp.nav.threadDrafts,fn:typeof WizardApp.nav.openContextDraft}));
await p.locator('article.movie').first().click();await p.waitForTimeout(800);
const shown=await p.locator('.detail-reply').isVisible().catch(()=>false);let status=null;
if(shown){await p.locator('.detail-reply').click();await p.waitForTimeout(500);status=await p.locator('.detail-reply-status').innerText();}
await p.screenshot({path:(process.env.TAG||'standalone')+'.png'});
console.log(JSON.stringify({info,shown,status}));await b.close();})();
