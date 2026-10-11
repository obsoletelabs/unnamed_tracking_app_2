import assert from 'node:assert/strict';

// The caller supplies a disposable signed-in app account. Steam responses are
// explicit fixtures; the QR contains no real sign-in capability or account data.
export async function checkSteamConnectionLogin({ member, origin, evidenceRoot, report }) {
  for (const width of [1440, 390]) {
    assert.equal((await member.request.patch(origin + '/api/preferences', { data: { ui_welcome_completed: true, ui_theme: width === 1440 ? 'dark' : 'light' } })).status(), 200);
    const page = await member.newPage();
    await page.setViewportSize({ width, height: 900 });
    let state = { connected: false, method: null, display_name: null, expires_at: null, pending: null };
    let polls = 0, guards = 0, cancels = 0;
    let delayPoll = false, releasePoll, pollStarted;
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/settings/steam**', async route => {
      const request = route.request(), url = new URL(request.url());
      assert.equal(url.search, '');
      const body = request.postDataJSON();
      if (url.pathname.endsWith('/login/qr')) {
        state.pending = { attempt_id: 'synthetic-qr', expires_at: Math.floor(Date.now() / 1000) + 300, interval: 3, phase: 'waiting', challenge_url: 'https://s.team/q/synthetic-ui-fixture', confirmation_types: [3] };
      } else if (url.pathname.endsWith('/login/account')) {
        assert.deepEqual(body, { account_name: 'fixture-account', password: 'Fixture-Password1!' });
        state.pending = { attempt_id: 'synthetic-account', expires_at: Math.floor(Date.now() / 1000) + 300, interval: 3, phase: 'waiting', challenge_url: null, confirmation_types: [1, 2, 3] };
      } else if (url.pathname.endsWith('/login/poll')) {
        polls++;
        if (delayPoll) {
          const stale = { ...state, pending: { ...state.pending } };
          await new Promise(resolve => { releasePoll = resolve; pollStarted(); });
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stale) });
          return;
        }
      } else if (url.pathname.endsWith('/login/guard')) {
        guards++;
        assert.deepEqual(body, { attempt_id: 'synthetic-account', code: 'ABCDE', code_type: 2 });
        state = { ...state, pending: null, connected: true, method: 'account', display_name: 'Fixture Player', expires_at: Math.floor(Date.now() / 1000) + 3600 };
      } else if (url.pathname.endsWith('/login/cancel')) {
        cancels++;
        assert.equal(body.attempt_id, state.pending?.attempt_id ?? body.attempt_id);
        state.pending = null;
      } else if (request.method() === 'DELETE') {
        state = { connected: false, method: null, display_name: null, expires_at: null, pending: null };
      }
      await route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(state) });
    });
    await page.goto(origin + '/settings?area=account&section=connections');
    const steam = page.locator('.rows .row').filter({ has: page.getByText('Steam', { exact: true }) });
    await steam.getByRole('button', { name: 'Manage', exact: true }).click();
    let modal = page.getByRole('dialog', { name: 'Connect Steam' });
    await modal.getByRole('button', { name: 'Show Steam QR code' }).click();
    const qr = modal.getByAltText('Steam sign-in QR code');
    await qr.waitFor();
    assert.match(await qr.getAttribute('src'), /^data:image\/png;base64,/);
    assert.equal(await qr.evaluate(image => image.naturalWidth), 256);
    assert(await modal.getByText('Scan with Steam Guard', { exact: false }).isVisible());
    await page.screenshot({ path: `${evidenceRoot}/qr-${width}.png`, fullPage: true });
    await modal.getByRole('button', { name: 'Cancel sign-in' }).click();
    await modal.getByRole('button', { name: 'Account sign-in', exact: true }).click();
    await modal.getByLabel('Steam account name').fill('fixture-account');
    await modal.getByLabel('Password', { exact: true }).fill('Fixture-Password1!');
    assert.equal(await modal.getByLabel('Password', { exact: true }).getAttribute('type'), 'password');
    await page.screenshot({ path: `${evidenceRoot}/account-${width}.png`, fullPage: true });
    await modal.getByRole('button', { name: 'Sign in to Steam', exact: true }).click();
    await modal.getByLabel('Enter the code from Steam Guard in your mobile app.').fill('ABCDE');
    await page.screenshot({ path: `${evidenceRoot}/guard-${width}.png`, fullPage: true });
    await modal.getByRole('button', { name: 'Submit Steam Guard code' }).click();
    await modal.getByText('Fixture Player connected', { exact: true }).waitFor();
    assert.equal(guards, 1);
    assert.equal(await page.evaluate(() => Object.values(localStorage).some(value => /Fixture-Password1!|ABCDE/.test(value))), false);
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    // Restart with a synthetic pending attempt and verify that closing cancels
    // and stops polling; a modal left behind after navigation cannot keep syncing.
    state = { connected: false, method: null, display_name: null, expires_at: null, pending: null };
    await steam.getByRole('button', { name: 'Manage', exact: true }).click();
    modal = page.getByRole('dialog', { name: 'Connect Steam' });
    delayPoll = true;
    const started = new Promise(resolve => { pollStarted = resolve; });
    await modal.getByRole('button', { name: 'Show Steam QR code' }).click();
    await modal.getByAltText('Steam sign-in QR code').waitFor();
    await Promise.race([started, new Promise((_, reject) => setTimeout(() => reject(new Error('Steam poll did not start')), 10000))]);
    await modal.getByRole('button', { name: 'Cancel sign-in' }).click();
    await modal.getByRole('button', { name: 'Show Steam QR code' }).waitFor();
    const lateReply = page.waitForResponse(response => response.url().endsWith('/api/settings/steam/login/poll'));
    releasePoll();
    await lateReply;
    await page.waitForTimeout(150);
    assert(await modal.getByRole('button', { name: 'Show Steam QR code' }).isVisible());
    assert.equal(await modal.getByRole('button', { name: 'Cancel sign-in' }).count(), 0);
    delayPoll = false;
    await modal.getByRole('button', { name: 'Show Steam QR code' }).click();
    await modal.getByAltText('Steam sign-in QR code').waitFor();
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    const closedPolls = polls;
    await page.waitForTimeout(3600);
    assert.equal(polls, closedPolls);
    assert(cancels >= 2);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    report.passed.push(`Steam QR/account/Guard, late-response cancellation, secret storage and responsive dialog at ${width}px`);
    await page.close();
  }
}
