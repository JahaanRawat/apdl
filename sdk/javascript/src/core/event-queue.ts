import type { ResolvedConfig } from './config';
import type { TrackEvent } from './types';
import { Transport } from './transport';
import { OfflineStorage } from './storage';
import type { Scrubber } from '../privacy/scrubber';
import type { ConsentManager } from '../privacy/consent';

/**
 * Batching event queue with offline fallback.
 * Collects events, batches them, and sends via Transport.
 * On failure, persists to IndexedDB for retry on next session.
 */
export class EventQueue {
  private queue: TrackEvent[] = [];
  private transport: Transport;
  private storage: OfflineStorage;
  private config: ResolvedConfig;
  private scrubber: Scrubber;
  private consentManager: ConsentManager;
  private flushTimer: ReturnType<typeof setInterval> | null = null;
  private isFlushing = false;
  private visibilityHandler: (() => void) | null = null;
  private unloadHandler: (() => void) | null = null;
  private started = false;

  constructor(
    config: ResolvedConfig,
    transport: Transport,
    storage: OfflineStorage,
    scrubber: Scrubber,
    consentManager: ConsentManager
  ) {
    this.config = config;
    this.transport = transport;
    this.storage = storage;
    this.scrubber = scrubber;
    this.consentManager = consentManager;
  }

  /**
   * Adds an event to the queue. Runs scrubbing, checks consent,
   * and auto-flushes when batch size is reached.
   */
  enqueue(event: TrackEvent): void {
    // Check consent for analytics
    if (!this.consentManager.isGranted('analytics')) {
      if (this.config.debug) {
        console.debug('APDL: Event dropped — analytics consent not granted');
      }
      return;
    }

    // Run through scrubber pipeline
    const scrubbed = this.scrubber.scrub(event);
    if (scrubbed === null) {
      if (this.config.debug) {
        console.debug('APDL: Event dropped by scrubber pipeline');
      }
      return;
    }

    // Enforce max queue size
    if (this.queue.length >= this.config.maxQueueSize) {
      if (this.config.debug) {
        console.warn('APDL: Queue full, dropping oldest event');
      }
      this.queue.shift();
    }

    this.queue.push(scrubbed);

    // Auto-flush when batch size is reached
    if (this.queue.length >= this.config.batchSize) {
      void this.flush();
    }
  }

  /**
   * Flushes the current queue by sending events via the transport.
   * On failure, persists events to offline storage.
   */
  async flush(): Promise<void> {
    if (this.isFlushing || this.queue.length === 0) {
      return;
    }

    this.isFlushing = true;

    // Snapshot and clear queue atomically
    const batch = this.queue.splice(0, this.config.batchSize);

    try {
      const url = `${this.config.host}/v1/batch`;
      const payload = {
        batch,
        sentAt: new Date().toISOString(),
      };

      const success = await this.transport.send(url, payload);

      if (!success) {
        // Persist failed batch to offline storage
        if (this.config.debug) {
          console.warn('APDL: Batch send failed, persisting to offline storage');
        }
        await this.storage.store(batch);
      }
    } catch (err) {
      if (this.config.debug) {
        console.error('APDL: Unexpected error during flush:', err);
      }
      await this.storage.store(batch);
    } finally {
      this.isFlushing = false;
    }

    // If there are more events remaining, flush again
    if (this.queue.length >= this.config.batchSize) {
      void this.flush();
    }
  }

  /**
   * Uses sendBeacon for reliable delivery during page unload.
   */
  flushOnUnload(): void {
    if (this.queue.length === 0) return;

    const batch = this.queue.splice(0);
    const url = `${this.config.host}/v1/batch`;
    const payload = {
      batch,
      sentAt: new Date().toISOString(),
    };

    const accepted = this.transport.sendBeacon(url, payload);

    if (!accepted) {
      // sendBeacon failed; try to persist to offline storage synchronously.
      // IndexedDB is async so we store in memory fallback which is best-effort.
      void this.storage.store(batch);
    }
  }

  /**
   * Starts the flush interval, registers page lifecycle listeners,
   * and drains any events from offline storage.
   */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    // Start periodic flush
    this.flushTimer = setInterval(() => {
      void this.flush();
    }, this.config.flushInterval);

    // Register visibility change listener for flush on hide
    if (typeof document !== 'undefined') {
      this.visibilityHandler = () => {
        if (document.visibilityState === 'hidden') {
          this.flushOnUnload();
        }
      };
      document.addEventListener('visibilitychange', this.visibilityHandler);
    }

    // Register beforeunload handler
    if (typeof window !== 'undefined') {
      this.unloadHandler = () => {
        this.flushOnUnload();
      };
      window.addEventListener('beforeunload', this.unloadHandler);
    }

    // Drain offline storage and re-enqueue events
    try {
      const offlineEvents = await this.storage.drain();
      if (offlineEvents.length > 0) {
        if (this.config.debug) {
          console.debug(`APDL: Drained ${offlineEvents.length} events from offline storage`);
        }
        // Push directly to queue (already scrubbed and consent-checked)
        this.queue.push(...offlineEvents);
        if (this.queue.length >= this.config.batchSize) {
          void this.flush();
        }
      }
    } catch (err) {
      if (this.config.debug) {
        console.error('APDL: Failed to drain offline storage:', err);
      }
    }
  }

  /**
   * Stops the flush interval and removes lifecycle listeners.
   */
  stop(): void {
    this.started = false;

    if (this.flushTimer !== null) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }

    if (typeof document !== 'undefined' && this.visibilityHandler) {
      document.removeEventListener('visibilitychange', this.visibilityHandler);
      this.visibilityHandler = null;
    }

    if (typeof window !== 'undefined' && this.unloadHandler) {
      window.removeEventListener('beforeunload', this.unloadHandler);
      this.unloadHandler = null;
    }
  }

  /**
   * Returns a snapshot of the current queue (for debugging).
   */
  getQueue(): TrackEvent[] {
    return [...this.queue];
  }

  /**
   * Returns the current queue length.
   */
  get length(): number {
    return this.queue.length;
  }
}
