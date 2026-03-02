import { describe, it, expect, beforeEach } from 'vitest';
import { ComponentRegistry } from '../../src/ui/registry';
import type { ComponentDefinition } from '../../src/ui/components/types';

function createTestComponent(name: string): ComponentDefinition {
  return {
    name,
    schema: {
      type: 'object',
      required: ['title'],
      properties: {
        title: { type: 'string', description: 'Component title' },
        count: { type: 'number', default: 0 },
        enabled: { type: 'boolean', default: true },
        variant: {
          type: 'string',
          default: 'primary',
          enum: ['primary', 'secondary', 'outline'],
        },
      },
    },
    render: (props, _context) => {
      const el = document.createElement('div');
      el.textContent = props.title as string;
      return el;
    },
  };
}

describe('ComponentRegistry', () => {
  let registry: ComponentRegistry;

  beforeEach(() => {
    registry = new ComponentRegistry();
  });

  describe('register()', () => {
    it('should register a component', () => {
      const component = createTestComponent('test-widget');
      registry.register(component);

      expect(registry.get('test-widget')).toBe(component);
    });

    it('should throw when registering duplicate names', () => {
      const component = createTestComponent('dupe');
      registry.register(component);

      expect(() => registry.register(createTestComponent('dupe'))).toThrow(
        'already registered'
      );
    });

    it('should list all registered component names', () => {
      registry.register(createTestComponent('alpha'));
      registry.register(createTestComponent('beta'));
      registry.register(createTestComponent('gamma'));

      const names = registry.list();
      expect(names).toContain('alpha');
      expect(names).toContain('beta');
      expect(names).toContain('gamma');
      expect(names).toHaveLength(3);
    });
  });

  describe('unregister()', () => {
    it('should remove a registered component', () => {
      registry.register(createTestComponent('removable'));
      expect(registry.get('removable')).toBeDefined();

      const result = registry.unregister('removable');
      expect(result).toBe(true);
      expect(registry.get('removable')).toBeUndefined();
    });

    it('should return false for non-existent component', () => {
      expect(registry.unregister('nonexistent')).toBe(false);
    });
  });

  describe('get()', () => {
    it('should return undefined for unknown components', () => {
      expect(registry.get('unknown')).toBeUndefined();
    });
  });

  describe('validate()', () => {
    beforeEach(() => {
      registry.register(createTestComponent('widget'));
    });

    it('should return empty array for valid props', () => {
      const errors = registry.validate('widget', { title: 'Hello' });
      expect(errors).toEqual([]);
    });

    it('should report missing required properties', () => {
      const errors = registry.validate('widget', {});
      expect(errors).toHaveLength(1);
      expect(errors[0]).toContain('title');
      expect(errors[0]).toContain('required');
    });

    it('should report type mismatches', () => {
      const errors = registry.validate('widget', {
        title: 'Valid',
        count: 'not a number',
      });
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain('count');
      expect(errors[0]).toContain('number');
    });

    it('should report enum violations', () => {
      const errors = registry.validate('widget', {
        title: 'Valid',
        variant: 'invalid-variant',
      });
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain('variant');
    });

    it('should allow unknown properties (pass-through)', () => {
      const errors = registry.validate('widget', {
        title: 'Valid',
        extraProp: 'should be fine',
      });
      expect(errors).toEqual([]);
    });

    it('should report error for unknown component', () => {
      const errors = registry.validate('nonexistent', { title: 'X' });
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain('not registered');
    });

    it('should validate multiple errors at once', () => {
      const errors = registry.validate('widget', {
        count: 'wrong type',
        enabled: 'also wrong',
      });
      // Missing required 'title' + wrong type for 'count' + wrong type for 'enabled'
      expect(errors.length).toBe(3);
    });
  });

  describe('resolveDefaults()', () => {
    beforeEach(() => {
      registry.register(createTestComponent('widget'));
    });

    it('should fill in defaults for missing optional properties', () => {
      const resolved = registry.resolveDefaults('widget', { title: 'Test' });
      expect(resolved).toEqual({
        title: 'Test',
        count: 0,
        enabled: true,
        variant: 'primary',
      });
    });

    it('should not override provided values', () => {
      const resolved = registry.resolveDefaults('widget', {
        title: 'Test',
        count: 42,
        variant: 'secondary',
      });
      expect(resolved.count).toBe(42);
      expect(resolved.variant).toBe('secondary');
    });

    it('should pass through unknown components unchanged', () => {
      const props = { foo: 'bar' };
      const resolved = registry.resolveDefaults('nonexistent', props);
      expect(resolved).toEqual({ foo: 'bar' });
    });
  });
});
