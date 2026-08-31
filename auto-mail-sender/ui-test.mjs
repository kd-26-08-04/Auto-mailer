import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:5001';
const errors = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.on('pageerror', (e) => errors.push('PAGE ERROR: ' + e.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push('CONSOLE: ' + msg.text());
  });

  // Login page
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
  const loginTitle = await page.title();
  console.log('Login page title:', loginTitle);

  // Register test user
  const user = 'uitest_' + Date.now();
  await page.goto(BASE + '/register');
  await page.fill('input[name="full_name"]', 'UI Tester');
  await page.fill('input[name="phone"]', '9999999999');
  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', 'testpass123');
  await page.fill('input[name="confirm_password"]', 'testpass123');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/login**', { timeout: 5000 }).catch(() => {});

  await page.fill('input[name="username"]', user);
  await page.fill('input[name="password"]', 'testpass123');
  await page.click('button[type="submit"]');
  await page.waitForURL(BASE + '/', { timeout: 8000 });

  console.log('Dashboard loaded:', page.url());

  // Check main nav
  const dashboardVisible = await page.locator('#view-dashboard').isVisible();
  const sequencesNav = page.locator('[data-view="sequences"]');
  console.log('Dashboard visible:', dashboardVisible);

  // Sequences view
  await sequencesNav.click();
  await page.waitForTimeout(500);
  const seqListVisible = await page.locator('#sequences-list-view').isVisible();
  console.log('Sequences list visible:', seqListVisible);

  // New sequence builder
  await page.click('button:has-text("New Sequence")');
  await page.waitForTimeout(800);
  const builderVisible = await page.locator('#sequence-builder-view').isVisible();
  console.log('Builder visible:', builderVisible);

  // Template picker
  await page.waitForTimeout(1000);
  const templateOptions = await page.locator('#template-picker option').count();
  console.log('Template options count:', templateOptions);
  if (templateOptions <= 1) errors.push('Template picker has no Apollo templates loaded');

  // Load a template
  await page.selectOption('#template-picker', { index: 1 });
  await page.waitForTimeout(1500);
  const stepCards = await page.locator('.sequence-step-card').count();
  console.log('Step cards after template load:', stepCards);
  if (stepCards < 5) errors.push('Expected 5 steps after loading Apollo template, got ' + stepCards);

  const seqName = await page.inputValue('#seq-name-input');
  console.log('Sequence name after template:', seqName);

  // Settings view
  await page.locator('[data-view="settings"]').click();
  await page.waitForTimeout(500);
  const settingsVisible = await page.locator('#view-settings').isVisible();
  console.log('Settings visible:', settingsVisible);

  // Dashboard back
  await page.locator('[data-view="dashboard"]').click();
  await page.waitForTimeout(500);

  // Screenshot
  await page.screenshot({ path: 'C:/projects/Auto-mailer/auto-mail-sender/ui-test-screenshot.png', fullPage: true });
  console.log('Screenshot saved');

  await browser.close();

  if (errors.length) {
    console.log('\n=== UI ERRORS ===');
    errors.forEach((e) => console.log(e));
    process.exit(1);
  }
  console.log('\n=== UI TEST PASSED ===');
})();
