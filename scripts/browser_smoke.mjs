// Read-only smoke test for an already-running deployment. No notes or settings are changed.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';

const base = process.env.REOUI_URL || 'http://127.0.0.1:8090';
const id = process.env.REOUI_RECORDING_ID;
if (!id) throw new Error('Set REOUI_RECORDING_ID to a prepared recording for the smoke test.');
const browser = await chromium.launch({headless:true,...(process.env.REOUI_BROWSER_PATH ? {executablePath:process.env.REOUI_BROWSER_PATH} : {})});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const recording = await fetch(`${base}/api/recordings/${id}`).then(r=>r.json());
  const day = recording.local_day;
  await page.goto(`${base}/?clip=${id}&day=${day}`,{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'Play selected recording',exact:true}).click();
  await page.waitForFunction(()=>{
    const video=document.querySelector('video');return video&&video.currentTime>0.2;
  },null,{timeout:20000});
  const playback = await page.locator('video').evaluate(video=>({
    currentTime:video.currentTime,duration:video.duration,width:video.videoWidth,height:video.videoHeight,
    error:video.error?.message||null,
  }));
  await page.locator('video').evaluate(video=>video.pause());
  if(errors.length) throw new Error(errors.join('\n'));
  await fs.mkdir('.local/screenshots',{recursive:true});
  await page.screenshot({path:'.local/screenshots/real-archive-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  const noOverflow = await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
  if(!noOverflow)throw new Error('Mobile layout overflows.');
  await page.screenshot({path:'.local/screenshots/real-archive-mobile.png'});
  console.log(JSON.stringify({playback,mobileOverflow:false,pageErrors:errors,recording:id}));
} finally {
  await browser.close();
}
