import type { FlagConfig } from './types';

type FlagChangeCallback = (flags: FlagConfig[]) => void;

/**
 * In-memory flag configuration store with change notification.
 */
export class FlagCache {
  private flags: Map<string, FlagConfig> = new Map();
  private version = 0;
  private listeners: Set<FlagChangeCallback> = new Set();

  /**
   * Bulk-updates all flag configurations.
   * Increments the version counter and notifies listeners.
   */
  set(flags: FlagConfig[]): void {
    this.flags.clear();
    for (const flag of flags) {
      this.flags.set(flag.key, flag);
    }
    this.version++;
    this.notifyListeners(flags);
  }

  /**
   * Returns the configuration for a single flag by key.
   */
  get(key: string): FlagConfig | undefined {
    return this.flags.get(key);
  }

  /**
   * Returns all flag configurations.
   */
  getAll(): FlagConfig[] {
    return Array.from(this.flags.values());
  }

  /**
   * Returns the current monotonic version counter.
   * Increments each time flags are updated.
   */
  getVersion(): number {
    return this.version;
  }

  /**
   * Registers a callback to be called when flags change.
   * Returns an unsubscribe function.
   */
  onChange(callback: FlagChangeCallback): () => void {
    this.listeners.add(callback);
    return () => {
      this.listeners.delete(callback);
    };
  }

  private notifyListeners(flags: FlagConfig[]): void {
    for (const listener of this.listeners) {
      try {
        listener(flags);
      } catch {
        // Listener errors should not break the notification chain
      }
    }
  }
}
