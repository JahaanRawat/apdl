import type { AutoCaptureConfig } from '../core/config';
import type { ManualCapture } from './manual';
import { shouldCapture } from '../privacy/no-capture';

interface ClickRecord {
  target: EventTarget | null;
  timestamp: number;
}

/**
 * Auto-capture module that listens for DOM events and generates
 * tracking events automatically based on configuration.
 */
export class AutoCapture {
  private config: AutoCaptureConfig;
  private capture: ManualCapture;
  private active = false;

  // Listener references for cleanup
  private clickHandler: ((e: MouseEvent) => void) | null = null;
  private submitHandler: ((e: SubmitEvent) => void) | null = null;
  private inputHandler: ((e: Event) => void) | null = null;
  private scrollHandler: (() => void) | null = null;
  private popstateHandler: (() => void) | null = null;

  // Rage click tracking
  private recentClicks: ClickRecord[] = [];
  private readonly RAGE_CLICK_THRESHOLD = 3;
  private readonly RAGE_CLICK_WINDOW = 500;

  // Scroll depth tracking
  private scrollThresholds = new Set<number>([25, 50, 75, 100]);
  private reportedThresholds = new Set<number>();
  private currentPageUrl = '';

  constructor(config: AutoCaptureConfig, capture: ManualCapture) {
    this.config = config;
    this.capture = capture;
  }

  /**
   * Starts auto-capture by attaching DOM event listeners.
   */
  start(): void {
    if (this.active) return;
    if (typeof document === 'undefined' || typeof window === 'undefined') return;

    this.active = true;

    // Page views
    if (this.config.pageViews) {
      this.capture.pageView();
      this.currentPageUrl = window.location.href;

      this.popstateHandler = () => {
        if (window.location.href !== this.currentPageUrl) {
          this.currentPageUrl = window.location.href;
          this.resetScrollTracking();
          this.capture.pageView();
        }
      };
      window.addEventListener('popstate', this.popstateHandler);
    }

    // Click tracking
    if (this.config.clicks || this.config.rage_clicks) {
      this.clickHandler = (e: MouseEvent) => {
        const target = e.target as Element | null;
        if (!target || !shouldCapture(target)) return;

        if (this.config.clicks) {
          this.capture.trackEvent('$click', {
            tag: target.tagName?.toLowerCase(),
            text: this.getElementText(target),
            href: (target as HTMLAnchorElement).href || undefined,
            id: target.id || undefined,
            classes: target.className || undefined,
            x: e.clientX,
            y: e.clientY,
          });
        }

        if (this.config.rage_clicks) {
          this.detectRageClick(e);
        }
      };
      document.addEventListener('click', this.clickHandler, true);
    }

    // Form submission tracking
    if (this.config.formSubmissions) {
      this.submitHandler = (e: SubmitEvent) => {
        const form = e.target as HTMLFormElement | null;
        if (!form || !shouldCapture(form)) return;

        this.capture.trackEvent('$form_submit', {
          formId: form.id || undefined,
          formName: form.name || undefined,
          formAction: form.action || undefined,
          formMethod: form.method || undefined,
        });
      };
      document.addEventListener('submit', this.submitHandler, true);
    }

    // Input change tracking (debounced)
    if (this.config.inputChanges) {
      this.inputHandler = (e: Event) => {
        const target = e.target as HTMLInputElement | null;
        if (!target || !shouldCapture(target)) return;

        const tagName = target.tagName?.toLowerCase();
        if (tagName !== 'input' && tagName !== 'select' && tagName !== 'textarea') {
          return;
        }

        // Never capture the actual value for privacy
        this.capture.trackEvent('$input_change', {
          tag: tagName,
          inputType: target.type || undefined,
          inputName: target.name || undefined,
          inputId: target.id || undefined,
          hasValue: !!target.value,
        });
      };
      document.addEventListener('change', this.inputHandler, true);
    }

    // Scroll depth tracking
    if (this.config.scrollDepth) {
      this.resetScrollTracking();

      let scrollTimeout: ReturnType<typeof setTimeout> | null = null;
      this.scrollHandler = () => {
        if (scrollTimeout) return;
        scrollTimeout = setTimeout(() => {
          scrollTimeout = null;
          this.trackScrollDepth();
        }, 150);
      };
      window.addEventListener('scroll', this.scrollHandler, { passive: true });
    }
  }

  /**
   * Stops auto-capture and removes all event listeners.
   */
  stop(): void {
    if (!this.active) return;
    this.active = false;

    if (typeof document === 'undefined' || typeof window === 'undefined') return;

    if (this.clickHandler) {
      document.removeEventListener('click', this.clickHandler, true);
      this.clickHandler = null;
    }

    if (this.submitHandler) {
      document.removeEventListener('submit', this.submitHandler, true);
      this.submitHandler = null;
    }

    if (this.inputHandler) {
      document.removeEventListener('change', this.inputHandler, true);
      this.inputHandler = null;
    }

    if (this.scrollHandler) {
      window.removeEventListener('scroll', this.scrollHandler);
      this.scrollHandler = null;
    }

    if (this.popstateHandler) {
      window.removeEventListener('popstate', this.popstateHandler);
      this.popstateHandler = null;
    }
  }

  private detectRageClick(e: MouseEvent): void {
    const now = Date.now();

    // Clean up old clicks outside the window
    this.recentClicks = this.recentClicks.filter(
      (c) => now - c.timestamp < this.RAGE_CLICK_WINDOW
    );

    this.recentClicks.push({ target: e.target, timestamp: now });

    // Check if we have enough clicks on the same element
    const targetClicks = this.recentClicks.filter(
      (c) => c.target === e.target
    );

    if (targetClicks.length >= this.RAGE_CLICK_THRESHOLD) {
      const target = e.target as Element | null;
      this.capture.trackEvent('$rage_click', {
        tag: target?.tagName?.toLowerCase(),
        text: target ? this.getElementText(target) : undefined,
        id: target?.id || undefined,
        classes: target?.className || undefined,
        clickCount: targetClicks.length,
        x: e.clientX,
        y: e.clientY,
      });

      // Reset so we don't fire repeatedly
      this.recentClicks = [];
    }
  }

  private trackScrollDepth(): void {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const docHeight = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight,
      document.body.offsetHeight,
      document.documentElement.offsetHeight
    );
    const winHeight = window.innerHeight;
    const scrollableHeight = docHeight - winHeight;

    if (scrollableHeight <= 0) return;

    const scrollPercent = Math.min(
      100,
      Math.round((scrollTop / scrollableHeight) * 100)
    );

    for (const threshold of this.scrollThresholds) {
      if (scrollPercent >= threshold && !this.reportedThresholds.has(threshold)) {
        this.reportedThresholds.add(threshold);
        this.capture.trackEvent('$scroll_depth', {
          threshold,
          percent: scrollPercent,
        });
      }
    }
  }

  private resetScrollTracking(): void {
    this.reportedThresholds.clear();
  }

  private getElementText(el: Element): string {
    // Get text content, truncated and cleaned
    const text =
      (el as HTMLElement).innerText ||
      el.textContent ||
      (el as HTMLInputElement).value ||
      '';
    return text.trim().substring(0, 255);
  }
}
