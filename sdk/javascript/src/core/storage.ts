import type { TrackEvent } from './types';

const DB_NAME = 'apdl-offline';
const STORE_NAME = 'events';
const DB_VERSION = 1;

/**
 * IndexedDB-backed offline event storage with in-memory fallback.
 * Events that fail to send are stored here and drained on next startup.
 */
export class OfflineStorage {
  private fallbackQueue: TrackEvent[] = [];
  private dbPromise: Promise<IDBDatabase> | null = null;
  private useMemory = false;

  constructor() {
    this.dbPromise = this.openDB();
  }

  private openDB(): Promise<IDBDatabase> | null {
    if (typeof indexedDB === 'undefined') {
      this.useMemory = true;
      return null;
    }

    return new Promise<IDBDatabase>((resolve, reject) => {
      try {
        const request = indexedDB.open(DB_NAME, DB_VERSION);

        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(STORE_NAME)) {
            db.createObjectStore(STORE_NAME, {
              keyPath: 'id',
              autoIncrement: true,
            });
          }
        };

        request.onsuccess = () => {
          resolve(request.result);
        };

        request.onerror = () => {
          this.useMemory = true;
          reject(request.error);
        };
      } catch {
        this.useMemory = true;
        reject(new Error('IndexedDB not available'));
      }
    }).catch(() => {
      this.useMemory = true;
      return null as unknown as IDBDatabase;
    });
  }

  private async getDB(): Promise<IDBDatabase | null> {
    if (this.useMemory) return null;
    try {
      const db = await this.dbPromise;
      return db ?? null;
    } catch {
      this.useMemory = true;
      return null;
    }
  }

  async store(events: TrackEvent[]): Promise<void> {
    if (events.length === 0) return;

    const db = await this.getDB();
    if (!db) {
      this.fallbackQueue.push(...events);
      return;
    }

    return new Promise<void>((resolve, reject) => {
      try {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);

        for (const event of events) {
          store.add({ data: event });
        }

        tx.oncomplete = () => resolve();
        tx.onerror = () => {
          // Fallback to memory on transaction error
          this.fallbackQueue.push(...events);
          resolve();
        };
        tx.onabort = () => {
          this.fallbackQueue.push(...events);
          resolve();
        };
      } catch {
        this.fallbackQueue.push(...events);
        reject(new Error('Failed to store events'));
      }
    }).catch(() => {
      // Ensure events end up in memory fallback even on unexpected errors
      // Avoid re-pushing if already pushed in onerror/onabort
    });
  }

  async drain(): Promise<TrackEvent[]> {
    const db = await this.getDB();
    if (!db) {
      const events = [...this.fallbackQueue];
      this.fallbackQueue = [];
      return events;
    }

    return new Promise<TrackEvent[]>((resolve) => {
      try {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const getAllRequest = store.getAll();

        getAllRequest.onsuccess = () => {
          const records = getAllRequest.result as Array<{ id: number; data: TrackEvent }>;
          const events = records.map((r) => r.data);

          // Clear all records
          const clearRequest = store.clear();
          clearRequest.onsuccess = () => {
            // Also drain memory fallback
            const memEvents = [...this.fallbackQueue];
            this.fallbackQueue = [];
            resolve([...events, ...memEvents]);
          };
          clearRequest.onerror = () => {
            const memEvents = [...this.fallbackQueue];
            this.fallbackQueue = [];
            resolve([...events, ...memEvents]);
          };
        };

        getAllRequest.onerror = () => {
          const memEvents = [...this.fallbackQueue];
          this.fallbackQueue = [];
          resolve(memEvents);
        };
      } catch {
        const memEvents = [...this.fallbackQueue];
        this.fallbackQueue = [];
        resolve(memEvents);
      }
    });
  }

  async clear(): Promise<void> {
    this.fallbackQueue = [];

    const db = await this.getDB();
    if (!db) return;

    return new Promise<void>((resolve) => {
      try {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const request = store.clear();
        request.onsuccess = () => resolve();
        request.onerror = () => resolve();
      } catch {
        resolve();
      }
    });
  }

  async count(): Promise<number> {
    const db = await this.getDB();
    if (!db) {
      return this.fallbackQueue.length;
    }

    return new Promise<number>((resolve) => {
      try {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const request = store.count();
        request.onsuccess = () => {
          resolve((request.result as number) + this.fallbackQueue.length);
        };
        request.onerror = () => resolve(this.fallbackQueue.length);
      } catch {
        resolve(this.fallbackQueue.length);
      }
    });
  }
}
