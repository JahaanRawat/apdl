import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { IDBFactory } from 'fake-indexeddb';
import { JSDOM } from 'jsdom';

const CLIENT_KEY = 'client_apdl_0123456789abcdef';
const FIRST_ENDPOINT = 'https://api.first.test';
const SECOND_ENDPOINT = 'https://api.second.test';
const bundle = await readFile(new URL('../dist/apdl.iife.js', import.meta.url), 'utf8');

await run(
  'RA-01 built bundle makes explicit denial authoritative and rejects legacy consent',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const scopedKey = storageKey('consent', FIRST_ENDPOINT);
    const anonymousKey = storageKey('anonymous_id', FIRST_ENDPOINT);
    const sessionKey = storageKey('session', FIRST_ENDPOINT);
    const grant = {
      analytics: true,
      personalization: true,
      experiments: true,
    };
    window.localStorage.setItem(scopedKey, JSON.stringify(grant));
    window.localStorage.setItem(anonymousKey, 'stale-anonymous-id');
    window.localStorage.setItem(sessionKey, JSON.stringify({
      id: 'stale-session',
      startedAt: Date.now(),
      lastActivityAt: Date.now(),
      eventCount: 1,
      pageCount: 1,
    }));
    window.localStorage.setItem('apdl_consent_apdl', JSON.stringify(grant));

    const client = new harness.APDLClient({
      endpoint: FIRST_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      autoCapture: false,
      persistence: 'localStorage',
      consent: {
        analytics: false,
        personalization: false,
        experiments: false,
      },
    });

    assert.deepEqual(normalize(client.consent.get()), {
      analytics: false,
      personalization: false,
      experiments: false,
    });
    assert.deepEqual(normalize(client.debug.getQueue()), []);
    assert.deepEqual(JSON.parse(window.localStorage.getItem(scopedKey)), {
      analytics: false,
      personalization: false,
      experiments: false,
    });
    assert.equal(window.localStorage.getItem(anonymousKey), null);
    assert.equal(window.localStorage.getItem(sessionKey), null);
    assert.equal(window.localStorage.getItem('apdl_consent_apdl'), null);

    client.track('denied-event');
    client.identify('denied-user');
    client.page('Denied page');
    client.reset();
    assert.deepEqual(normalize(client.debug.getQueue()), []);
    assert.equal(window.localStorage.getItem(anonymousKey), null);
    assert.equal(window.localStorage.getItem(sessionKey), null);

    client.consent.update({ analytics: true });
    assert.notEqual(window.localStorage.getItem(anonymousKey), null);
    assert.equal(window.localStorage.getItem(sessionKey), null);
    client.track('granted-event');
    assert.notEqual(window.localStorage.getItem(sessionKey), null);

    client.consent.update({ analytics: false });
    await settle(window);
    assert.deepEqual(normalize(client.debug.getQueue()), []);
    assert.equal(window.localStorage.getItem(anonymousKey), null);
    assert.equal(window.localStorage.getItem(sessionKey), null);

    await client.shutdown();
    harness.close();
  }
);

await run(
  'RA-01 built bundle rejects a legacy-only persisted grant when consent is omitted',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const grant = {
      analytics: true,
      personalization: true,
      experiments: true,
    };
    window.localStorage.setItem('apdl_consent_apdl', JSON.stringify(grant));

    const client = new harness.APDLClient({
      endpoint: FIRST_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      autoCapture: false,
      persistence: 'localStorage',
    });

    assert.deepEqual(normalize(client.consent.get()), {
      analytics: false,
      personalization: false,
      experiments: false,
    });
    assert.equal(window.localStorage.getItem('apdl_consent_apdl'), null);
    assert.deepEqual(
      JSON.parse(window.localStorage.getItem(storageKey('consent', FIRST_ENDPOINT))),
      {
        analytics: false,
        personalization: false,
        experiments: false,
      }
    );

    await client.shutdown();
    harness.close();
  }
);

