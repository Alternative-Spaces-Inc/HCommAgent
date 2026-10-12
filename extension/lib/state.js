import { serialExecutor } from "./offlinequeue.js";
export const API_BASE_KEY = "hca_api_base";
export const STATE_PREFIX = "hca_state_v2:";
export function stateKey({ origin, deviceId }) {
  if (!origin || !deviceId) throw new Error("A state scope requires origin and device");
  return `${STATE_PREFIX}${encodeURIComponent(origin)}:${deviceId}`;
}
export function createScopedState(storage) {
  const serial = serialExecutor();
  return {
    async read(scope) { const key = stateKey(scope); return (await storage.get(key))[key] || { cache: {}, cursor: null, focusId: null }; },
    update(scope, fn, isCurrent = () => true) {
      return serial(async () => {
        if (!isCurrent()) return null;
        const key = stateKey(scope);
        const state = (await storage.get(key))[key] || { cache: {}, cursor: null, focusId: null };
        const next = fn(state);
        if (!isCurrent()) return null;
        await storage.set({ [key]: next });
        return next;
      });
    },
    clear(scope) { return serial(() => storage.remove(stateKey(scope))); },
  };
}
