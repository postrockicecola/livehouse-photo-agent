/** 限制首页同时进行的胶片 ``film-render`` 预加载数量，避免滚屏时 CPU 尖峰。 */
const MAX_CONCURRENT = 3;

let active = 0;
const waitQueue: Array<() => void> = [];

export function acquireFilmUpgradeSlot(): Promise<() => void> {
  return new Promise((resolve) => {
    const tryRun = () => {
      if (active < MAX_CONCURRENT) {
        active += 1;
        resolve(() => {
          active = Math.max(0, active - 1);
          const next = waitQueue.shift();
          if (next) next();
        });
        return;
      }
      waitQueue.push(tryRun);
    };
    tryRun();
  });
}
