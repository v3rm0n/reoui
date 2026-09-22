// Opens existing read-only camera streams, then releases every viewer.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
const base=process.env.REOUI_URL||'http://127.0.0.1:8090';
const browser=await chromium.launch({headless:true,...(process.env.REOUI_BROWSER_PATH?{executablePath:process.env.REOUI_BROWSER_PATH}:{})});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base,{waitUntil:'networkidle'});
 await page.getByRole('button',{name:'Live view',exact:true}).click();
 const cards=page.locator('.live-card');
 await cards.first().waitFor();
 const count=await cards.count();if(count===0)throw new Error('No camera players');
 for(let i=0;i<count;i++)await cards.nth(i).getByRole('button',{name:'Connect',exact:true}).click();
 await page.waitForFunction(()=>{const videos=[...document.querySelectorAll('.live-card video')];return videos.length===3&&videos.every(v=>v.currentTime>1&&v.videoWidth>0);},null,{timeout:45000});
 const playback=await page.locator('.live-card video').evaluateAll(videos=>videos.map(v=>({label:v.getAttribute('aria-label'),time:v.currentTime,width:v.videoWidth,height:v.videoHeight,error:v.error?.message||null})));
 await page.screenshot({path:'.local/screenshots/live-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 if(!await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))throw new Error('Mobile overflow');
 await page.screenshot({path:'.local/screenshots/live-mobile.png'});
 await page.getByRole('button',{name:'Open navigation'}).click();
 await page.locator('nav').getByRole('button',{name:/^Recordings/}).click();
 await page.waitForFunction(()=>!document.querySelector('.live-card'));
 if(errors.length)throw new Error(errors.join('\n'));
 console.log(JSON.stringify({playback,mobileOverflow:false,pageErrors:errors}));
} finally {await browser.close();}
