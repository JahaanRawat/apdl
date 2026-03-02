const NO_CAPTURE_CLASS = 'apdl-no-capture';
const NO_CAPTURE_ATTR = 'data-apdl-no-capture';

/**
 * Checks whether an element should be captured.
 * Returns false if the element or any of its ancestors has the
 * 'apdl-no-capture' class or the 'data-apdl-no-capture' attribute.
 */
export function shouldCapture(element: Element): boolean {
  let current: Element | null = element;

  while (current) {
    if (current.classList?.contains(NO_CAPTURE_CLASS)) {
      return false;
    }

    if (current.hasAttribute?.(NO_CAPTURE_ATTR)) {
      return false;
    }

    current = current.parentElement;
  }

  return true;
}
