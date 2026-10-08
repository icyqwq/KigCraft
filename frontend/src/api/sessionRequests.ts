// Cookie-setting responses must complete in request order, including across tabs.
const lockName = "kigcraft-auth-session";
let tail: Promise<unknown> = Promise.resolve();

export function sequenceSessionRequest<T>(request: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const run = () => {
    signal?.throwIfAborted();
    return request();
  };
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request(lockName, {mode: "exclusive", signal}, run);
  }
  // Older/non-secure contexts still preserve ordering within this page.
  const result = tail.then(run);
  tail = result.catch(() => undefined);
  return result;
}
