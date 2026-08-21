import '@testing-library/jest-dom';

// jsdom does not implement scrollIntoView — mock it so ReasoningFeed's
// useEffect doesn't throw TypeError during the smoke test.
window.HTMLElement.prototype.scrollIntoView = () => {};

// Recharts uses ResizeObserver internally — provide a no-op stub.
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

