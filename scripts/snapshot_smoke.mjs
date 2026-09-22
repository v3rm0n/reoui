// Read-only camera snapshot verification; never starts persistent live viewers.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
const base=process.env.REOUI_URL||'http://127.0.0.1:8090';
const browser=await chromium.launch({headless:true,...(process.env.REOUI_BROWSER_PATH?{executablePath:process.env.REOUI_BROWSER_PATH}:{})});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base,{waitUntil:'networkidle'});
 await page.getByRole('button',{name:'Live view',exact:true}).click();
 await page.waitForFunction(()=>{const images=[...document.querySelectorAll('.snapshot-image')];return images.length===3&&images.every(i=>i.complete&&i.naturalWidth>0);},null,{timeout:25000});
 const images=await page.locator('.snapshot-image').evaluateAll(list=>list.map(i=>({alt:i.alt,width:i.naturalWidth,height:i.naturalHeight,url:i.getAttribute('src')})));
 const first=page.locator('.live-card').first();
 const old=await first.locator('img').getAttribute('src');
 await first.getByRole('button',{name:/Refresh .* snapshot/}).click();
 await page.waitForFunction(old=>document.querySelector('.live-card .snapshot-image')?.getAttribute('src')!==old,old,{timeout:25000});
 await page.waitForFunction(()=>{const i=document.querySelector('.live-card .snapshot-image');return i?.complete&&i.naturalWidth>0;});
 await page.screenshot({path:'.local/screenshots/snapshots-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw new Error('Mobile overflow');
 if(errors.length)throw new Error(errors.join('\n'));
 console.log(JSON.stringify({images,refreshWorked:true,mobileOverflow:false,pageErrors:errors}));
}finally{await browser.close();}
