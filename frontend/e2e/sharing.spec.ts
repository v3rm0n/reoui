import { expect, test } from '@playwright/test';

test('share one recording, play as a guest, and revoke access', async ({page, browser}) => {
  if (process.env.REOUI_TEST_AUTH_TOKEN) await page.request.post('/api/login', {data: {token: process.env.REOUI_TEST_AUTH_TOKEN}});
  await page.goto('/');
  await page.getByRole('button', {name: 'Share recording', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: 'Share recording'});
  await expect(dialog).toBeVisible();
  await page.getByLabel('Share link expiry').selectOption('1');
  await page.getByRole('button', {name: 'Create link', exact: true}).click();
  const input = page.getByRole('textbox', {name: 'New share link'});
  await expect(input).toHaveValue(/\/share\/[A-Za-z0-9_-]{43}$/);
  const url = await input.inputValue();
  const guestContext = await browser.newContext();
  const guest = await guestContext.newPage();
  try {
    await guest.goto(url);
    await expect(guest.getByText('Shared recording', {exact: true})).toBeVisible();
    await expect(guest.getByRole('button', {name: 'Open archive'})).toHaveCount(0);
    await guest.locator('video').evaluate((video: HTMLVideoElement) => video.play());
    await expect.poll(() => guest.locator('video').evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.1);
    expect(await guest.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.getByRole('button', {name: 'Revoke', exact: true}).first().click();
    await expect(input).toHaveCount(0);
    await guest.reload();
    await expect(guest.getByRole('heading', {name: 'Recording unavailable'})).toBeVisible();
    await expect(guest.locator('video')).toHaveCount(0);
  } finally { await guestContext.close(); }
  await page.getByRole('button', {name: 'Close sharing'}).click();
});

test('sign in, sign out, and protect direct recording links', async ({page}) => {
  test.skip(!process.env.REOUI_TEST_AUTH_TOKEN, 'Set REOUI_TEST_AUTH_TOKEN to exercise real authentication');
  await page.goto('/?clip=private-recording');
  await expect(page.getByRole('heading', {name: 'Access archive'})).toBeVisible();
  await page.getByLabel('Access token').fill('incorrect-token');
  await page.getByRole('button', {name: 'Open archive'}).click();
  await expect(page.getByRole('alert')).toHaveText('Incorrect access token');
  await page.getByLabel('Access token').fill(process.env.REOUI_TEST_AUTH_TOKEN!);
  await page.getByRole('button', {name: 'Open archive'}).click();
  await expect(page.getByRole('heading', {name: 'Recording archive', exact: true})).toBeVisible();
  if (test.info().project.name === 'mobile') await page.getByRole('button', {name: 'Open navigation'}).click();
  await page.getByRole('button', {name: 'Sign out'}).click();
  await expect(page.getByRole('heading', {name: 'Access archive'})).toBeVisible();
  expect((await page.request.get('/api/recordings')).status()).toBe(401);
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Access archive'})).toBeVisible();
});
