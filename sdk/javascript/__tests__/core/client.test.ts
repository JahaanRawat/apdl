import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { APDLClient } from '../../src/core/client';

// Mock fetch globally
const fetchMock = vi.fn().mockResolvedValue({
  ok: true,
  json: () => Promise.resolve({ flags: [] }),
  status: 200,
  headers: new Headers(),
});

vi.stubGlobal('fetch', fetchMock);

// Mock EventSource
class MockEventSource {
  static instances: MockEventSource[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  readyState = 0;

  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener() {}
  close() {
    this.readyState = 2;
  }
}

vi.stubGlobal('EventSource', MockEventSource);

describe('APDLClient', () => {
  let client: APDLClient;

  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock.mockClear();
    MockEventSource.instances = [];
    localStorage.clear();

    client = new APDLClient({
      apiKey: 'test-key-123',
      host: 'https://ingest.test.dev',
      configHost: 'https://config.test.dev',
      autoCapture: false,
      persistence: 'memory',
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('initialization', () => {
    it('should create a client with valid config', () => {
      expect(client).toBeInstanceOf(APDLClient);
    });

    it('should throw on missing apiKey', () => {
      expect(() => new APDLClient({ apiKey: '' })).toThrow('apiKey is required');
    });

    it('should expose public namespaces', () => {
      expect(client.ui).toBeDefined();
      expect(client.ui.register).toBeTypeOf('function');
      expect(client.ui.render).toBeTypeOf('function');
      expect(client.ui.onSlotUpdate).toBeTypeOf('function');

      expect(client.consent).toBeDefined();
      expect(client.consent.get).toBeTypeOf('function');
      expect(client.consent.update).toBeTypeOf('function');
      expect(client.consent.onUpdate).toBeTypeOf('function');

      expect(client.privacy).toBeDefined();
      expect(client.privacy.addScrubber).toBeTypeOf('function');
      expect(client.privacy.removeScrubber).toBeTypeOf('function');

      expect(client.debug).toBeDefined();
      expect(client.debug.enable).toBeTypeOf('function');
      expect(client.debug.disable).toBeTypeOf('function');
      expect(client.debug.getQueue).toBeTypeOf('function');
      expect(client.debug.flush).toBeTypeOf('function');
    });
  });

  describe('track()', () => {
    it('should enqueue a track event', () => {
      client.track('button_clicked', { buttonId: 'signup' });

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(1);
      expect(queue[0]).toMatchObject({
        type: 'track',
        event: 'button_clicked',
        properties: { buttonId: 'signup' },
      });
    });

    it('should include a timestamp and messageId', () => {
      client.track('test_event');

      const queue = client.debug.getQueue();
      expect(queue[0]).toHaveProperty('timestamp');
      expect(queue[0]).toHaveProperty('messageId');
      expect(typeof (queue[0] as Record<string, unknown>).timestamp).toBe('string');
      expect(typeof (queue[0] as Record<string, unknown>).messageId).toBe('string');
    });

    it('should include a sessionId', () => {
      client.track('test_event');

      const queue = client.debug.getQueue();
      expect(queue[0]).toHaveProperty('sessionId');
      expect(typeof (queue[0] as Record<string, unknown>).sessionId).toBe('string');
    });
  });

  describe('identify()', () => {
    it('should enqueue an identify event', () => {
      client.identify('user-42', { plan: 'pro', name: 'Alice' });

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(1);
      expect(queue[0]).toMatchObject({
        type: 'identify',
        userId: 'user-42',
        traits: { plan: 'pro', name: 'Alice' },
      });
    });

    it('should set userId on subsequent events', () => {
      client.identify('user-42');
      client.track('page_loaded');

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(2);
      expect((queue[1] as Record<string, unknown>).userId).toBe('user-42');
    });
  });

  describe('group()', () => {
    it('should enqueue a group event', () => {
      client.group('company-99', { industry: 'tech' });

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(1);
      expect(queue[0]).toMatchObject({
        type: 'group',
        groupId: 'company-99',
        traits: { industry: 'tech' },
      });
    });
  });

  describe('page()', () => {
    it('should enqueue a page event', () => {
      client.page('Home');

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(1);
      expect(queue[0]).toMatchObject({
        type: 'page',
        event: 'Home',
      });
    });

    it('should include page URL context', () => {
      client.page();

      const queue = client.debug.getQueue();
      const event = queue[0] as Record<string, unknown>;
      const props = event.properties as Record<string, unknown>;
      expect(props).toHaveProperty('url');
      expect(props).toHaveProperty('title');
    });
  });

  describe('reset()', () => {
    it('should clear userId after reset', () => {
      client.identify('user-42');
      client.reset();
      client.track('after_reset');

      const queue = client.debug.getQueue();
      // The identify and after_reset events (queue was flushed/cleared partially)
      const afterReset = queue.find(
        (e: unknown) => (e as Record<string, unknown>).event === 'after_reset'
      ) as Record<string, unknown> | undefined;
      expect(afterReset?.userId).toBeUndefined();
    });
  });

  describe('flag()', () => {
    it('should return default value when flag not found', () => {
      expect(client.flag('nonexistent')).toBe(false);
      expect(client.flag('nonexistent', true)).toBe(true);
    });
  });

  describe('experiment()', () => {
    it('should return "control" when flag not found', () => {
      expect(client.experiment('nonexistent')).toBe('control');
    });
  });

  describe('consent', () => {
    it('should return default consent state', () => {
      const state = client.consent.get();
      expect(state.analytics).toBe(true);
      expect(state.personalization).toBe(true);
      expect(state.experiments).toBe(true);
    });

    it('should update consent state', () => {
      client.consent.update({ analytics: false });

      const state = client.consent.get();
      expect(state.analytics).toBe(false);
      expect(state.personalization).toBe(true);
    });

    it('should notify on consent change', () => {
      const callback = vi.fn();
      client.consent.onUpdate(callback);

      client.consent.update({ analytics: false });
      expect(callback).toHaveBeenCalledWith(
        expect.objectContaining({ analytics: false })
      );
    });

    it('should drop events when analytics consent is denied', () => {
      client.consent.update({ analytics: false });
      client.track('should_be_dropped');

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(0);
    });
  });

  describe('debug namespace', () => {
    it('should return current queue', () => {
      client.track('e1');
      client.track('e2');

      const queue = client.debug.getQueue();
      expect(queue.length).toBe(2);
    });

    it('should flush the queue', async () => {
      client.track('flush_test');
      await client.debug.flush();

      // After flush, queue should be empty (events sent or stored)
      const queue = client.debug.getQueue();
      expect(queue.length).toBe(0);
    });
  });

  describe('shutdown()', () => {
    it('should gracefully shut down', async () => {
      client.track('before_shutdown');
      await client.shutdown();
      // No errors thrown
    });
  });
});
