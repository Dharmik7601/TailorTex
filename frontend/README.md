# Extension

Chrome MV3 side-panel extension (`frontend/extension/`). Talks to the local FastAPI backend at `http://localhost:8001`.

## Loading

1. Go to `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → select `frontend/extension/`

After any code change, click the reload icon on the extension card, then reopen the side panel.

## Tests

```bash
cd frontend/extension
npm install
npm test
```