await run(
  'RA-01 built bundle isolates all persisted state by deployment and project',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const consent = {
      analytics: true,
      personalization: true,
      experiments: true,
    };
    const first = new harness.APDLClient({
      endpoint: FIRST_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      persistence: 'localStorage',
      consent,
    });
    first.track('first_deployment_event');
    assert.equal(first.debug.getQueue().length, 1);
    await settle(window);

    const secondConfig = {
      endpoint: SECOND_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      persistence: 'localStorage',
    };
    const second = new harness.APDLClient(secondConfig);
    await settle(window);

    assert.deepEqual(normalize(second.consent.get()), {
      analytics: false,
      personalization: false,
      experiments: false,
    });
    assert.deepEqual(normalize(second.debug.getQueue()), []);
    second.consent.update(consent);
    second.track('second_deployment_event');
    await settle(window);

    for (const kind of ['anonymous_id', 'consent', 'flags', 'session']) {
      const firstKey = storageKey(kind, FIRST_ENDPOINT);
      const secondKey = storageKey(kind, SECOND_ENDPOINT);
      assert.notEqual(window.localStorage.getItem(firstKey), null, `${kind} missing`);
      assert.notEqual(window.localStorage.getItem(secondKey), null, `${kind} missing`);
      assert.notEqual(firstKey, secondKey);
    }
    assert.notEqual(
      window.localStorage.getItem(storageKey('anonymous_id', FIRST_ENDPOINT)),
      window.localStorage.getItem(storageKey('anonymous_id', SECOND_ENDPOINT))
    );
    assert.notEqual(
      window.localStorage.getItem(storageKey('session', FIRST_ENDPOINT)),
      window.localStorage.getItem(storageKey('session', SECOND_ENDPOINT))
    );
    assert.notEqual(
      window.localStorage.getItem(storageKey('flags', FIRST_ENDPOINT)),
      window.localStorage.getItem(storageKey('flags', SECOND_ENDPOINT))
    );
    assert.equal(first.storage.deploymentOrigin, FIRST_ENDPOINT);
    assert.equal(second.storage.deploymentOrigin, SECOND_ENDPOINT);
    assert.equal(first.storage.projectId, 'apdl');
    assert.equal(second.storage.projectId, 'apdl');

    await Promise.all([first.shutdown(), second.shutdown()]);
    harness.close();
  }
);

await run(
  'RA-01 built bundle isolates IndexedDB offline events by deployment and project',
  async () => {
    const harness = createHarness({
      accelerateRetryTimers: true,
      emulateIndexedDbTargetRealm: true,
      retryableEventOrigins: [FIRST_ENDPOINT],
    });
    const { window } = harness;
    const consent = {
      analytics: true,
      personalization: false,
      experiments: false,
    };
    const first = new harness.APDLClient({
      endpoint: FIRST_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      autoCapture: false,
      persistence: 'localStorage',
      consent,
    });
    await settle(window);
    first.track('first_deployment_offline_event');

    const firstReport = await first.debug.flush();
    assert.equal(firstReport.persisted, 1);
    assert.deepEqual(normalize(firstReport.pending), []);
    let records = await readOfflineRecords(window);
    assert.equal(await first.storage.count(), 1);
    assert.equal(records.length, 1);
    assert.equal(records[0].deployment_origin, FIRST_ENDPOINT);
    assert.equal(records[0].project_id, 'apdl');
    assert.equal(records[0].data.event, 'first_deployment_offline_event');

    const second = new harness.APDLClient({
      endpoint: SECOND_ENDPOINT,
      auth: { clientKey: CLIENT_KEY },
      autoCapture: false,
      persistence: 'localStorage',
      consent,
    });
    const secondReport = await second.debug.flush();

    assert.equal(secondReport.delivered, 0);
    assert.equal(secondReport.persisted, 0);
    assert.deepEqual(normalize(second.debug.getQueue()), []);
    records = await readOfflineRecords(window);
    assert.equal(records.length, 1);
    assert.equal(records[0].deployment_origin, FIRST_ENDPOINT);
    assert.equal(records[0].data.event, 'first_deployment_offline_event');

    await Promise.all([first.shutdown(), second.shutdown()]);
    harness.close();
  }
);

