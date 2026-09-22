import { expect, test } from '@playwright/test';

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
  await page.getByLabel('YOUR NOTE').fill(`Browser test bookmark ${testInfo.project.name}`);
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await expect(page.getByRole('status')).toHaveText('Note saved');
  await page.getByRole('button',{name:'Bookmark selected recording',exact:true}).click();
  await expect(page.getByRole('button',{name:'Remove selected bookmark',exact:true})).toBeVisible();

  if(testInfo.project.name==='mobile')await page.getByRole('button',{name:'Open navigation'}).click();
  await page.getByRole('button',{name:'Bookmarks',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Bookmarked moments',exact:true})).toBeVisible();
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
