// Minimal Chrome API stubs so popup.js can be required without errors.
// Tests override these with functional mocks in beforeEach.
global.chrome = {
  storage: {
    local: {
      get: jest.fn(),
      set: jest.fn(),
    },
    onChanged: {
      addListener: jest.fn(),
    },
  },
  tabs: { query: jest.fn() },
  scripting: { executeScript: jest.fn() },
  runtime: { lastError: null },
};

// Stub EventSource globally — tests replace with MockEventSource
global.EventSource = jest.fn();