await run(
  'RA-03 built bundle renders untrusted modal markup as Trusted Types-compatible text',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const client = createUiClient(harness);
    const target = window.document.createElement('div');
    window.document.body.appendChild(target);
    window.__apdlXssExecuted = false;
    const payload = [
      '<img src=x onerror="window.__apdlXssExecuted = true">',
      '<svg onload="window.__apdlXssExecuted = true"></svg>',
      '<script>window.__apdlXssExecuted = true</script>',
    ].join('');
    const descriptor = Object.getOwnPropertyDescriptor(
      window.Element.prototype,
      'innerHTML'
    );
    assert.ok(descriptor);
    Object.defineProperty(window.Element.prototype, 'innerHTML', {
      ...descriptor,
      set() {
        throw new window.TypeError('Trusted Types policy rejected innerHTML');
      },
    });

    try {
      const rendered = client.ui.render({
        component: 'modal',
        props: { title: 'Untrusted content', body: payload },
        slotId: 'security-modal',
      }, target);

      assert.ok(rendered);
      assert.equal(rendered.querySelector('img, svg, script'), null);
      assert.equal(rendered.textContent.includes(payload), true);
      assert.equal(window.__apdlXssExecuted, false);
    } finally {
      Object.defineProperty(window.Element.prototype, 'innerHTML', descriptor);
    }

    await client.shutdown();
    harness.close();
  }
);

await run(
  'RA-03 built bundle treats absent-like optional UI URLs as no URL',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const client = createUiClient(harness);

    for (const [valueIndex, optionalUrl] of [undefined, null, ''].entries()) {
      const cases = [
        ['banner', {
          text: 'Banner',
          ctaText: 'Continue',
          ctaHref: optionalUrl,
        }],
        ['card', {
          title: 'Card',
          imageUrl: optionalUrl,
          ctaText: 'Continue',
          ctaHref: optionalUrl,
        }],
        ['cta-button', {
          text: 'Continue',
          href: optionalUrl,
        }],
        ['modal', {
          title: 'Modal',
          ctaText: 'Continue',
          ctaHref: optionalUrl,
        }],
      ];

      for (const [caseIndex, [component, props]] of cases.entries()) {
        const target = window.document.createElement('div');
        window.document.body.appendChild(target);
        const rendered = client.ui.render({
          component,
          props,
          slotId: `absent-url-${valueIndex}-${caseIndex}`,
        }, target);
        assert.ok(rendered, `${component} rejected an absent-like optional URL`);
        assert.equal(rendered.querySelector('a, img'), null);
        if (component === 'cta-button') {
          assert.equal(rendered.tagName, 'BUTTON');
        } else {
          assert.equal(rendered.textContent.includes('Continue'), true);
        }
      }
    }

    await client.shutdown();
    harness.close();
  }
);

await run(
  'RA-03 built bundle rejects scriptable, data, malformed, and relative UI URLs',
  async () => {
    const harness = createHarness();
    const { window } = harness;
    const client = createUiClient(harness);
    const cases = [
      ['banner', {
        text: 'Banner',
        ctaText: 'Open',
        ctaHref: 'javascript:window.__apdlXssExecuted=true',
      }],
      ['card', {
        title: 'Card',
        ctaText: 'Open',
        ctaHref: 'data:text/html,<script>alert(1)</script>',
      }],
      ['card', {
        title: 'Card',
        imageUrl: 'data:image/svg+xml,<svg onload=alert(1)>',
      }],
      ['cta-button', { text: 'Open', href: 'java\nscript:alert(1)' }],
      ['cta-button', { text: 'Open', href: 'jav\u0000ascript:alert(1)' }],
      ['cta-button', { text: 'Open', href: 'https:\\evil.example/path' }],
      ['modal', { title: 'Modal', ctaText: 'Open', ctaHref: '//evil.example' }],
      ['modal', { title: 'Modal', ctaText: 'Open', ctaHref: '/relative' }],
      ['modal', { title: 'Modal', ctaText: 'Open', ctaHref: '#fragment' }],
    ];

    for (const [index, [component, props]] of cases.entries()) {
      const target = window.document.createElement('div');
      window.document.body.appendChild(target);
      const rendered = client.ui.render({
        component,
        props,
        slotId: `unsafe-url-${index}`,
      }, target);
      assert.equal(rendered, null, `${component} accepted an unsafe URL`);
      assert.equal(target.querySelector('a, img, svg, script'), null);
    }

    const safeTarget = window.document.createElement('div');
    window.document.body.appendChild(safeTarget);
    const safeLink = client.ui.render({
      component: 'cta-button',
      props: {
        text: 'Safe link',
        href: 'https://safe.example/path',
        target: '_blank',
      },
      slotId: 'safe-url',
    }, safeTarget);
    assert.ok(safeLink);
    assert.equal(safeLink.href, 'https://safe.example/path');
    assert.equal(safeLink.rel, 'noopener noreferrer');

    await client.shutdown();
    harness.close();
  }
);

