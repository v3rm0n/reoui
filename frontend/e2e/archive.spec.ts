import { expect, test } from '@playwright/test';

test.beforeEach(async ({page}) => {
  if (process.env.REOUI_TEST_AUTH_TOKEN) {
    await page.request.post('/api/login', {data: {token: process.env.REOUI_TEST_AUTH_TOKEN}});
  }
});

test('archive, filters, browser playback, notes, bookmarks and navigation', async ({page},testInfo) => {
  const errors: string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Recording archive',exact:true})).toBeVisible();
  await expect(page.locator('.recording-card')).toHaveCount(6);
  await expect(page.locator('.player-poster')).toBeVisible();
  await page.getByRole('button',{name:'Person',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(3);
  await page.getByRole('button',{name:'All recordings',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(6);

  await page.getByRole('button',{name:'Play selected recording',exact:true}).click();
  await expect.poll(()=>page.locator('video').evaluate((video:HTMLVideoElement)=>video.currentTime),{timeout:15000}).toBeGreaterThan(0.1);
  await page.locator('video').evaluate((video:HTMLVideoElement)=>video.pause());
  await page.getByLabel('Note').fill(`Browser test bookmark ${testInfo.project.name}`);
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.getByRole('status')).toHaveText('Note saved');
  await page.getByRole('button',{name:'Bookmark selected recording',exact:true}).click();
  await expect(page.getByRole('button',{name:'Remove selected bookmark',exact:true})).toBeVisible();

  if(testInfo.project.name==='mobile')await page.getByRole('button',{name:'Open navigation'}).click();
  await page.getByRole('button',{name:'Bookmarks',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Bookmarks',exact:true})).toBeVisible();
  await expect(page.locator('.recording-card')).toHaveCount(1);
  await page.getByRole('button',{name:'Remove selected bookmark',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(0);

  if(testInfo.project.name==='mobile')await page.getByRole('button',{name:'Open navigation'}).click();
  await page.getByRole('button',{name:'Live view',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Live view',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'No live cameras connected'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Cameras',exact:true})).toHaveCount(0);
  if(testInfo.project.name==='mobile')await page.getByRole('button',{name:'Open navigation'}).click();
  await page.getByRole('button',{name:'Indexing & storage',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Indexing & storage',exact:true})).toBeVisible();
  await page.getByText('Camera connections & metadata',{exact:true}).click();
  await expect(page.locator('.device-card')).toHaveCount(2);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(errors).toEqual([]);
});

test('camera and date selection, empty state, and scrub previews',async({page},testInfo)=>{
  await page.goto('/');
  await expect(page.locator('.recording-card')).toHaveCount(6);
  if(testInfo.project.name==='mobile')await page.getByRole('button',{name:'Open navigation'}).click();
  await page.locator('.camera-item').filter({hasText:'Fixture Garden'}).click();
  await expect(page.locator('.recording-card')).toHaveCount(3);
  await page.getByRole('textbox',{name:'Search recordings'}).fill('not-a-real-recording');
  await expect(page.getByRole('heading',{name:'No recordings match these filters'})).toBeVisible();
  await page.getByRole('button',{name:'Clear filters',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(6);
  await page.getByLabel('Recording date').fill('2026-09-18');
  await expect(page.locator('.timeline-lane')).toHaveCount(2);
  if(testInfo.project.name==='desktop'){
    await page.locator('.card-image').first().hover();
    await expect(page.locator('.sprite-preview')).toBeVisible();
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:`test-results/archive-${testInfo.project.name}.png`,fullPage:true});
});

test('event and search filters also update timeline coverage',async({page},testInfo)=>{
  test.skip(testInfo.project.name!=='desktop');
  await page.goto('/');
  await expect(page.locator('.recording-card')).toHaveCount(6);
  await page.getByRole('button',{name:'Motion',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(0);
  await expect(page.locator('.timeline-lane')).toHaveCount(0);
  await expect(page.getByText('No timed recordings match these filters.')).toBeVisible();
  await expect(page.locator('.timeline-range-rail')).toBeVisible();

  await page.getByRole('button',{name:'Person',exact:true}).click();
  await expect(page.locator('.recording-card')).toHaveCount(3);
  await expect.poll(()=>page.locator('.timeline-lane').evaluateAll(lanes=>lanes.reduce((total,lane)=>total+lane.querySelectorAll('button.filled').length,0))).toBeGreaterThan(0);

  await page.getByRole('textbox',{name:'Search recordings'}).fill('no-such-clip');
  await expect(page.locator('.recording-card')).toHaveCount(0);
  await expect(page.locator('.timeline-lane')).toHaveCount(0);
});

test('new recording day appears without refreshing the page',async({page},testInfo)=>{
  test.skip(testInfo.project.name!=='desktop');
  let added=false;
  await page.route('**/api/status',async route=>{
    const response=await route.fetch();
    const body=await response.json();
    if(added){body.counts.recordings+=1;body.counts.last_day='2026-09-19';}
    await route.fulfill({response,json:body});
  });
  await page.route('**/api/dates*',async route=>{
    const response=await route.fetch();
    const body=await response.json();
    if(added)body.unshift({day:'2026-09-19',count:1});
    await route.fulfill({response,json:body});
  });
  await page.route('**/api/recordings?*',async route=>{
    if(!added||!new URL(route.request().url()).searchParams.has('day')||!route.request().url().includes('2026-09-19')){
      await route.continue();return;
    }
    const url=new URL(route.request().url());url.searchParams.set('day','2026-09-18');
    const response=await route.fetch({url:url.toString()});
    const body=await response.json();
    body.items=body.items.slice(0,1).map((item:Record<string,unknown>)=>({...item,local_day:'2026-09-19'}));
    body.next_cursor=null;
    await route.fulfill({response,json:body});
  });
  await page.goto('/');
  await expect(page.locator('.recording-card')).toHaveCount(6);
  await expect(page.getByLabel('Recording date')).toHaveValue('2026-09-18');
  added=true;
  await expect(page.getByLabel('Recording date')).toHaveValue('2026-09-19',{timeout:10000});
  await expect(page.locator('.recording-card')).toHaveCount(1);
});

test('timeline range filters recordings and can be moved, resized, and cleared',async({page},testInfo)=>{
  test.skip(testInfo.project.name!=='desktop');
  await page.goto('/');
  await expect(page.locator('.recording-card')).toHaveCount(6);
  const rail=page.locator('.timeline-range-rail');
  await expect(rail).toBeVisible();
  let box=await rail.boundingBox();
  if(!box)throw new Error('Timeline selection rail is missing');
  const x=(bin:number)=>box!.x+box!.width*bin/96;
  const y=box.y+box.height/2;

  await page.mouse.move(x(42),y);
  await page.mouse.down();
  await page.mouse.move(x(49),y,{steps:8});
  await page.mouse.up();
  await expect(page.locator('.recording-card')).toHaveCount(4);
  await expect(page).toHaveURL(/rangeStart=42&rangeEnd=49/);
  await page.screenshot({path:'test-results/timeline-range-desktop.png',fullPage:true});
  await page.reload();
  await expect(page.locator('.recording-card')).toHaveCount(4);

  box=await rail.boundingBox();
  if(!box)throw new Error('Timeline selection rail disappeared');
  let handle=await page.locator('.timeline-range-move').boundingBox();
  if(!handle)throw new Error('Move handle is missing');
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
  await page.mouse.down();
  await page.mouse.move(handle.x+handle.width/2+box.width*4/96,handle.y+handle.height/2,{steps:8});
  await page.mouse.up();
  await expect(page.locator('.recording-card')).toHaveCount(6);

  box=await rail.boundingBox();
  if(!box)throw new Error('Timeline selection rail disappeared');
  handle=await page.locator('.timeline-range-handle.start').boundingBox();
  if(!handle)throw new Error('Start resize handle is missing');
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
  await page.mouse.down();
  await page.mouse.move(box.x+box.width*49/96,handle.y+handle.height/2,{steps:8});
  await page.mouse.up();
  await expect(page.locator('.recording-card')).toHaveCount(2);

  box=await rail.boundingBox();
  if(!box)throw new Error('Timeline selection rail disappeared');
  handle=await page.locator('.timeline-range-move').boundingBox();
  if(!handle)throw new Error('Move handle is missing');
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
  await page.mouse.down();
  await page.mouse.move(handle.x+handle.width/2+box.width*7/96,handle.y+handle.height/2,{steps:8});
  await page.mouse.up();
  await expect(page.locator('.recording-card')).toHaveCount(0);
  await expect(page.getByText('No recordings match this time range.')).toBeVisible();
  await expect(rail).toBeVisible();
  await page.getByRole('button',{name:'Clear time range'}).click();
  await expect(page.locator('.recording-card')).toHaveCount(6);
  await expect(page).not.toHaveURL(/rangeStart=/);

  await rail.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/rangeStart=48&rangeEnd=52/);
  await page.locator('.timeline-range-move').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page).toHaveURL(/rangeStart=49&rangeEnd=53/);
  await page.keyboard.press('Escape');
  await expect(page).not.toHaveURL(/rangeStart=/);
});
