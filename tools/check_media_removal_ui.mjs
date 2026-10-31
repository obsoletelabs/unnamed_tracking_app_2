// Rendered UI verification with deterministic API fixtures; no live account data is changed.
// Usage: node tools/check_media_removal_ui.mjs browser-dependencies evidence-directory [origin]
import { createRequire } from 'node:module';
import path from 'node:path';
const [dependencyRoot, evidenceRoot, origin = 'http://127.0.0.1'] = process.argv.slice(2);
if (!dependencyRoot || !evidenceRoot) throw new Error('Supply browser dependencies and an evidence directory.');
const { chromium } = createRequire(path.resolve(dependencyRoot, 'package.json'))('playwright');
import * as fs from 'node:fs/promises';
import assert from 'node:assert/strict';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  await fs.mkdir(evidenceRoot, { recursive: true });
  for (const style of ['archive-pocket', 'classic']) for (const width of [390,1440]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.stack));
    let failGames = false; let revoked = false;
    let movies = ['one','two'].map((id,i)=>({id,user_id:'test',title:i?'Test import B':'Test import A',sort_title:id,status:'WATCHED',genres:[],studios:[],countries:[],languages:[],tags:[],features:[],poster_url:null,backdrop_url:null,favorite:false,rewatches:0,created_at:1,updated_at:1,locked_fields:[],rating_overall:null}));
    const grant = {id:'old',name:'Playnite',origin:'desktop:playnite',kind:'desktop',enabled:true,scopes:['games.read'],created_at:1,expires_at:null,revoked_at:null,external_account:{provider:'playnite',subject:'test-pc',username:'Test PC'}};
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()); const p = url.pathname;
      let body = [];
      if (p === '/api/setup/status') body = {setup_required:false,startup_mode:'dev',startup_ui_enabled:false};
      else if (p === '/api/auth/me') body = {id:'test',username:'Tester',email:'tester@example.test',is_admin:true,steamgriddb_api_key:null};
      else if (p === '/api/movie/list') body = {items:movies,total:movies.length,offset:0,limit:48,status_counts:{WATCHED:movies.length},score_ranks:{}};
      else if (p.startsWith('/api/movie/delete/')) {
        if(p.endsWith('/two')) return route.fulfill({status:503,json:{detail:'Test removal unavailable'}});
        movies = movies.filter(movie=>!p.endsWith('/'+movie.id)); body = {};
      }
      else if (p === '/api/themes') body = {default_theme:'builtin',themes:[]};
      else if (p === '/api/preferences') body = {ui_style:style,ui_theme:'light',ui_welcome_completed:true,home_widgets:['library-summary','weekly-digest']};
      else if (p === '/api/stats/weekly-digest') body = {period_start:0,games_played:3,games_added:2,achievements_unlocked:4,metadata_changes:2};
      else if (p === '/api/game/list' && failGames) return route.fulfill({status:503,json:{detail:'Test refresh unavailable'}});
      else if (p === '/api/delegation/v1/grants') body = revoked ? [] : [grant];
      else if (p === '/api/delegation/v1/grants/old') {revoked=true;body={};}
      else if (p === '/api/notifications/unread-count') body = {unread:0};
      await route.fulfill({status:200,json:body,headers:{'X-Total-Count':'0'}});
    });
    await page.goto(origin+'/movies');
    await page.getByText('Test import A',{exact:true}).waitFor();
    if (width === 390) await page.getByRole('button',{name:'Library controls',exact:true}).click();
    await page.getByRole('button',{name:'Select',exact:true}).click();
    await page.getByRole('button',{name:'Select shown titles',exact:true}).click();
    await page.getByRole('button',{name:'Remove from library',exact:true}).click();
    await page.getByRole('dialog').waitFor();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:evidenceRoot+'/removal-confirm-'+style+'-'+width+'.png',fullPage:true});
    await page.getByRole('dialog').getByRole('button',{name:'Remove from library',exact:true}).click();
    await page.getByText('1 of 2 titles could not be removed.',{exact:false}).waitFor();
    await page.getByText('Test import A',{exact:true}).waitFor({state:'hidden'});
    assert.equal(await page.locator('.bulk-bar .count').textContent(),'1 selected');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:evidenceRoot+'/removal-partial-'+style+'-'+width+'.png',fullPage:true});
    assert.deepEqual(errors,[]);
    await context.close();
  }
  await browser.close(); console.log('Modern/Classic at 390/1440: media selection, confirmation, partial failure and refreshed rows verified; no overflow or page errors. API fixtures, not live-provider verification.');
})().catch(e=>{console.error(e);process.exit(1)});