function createHarness(options = {}) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'https://customer.test/',
  });
  const { window } = dom;
  const indexedDB = new IDBFactory();
  const nativeStructuredClone = globalThis.structuredClone;

  if (options.emulateIndexedDbTargetRealm) {
    // fake-indexeddb clones through Node's realm even when the IDBFactory is
    // installed on a JSDOM window. IndexedDB deserializes into the requesting
    // realm in browsers, so re-home the JSON-only SDK records for this test.
    globalThis.structuredClone = (value, structuredCloneOptions) => {
      const clone = nativeStructuredClone(value, structuredCloneOptions);
      if (clone === null || typeof clone !== 'object') return clone;
      return window.JSON.parse(JSON.stringify(clone));
    };
  }

  Object.defineProperty(window, 'indexedDB', {
    configurable: true,
    value: indexedDB,
  });
  for (const name of [
    'Headers',
    'ReadableStream',
    'Request',
    'Response',
    'TextDecoder',
    'TextEncoder',
  ]) {
    if (!(name in window) && name in globalThis) {
      Object.defineProperty(window, name, {
        configurable: true,
        value: globalThis[name],
      });
    }
  }
  if (options.accelerateRetryTimers) {
    const nativeSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = (callback, delay = 0, ...args) =>
      nativeSetTimeout(callback, Number(delay) >= 1000 ? 0 : delay, ...args);
  }
  const retryableEventOrigins = new Set(options.retryableEventOrigins ?? []);

  window.fetch = async (input, init = {}) => {
    const url = String(input);
    if (url.endsWith('/v1/events')) {
      if (retryableEventOrigins.has(new URL(url).origin)) {
        throw new window.TypeError('Simulated offline event endpoint');
      }
      return new window.Response(null, { status: 202 });
    }
    if (url.endsWith('/v1/flags')) {
      const flagKey = url.startsWith(FIRST_ENDPOINT) ? 'first-flag' : 'second-flag';
      return new window.Response(JSON.stringify({
        schema_version: 2,
        project_id: 'apdl',
        flags: [makeFlag(flagKey)],
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.endsWith('/v1/stream')) {
      const body = new window.ReadableStream({
        start(controller) {
          init.signal?.addEventListener('abort', () => {
            controller.error(new window.DOMException('Aborted', 'AbortError'));
          }, { once: true });
        },
      });
      return new window.Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    }
    throw new Error(`Unexpected built-browser request: ${url}`);
  };

  window.eval(bundle);
  assert.equal(typeof window.APDL?.APDLClient, 'function');
  return {
    APDLClient: window.APDL.APDLClient,
    close: () => {
      globalThis.structuredClone = nativeStructuredClone;
      dom.window.close();
    },
    window,
  };
}

function createUiClient(harness) {
  return new harness.APDLClient({
    endpoint: FIRST_ENDPOINT,
    auth: { clientKey: CLIENT_KEY },
    persistence: 'memory',
    consent: {
      analytics: false,
      personalization: true,
      experiments: false,
    },
  });
}

function readOfflineRecords(window) {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open('apdl-offline');
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const transaction = db.transaction('events', 'readonly');
      const allRecords = transaction.objectStore('events').getAll();
      let records = [];
      allRecords.onerror = () => reject(allRecords.error);
      allRecords.onsuccess = () => {
        records = normalize(allRecords.result);
      };
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
      transaction.oncomplete = () => {
        db.close();
        resolve(records);
      };
    };
  });
}

function storageKey(kind, endpoint) {
  return `apdl_${kind}_v2_${encodeURIComponent(new URL(endpoint).origin)}_apdl`;
}

function makeFlag(key) {
  return {
    key,
    enabled: true,
    default_variant: 'control',
    variants: [
      { key: 'control', weight: 1 },
      { key: 'treatment', weight: 1 },
    ],
    salt: 'built-browser-salt',
    rules: [],
    fallthrough: {
      rollout: { percentage: 100, bucket_by: 'user_id' },
    },
    version: 1,
  };
}

function normalize(value) {
  return JSON.parse(JSON.stringify(value));
}

async function settle(window) {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}

async function run(name, test) {
  try {
    await test();
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`not ok - ${name}`);
    throw error;
  }
}
