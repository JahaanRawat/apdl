/**
 * Defines the shape of a UI component that can be rendered by the SDK.
 */
export interface ComponentDefinition {
  /** Unique component name, e.g. "banner", "modal", "toast" */
  name: string;

  /** JSON Schema describing the component's expected props */
  schema: ComponentSchema;

  /**
   * Render function that creates DOM elements from the given props.
   * Returns the root element to be mounted into the slot.
   */
  render: (props: Record<string, unknown>, context: RenderContext) => HTMLElement;

  /**
   * Optional cleanup function called when the component is unmounted.
   */
  destroy?: (element: HTMLElement) => void;
}

export interface ComponentSchema {
  type: 'object';
  required?: string[];
  properties: Record<string, SchemaProperty>;
}

export interface SchemaProperty {
  type: 'string' | 'number' | 'boolean' | 'object' | 'array';
  default?: unknown;
  enum?: unknown[];
  description?: string;
}

export interface RenderContext {
  /**
   * Tracks an event from the UI component.
   */
  track: (event: string, properties?: Record<string, unknown>) => void;

  /**
   * Dismisses / removes the component from its slot.
   */
  dismiss: () => void;
}

export interface UIConfig {
  component: string;
  props: Record<string, unknown>;
  slotId?: string;
}
