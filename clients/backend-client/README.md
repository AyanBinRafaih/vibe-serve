# VibeSys backend client

`@vibesys/backend-client` owns the TypeScript boundary to the Python supervision
backend: generated protocol types, JSONL framing, request correlation, and event
subscriptions.

The package does not project events into application state and has no UI
dependency. Consumers interpret its typed messages in their own state model.

From the repository root:

```bash
pnpm --dir clients/backend-client generate:protocol
pnpm --dir clients/backend-client check
pnpm --dir clients/backend-client test
pnpm --dir clients/backend-client build
```
