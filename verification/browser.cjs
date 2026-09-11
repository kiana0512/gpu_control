const { chromium } = require('/pw/node_modules/playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
const live = JSON.parse(fs.readFileSync('/evidence/nodes-after-control.json', 'utf8'));
const report = { data: 'captured production nodes snapshot; synthetic failures explicitly separated', checks: [], errors: [] };
const now = Math.max(...live.map(n => Date.parse(n.codex_cli?.worker_last_heartbeat_at ?? n.last_heartbeat_at))) + 1000;
const activeNodes = live.filter(n=>n.mode==='ACTIVE' && n.health==='ONLINE').length;
const asset = {workers:[], jobs:[], summary:{counts:{},online_workers:0}};
let nodes = structuredClone(live), failNodes = false, assetFailure = false;
function esrgan() { return { nodes:nodes.map(n=>({id:n.id,name:n.display_name,ready:n.mode==='ACTIVE',active:0,capacity:1,device:n.display_name,vram_free_mb:n.free_vram_mb,vram_total_mb:n.total_vram_mb,last_metrics:{}})), capacity:4,queue_depth:0,max_queue:64,tasks:[],api:{},model_sha256:'verified fixture',image_version:'browser fixture'}; }
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}});
 await page.clock.install({time:new Date(now)});
 await page.addInitScript(()=>{sessionStorage.setItem('gpu-control-session','isolated-browser-fixture');sessionStorage.setItem('gpu-control-session-expires',String(Date.now()+86400000));});
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.route('**/admin/**', async route=>{
  assert.equal(route.request().method(),'GET','browser checks must never mutate production');
  const path=new URL(route.request().url()).pathname;
  let data;
  if(path==='/admin/nodes') { if(failNodes)return route.fulfill({status:503,json:{detail:'fixture API unavailable'}}); data=nodes; }
  else if(path==='/admin/asset-processing') {if(assetFailure)return route.fulfill({status:503,json:{detail:'fixture history unavailable'}});data=asset;}
  else if(path==='/admin/realesrgan')data=esrgan();
  else if(path==='/admin/jobs'||path==='/admin/workflows')data=[];
  else if(path==='/admin/dashboard')data={jobs:{QUEUED:0,RUNNING:0,SUCCEEDED:0,FAILED:0},oldest_wait_seconds:0,submission_trend:[],active_alerts:[]};
  else if(path==='/admin/settings')data={};
  else throw Error('Unexpected API '+path);
  await route.fulfill({json:data});
 });
 const go=async(path,heading)=>{await page.goto('http://127.0.0.1:18751'+path);await page.getByRole('heading',{name:heading,exact:true}).waitFor();await page.waitForTimeout(250);};
 await go('/nodes','GPU 推理节点');
 assert.equal(await page.locator('.node-card').count(),5);
 assert((await page.locator('body').innerText()).includes('专项验收 3 个工作流版本 / 16 GB'));
 for (const width of [1440,1280,390]) {
   await page.setViewportSize({width,height:1080});
   assert(await page.evaluate(()=>[...document.querySelectorAll('.node-card')].every(card=>{
    const bounds=card.getBoundingClientRect();
    return card.scrollWidth<=card.clientWidth+1 && [...card.querySelectorAll('button,.offline-node-note')].every(el=>{
     const box=el.getBoundingClientRect();return box.left>=bounds.left && box.right<=bounds.right && el.scrollWidth<=el.clientWidth+1;
    });
   })),`node content clipped at ${width}px`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),`node page overflow at ${width}px`);
   await page.screenshot({path:`/out/nodes-real-snapshot-${width}.png`,fullPage:true});
 }
 await page.setViewportSize({width:1440,height:1080});
 report.checks.push('node actions and degraded notes stay within cards at 1440/1280/390px');
 await page.screenshot({path:'/out/nodes-real-snapshot-desktop.png',fullPage:true});report.checks.push('real 5 registered nodes, DRAINING node and 3 validation profiles visible');
 await go('/codex','Codex 运行中心');
 assert.equal(await page.locator('.codex-runtime-card').count(),5);
 await page.screenshot({path:'/out/codex-real-snapshot-desktop.png',fullPage:true});report.checks.push('real five-node Codex snapshot renders without hiding unready nodes');
 const fresh={health:'HEALTHY',host_entry_installed:true,host_version:'installed fixture',runtime_version:'installed fixture',auth_status:'AUTHENTICATED',probe_status:'HEALTHY',probe_latency_ms:12000,last_checked_at:new Date(now).toISOString(),last_success_at:new Date(now).toISOString(),worker_status:'ONLINE',worker_last_heartbeat_at:new Date(now).toISOString(),heartbeat_fresh:true,probe_fresh:true,eligibility_reason:'ELIGIBLE',error_code:null,task:null,scheduler_eligible:true};
 nodes=structuredClone(live).map(n=>({...n,mode:'ACTIVE',codex_cli:{...fresh}}));
 Object.assign(nodes[1].codex_cli,{auth_status:'EXPIRED',error_code:'AUTH_REFRESH_REUSED'});
 Object.assign(nodes[2].codex_cli,{probe_status:'FAILED',error_code:'PROBE_TIMEOUT'});
 Object.assign(nodes[3].codex_cli,{heartbeat_fresh:false});
 Object.assign(nodes[4],{mode:'DRAINING'});Object.assign(nodes[4].codex_cli,{auth_status:'MISSING',probe_status:'BLOCKED',error_code:'AUTH_MISSING'});
 nodes.push({...structuredClone(nodes[4]),id:'worker-future-6',display_name:'Future worker 6',health:'OFFLINE',last_heartbeat_at:null,codex_cli:undefined});
 await page.getByRole('button',{name:'立即刷新',exact:true}).click();
 await page.getByText('尚未授权',{exact:true}).first().waitFor();
 assert.equal(await page.locator('.codex-runtime-card').count(),6);
 for(const label of ['真实调用正常','授权已失效','探针失败','心跳已过期','尚未授权','等待 Worker 接入'])assert(await page.getByText(label,{exact:true}).count()>0,label);
 await page.screenshot({path:'/out/codex-synthetic-status-matrix.png',fullPage:true});report.checks.push('six dynamic nodes: healthy, expired auth, probe failure, stale heartbeat, missing auth, unregistered');
 failNodes=true;
 await page.clock.fastForward(31000);
 await page.waitForTimeout(100);
 assert.equal(await page.locator('.codex-runtime-card.healthy').count(),0);
 assert(await page.getByText('fixture API unavailable',{exact:false}).count()>0);
 report.checks.push('cached healthy status expires during API outage without new data');
 failNodes=false;assetFailure=true;nodes=structuredClone(live);nodes[4].display_name='RTX 5070 Ti · history unavailable';
 await page.getByRole('button',{name:'立即刷新',exact:true}).click();await page.getByRole('heading',{name:nodes[4].display_name,exact:true}).waitFor();report.checks.push('node refresh succeeds independently when asset history endpoint fails');
 assetFailure=false;nodes=structuredClone(live);
 await go('/realesrgan','Real-ESRGAN AI 高清化');assert(await page.getByText('4 / 5 节点就绪',{exact:true}).count());
 await page.screenshot({path:'/out/realesrgan-five-node-fixture.png',fullPage:true});report.checks.push('RealESRGAN dynamic 4/5 denominator uses its own registered workers');
 nodes=[];await page.getByRole('button',{name:'立即刷新',exact:true}).click();await page.getByText('等待节点接入',{exact:true}).waitFor();report.checks.push('empty RealESRGAN list never reports healthy 0/0');
 nodes=structuredClone(live);await go('/','生产运行总览');assert(await page.getByText(`${activeNodes} / ${live.length} 节点参与接单`,{exact:true}).count());report.checks.push('dashboard includes fifth node and describes planned drain neutrally');
 await go('/scheduling','调度运行说明');assert((await page.locator('body').innerText()).includes(`${activeNodes}/${live.length} 节点参与接单`));report.checks.push('scheduling renders dynamic five-node capacity');
 await page.setViewportSize({width:390,height:844});await go('/codex','Codex 运行中心');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth+1),'Codex mobile horizontal overflow');
 await page.screenshot({path:'/out/codex-real-snapshot-mobile.png',fullPage:true});report.checks.push('390px mobile Codex layout fits viewport');
 assert.deepEqual(report.errors,[]);fs.writeFileSync('/out/browser-report.json',JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);fs.writeFileSync('/out/browser-report.json',JSON.stringify({...report,failure:String(e)},null,2));process.exit(1)});
