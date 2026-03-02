import type {
  ComponentDefinition,
  ComponentSchema,
  SchemaProperty,
} from './components/types';

/**
 * Registry for UI components.
 * Validates props against schema before rendering.
 */
export class ComponentRegistry {
  private components: Map<string, ComponentDefinition> = new Map();

  /**
   * Registers a component definition.
   * Throws if a component with the same name is already registered.
   */
  register(definition: ComponentDefinition): void {
    if (this.components.has(definition.name)) {
      throw new Error(
        `APDL: Component "${definition.name}" is already registered`
      );
    }
    this.components.set(definition.name, definition);
  }

  /**
   * Unregisters a component by name.
   */
  unregister(name: string): boolean {
    return this.components.delete(name);
  }

  /**
   * Returns a registered component definition by name.
   */
  get(name: string): ComponentDefinition | undefined {
    return this.components.get(name);
  }

  /**
   * Returns all registered component names.
   */
  list(): string[] {
    return Array.from(this.components.keys());
  }

  /**
   * Validates a props object against a component's schema.
   * Returns an array of validation error messages (empty if valid).
   */
  validate(
    componentName: string,
    props: Record<string, unknown>
  ): string[] {
    const definition = this.components.get(componentName);
    if (!definition) {
      return [`Component "${componentName}" is not registered`];
    }

    return this.validateSchema(definition.schema, props);
  }

  /**
   * Validates props against a ComponentSchema.
   */
  private validateSchema(
    schema: ComponentSchema,
    props: Record<string, unknown>
  ): string[] {
    const errors: string[] = [];

    // Check required properties
    if (schema.required) {
      for (const key of schema.required) {
        if (props[key] === undefined || props[key] === null) {
          errors.push(`Missing required property: "${key}"`);
        }
      }
    }

    // Validate each provided property against its schema
    for (const [key, value] of Object.entries(props)) {
      const propSchema = schema.properties[key];
      if (!propSchema) {
        // Unknown properties are allowed (pass-through)
        continue;
      }

      const propErrors = this.validateProperty(key, value, propSchema);
      errors.push(...propErrors);
    }

    return errors;
  }

  /**
   * Validates a single property value against its schema definition.
   */
  private validateProperty(
    key: string,
    value: unknown,
    schema: SchemaProperty
  ): string[] {
    const errors: string[] = [];

    // Type checking
    if (value !== undefined && value !== null) {
      const actualType = Array.isArray(value) ? 'array' : typeof value;
      if (actualType !== schema.type) {
        errors.push(
          `Property "${key}" expected type "${schema.type}" but got "${actualType}"`
        );
      }
    }

    // Enum checking
    if (schema.enum && value !== undefined && value !== null) {
      if (!schema.enum.includes(value)) {
        errors.push(
          `Property "${key}" must be one of: ${schema.enum.join(', ')}`
        );
      }
    }

    return errors;
  }

  /**
   * Resolves props with defaults from the schema.
   * Missing optional properties are filled with their default values.
   */
  resolveDefaults(
    componentName: string,
    props: Record<string, unknown>
  ): Record<string, unknown> {
    const definition = this.components.get(componentName);
    if (!definition) return { ...props };

    const resolved: Record<string, unknown> = { ...props };

    for (const [key, schema] of Object.entries(
      definition.schema.properties
    )) {
      if (resolved[key] === undefined && schema.default !== undefined) {
        resolved[key] = schema.default;
      }
    }

    return resolved;
  }
}
