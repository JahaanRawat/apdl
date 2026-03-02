import { describe, it, expect, vi, beforeEach } from 'vitest';
import { UIRenderer } from '../../src/ui/renderer';
import { ComponentRegistry } from '../../src/ui/registry';
import type { ManualCapture } from '../../src/capture/manual';
import type { ComponentDefinition, UIConfig } from '../../src/ui/components/types';

function createMockCapture(): ManualCapture {
  return {
    trackEvent: vi.fn(),
    identifyUser: vi.fn(),
    groupUser: vi.fn(),
    pageView: vi.fn(),
    reset: vi.fn(),
    getUserId: vi.fn().mockReturnValue(undefined),
    getAnonymousId: vi.fn().mockReturnValue('anon-1'),
    getTraits: vi.fn().mockReturnValue({}),
    getGroupId: vi.fn().mockReturnValue(undefined),
    setAnonymousId: vi.fn(),
  } as unknown as ManualCapture;
}

function createTestComponent(): ComponentDefinition {
  return {
    name: 'test-component',
    schema: {
      type: 'object',
      required: ['text'],
      properties: {
        text: { type: 'string' },
        color: { type: 'string', default: 'blue' },
      },
    },
    render: (props, context) => {
      const el = document.createElement('div');
      el.setAttribute('data-apdl-component', 'test-component');
      el.textContent = props.text as string;
      el.style.color = (props.color as string) || 'blue';

      const dismissBtn = document.createElement('button');
      dismissBtn.textContent = 'Close';
      dismissBtn.addEventListener('click', () => context.dismiss());
      el.appendChild(dismissBtn);

      context.track('test_component_rendered', { text: props.text });
      return el;
    },
    destroy: (element) => {
      element.remove();
    },
  };
}

describe('UIRenderer', () => {
  let registry: ComponentRegistry;
  let capture: ManualCapture;
  let renderer: UIRenderer;
  let slotElement: HTMLElement;

  beforeEach(() => {
    registry = new ComponentRegistry();
    capture = createMockCapture();
    renderer = new UIRenderer(registry, capture);

    slotElement = document.createElement('div');
    slotElement.setAttribute('data-apdl-slot', 'main');
    document.body.appendChild(slotElement);

    registry.register(createTestComponent());
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  describe('render()', () => {
    it('should render a component into the target element', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: { text: 'Hello World' },
      };

      const element = renderer.render(config, slotElement);

      expect(element).not.toBeNull();
      expect(element!.textContent).toContain('Hello World');
      expect(slotElement.children.length).toBe(1);
    });

    it('should track component_rendered event', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: { text: 'Tracked' },
      };

      renderer.render(config, slotElement);

      // Should have been called for the component's own tracking + the renderer's tracking
      expect(capture.trackEvent).toHaveBeenCalledWith(
        'component_rendered',
        expect.objectContaining({ component: 'test-component' })
      );
    });

    it('should resolve default props', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: { text: 'Default color' },
      };

      const element = renderer.render(config, slotElement);
      // The default color should be 'blue'
      expect(element!.style.color).toBe('blue');
    });

    it('should return null for unregistered components', () => {
      const config: UIConfig = {
        component: 'nonexistent',
        props: {},
      };

      const element = renderer.render(config, slotElement);
      expect(element).toBeNull();
    });

    it('should return null for invalid props', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: {}, // Missing required 'text'
      };

      const element = renderer.render(config, slotElement);
      expect(element).toBeNull();
    });
  });

  describe('cleanup()', () => {
    it('should remove a rendered component from the DOM', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: { text: 'Will be removed' },
        slotId: 'main',
      };

      renderer.render(config, slotElement);
      expect(slotElement.children.length).toBe(1);

      renderer.cleanup('main');
      expect(slotElement.children.length).toBe(0);
    });

    it('should handle cleanup of non-existent slot gracefully', () => {
      expect(() => renderer.cleanup('nonexistent')).not.toThrow();
    });
  });

  describe('reconciliation', () => {
    it('should replace previous render in the same slot', () => {
      const config1: UIConfig = {
        component: 'test-component',
        props: { text: 'First' },
        slotId: 'main',
      };

      const config2: UIConfig = {
        component: 'test-component',
        props: { text: 'Second' },
        slotId: 'main',
      };

      renderer.render(config1, slotElement);
      expect(slotElement.children.length).toBe(1);
      expect(slotElement.children[0].textContent).toContain('First');

      renderer.render(config2, slotElement);
      expect(slotElement.children.length).toBe(1);
      expect(slotElement.children[0].textContent).toContain('Second');
    });
  });

  describe('dismiss()', () => {
    it('should remove component when dismiss is called from context', () => {
      const config: UIConfig = {
        component: 'test-component',
        props: { text: 'Dismissible' },
        slotId: 'main',
      };

      const element = renderer.render(config, slotElement);
      expect(slotElement.children.length).toBe(1);

      // Click the dismiss button which calls context.dismiss()
      const button = element!.querySelector('button');
      button!.click();

      expect(slotElement.children.length).toBe(0);
    });
  });

  describe('cleanupAll()', () => {
    it('should clean up all active renders', () => {
      const slot2 = document.createElement('div');
      slot2.setAttribute('data-apdl-slot', 'sidebar');
      document.body.appendChild(slot2);

      renderer.render(
        { component: 'test-component', props: { text: 'A' }, slotId: 'main' },
        slotElement
      );
      renderer.render(
        {
          component: 'test-component',
          props: { text: 'B' },
          slotId: 'sidebar',
        },
        slot2
      );

      expect(slotElement.children.length).toBe(1);
      expect(slot2.children.length).toBe(1);

      renderer.cleanupAll();

      expect(slotElement.children.length).toBe(0);
      expect(slot2.children.length).toBe(0);
    });
  });

  describe('getActiveRender()', () => {
    it('should return active render info', () => {
      renderer.render(
        { component: 'test-component', props: { text: 'Active' }, slotId: 'main' },
        slotElement
      );

      const active = renderer.getActiveRender('main');
      expect(active).toBeDefined();
      expect(active!.componentName).toBe('test-component');
      expect(active!.element).toBeInstanceOf(HTMLElement);
    });

    it('should return undefined for empty slots', () => {
      expect(renderer.getActiveRender('empty')).toBeUndefined();
    });
  });
});
