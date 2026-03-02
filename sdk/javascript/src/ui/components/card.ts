import type { ComponentDefinition, RenderContext } from './types';

/**
 * Content card component.
 * Renders a card with optional image, title, description, and action button.
 */
export const CardComponent: ComponentDefinition = {
  name: 'card',

  schema: {
    type: 'object',
    required: ['title'],
    properties: {
      title: { type: 'string', description: 'Card title' },
      description: { type: 'string', description: 'Card description text' },
      imageUrl: { type: 'string', description: 'Card image URL' },
      imageAlt: { type: 'string', description: 'Image alt text' },
      ctaText: { type: 'string', description: 'Action button text' },
      ctaHref: { type: 'string', description: 'Action button link' },
      width: { type: 'string', default: '320px' },
    },
  },

  render(props: Record<string, unknown>, context: RenderContext): HTMLElement {
    const title = (props.title as string) || '';
    const description = props.description as string | undefined;
    const imageUrl = props.imageUrl as string | undefined;
    const imageAlt = (props.imageAlt as string) || title;
    const ctaText = props.ctaText as string | undefined;
    const ctaHref = props.ctaHref as string | undefined;
    const width = (props.width as string) || '320px';

    const card = document.createElement('div');
    card.setAttribute('data-apdl-component', 'card');
    card.style.cssText = `
      display: flex;
      flex-direction: column;
      width: ${width};
      max-width: 100%;
      background: #ffffff;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    `;

    // Image
    if (imageUrl) {
      const img = document.createElement('img');
      img.src = imageUrl;
      img.alt = imageAlt;
      img.style.cssText = `
        width: 100%;
        height: 180px;
        object-fit: cover;
        display: block;
      `;
      img.onerror = () => {
        img.style.display = 'none';
      };
      card.appendChild(img);
    }

    // Content area
    const content = document.createElement('div');
    content.style.cssText = `
      padding: 16px;
      flex: 1;
      display: flex;
      flex-direction: column;
    `;

    // Title
    const titleEl = document.createElement('h3');
    titleEl.textContent = title;
    titleEl.style.cssText = `
      margin: 0 0 8px;
      font-size: 16px;
      font-weight: 600;
      color: #1a1a1a;
      line-height: 1.3;
    `;
    content.appendChild(titleEl);

    // Description
    if (description) {
      const descEl = document.createElement('p');
      descEl.textContent = description;
      descEl.style.cssText = `
        margin: 0 0 16px;
        font-size: 14px;
        color: #666;
        line-height: 1.5;
        flex: 1;
      `;
      content.appendChild(descEl);
    }

    // CTA button
    if (ctaText) {
      const cta = document.createElement('a');
      cta.textContent = ctaText;
      cta.href = ctaHref || '#';
      cta.style.cssText = `
        display: inline-block;
        padding: 8px 16px;
        background-color: #1a73e8;
        color: #fff;
        border-radius: 4px;
        text-decoration: none;
        font-size: 13px;
        font-weight: 600;
        text-align: center;
        cursor: pointer;
        align-self: flex-start;
      `;
      cta.addEventListener('click', (e) => {
        if (!ctaHref || ctaHref === '#') {
          e.preventDefault();
        }
        context.track('card_cta_clicked', { title, ctaText, ctaHref });
      });
      content.appendChild(cta);
    }

    card.appendChild(content);
    context.track('card_rendered', { title });
    return card;
  },

  destroy(element: HTMLElement): void {
    element.remove();
  },
};
